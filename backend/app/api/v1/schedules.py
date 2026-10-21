"""调度 CRUD + 启停(issue #36,API 设计 §3 schedules)。

时间语义(spec 决策 #2/#5):
- 创建/启用/修改 cron 或 timezone → 从当前时间重算 next_run_at;
- 停用 → 置空;仅改名/改环境不重算;
- 删除 = 软删 + 停用 + 清空 next_run_at。
归属校验一律「不符即 404,不探测」,与 plans 端点同形。
"""

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.errors import NotFoundError, not_found
from app.core.schedule_time import compute_next
from app.models import Environment, Project, Schedule, TestPlan, User
from app.schemas.schedules import ScheduleCreate, ScheduleOut, ScheduleUpdate

router = APIRouter(prefix="/schedules", tags=["schedules"])
project_router = APIRouter(prefix="/projects/{project_id}/schedules", tags=["schedules"])


async def _get_schedule_or_404(session: AsyncSession, schedule_id: int) -> Schedule:
    result = await session.execute(
        select(Schedule, Project)
        .join(Project, Schedule.project_id == Project.id)
        .where(Schedule.id == schedule_id, Schedule.alive())
    )
    row = result.first()
    if row is None:
        raise NotFoundError(
            "schedule 不存在", code="schedule_not_found", details={"schedule_id": schedule_id}
        )
    _, project = row
    if project.deleted_at is not None:
        # 项目已删,归属资源一律 404(不探测)
        raise NotFoundError(
            "schedule 不存在", code="schedule_not_found", details={"schedule_id": schedule_id}
        )
    return row[0]


async def _validate_plan(session: AsyncSession, project_id: int, plan_id: int) -> None:
    plan = await session.get(TestPlan, plan_id)
    if plan is None or plan.deleted_at is not None or plan.project_id != project_id:
        raise not_found("plan", plan_id)


async def _validate_environment(
    session: AsyncSession, project_id: int, environment_id: int | None
) -> None:
    if environment_id is None:
        return
    env = await session.get(Environment, environment_id)
    if env is None or env.deleted_at is not None or env.project_id != project_id:
        raise not_found("environment", environment_id)


@project_router.post("", status_code=201, response_model=ScheduleOut)
async def create_schedule(
    project_id: int,
    payload: ScheduleCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleOut:
    await get_project_or_404(session, project_id)
    await _validate_plan(session, project_id, payload.plan_id)
    await _validate_environment(session, project_id, payload.environment_id)
    next_run_at = compute_next(
        payload.cron_expression, payload.timezone, datetime.datetime.now(datetime.UTC)
    )
    schedule = Schedule(
        project_id=project_id,
        next_run_at=next_run_at,
        created_by=user.id,
        **payload.model_dump(),
    )
    session.add(schedule)
    await session.flush()
    await session.refresh(schedule)
    return ScheduleOut.model_validate(schedule)


@project_router.get("", response_model=list[ScheduleOut])
async def list_schedules(
    project_id: int,
    plan_id: int | None = None,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[ScheduleOut]:
    await get_project_or_404(session, project_id)
    stmt = select(Schedule).where(Schedule.project_id == project_id, Schedule.alive())
    if plan_id is not None:
        stmt = stmt.where(Schedule.plan_id == plan_id)
    rows = await session.execute(stmt.order_by(Schedule.id))
    return [ScheduleOut.model_validate(s) for s in rows.scalars()]


@router.patch("/{schedule_id}", response_model=ScheduleOut)
async def update_schedule(
    schedule_id: int,
    payload: ScheduleUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleOut:
    schedule = await _get_schedule_or_404(session, schedule_id)
    data = payload.model_dump(exclude_unset=True)
    if "environment_id" in data:
        await _validate_environment(session, schedule.project_id, data["environment_id"])
    recompute = "cron_expression" in data or "timezone" in data
    for field, value in data.items():
        setattr(schedule, field, value)
    if recompute:
        schedule.next_run_at = compute_next(
            schedule.cron_expression, schedule.timezone, datetime.datetime.now(datetime.UTC)
        )
    await session.flush()
    await session.refresh(schedule)
    return ScheduleOut.model_validate(schedule)


@router.delete("/{schedule_id}", status_code=204)
async def delete_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    schedule = await _get_schedule_or_404(session, schedule_id)
    schedule.deleted_at = func.now()
    schedule.enabled = False
    schedule.next_run_at = None
    await session.flush()


@router.post("/{schedule_id}/enable", response_model=ScheduleOut)
async def enable_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleOut:
    schedule = await _get_schedule_or_404(session, schedule_id)
    schedule.enabled = True
    schedule.next_run_at = compute_next(
        schedule.cron_expression, schedule.timezone, datetime.datetime.now(datetime.UTC)
    )
    await session.flush()
    await session.refresh(schedule)
    return ScheduleOut.model_validate(schedule)


@router.post("/{schedule_id}/disable", response_model=ScheduleOut)
async def disable_schedule(
    schedule_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ScheduleOut:
    schedule = await _get_schedule_or_404(session, schedule_id)
    schedule.enabled = False
    schedule.next_run_at = None
    await session.flush()
    await session.refresh(schedule)
    return ScheduleOut.model_validate(schedule)
