"""引擎韧性:错误分类、步骤级重试、超时分层、取消协作点。

设计文档《执行引擎》§9/§10 与调研文档取消缺陷表的落地验证:
- 只有网络类 error 重试;断言 failed 是确定的业务结果,不重试
- 取消不是强制中断,在协作检查点生效;run 终态 stopped
- 用例总时长超限 → 剩余步骤 skipped,run 终态 error
"""

import asyncio
import time

import httpx
import pytest

from app.engine.context import EngineContext
from app.engine.defaults import InMemorySink
from app.engine.dsl import parse_dsl
from app.engine.runner import execute
from app.engine.steps.http import build_request_timeout
from tests.engine.conftest import json_response, make_context


def step_doc(**step_overrides: object) -> dict:
    return {
        "dsl_version": 1,
        "config": {},
        "steps": [
            {
                "id": "s1",
                "name": "s1",
                "type": "http",
                "request": {"method": "GET", "url": "https://api.example.com/x"},
                **step_overrides,
            }
        ],
    }


class CancelAfter:
    """第 N 次检查之后开始报告已取消(模拟执行中途收到取消信号)。"""

    def __init__(self, after_checks: int) -> None:
        self.after_checks = after_checks
        self.checks = 0

    def cancelled(self) -> bool:
        self.checks += 1
        return self.checks > self.after_checks


class TestRetryClassification:
    async def test_network_error_retried_until_success(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise httpx.ConnectError("boom")
            return json_response(200)

        doc = step_doc(retry={"times": 2, "interval_ms": 10})
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert calls == 3
        assert sink.results[0].status == "passed"
        assert sink.results[0].attempts == 3
        assert summary.status == "success"

    async def test_retry_exhausted_marks_error(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("down")

        doc = step_doc(retry={"times": 2, "interval_ms": 10})
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert calls == 3  # 首次 + 2 次重试
        assert sink.results[0].status == "error"
        assert sink.results[0].attempts == 3
        assert summary.status == "error"

    async def test_assertion_failure_never_retried(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return json_response(500, {"code": 99})

        doc = step_doc(
            **{
                "retry": {"times": 3, "interval_ms": 10},
                "assert": [{"type": "status_code", "operator": "eq", "expected": 200}],
            }
        )
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert calls == 1  # 断言失败是确定结果,不重试
        assert sink.results[0].status == "failed"
        assert sink.results[0].attempts == 1
        assert summary.status == "failed"

    async def test_5xx_with_failing_assertion_not_retried(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return json_response(503)

        doc = step_doc(
            **{
                "retry": {"times": 2, "interval_ms": 10},
                "assert": [{"type": "status_code", "operator": "lt", "expected": 500}],
            }
        )
        await execute(parse_dsl(doc), make_context(sink, handler))

        assert calls == 1
        assert sink.results[0].status == "failed"

    async def test_read_timeout_classified_as_error_and_retried(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ReadTimeout("timed out")

        doc = step_doc(retry={"times": 1, "interval_ms": 10})
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert calls == 2
        assert sink.results[0].status == "error"
        assert "ReadTimeout" in sink.results[0].error_message

    async def test_variables_rebuilt_each_attempt(self, sink: InMemorySink) -> None:
        """重试必须重新求值变量(§9):两次请求都携带解析后的头。"""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers.get("authorization", ""))
            raise httpx.ConnectError("down")

        doc = step_doc(
            request={
                "method": "GET",
                "url": "https://api.example.com/x",
                "headers": {"Authorization": "Bearer {{token}}"},
            },
            retry={"times": 1, "interval_ms": 10},
        )
        doc["config"]["variables"] = {"token": "tk-1"}
        await execute(parse_dsl(doc), make_context(sink, handler))

        assert seen == ["Bearer tk-1", "Bearer tk-1"]


class TestRetryTiming:
    async def test_exponential_backoff_waits(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("down")

        doc = step_doc(retry={"times": 2, "interval_ms": 60, "backoff": "exponential"})
        start = time.perf_counter()
        await execute(parse_dsl(doc), make_context(sink, handler))
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 间隔 60 + 120(指数)≈ 180ms;给 CI 抖动留裕量
        assert elapsed_ms > 120


class TestTimeoutLayering:
    def test_step_timeout_overrides_case_default(self) -> None:
        t = build_request_timeout(step_timeout_ms=3000, default_timeout_ms=10_000)
        assert t.read == 3.0

    def test_falls_back_to_case_default(self) -> None:
        t = build_request_timeout(step_timeout_ms=None, default_timeout_ms=10_000)
        assert t.read == 10.0

    def test_connect_and_pool_capped_at_5s(self) -> None:
        t = build_request_timeout(step_timeout_ms=30_000, default_timeout_ms=30_000)
        assert t.connect == 5.0
        assert t.pool == 5.0

    async def test_case_max_duration_stops_remaining_steps_as_error(
        self, sink: InMemorySink
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            time.sleep(0.02)  # 步骤 a 消耗 20ms,超出 1ms 总预算
            return json_response(200)

        doc = {
            "dsl_version": 1,
            "config": {"max_duration_ms": 1},
            "steps": [
                {"id": "a", "name": "a", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/a"}},
                {"id": "b", "name": "b", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/b"}},
            ],
        }
        summary = await execute(parse_dsl(doc), make_context(sink, handler))

        assert [r.status for r in sink.results] == ["passed", "skipped"]
        assert "总时长" in sink.results[1].error_message
        assert summary.status == "error"


class TestCancellation:
    async def test_cancelled_before_start_all_steps_skipped(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return json_response(200)

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "a", "name": "a", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/a"}},
                {"id": "b", "name": "b", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/b"}},
            ],
        }
        context = make_context(sink, handler, cancellation=CancelAfter(0))
        summary = await execute(parse_dsl(doc), context)

        assert calls == 0
        assert all(r.status == "skipped" for r in sink.results)
        assert summary.status == "stopped"

    async def test_cancel_midrun_keeps_completed_steps(self, sink: InMemorySink) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return json_response(200)

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "a", "name": "a", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/a"}},
                {"id": "b", "name": "b", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/b"}},
                {"id": "c", "name": "c", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/c"}},
            ],
        }
        # 第 1 次检查(步骤 a 前)未取消,之后取消 → a 保留,b/c skipped
        context = make_context(sink, handler, cancellation=CancelAfter(1))
        summary = await execute(parse_dsl(doc), context)

        assert [r.status for r in sink.results] == ["passed", "skipped", "skipped"]
        assert summary.status == "stopped"
        assert summary.passed == 1

    async def test_cancel_during_retry_backoff_stops_retrying(self, sink: InMemorySink) -> None:
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("down")

        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "a", "name": "a", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/a"},
                 "retry": {"times": 5, "interval_ms": 5000}},
                {"id": "b", "name": "b", "type": "http",
                 "request": {"method": "GET", "url": "https://api.example.com/b"}},
            ],
        }
        context = make_context(sink, handler, cancellation=CancelAfter(1))
        start = time.perf_counter()
        summary = await execute(parse_dsl(doc), context)
        elapsed = time.perf_counter() - start

        assert calls == 1  # 首次失败后,退避等待期间发现取消,不再重试
        assert elapsed < 2.0  # 分段 sleep 生效,没有傻等 5s
        assert sink.results[0].status == "error"
        assert sink.results[0].attempts == 1
        assert sink.results[1].status == "skipped"
        assert summary.status == "stopped"
