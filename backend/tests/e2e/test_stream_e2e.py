"""真实 socket 路径的截断与超时 e2e(spec #1 测试决策第 2 条)。

MockTransport 绕过 socket 层,`client.stream()` 的截断行为 mock 与真实
不一致,而截断写错的后果是 worker OOM —— 所以这些用例打真实本地服务。
默认测试命令不执行(pyproject addopts 已排除 e2e),单独跑:

    uv run pytest -m e2e
"""

import tracemalloc

import httpx
import pytest

from app.engine.context import EngineContext
from app.engine.defaults import InMemorySink
from app.engine.dsl import parse_dsl
from app.engine.runner import execute
from tests.e2e.server import E2EServer

BODY_LIMIT = 64 * 1024


@pytest.fixture
def server() -> E2EServer:
    import threading

    srv = E2EServer()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def make_context_for(sink: InMemorySink, port: int, **kwargs: object) -> EngineContext:
    return EngineContext(
        sink=sink,
        http_client_factory=lambda verify=True: httpx.AsyncClient(verify=verify),
        body_max_bytes=BODY_LIMIT,
        **kwargs,
    )


def single_step(
    port: int, path: str, step_overrides: dict | None = None, **request_overrides: object
) -> dict:
    step = {
        "id": "s1",
        "name": "s1",
        "type": "http",
        "request": {"method": "GET", "url": f"http://127.0.0.1:{port}{path}", **request_overrides},
        **(step_overrides or {}),
    }
    return {"dsl_version": 1, "config": {}, "steps": [step]}


class TestRealSocketE2E:
    @pytest.mark.e2e
    async def test_normal_json_step_passes(self, sink: InMemorySink, server: E2EServer) -> None:
        doc = single_step(server.server_address[1], "/json")
        summary = await execute(parse_dsl(doc), make_context_for(sink, server.server_address[1]))

        assert summary.status == "success"
        assert sink.results[0].response.truncated is False
        assert '"code": 0' in sink.results[0].response.body

    @pytest.mark.e2e
    async def test_huge_response_truncated_with_bounded_memory(
        self, sink: InMemorySink, server: E2EServer
    ) -> None:
        port = server.server_address[1]
        doc = single_step(port, "/huge")

        tracemalloc.start()
        baseline = tracemalloc.get_traced_memory()[0]
        summary = await execute(parse_dsl(doc), make_context_for(sink, port))
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        assert summary.status == "success"
        resp = sink.results[0].response
        assert resp.truncated is True
        assert len(resp.body) == BODY_LIMIT
        # 50MB 响应,引擎内存峰值必须在截断阈值的量级(64KB),而不是随响应体线性增长
        assert peak - baseline < 8 * 1024 * 1024

    @pytest.mark.e2e
    async def test_real_read_timeout_classified_as_error(
        self, sink: InMemorySink, server: E2EServer
    ) -> None:
        port = server.server_address[1]
        doc = single_step(port, "/slow", timeout_ms=300)
        summary = await execute(parse_dsl(doc), make_context_for(sink, port))

        result = sink.results[0]
        assert result.status == "error"
        assert "ReadTimeout" in result.error_message
        assert 0.2 <= result.duration_ms / 1000 < 1.5  # 300ms 超时生效,而非等满 2s
        assert summary.status == "error"

    @pytest.mark.e2e
    async def test_connection_reused_across_steps(self, sink: InMemorySink, server: E2EServer) -> None:
        port = server.server_address[1]
        doc = {
            "dsl_version": 1,
            "config": {},
            "steps": [
                {"id": "a", "name": "a", "type": "http",
                 "request": {"method": "GET", "url": f"http://127.0.0.1:{port}/json"}},
                {"id": "b", "name": "b", "type": "http",
                 "request": {"method": "GET", "url": f"http://127.0.0.1:{port}/json"}},
            ],
        }
        await execute(parse_dsl(doc), make_context_for(sink, port))

        assert server.connections == 1  # keep-alive:两条步骤共用一条 TCP 连接

    @pytest.mark.e2e
    async def test_500_response_fails_via_assertion_not_error(
        self, sink: InMemorySink, server: E2EServer
    ) -> None:
        port = server.server_address[1]
        doc = single_step(
            port, "/boom",
            step_overrides={"assert": [{"type": "status_code", "operator": "eq", "expected": 200}]},
        )
        summary = await execute(parse_dsl(doc), make_context_for(sink, port))

        assert sink.results[0].status == "failed"
        assert sink.results[0].response.status_code == 500
        assert summary.status == "failed"
