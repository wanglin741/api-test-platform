"""响应体流式截断的边界语义(MockTransport 层)。

真实 socket 路径的 e2e 见 tests/e2e/(MockTransport 不走 socket,
但截断的累计/停止逻辑在这里以字节级边界验证)。
"""

import httpx

from app.engine.defaults import InMemorySink
from app.engine.dsl import parse_dsl
from app.engine.runner import execute
from tests.engine.conftest import make_context

STEP_URL = "https://api.example.com/big"


def doc_with_limit(context_limit: int) -> tuple[dict, object]:
    doc = {
        "dsl_version": 1,
        "config": {},
        "steps": [
            {
                "id": "s1",
                "name": "s1",
                "type": "http",
                "request": {"method": "GET", "url": STEP_URL},
            }
        ],
    }
    return doc, context_limit


def handler_returning(payload: bytes) -> object:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    return handler


def make_ctx(sink: InMemorySink, handler: object, body_max_bytes: int):
    return make_context(sink, handler, body_max_bytes=body_max_bytes)


class TestTruncationBoundaries:
    async def test_body_under_limit_not_truncated(self, sink: InMemorySink) -> None:
        payload = b"x" * 1000
        doc, _ = doc_with_limit(64 * 1024)
        await execute(parse_dsl(doc), make_ctx(sink, handler_returning(payload), 64 * 1024))

        resp = sink.results[0].response
        assert resp.truncated is False
        assert resp.body == "x" * 1000

    async def test_body_exactly_at_limit_not_truncated(self, sink: InMemorySink) -> None:
        payload = b"x" * 65536
        doc, _ = doc_with_limit(64 * 1024)
        await execute(parse_dsl(doc), make_ctx(sink, handler_returning(payload), 64 * 1024))

        resp = sink.results[0].response
        assert resp.truncated is False
        assert len(resp.body) == 65536

    async def test_body_over_limit_truncated_to_limit(self, sink: InMemorySink) -> None:
        payload = b"abcde" * 40000  # 200 KB
        doc, _ = doc_with_limit(64 * 1024)
        await execute(parse_dsl(doc), make_ctx(sink, handler_returning(payload), 64 * 1024))

        resp = sink.results[0].response
        assert resp.truncated is True
        assert len(resp.body) == 65536
        assert resp.body == ("abcde" * 20000)[:65536]  # 前缀完整保留

    async def test_truncated_body_is_decodable(self, sink: InMemorySink) -> None:
        # UTF-8 多字节字符被从中间截断 → 替换而非崩溃
        payload = ("汉" * 50000).encode("utf-8")  # 150 KB
        doc, _ = doc_with_limit(64 * 1024)
        await execute(parse_dsl(doc), make_ctx(sink, handler_returning(payload), 64 * 1024))

        resp = sink.results[0].response
        assert resp.truncated is True
        # 65536 字节 ÷ 3 字节/字 = 21845 字符,截断点落在字符中间 → 1 个替换符
        assert 21845 <= len(resp.body) <= 21846
        assert "�" in resp.body  # 解码不崩
