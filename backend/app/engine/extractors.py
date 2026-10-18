"""提取器:从响应中取值注入运行时变量(设计文档《执行引擎》§4)。

MVP 支持 jsonpath / header / status_code。提取发生在断言执行前;
required: true 提取不到 → 步骤 failed,required: false → 变量不注入。
"""

from typing import Any

import orjson
from jsonpath_ng import parse as jsonpath_parse
from jsonpath_ng.exceptions import JsonPathParserError

from app.engine.dsl import ExtractSpec
from app.engine.results import ResponseSnapshot

_BODY_SNIPPET_LEN = 200


class ExtractionFailure(Exception):
    """提取不到值。required: true 时步骤 failed。"""


def extract_value(spec: ExtractSpec, response: ResponseSnapshot) -> Any:
    """按提取器规格从响应取值。取不到抛 ExtractionFailure。"""
    if spec.type == "status_code":
        return response.status_code

    if spec.type == "header":
        # HTTP 头大小写不敏感
        if spec.expression is None:
            raise ExtractionFailure(f"header 提取器缺少 expression(头名): {spec.name}")
        wanted = spec.expression.lower()
        for key, value in response.headers.items():
            if key.lower() == wanted:
                return value
        raise ExtractionFailure(f"响应头 {spec.expression} 不存在")

    if spec.type == "jsonpath":
        # 空串与 None 同样视为缺少表达式(前端表单允许留空保存)
        if not spec.expression:
            raise ExtractionFailure(f"jsonpath 提取器缺少 expression: {spec.name}")
        try:
            data = orjson.loads(response.body)
        except orjson.JSONDecodeError:
            snippet = response.body[:_BODY_SNIPPET_LEN]
            raise ExtractionFailure(
                f"响应体不是 JSON,无法按 {spec.expression} 提取;响应体片段: {snippet}"
            ) from None
        try:
            matches = jsonpath_parse(spec.expression).find(data)
        except JsonPathParserError as exc:
            # 表达式不合法是用户输入错误:required 语义照常生效,不让 run 崩溃
            raise ExtractionFailure(
                f"JSONPath 表达式不合法 {spec.expression!r}: {exc}"
            ) from None
        if not matches:
            snippet = response.body[:_BODY_SNIPPET_LEN]
            raise ExtractionFailure(
                f"JSONPath {spec.expression} 未命中;响应体片段: {snippet}"
            )
        return matches[0].value

    raise ExtractionFailure(f"不支持的提取器类型: {spec.type}")
