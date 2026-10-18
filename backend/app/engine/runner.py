"""引擎入口:execute(dsl, context)。

单用例内步骤严格串行(设计文档《执行引擎》§6)——步骤间存在变量依赖,
不做步骤级并行。每步结束立即回调 ResultSink(§11 写入时机)。

取消是协作式的(§10):每个步骤开始前检查 token;取消后已完成的步骤
保留、未开始的标记 skipped,run 终态 stopped。用例总时长超限同样在此
检查,终态 error(数据模型文档的 run 状态机)。

本模块不 import 任何框架;HTTP 客户端经 EngineContext 注入。
"""

import time

import httpx

from app.engine.context import EngineContext
from app.engine.dsl import CaseDSL
from app.engine.results import RunStatus, RunSummary, StepResult
from app.engine.steps.http import run_http_step
from app.engine.variables import VariableScope
from app.event_names import RUN_PROGRESS, STEP_FINISHED


async def execute(dsl: CaseDSL, context: EngineContext) -> RunSummary:
    scope = VariableScope(
        project=context.project_variables,
        environment=context.environment_variables,
        case=dsl.config.variables,
        env_prefixes=context.env_prefixes,
    )

    # verify_ssl 是步骤级开关,httpx 的 verify 只能挂在整个 client 上:
    # 按 flag 缓存,分别创建与关闭
    clients: dict[bool, httpx.AsyncClient] = {}

    def _client_for(verify: bool) -> httpx.AsyncClient:
        if verify not in clients:
            clients[verify] = context.http_client_factory(verify=verify)
        return clients[verify]

    start = time.perf_counter()
    results: list[StepResult] = []
    cancelled = False
    timed_out = False
    fail_fast_hit = False
    try:
        for index, step in enumerate(dsl.steps):
            # ── 协作检查点(§10):步骤开始前 ─────────────────────────
            if not cancelled and context.cancellation.cancelled():
                cancelled = True
            if (
                not cancelled
                and not timed_out
                and dsl.config.max_duration_ms is not None
                and (time.perf_counter() - start) * 1000 > dsl.config.max_duration_ms
            ):
                timed_out = True

            if cancelled or timed_out:
                reason = "已取消" if cancelled else "用例总时长超限"
                result = StepResult(
                    step_id=step.id, step_name=step.name, step_index=index,
                    status="skipped", error_message=reason,
                )
            elif fail_fast_hit:
                result = StepResult(
                    step_id=step.id, step_name=step.name, step_index=index,
                    status="skipped", error_message="fail_fast:前序步骤失败",
                )
            else:
                result = await run_http_step(
                    step, scope, context, _client_for(step.request.verify_ssl),
                    dsl.config.timeout_ms, step_index=index,
                )
                result.step_index = index
                if (
                    dsl.config.fail_fast
                    and result.status in ("failed", "error")
                ):
                    fail_fast_hit = True

            results.append(result)
            # 每步立即回调,不批量缓冲(§11)—— 长跑期间轮询可见部分结果
            context.sink.on_step_finished(result)
            # 载荷字段对齐落库列与步骤出参(step_index 而非 index);限流是
            # 发布器的投递策略,引擎每步都发进度,单测保持确定性
            passed = sum(1 for a in result.assertions if a.passed)
            context.publisher.publish(
                STEP_FINISHED,
                {
                    "step_index": index,
                    "step_name": result.step_name,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "assertion_total": len(result.assertions),
                    "assertion_passed": passed,
                },
            )
            context.publisher.publish(
                RUN_PROGRESS, {"completed": index + 1, "total": len(dsl.steps)}
            )
    finally:
        for client in clients.values():
            await client.aclose()

    duration_ms = (time.perf_counter() - start) * 1000
    summary = _summarize(results, duration_ms, cancelled=cancelled, timed_out=timed_out)
    # 执行级事件(run.started/run.finished)由任务层发布:started_at 是数据库
    # 时间、summary 含断言统计,引擎不掌握这些事实 —— 引擎只发逐步事件
    return summary


def _summarize(
    results: list[StepResult], duration_ms: float, *, cancelled: bool, timed_out: bool
) -> RunSummary:
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for r in results:
        counts[r.status] += 1

    if cancelled:
        status: RunStatus = "stopped"
    elif timed_out:
        status = "error"
    elif counts["error"]:
        status = "error"
    elif counts["failed"]:
        status = "failed"
    else:
        status = "success"

    return RunSummary(
        status=status,
        total_steps=len(results),
        passed=counts["passed"],
        failed=counts["failed"],
        errored=counts["error"],
        skipped=counts["skipped"],
        duration_ms=duration_ms,
    )
