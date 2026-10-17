"""提取器:jsonpath / header / status_code,与提取失败语义。

设计文档《执行引擎》§4:
- required: true(默认)→ 提取不到则步骤 failed,错误信息含表达式和响应体片段
- required: false → 变量不注入,后续引用它的步骤会 skipped
- header 大小写不敏感
"""

import pytest

from app.engine.dsl import ExtractSpec
from app.engine.extractors import ExtractionFailure, extract_value
from app.engine.results import ResponseSnapshot

JSON_RESPONSE = ResponseSnapshot(
    status_code=200,
    headers={"content-type": "application/json", "x-request-id": "req-42"},
    body='{"code": 0, "data": {"token": "tk-1", "items": [10, 20]}}',
)


def spec(**kwargs: object) -> ExtractSpec:
    return ExtractSpec(name="v", **kwargs)  # type: ignore[arg-type]


class TestJsonpath:
    def test_extracts_nested_value(self) -> None:
        assert extract_value(spec(type="jsonpath", expression="$.data.token"), JSON_RESPONSE) == "tk-1"

    def test_extracts_array_element(self) -> None:
        assert extract_value(spec(type="jsonpath", expression="$.data.items[1]"), JSON_RESPONSE) == 20

    def test_extracts_object(self) -> None:
        assert extract_value(spec(type="jsonpath", expression="$.data"), JSON_RESPONSE) == {
            "token": "tk-1",
            "items": [10, 20],
        }

    def test_missing_path_fails_with_expression_and_body_snippet(self) -> None:
        with pytest.raises(ExtractionFailure) as exc:
            extract_value(spec(type="jsonpath", expression="$.data.missing"), JSON_RESPONSE)
        assert "$.data.missing" in str(exc.value)
        assert "tk-1" in str(exc.value)  # 响应体片段

    def test_non_json_body_fails(self) -> None:
        resp = ResponseSnapshot(status_code=200, headers={}, body="<html>oops</html>")
        with pytest.raises(ExtractionFailure):
            extract_value(spec(type="jsonpath", expression="$.x"), resp)


class TestHeader:
    def test_case_insensitive(self) -> None:
        assert extract_value(spec(type="header", expression="X-Request-Id"), JSON_RESPONSE) == "req-42"

    def test_missing_header_fails(self) -> None:
        with pytest.raises(ExtractionFailure):
            extract_value(spec(type="header", expression="X-Missing"), JSON_RESPONSE)


class TestStatusCode:
    def test_extracts_status_code(self) -> None:
        assert extract_value(spec(type="status_code"), JSON_RESPONSE) == 200
