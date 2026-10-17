"""断言求值:类型 × 操作符矩阵与宽容比较规则(设计文档《执行引擎》§5)。

比较规则(刻意收窄):
- 数字与数字字符串互通("200" == 200)
- bool 只与 bool 比较 —— True != 1 也 != "true",否则 {"deleted": true}
  会被断言 "1" 误判通过
- 顺序操作符先数值后字符串;两侧不可比较 → 断言失败并说明原因
"""

import re
from typing import Any

import orjson
from jsonpath_ng import parse as jsonpath_parse

from app.engine.dsl import AssertSpec
from app.engine.results import AssertionResult, ResponseSnapshot
from app.engine.variables import stringify

_ORDERING_OPS = {"gt", "gte", "lt", "lte"}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _to_number(value: Any) -> float | None:
    """数字或数字字符串 → float;bool 与其他类型不参与数值比较。"""
    if _is_number(value):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _both_bool(a: Any, b: Any) -> bool:
    return isinstance(a, bool) and isinstance(b, bool)


def _none_eq(a: Any, b: Any) -> bool:
    """None 的相等语义:两侧都是 None 才相等。"""
    return a is None and b is None


def compare(actual: Any, operator: str, expected: Any) -> bool:
    """单一比较核心,操作符矩阵共用。不可比较的顺序断言返回 False。"""
    if operator == "eq":
        return _eq(actual, expected)
    if operator == "ne":
        return not _eq(actual, expected)
    if operator in _ORDERING_OPS:
        return _ordering(actual, operator, expected)
    if operator == "contains":
        return _contains(actual, expected)
    if operator == "regex_match":
        return re.search(str(expected), stringify(actual)) is not None
    raise ValueError(f"不支持的断言操作符: {operator}")


def _eq(actual: Any, expected: Any) -> bool:
    if _both_bool(actual, expected):
        return actual is expected
    if isinstance(actual, bool) or isinstance(expected, bool):
        return False
    if actual is None or expected is None:
        return _none_eq(actual, expected)

    a_num, b_num = _to_number(actual), _to_number(expected)
    if a_num is not None and b_num is not None:
        return a_num == b_num
    if isinstance(actual, str) and isinstance(expected, str):
        return actual == expected
    # dict / list:结构相等(或jsonpath 提取出对象与字面量比较)
    if type(actual) is type(expected):
        return actual == expected
    return False


def _ordering(actual: Any, operator: str, expected: Any) -> bool:
    a_num, b_num = _to_number(actual), _to_number(expected)
    if a_num is not None and b_num is not None:
        a, b = a_num, b_num
    elif isinstance(actual, str) and isinstance(expected, str):
        a, b = actual, expected
    else:
        return False  # 不可比较(含 bool)→ 断言失败

    if operator == "gt":
        return a > b
    if operator == "gte":
        return a >= b
    if operator == "lt":
        return a < b
    return a <= b


def _contains(actual: Any, expected: Any) -> bool:
    try:
        return expected in actual
    except TypeError:
        return False


def _resolve_source(spec: AssertSpec, response: ResponseSnapshot | None, duration_ms: float | None) -> Any:
    """按断言类型取 actual 值。取不到抛 _SourceFailure。"""
    if spec.type == "status_code":
        if response is None:
            raise _SourceFailure("没有响应可断言")
        return response.status_code

    if spec.type == "response_time_ms":
        if duration_ms is None:
            raise _SourceFailure("没有响应耗时数据")
        return duration_ms

    if spec.type == "jsonpath":
        if response is None:
            raise _SourceFailure("没有响应可断言")
        if not spec.expression:
            raise _SourceFailure("jsonpath 断言缺少 expression")
        try:
            data = orjson.loads(response.body)
        except orjson.JSONDecodeError:
            raise _SourceFailure(
                f"响应体不是 JSON,无法按 {spec.expression} 取值"
            ) from None
        matches = jsonpath_parse(spec.expression).find(data)
        if not matches:
            raise _SourceFailure(f"JSONPath {spec.expression} 未命中")
        return matches[0].value

    raise _SourceFailure(f"不支持的断言类型: {spec.type}")


class _SourceFailure(Exception):
    """断言取值失败 → 该断言不通过,信息进 message。"""


def evaluate(
    spec: AssertSpec,
    response: ResponseSnapshot | None,
    duration_ms: float | None,
) -> AssertionResult:
    try:
        actual = _resolve_source(spec, response, duration_ms)
    except _SourceFailure as exc:
        return AssertionResult(
            type=spec.type,
            operator=spec.operator,
            expected=spec.expected,
            actual=None,
            passed=False,
            message=spec.message or str(exc),
        )

    try:
        passed = compare(actual, spec.operator, spec.expected)
    except ValueError as exc:  # 未知操作符
        return AssertionResult(
            type=spec.type, operator=spec.operator, expected=spec.expected,
            actual=actual, passed=False, message=str(exc),
        )

    message = None
    if not passed:
        message = spec.message or (
            f"断言失败: {spec.type} {spec.operator} 期望 {spec.expected!r},实际 {actual!r}"
        )
    return AssertionResult(
        type=spec.type,
        operator=spec.operator,
        expected=spec.expected,
        actual=actual,
        passed=passed,
        message=message,
    )


def evaluate_all(
    specs: list[AssertSpec],
    response: ResponseSnapshot | None,
    duration_ms: float | None,
) -> list[AssertionResult]:
    """单步内所有断言全部执行,不短路(§5)。"""
    return [evaluate(spec, response, duration_ms) for spec in specs]
