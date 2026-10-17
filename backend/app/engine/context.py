"""EngineContext:一次执行的外部依赖与配置,全部显式注入。"""

from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

from app.engine.defaults import NeverCancelled, NullPublisher
from app.engine.protocols import CancellationToken, ProgressPublisher, ResultSink


@dataclass
class EngineContext:
    sink: ResultSink
    # 每次执行调用一次,返回新 AsyncClient;测试传 MockTransport 绑定的工厂
    http_client_factory: Callable[[], httpx.AsyncClient]

    project_variables: dict[str, Any] = field(default_factory=dict)
    environment_variables: dict[str, Any] = field(default_factory=dict)
    publisher: ProgressPublisher = field(default_factory=NullPublisher)
    cancellation: CancellationToken = field(default_factory=NeverCancelled)

    env_prefixes: tuple[str, ...] = ("TEST_",)
    body_max_bytes: int = 64 * 1024
