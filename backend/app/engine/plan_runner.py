"""计划执行器(#30 串行、#32 并行)。纯库 —— 不 import 框架/ORM/Redis。

输入 = 已解析的用例 DSL 列表(PlanCase)+ 既有 EngineContext(sink/
publisher/cancellation 原样复用);输出 = 计划级 summary(用例级求和,
同一份字段形状,外加逐用例归属 outcome —— 被跳过的用例没有步骤行,
不记下来就「凭空消失」)。

定案(spec #28):
- 串行 = 严格按清单顺序逐用例执行;并行 = 单任务内 asyncio.gather +
  Semaphore(plan.concurrency)(执行引擎 §8,不用 Celery 编排)。
- step.* 事件带 case_index/case_name;run.progress 保持步骤级全计划
  连续计数 —— progress_base 用清单前置步数偏移,并行下每步占唯一刻度,
  不随用例切换重置、不产生重复值。
- stop_on_fail / 取消:未开始的用例只记 outcome,不发步骤事件 ——
  跳过没有「开始」;并行下已开始的用例自然跑完,不强杀;取消优先于
  失败即停(状态落 stopped)。
"""

import asyncio
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
    mode: str = "serial",
    concurrency: int = 1,
) -> PlanSummary:
    total_steps = sum(len(c.dsl.steps) for c in cases)
    # 每个用例的进度刻度偏移 = 清单里排在前面的用例步数之和:
    # 并行下各步的 completed 值唯一,合流后仍是 1..total 的连续推进
    offsets = [0] * len(cases)
    for i in range(1, len(cases)):
        offsets[i] = offsets[i - 1] + len(cases[i - 1].dsl.steps)

    # 跨用例共享的两面旗:asyncio 单线程,赋值即同步,无需加锁
    stop = False
    cancelled = False

    start = time.perf_counter()

    async def run_one(case_index: int, case: PlanCase) -> CaseOutcome:
        nonlocal stop, cancelled
        # 用例边界检查:并行下只拦「还没拿到并发名额」的用例,
        # 已开始的用例自然跑完,不强杀
        if context.cancellation.cancelled():
            cancelled = True
        if cancelled or stop:
            return _skipped_outcome(case, case_index)

        summary = await execute(
            case.dsl,
            context,
            case_ref=CaseRef(case_index=case_index, case_id=case.case_id, case_name=case.case_name),
            progress_base=offsets[case_index],
            progress_total=total_steps,
        )
        if stop_on_fail and summary.status in ("failed", "error"):
            stop = True
        return CaseOutcome(
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

    if mode == "parallel" and len(cases) > 1:
        # 用例级并行受 Semaphore 约束(执行引擎 §8):单任务内 gather,
        # 不引入 Celery 编排;outcomes 按 gather 保序 = 清单位置
        semaphore = asyncio.Semaphore(max(1, concurrency))

        async def bounded(index: int, case: PlanCase) -> CaseOutcome:
            async with semaphore:
                return await run_one(index, case)

        outcomes = list(await asyncio.gather(*(bounded(i, c) for i, c in enumerate(cases))))
    else:
        outcomes = []
        for case_index, case in enumerate(cases):
            outcomes.append(await run_one(case_index, case))

    duration_ms = (time.perf_counter() - start) * 1000
    if cancelled:
        status: RunStatus = "stopped"
    elif any(o.status == "error" for o in outcomes):
        status = "error"
    elif any(o.status == "failed" for o in outcomes):
        status = "failed"
    else:
        status = "success"

    return PlanSummary(
        status=status,
        total_steps=sum(o.total_steps for o in outcomes),
        passed=sum(o.passed for o in outcomes),
        failed=sum(o.failed for o in outcomes),
        errored=sum(o.errored for o in outcomes),
        skipped=sum(o.skipped for o in outcomes),
        duration_ms=duration_ms,
        cases=outcomes,
    )


def _skipped_outcome(case: PlanCase, case_index: int) -> CaseOutcome:
    """未开始即被跳过的用例:无步骤行、无步骤事件,只记归属。"""
    return CaseOutcome(
        case_index=case_index, case_id=case.case_id, case_name=case.case_name, status="skipped"
    )
