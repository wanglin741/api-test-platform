"""断言矩阵:3 类型 × 8 操作符,重点覆盖类型宽容规则。

设计文档《执行引擎》§5 的比较规则:
- 数字与数字字符串互通("200" == 200)
- bool 只与 bool 比较(True != 1,True != "true")
- 其余一律不等;顺序操作符先数值后字符串,不可比较即失败
"""

import pytest

from app.engine.assertions import evaluate
from app.engine.dsl import AssertSpec
from app.engine.results import ResponseSnapshot

JSON_RESPONSE = ResponseSnapshot(
    status_code=200,
    headers={"content-type": "application/json"},
    body='{"code": 0, "data": {"name": "alice", "tags": ["a", "b"], "score": 88}}',
)


def assert_spec(**kwargs: object) -> AssertSpec:
    return AssertSpec(**kwargs)  # type: ignore[arg-type]


class TestStatusCodeSource:
    def test_eq_pass(self) -> None:
        r = evaluate(assert_spec(type="status_code", operator="eq", expected=200), JSON_RESPONSE, 12.0)
        assert r.passed is True

    def test_eq_fail_message_shows_actual(self) -> None:
        r = evaluate(assert_spec(type="status_code", operator="eq", expected=201), JSON_RESPONSE, 12.0)
        assert r.passed is False
        assert "200" in r.message


class TestJsonpathSource:
    def test_extracts_and_compares(self) -> None:
        r = evaluate(
            assert_spec(type="jsonpath", operator="eq", expected=0, expression="$.code"),
            JSON_RESPONSE,
            12.0,
        )
        assert r.passed is True

    def test_missing_path_fails(self) -> None:
        r = evaluate(
            assert_spec(type="jsonpath", operator="eq", expected=1, expression="$.missing"),
            JSON_RESPONSE,
            12.0,
        )
        assert r.passed is False

    def test_non_json_body_fails(self) -> None:
        resp = ResponseSnapshot(status_code=200, headers={}, body="plain")
        r = evaluate(
            assert_spec(type="jsonpath", operator="eq", expected=1, expression="$.x"),
            resp,
            12.0,
        )
        assert r.passed is False

    def test_custom_message_used_on_failure(self) -> None:
        r = evaluate(
            assert_spec(type="jsonpath", operator="eq", expected=0, expression="$.code",
                        message="业务码应为 0"),
            JSON_RESPONSE,
            12.0,
        )
        r2 = evaluate(
            assert_spec(type="jsonpath", operator="eq", expected=1, expression="$.code",
                        message="业务码应为 0"),
            JSON_RESPONSE,
            12.0,
        )
        assert "业务码应为 0" in r2.message
        assert r.passed is True


class TestResponseTimeSource:
    def test_lte_pass(self) -> None:
        r = evaluate(
            assert_spec(type="response_time_ms", operator="lte", expected=100),
            JSON_RESPONSE,
            42.5,
        )
        assert r.passed is True
        assert r.actual == 42.5

    def test_without_duration_fails(self) -> None:
        r = evaluate(
            assert_spec(type="response_time_ms", operator="lte", expected=100),
            JSON_RESPONSE,
            None,
        )
        assert r.passed is False


class TestOperatorEq:
    @pytest.mark.parametrize(
        ("actual", "expected", "passed"),
        [
            (200, 200, True),
            ("200", 200, True),  # 数字字符串互通
            (200, "200", True),
            ("200", "200", True),
            (200, 201, False),
            (200.0, 200, True),
            ("abc", "abc", True),
            ("abc", "abd", False),
            (True, True, True),
            (True, "true", False),  # bool 不与字符串互通
            (True, 1, False),  # bool 不与数字互通(尽管 Python 里 bool 是 int 子类)
            (False, False, True),
            (None, None, True),
            (None, 0, False),
        ],
    )
    def test_cases(self, actual: object, expected: object, passed: bool) -> None:
        assert is_comparable_eq(actual, expected) is passed


def is_comparable_eq(actual: object, expected: object) -> bool:
    """直接驱动比较核心(操作符矩阵的表格入口)。"""
    from app.engine.assertions import compare

    return compare(actual, "eq", expected)


class TestOperatorNe:
    @pytest.mark.parametrize(
        ("actual", "expected", "passed"),
        [
            (200, 201, True),
            (200, 200, False),
            ("abc", 200, True),  # 不可比较 ≠ 相等 → ne 成立
        ],
    )
    def test_cases(self, actual: object, expected: object, passed: bool) -> None:
        from app.engine.assertions import compare

        assert compare(actual, "ne", expected) is passed


class TestOperatorOrdering:
    @pytest.mark.parametrize(
        ("actual", "op", "expected", "passed"),
        [
            (5, "gt", 3, True),
            (5, "gt", 5, False),
            (5, "gte", 5, True),
            ("5", "gte", 5, True),  # 数字字符串互通
            (3, "lt", 5, True),
            (3, "lte", 3, True),
            (2.5, "gt", 2, True),
            ("b", "gt", "a", True),  # 字符串退化为字典序
            ("abc", "gt", 3, False),  # 不可比较 → 断言失败
        ],
    )
    def test_cases(self, actual: object, op: str, expected: object, passed: bool) -> None:
        from app.engine.assertions import compare

        assert compare(actual, op, expected) is passed


class TestOperatorContains:
    @pytest.mark.parametrize(
        ("actual", "expected", "passed"),
        [
            ("hello world", "world", True),
            ("hello", "xyz", False),
            (["a", "b"], "a", True),
            ([1, 2, 3], 2, True),
            ([1, 2, 3], 4, False),
            (123, "1", False),  # actual 不是容器 → 失败
        ],
    )
    def test_cases(self, actual: object, expected: object, passed: bool) -> None:
        from app.engine.assertions import compare

        assert compare(actual, "contains", expected) is passed


class TestOperatorRegexMatch:
    @pytest.mark.parametrize(
        ("actual", "expected", "passed"),
        [
            ("token tk-1 issued", r"tk-\d+", True),
            ("no match here", r"tk-\d+", False),
            ("line1\nline2", r"^line2", False),  # search 语义,无 re.MULTILINE
            (200, r"^2", True),  # actual 先转字符串
        ],
    )
    def test_cases(self, actual: object, expected: object, passed: bool) -> None:
        from app.engine.assertions import compare

        assert compare(actual, "regex_match", expected) is passed


class TestJsonpathListValue:
    def test_contains_on_extracted_array(self) -> None:
        r = evaluate(
            assert_spec(type="jsonpath", operator="contains", expected="a",
                        expression="$.data.tags"),
            JSON_RESPONSE,
            1.0,
        )
        assert r.passed is True
