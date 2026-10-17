"""引擎自带的可注入默认实现(空操作 / 永不取消)。"""

from typing import Any

from app.engine.results import StepResult


class NullPublisher:
    """丢弃全部进度事件。接 SSE / Redis Streams 时替换为真实实现。"""

    def publish(self, event: str, payload: dict[str, Any]) -> None: ...


class NeverCancelled:
    """永不取消。生产实现走 Redis 取消标志(设计文档 D3)。"""

    def cancelled(self) -> bool:
        return False


class InMemorySink:
    """把步骤结果收进内存列表。引擎单测与进程内同步调试用。"""

    def __init__(self) -> None:
        self.results: list[StepResult] = []

    def on_step_finished(self, result: StepResult) -> None:
        self.results.append(result)
