"""引擎测试的公共夹具:MockTransport 工厂与内存 Sink。"""

import json
from typing import Any, Callable

import httpx
import pytest

from app.engine.context import EngineContext
from app.engine.defaults import InMemorySink


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def make_context(
    sink: InMemorySink,
    handler: Callable[[httpx.Request], httpx.Response],
    **kwargs: Any,
) -> EngineContext:
    return EngineContext(
        sink=sink,
        http_client_factory=lambda: make_client(handler),
        **kwargs,
    )


def json_response(
    status_code: int = 200,
    payload: Any = None,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    return httpx.Response(
        status_code,
        json=payload,
        headers=headers or {},
    )


def captured_body(request: httpx.Request) -> Any:
    """MockTransport handler 里取请求体:JSON → 对象,其余 → 文本。"""
    raw = request.read()
    if not raw:
        return None
    content_type = request.headers.get("content-type", "")
    if "json" in content_type:
        return json.loads(raw)
    return raw.decode("utf-8", errors="replace")


@pytest.fixture
def sink() -> InMemorySink:
    return InMemorySink()
