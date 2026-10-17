"""引擎结果结构。DSL 进、结果出 —— 这是引擎与调用方之间的数据契约。

`StepResult` 由 ResultSink 逐步消费(落库);`RunSummary` 是 execute 的返回值。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

StepStatus = Literal["passed", "failed", "error", "skipped"]
RunStatus = Literal["success", "failed", "error", "stopped"]


@dataclass
class RequestSnapshot:
    method: str
    url: str
    headers: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)
    body: str | None = None


@dataclass
class ResponseSnapshot:
    status_code: int
    headers: dict[str, str] = field(default_factory=dict)
    body: str = ""
    truncated: bool = False


@dataclass
class AssertionResult:
    type: str
    operator: str
    expected: Any
    actual: Any
    passed: bool
    message: str | None = None  # 失败说明;断言自带 message 优先


@dataclass
class StepResult:
    step_id: str
    step_name: str
    step_index: int
    status: StepStatus
    request: RequestSnapshot | None = None
    response: ResponseSnapshot | None = None
    duration_ms: float | None = None
    error_message: str | None = None
    attempts: int = 1
    extracted: dict[str, Any] = field(default_factory=dict)
    assertions: list[AssertionResult] = field(default_factory=list)


@dataclass
class RunSummary:
    status: RunStatus
    total_steps: int
    passed: int
    failed: int
    errored: int
    skipped: int
    duration_ms: float
