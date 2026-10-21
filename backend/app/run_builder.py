"""HTTP 与 tick 共用的计划 run 构造器(spec #35:两种触发来源语义只写一遍)。

计划归属校验、清单非空、环境解析(请求覆盖 → 计划默认)只在这里写一遍:
- HTTP 端点让 AppError 直接映射为 4xx 响应(既有行为不变);
- tick 捕获后按 code 落「跳过推进 / 停用」语义(app/tasks/schedule_tasks.py)。

同步 Session 形态:tick(worker)全程同步会话;HTTP 侧经
AsyncSession.run_sync 复用同一份逻辑 —— 两种触发来源的校验不复制。
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import ValidationFailedError, not_found
from app.models import Environment, Run, TestPlan, TestPlanItem


def build_plan_run(
    session: Session,
    *,
    project_id: int,
    plan_id: int,
    environment_id: int | None,
    trigger_mode: str,
    triggered_by: int | None,
    schedule_id: int | None = None,
) -> Run:
    """构造计划 run 并加入会话;提交与投递由调用方控制(先提交再投递)。

    校验失败抛 AppError 子类(code 与触发端点既有契约一致):
    plan_not_found / plan_items_empty / environment_required / environment_not_found。
    """
    plan = session.get(TestPlan, plan_id)
    if plan is None or plan.deleted_at is not None or plan.project_id != project_id:
        raise not_found("plan", plan_id)
    item_count = session.scalar(
        select(func.count()).select_from(TestPlanItem).where(TestPlanItem.plan_id == plan.id)
    )
    if not item_count:
        raise ValidationFailedError(
            "计划没有用例,先在计划详情里编排清单",
            code="plan_items_empty",
            details={"plan_id": plan.id},
        )
    # 调用方(请求体/调度)的 environment_id 覆盖计划默认(数据模型 §8「运行时指定则覆盖」)
    resolved_environment_id = environment_id or plan.environment_id
    if resolved_environment_id is None:
        raise ValidationFailedError(
            "计划未配置默认环境,触发时必须指定 environment_id",
            code="environment_required",
            details={"plan_id": plan.id},
        )
    environment = session.get(Environment, resolved_environment_id)
    if (
        environment is None
        or environment.deleted_at is not None
        or environment.project_id != project_id
    ):
        raise not_found("environment", resolved_environment_id)
    run = Run(
        project_id=project_id,
        plan_id=plan.id,
        schedule_id=schedule_id,
        environment_id=environment.id,
        trigger_mode=trigger_mode,
        triggered_by=triggered_by,
        status="pending",
    )
    session.add(run)
    # flush 取回 uuid 默认主键:tick 要把 run.id 写进 schedule.last_run_id
    session.flush()
    return run
