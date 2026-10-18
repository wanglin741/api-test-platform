"""引擎缝上的提取→引用→断言链路,即 spec 验收场景的引擎部分。

登录提取 token → 下一步引用并断言;提取失败与断言失败的状态语义。
"""

import httpx

from app.engine.defaults import InMemorySink
from app.engine.dsl import parse_dsl
from app.engine.runner import execute
from tests.engine.conftest import json_response, make_context

TWO_STEP_DOC = {
    "dsl_version": 1,
    "config": {"variables": {"base_url": "https://api.example.com"}},
    "steps": [
        {
            "id": "login",
            "name": "登录",
            "type": "http",
            "request": {
                "method": "POST",
                "url": "{{base_url}}/login",
                "body": {"type": "json", "content": '{"user": "alice"}'},
            },
            "extract": [
                {"name": "token", "type": "jsonpath", "expression": "$.data.token"},
                {"name": "resp_code", "type": "jsonpath", "expression": "$.code"},
            ],
            "assert": [
                {"type": "status_code", "operator": "eq", "expected": 200},
                {"type": "jsonpath", "operator": "eq", "expected": 0, "expression": "$.code"},
            ],
        },
        {
            "id": "query",
            "name": "查询",
            "type": "http",
            "request": {
                "method": "GET",
                "url": "{{base_url}}/orders",
                "headers": {"Authorization": "Bearer {{token}}"},
            },
            "assert": [
                {"type": "status_code", "operator": "eq", "expected": 200},
                {"type": "jsonpath", "operator": "eq", "expected": 0, "expression": "$.code"},
            ],
        },
    ],
}


def chain_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/login":
        return json_response(200, {"code": 0, "data": {"token": "tk-9"}})
    return json_response(200, {"code": 0, "data": {"orders": []}})


class TestChainedCase:
    async def test_extract_then_reference_then_assert(self, sink: InMemorySink) -> None:
        seen_auth: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_auth.append(request.headers.get("authorization", ""))
            return chain_handler(request)

        summary = await execute(parse_dsl(TWO_STEP_DOC), make_context(sink, handler))

        assert summary.status == "success"
        assert [r.status for r in sink.results] == ["passed", "passed"]
        assert sink.results[0].extracted == {"token": "tk-9", "resp_code": 0}
        assert seen_auth[1] == "Bearer tk-9"
        assert all(a.passed for r in sink.results for a in r.assertions)

    async def test_header_extractor_feeds_next_step(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/login":
                return json_response(200, {}, headers={"X-Trace-Id": "tr-7"})
            return json_response(200, {"trace": request.headers.get("x-trace-id")})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/login"},
                    "extract": [{"name": "trace", "type": "header", "expression": "X-Trace-Id"}],
                },
                {
                    "id": "s2", "name": "s2", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/{{trace}}"},
                },
            ],
        }
        await execute(parse_dsl(doc), make_context(sink, handler))

        assert sink.results[0].extracted == {"trace": "tr-7"}
        assert sink.results[1].request.url == "https://api.example.com/tr-7"


class TestExtractionFailureSemantics:
    async def test_required_extraction_failure_fails_step(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {"code": 500, "msg": "boom"})  # 没有 data.token

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "extract": [
                        {"name": "token", "type": "jsonpath", "expression": "$.data.token"}
                    ],
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        result = sink.results[0]
        assert result.status == "failed"
        assert "$.data.token" in result.error_message
        assert "boom" in result.error_message  # 响应体片段
        assert summary.status == "failed"

    async def test_optional_extraction_failure_does_not_fail_step_but_later_ref_skips(
        self, sink: InMemorySink
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {"code": 0})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "extract": [
                        {"name": "token", "type": "jsonpath", "expression": "$.data.token",
                         "required": False}
                    ],
                },
                {
                    "id": "s2", "name": "s2", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/{{token}}"},
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert [r.status for r in sink.results] == ["passed", "skipped"]
        assert summary.status == "success"  # 链条断在第 2 步,第 1 步本身没问题

    async def test_status_code_extractor(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(201, {})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "extract": [{"name": "sc", "type": "status_code"}],
                    "assert": [
                        {"type": "jsonpath", "operator": "eq", "expected": 201,
                         "expression": "x"}  # 占位,真正校验在 extracted
                    ],
                },
            ],
        }
        # status_code 提取值可直接被下一步引用;这里验证提取进结果
        doc["steps"][0]["assert"] = []
        await execute(parse_dsl(doc), make_context(sink, handler))
        assert sink.results[0].extracted == {"sc": 201}


class TestAssertionFailureSemantics:
    async def test_all_assertions_run_no_short_circuit(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(500, {"code": 99})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "assert": [
                        {"type": "status_code", "operator": "eq", "expected": 200},
                        {"type": "jsonpath", "operator": "eq", "expected": 0,
                         "expression": "$.code"},
                        {"type": "status_code", "operator": "lt", "expected": 600},
                    ],
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        result = sink.results[0]
        assert [a.passed for a in result.assertions] == [False, False, True]
        assert result.status == "failed"
        assert summary.failed == 1
        assert summary.status == "failed"

    async def test_failed_is_not_error(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(500, {})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert sink.results[0].status == "failed"
        assert sink.results[0].error_message is not None
        assert summary.status == "failed"  # failed 与 error 是两类终态


class TestMalformedExpression:
    """用户写错表达式是常态:断言/提取失败,而不是引擎崩溃把 run 卡在 running。"""

    async def test_malformed_jsonpath_assertion_fails_gracefully(
        self, sink: InMemorySink
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {"a": 1})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "assert": [
                        {"type": "jsonpath", "operator": "eq", "expected": 1,
                         "expression": "$.a["}
                    ],
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        result = sink.results[0]
        assert result.status == "failed"
        assert result.request is not None and result.response is not None  # 现场保留
        assert result.assertions[0].passed is False
        assert "不合法" in (result.assertions[0].message or "")
        assert summary.status == "failed"

    async def test_required_extract_with_malformed_jsonpath_fails_step(
        self, sink: InMemorySink
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {"a": 1})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "extract": [
                        {"name": "bad", "type": "jsonpath", "expression": "$.a["}
                    ],
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert sink.results[0].status == "failed"
        assert "不合法" in (sink.results[0].error_message or "")
        assert summary.status == "failed"

    async def test_optional_extract_with_malformed_jsonpath_skips_later_ref(
        self, sink: InMemorySink
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {"a": 1})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "extract": [
                        {"name": "bad", "type": "jsonpath", "expression": "$.a[",
                         "required": False}
                    ],
                },
                {
                    "id": "s2", "name": "s2", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/{{bad}}"},
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert [r.status for r in sink.results] == ["passed", "skipped"]
        assert summary.status == "success"

    async def test_invalid_regex_pattern_fails_assertion_not_crash(
        self, sink: InMemorySink
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {})

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {
                    "id": "s1", "name": "s1", "type": "http",
                    "request": {"method": "GET", "url": "https://api.example.com/x"},
                    "assert": [
                        {"type": "status_code", "operator": "regex_match", "expected": "["}
                    ],
                },
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        result = sink.results[0]
        assert result.status == "failed"
        assert "正则表达式不合法" in (result.assertions[0].message or "")
