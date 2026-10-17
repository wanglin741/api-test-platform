"""引擎入口:execute(dsl, context)。

单用例内步骤严格串行(设计文档《执行引擎》§6)——步骤间存在变量依赖,
不做步骤级并行。每步结束立即回调 ResultSink(§11 写入时机)。

本模块不 import 任何框架;HTTP 客户端经 EngineContext 注入。
"""

import time

import httpx

from app.engine.context import EngineContext
from app.engine.dsl import CaseDSL
from app.engine.results import RunSummary, StepResult
from app.engine.steps.http import run_http_step
from app.engine.variables import VariableScope


async def execute(dsl: CaseDSL, context: EngineContext) -> RunSummary:
    scope = VariableScope(
        project=context.project_variables,
        environment=context.environment_variables,
        case=dsl.config.variables,
        env_prefixes=context.env_prefixes,
    )

    client = context.http_client_factory()
    start = time.perf_counter()
    results: list[StepResult] = []
    try:
        context.publisher.publish("run.started", {"total_steps": len(dsl.steps)})
        for index, step in enumerate(dsl.steps):
            result = await run_http_step(step, scope, context, client)
            result.step_index = index
            results.append(result)
            # 每步立即回调,不批量缓冲(§11)—— 长跑期间轮询可见部分结果
            context.sink.on_step_finished(result)
            context.publisher.publish(
                "step.finished",
                {"step_id": step.id, "index": index, "status": result.status},
            )
    finally:
        await client.aclose()

    duration_ms = (time.perf_counter() - start) * 1000
    summary = _summarize(results, duration_ms)
    context.publisher.publish("run.finished", {"status": summary.status})
    return summary


def _summarize(results: list[StepResult], duration_ms: float) -> RunSummary:
    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for r in results:
        counts[r.status] += 1
    # skipped 不参与通过率,也不拉低终态(设计文档 §3 / §9)
    if counts["error"]:
        status = "error"
    elif counts["failed"]:
        status = "failed"
    else:
        status = "success"
    return RunSummary(
        status=status,  # type: ignore[arg-type]
        total_steps=len(results),
        passed=counts["passed"],
        failed=counts["failed"],
        errored=counts["error"],
        skipped=counts["skipped"],
        duration_ms=duration_ms,
    )
