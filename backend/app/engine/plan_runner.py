"""计划执行器(#30):串行多用例编排。纯库 —— 不 import 框架/ORM/Redis。

输入 = 已解析的用例 DSL 列表(PlanCase)+ 既有 EngineContext(sink/
publisher/cancellation 原样复用);输出 = 计划级 summary(用例级求和,
同一份字段形状,外加逐用例归属 outcome —— 被跳过的用例没有步骤行,
不记下来就「凭空消失」)。

定案(spec #28):
- 串行 = 严格按清单顺序逐用例执行;并行(asyncio.gather + Semaphore)
  在 #32 接线,execute_mode 的拒绝在任务层。
- step.* 事件带 case_index/case_name;run.progress 保持步骤级全计划
  连续计数,不随用例切换重置。
- stop_on_fail / 取消:未开始的用例只记 outcome,不发步骤事件 ——
  跳过没有「开始」。
"""

import time
from dataclasses import dataclass, field

from app.engine.context import EngineContext
from app.engine.dsl import CaseDSL
from app.engine.results import CaseRef, RunStatus
from app.engine.runner import execute

CaseStatus = str  # success / failed / error / stopped / skipped


@dataclass
class PlanCase:
    """一个待执行用例:归属 + 已解析 DSL。计划如何从数据库拼装是任务层的事。"""

    case_id: int | None
    case_name: str
    dsl: CaseDSL


@dataclass
class CaseOutcome:
    """单用例结局:计划级报告按用例分组与「被跳过」展示的依据。"""

    case_index: int
    case_id: int | None
    case_name: str
    status: CaseStatus
    total_steps: int = 0
    passed: int = 0
    failed: int = 0
    errored: int = 0
    skipped: int = 0


@dataclass
class PlanSummary:
    status: RunStatus
    total_steps: int
    passed: int
    failed: int
    errored: int
    skipped: int
    duration_ms: float
    cases: list[CaseOutcome] = field(default_factory=list)


async def execute_plan(
    cases: list[PlanCase],
    context: EngineContext,
    *,
    stop_on_fail: bool = False,
) -> PlanSummary:
    total_steps = sum(len(c.dsl.steps) for c in cases)
    outcomes: list[CaseOutcome] = []
    sums = {"total": 0, "passed": 0, "failed": 0, "error": 0, "skipped": 0}
    cancelled = False
    stop = False

    start = time.perf_counter()
    for case_index, case in enumerate(cases):
        if context.cancellation.cancelled():
            cancelled = True
        if cancelled:
            outcomes.append(_skipped_outcome(case, case_index))
            continue
        if stop:
            outcomes.append(_skipped_outcome(case, case_index))
            continue

        summary = await execute(
            case.dsl,
            context,
            case_ref=CaseRef(case_index=case_index, case_id=case.case_id, case_name=case.case_name),
            progress_base=sums["total"],
            progress_total=total_steps,
        )
        sums["total"] += summary.total_steps
        sums["passed"] += summary.passed
        sums["failed"] += summary.failed
        sums["error"] += summary.errored
        sums["skipped"] += summary.skipped
        outcomes.append(
            CaseOutcome(
                case_index=case_index,
                case_id=case.case_id,
                case_name=case.case_name,
                status=summary.status,
                total_steps=summary.total_steps,
                passed=summary.passed,
                failed=summary.failed,
                errored=summary.errored,
                skipped=summary.skipped,
            )
        )
        if stop_on_fail and summary.status in ("failed", "error"):
            stop = True

    duration_ms = (time.perf_counter() - start) * 1000
    if cancelled:
        status: RunStatus = "stopped"
    elif sums["error"]:
        status = "error"
    elif sums["failed"]:
        status = "failed"
    else:
        status = "success"

    return PlanSummary(
        status=status,
        total_steps=sums["total"],
        passed=sums["passed"],
        failed=sums["failed"],
        errored=sums["error"],
        skipped=sums["skipped"],
        duration_ms=duration_ms,
        cases=outcomes,
    )


def _skipped_outcome(case: PlanCase, case_index: int) -> CaseOutcome:
    """未开始即被跳过的用例:无步骤行、无步骤事件,只记归属。"""
    return CaseOutcome(
        case_index=case_index, case_id=case.case_id, case_name=case.case_name, status="skipped"
    )
