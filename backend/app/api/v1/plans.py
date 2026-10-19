"""计划 CRUD + 用例清单整体替换(issue #29,数据模型 §8 / API设计 §资源清单)。

items 是整体替换语义(PUT):请求体即清单全量,按数组顺序落 sort_order。
归属校验一律「不符即 404,不探测」:跨项目/已删的环境、用例、计划同形。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import delete as sa_delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.errors import ValidationFailedError, not_found
from app.models import Environment, Project, TestCase, TestPlan, TestPlanItem, User
from app.schemas.plans import (
    PlanCreate,
    PlanItemOut,
    PlanItemsPut,
    PlanOut,
    PlanUpdate,
)

router = APIRouter(prefix="/plans", tags=["plans"])
project_router = APIRouter(prefix="/projects/{project_id}/plans", tags=["plans"])


async def _get_plan_or_404(session: AsyncSession, plan_id: int) -> TestPlan:
    result = await session.execute(
        select(TestPlan, Project)
        .join(Project, TestPlan.project_id == Project.id)
        .where(TestPlan.id == plan_id, TestPlan.alive())
    )
    row = result.first()
    if row is None:
        raise not_found("plan", plan_id)
    plan, project = row
    if project.deleted_at is not None:
        raise not_found("plan", plan_id)  # 项目已删,归属资源一律 404
    return plan


async def _validate_environment(
    session: AsyncSession, project_id: int, environment_id: int | None
) -> None:
    if environment_id is None:
        return
    env = await session.get(Environment, environment_id)
    if env is None or env.deleted_at is not None or env.project_id != project_id:
        raise not_found("environment", environment_id)


async def _item_counts(session: AsyncSession, plan_ids: list[int]) -> dict[int, int]:
    if not plan_ids:
        return {}
    rows = await session.execute(
        select(TestPlanItem.plan_id, func.count())
        .where(TestPlanItem.plan_id.in_(plan_ids))
        .group_by(TestPlanItem.plan_id)
    )
    return dict(rows.all())


async def _plan_out(session: AsyncSession, plan: TestPlan) -> PlanOut:
    counts = await _item_counts(session, [plan.id])
    out = PlanOut.model_validate(plan)
    out.item_count = counts.get(plan.id, 0)
    return out


@project_router.post("", status_code=201, response_model=PlanOut)
async def create_plan(
    project_id: int,
    payload: PlanCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanOut:
    await get_project_or_404(session, project_id)
    await _validate_environment(session, project_id, payload.environment_id)
    plan = TestPlan(project_id=project_id, created_by=user.id, **payload.model_dump())
    session.add(plan)
    await session.flush()
    await session.refresh(plan)  # 取回 server_default 列
    return await _plan_out(session, plan)


@project_router.get("", response_model=list[PlanOut])
async def list_plans(
    project_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PlanOut]:
    await get_project_or_404(session, project_id)
    plans = list(
        (
            await session.execute(
                select(TestPlan)
                .where(TestPlan.project_id == project_id, TestPlan.alive())
                .order_by(TestPlan.id)
            )
        ).scalars()
    )
    counts = await _item_counts(session, [p.id for p in plans])
    outs = [PlanOut.model_validate(p) for p in plans]
    for out in outs:
        out.item_count = counts.get(out.id, 0)
    return outs


@router.get("/{plan_id}", response_model=PlanOut)
async def get_plan(
    plan_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanOut:
    plan = await _get_plan_or_404(session, plan_id)
    return await _plan_out(session, plan)


@router.patch("/{plan_id}", response_model=PlanOut)
async def update_plan(
    plan_id: int,
    payload: PlanUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> PlanOut:
    plan = await _get_plan_or_404(session, plan_id)
    data = payload.model_dump(exclude_unset=True)
    if "environment_id" in data:
        await _validate_environment(session, plan.project_id, data["environment_id"])
    for field, value in data.items():
        setattr(plan, field, value)
    await session.flush()
    await session.refresh(plan)
    return await _plan_out(session, plan)


@router.delete("/{plan_id}", status_code=204)
async def delete_plan(
    plan_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    plan = await _get_plan_or_404(session, plan_id)
    plan.deleted_at = func.now()
    await session.flush()


@router.get("/{plan_id}/items", response_model=list[PlanItemOut])
async def list_plan_items(
    plan_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PlanItemOut]:
    plan = await _get_plan_or_404(session, plan_id)
    return await _load_items(session, plan)


async def _load_items(session: AsyncSession, plan: TestPlan) -> list[PlanItemOut]:
    rows = await session.execute(
        select(TestPlanItem, TestCase.name)
        .join(TestCase, TestPlanItem.item_id == TestCase.id)
        .where(TestPlanItem.plan_id == plan.id, TestPlanItem.item_type == "case")
        .order_by(TestPlanItem.sort_order, TestPlanItem.id)
    )
    return [
        PlanItemOut(
            id=item.id,
            item_type=item.item_type,
            case_id=item.item_id,
            case_name=case_name,
            sort_order=item.sort_order,
        )
        for item, case_name in rows.all()
    ]


@router.put("/{plan_id}/items", response_model=list[PlanItemOut])
async def replace_plan_items(
    plan_id: int,
    payload: PlanItemsPut,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[PlanItemOut]:
    plan = await _get_plan_or_404(session, plan_id)

    case_ids = [item.case_id for item in payload.items]
    if len(set(case_ids)) != len(case_ids):
        dup = sorted({c for c in case_ids if case_ids.count(c) > 1})
        raise ValidationFailedError(
            "用例清单里有重复用例",
            code="plan_items_invalid",
            details={"duplicate_case_ids": dup},
        )

    # 逐个校验归属:同项目、未删。多不了几百条,逐个 get 可接受
    seen: set[int] = set()
    for case_id in case_ids:
        if case_id in seen:
            continue
        seen.add(case_id)
        case = await session.get(TestCase, case_id)
        if case is None or case.deleted_at is not None or case.project_id != plan.project_id:
            raise not_found("case", case_id)

    # 整体替换:旧行全删,新行按数组顺序落 sort_order。
    # 先提交删除再插入 —— 同一 flush 里 SQLAlchemy 会把 INSERT 排在 DELETE 前,
    # 交换顺序时会撞 (plan_id, item_type, item_id) 唯一约束
    await session.execute(sa_delete(TestPlanItem).where(TestPlanItem.plan_id == plan.id))
    for order, item in enumerate(payload.items):
        session.add(
            TestPlanItem(
                plan_id=plan.id, item_type="case", item_id=item.case_id, sort_order=order
            )
        )
    await session.flush()
    return await _load_items(session, plan)
