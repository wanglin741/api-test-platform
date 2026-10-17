"""EngineContext:一次执行的外部依赖与配置,全部显式注入。"""

from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.engine.defaults import NeverCancelled, NullPublisher
from app.engine.protocols import CancellationToken, ProgressPublisher, ResultSink


@dataclass
class EngineContext:
    sink: ResultSink
    # 工厂签名 factory(verify: bool) -> AsyncClient:verify_ssl 是步骤级开关,
    # 而 httpx 的 verify 只能挂在整个 client 上,runner 按 flag 缓存复用。
    # 测试传 MockTransport 绑定的工厂(忽略 verify)。
    http_client_factory: Callable[..., httpx.AsyncClient]

    project_variables: dict[str, Any] = field(default_factory=dict)
    environment_variables: dict[str, Any] = field(default_factory=dict)
    publisher: ProgressPublisher = field(default_factory=NullPublisher)
    cancellation: CancellationToken = field(default_factory=NeverCancelled)

    env_prefixes: tuple[str, ...] = ("TEST_",)
    body_max_bytes: int = 64 * 1024
