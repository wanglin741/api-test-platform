"""引擎入口缝:execute(dsl, context) 的编排行为。

单步 http 执行、5 层变量、skipped 语义、请求体变体、汇总统计。
HTTP 层全部经 httpx.MockTransport 伪造。
"""

import json

import httpx
import pytest

from app.engine.context import EngineContext
from app.engine.defaults import InMemorySink
from app.engine.dsl import parse_dsl
from app.engine.runner import execute
from tests.engine.conftest import captured_body, json_response, make_context

LOGIN_STEP = {
    "id": "s1",
    "name": "登录",
    "type": "http",
    "request": {"method": "POST", "url": "{{base_url}}/login"},
}


def one_step_doc(**overrides: object) -> dict:
    step = {**LOGIN_STEP, **overrides}
    return {"dsl_version": 1, "config": {}, "steps": [step]}


class TestSingleStep:
    async def test_http_step_passes_with_request_and_response_snapshots(
        self, sink: InMemorySink
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200, {"code": 0}, headers={"X-Request-Id": "req-1"})

        doc = one_step_doc(
            request={
                "method": "POST",
                "url": "https://api.example.com/login",
                "headers": {"Content-Type": "application/json"},
            }
        )
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert summary.status == "success"
        assert summary.total_steps == 1
        assert summary.passed == 1

        result = sink.results[0]
        assert result.status == "passed"
        assert result.request is not None
        assert result.request.method == "POST"
        assert result.request.url == "https://api.example.com/login"
        assert result.request.headers["Content-Type"] == "application/json"
        assert result.response is not None
        assert result.response.status_code == 200
        # httpx 将响应头键规范化为小写(HTTP 头大小写不敏感)
        assert result.response.headers["x-request-id"] == "req-1"
        assert json.loads(result.response.body) == {"code": 0}
        assert result.duration_ms is not None and result.duration_ms >= 0
        assert len(seen) == 1


class TestVariableLayering:
    async def test_case_beats_environment_beats_project(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200, {"url": str(request.url)})

        doc = one_step_doc(request={"method": "GET", "url": "{{token_host}}/who"})
        context = make_context(
            sink,
            handler,
            project_variables={"token_host": "https://project.example.com"},
            environment_variables={"token_host": "https://env.example.com"},
        )
        doc["config"]["variables"] = {"token_host": "https://case.example.com"}

        await execute(parse_dsl(doc), context)

        assert sink.results[0].request.url == "https://case.example.com/who"

    async def test_headers_and_query_resolved(self, sink: InMemorySink) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200)

        doc = one_step_doc(
            request={
                "method": "GET",
                "url": "https://api.example.com/users",
                "headers": {"Authorization": "Bearer {{token}}"},
                "params": {"page": "{{page}}"},
            }
        )
        context = make_context(sink, handler, environment_variables={"token": "t-1", "page": "2"})
        await execute(parse_dsl(doc), context)

        assert seen[0].headers["authorization"] == "Bearer t-1"
        assert sink.results[0].request.query == {"page": "2"}


class TestSkippedSemantics:
    async def test_undefined_variable_skips_step_not_whole_case(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200)

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "ok", "name": "健康检查", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/health"}},
                {"id": "bad", "name": "引用未定义", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/{{missing}}"}},
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert [r.status for r in sink.results] == ["passed", "skipped"]
        assert "missing" in sink.results[1].error_message
        assert summary.skipped == 1
        assert summary.passed == 1
        assert summary.status == "success"  # skipped 不拉低整条 run

    async def test_cycle_reference_skips_step_with_chain(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200)

        doc = {
            "dsl_version": 1,
            "config": {"variables": {"a": "{{b}}", "b": "{{a}}"}},
            "steps": [
                {"id": "s1", "name": "循环", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/{{a}}"}}
            ],
        }
        await execute(parse_dsl(doc), make_context(sink, handler))

        result = sink.results[0]
        assert result.status == "skipped"
        assert "循环引用: a -> b -> a" in result.error_message

    async def test_disabled_step_skipped(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200)

        doc = one_step_doc(enabled=False)
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert sink.results[0].status == "skipped"
        assert summary.total_steps == 1


class TestNetworkErrors:
    async def test_connection_error_marks_step_error(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        doc = one_step_doc(request={"method": "GET", "url": "https://api.example.com/x"})
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert sink.results[0].status == "error"
        assert "connection refused" in sink.results[0].error_message
        assert summary.errored == 1
        assert summary.status == "error"


class TestRequestBodyVariants:
    async def test_json_body_string_content_with_type_inference(
        self, sink: InMemorySink
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200)

        doc = one_step_doc(
            request={
                "method": "POST",
                "url": "https://api.example.com/users",
                "body": {"type": "json", "content": '{"id": "{{user_id}}", "name": "u-{{user_id}}"}'},
            }
        )
        context = make_context(sink, handler)
        doc["config"]["variables"] = {"user_id": 5}
        await execute(parse_dsl(doc), context)

        sent = captured_body(seen[0])
        # 整体占位符 → 原生 int;拼接 → 字符串
        assert sent == {"id": 5, "name": "u-5"}
        assert seen[0].headers["content-type"].startswith("application/json")

    async def test_json_body_dict_content(self, sink: InMemorySink) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200)

        doc = one_step_doc(
            request={
                "method": "POST",
                "url": "https://api.example.com/users",
                "body": {"type": "json", "content": {"id": "{{user_id}}"}},
            }
        )
        context = make_context(sink, handler)
        doc["config"]["variables"] = {"user_id": 7}
        await execute(parse_dsl(doc), context)

        assert captured_body(seen[0]) == {"id": 7}

    async def test_urlencoded_body(self, sink: InMemorySink) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200)

        doc = one_step_doc(
            request={
                "method": "POST",
                "url": "https://api.example.com/token",
                "body": {"type": "urlencoded", "content": {"grant_type": "{{grant}}"}},
            }
        )
        context = make_context(sink, handler)
        doc["config"]["variables"] = {"grant": "client_credentials"}
        await execute(parse_dsl(doc), context)

        assert seen[0].headers["content-type"] == "application/x-www-form-urlencoded"
        assert captured_body(seen[0]) == "grant_type=client_credentials"

    async def test_form_data_body_is_multipart(self, sink: InMemorySink) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200)

        doc = one_step_doc(
            request={
                "method": "POST",
                "url": "https://api.example.com/upload",
                "body": {"type": "form-data", "content": {"field": "value"}},
            }
        )
        await execute(parse_dsl(doc), make_context(sink, handler))

        assert seen[0].headers["content-type"].startswith("multipart/form-data")
        assert b"value" in seen[0].read()

    async def test_raw_body(self, sink: InMemorySink) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return json_response(200)

        doc = one_step_doc(
            request={
                "method": "POST",
                "url": "https://api.example.com/echo",
                "body": {"type": "raw", "content": "hello {{who}}"},
            }
        )
        context = make_context(sink, handler)
        doc["config"]["variables"] = {"who": "world"}
        await execute(parse_dsl(doc), context)

        assert captured_body(seen[0]) == "hello world"


class TestSummary:
    async def test_summary_counts_and_duration(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200)

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "a", "name": "a", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/a"}},
                {"id": "b", "name": "b", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/{{undef}}"}},
                {"id": "c", "name": "c", "type": "http", "enabled": False,
                 "request": {"method": "GET", "url": "https://api.example.com/c"}},
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert (summary.total_steps, summary.passed, summary.skipped) == (3, 1, 2)
        assert summary.duration_ms >= 0
