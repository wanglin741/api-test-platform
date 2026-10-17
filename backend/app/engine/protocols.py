"""引擎与外界的三处交互,全部通过 Protocol 注入(设计文档《执行引擎》§1)。

生产实现在引擎外(服务层 / 任务层),测试实现用纯内存结构 ——
这条边界让引擎单测不起任何基础设施。
"""

from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from app.engine.results import StepResult


class ResultSink(Protocol):
    """每步结束立即回调(写 PG / 收集),不等整条用例跑完。"""

    def on_step_finished(self, result: "StepResult") -> None: ...


class ProgressPublisher(Protocol):
    """进度事件推送。首版只有 NullPublisher,SSE/Redis Streams 将来接这里。"""

    def publish(self, event: str, payload: dict[str, Any]) -> None: ...


class CancellationToken(Protocol):
    """协作式取消查询。引擎在既定检查点轮询,不做强制中断。"""

    def cancelled(self) -> bool: ...
