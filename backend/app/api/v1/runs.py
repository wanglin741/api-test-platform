"""执行触发 / 状态查询 / 取消。执行结果查询的完整报告端点在 T9。"""

import uuid

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.config import get_settings
from app.core.errors import ConflictError, not_found
from app.models import Environment, Run, RUN_TERMINAL_STATUSES, RunStepResult, TestCase, User
from app.schemas.runs import (
    CancelAck,
    RequestSnapshotOut,
    ResponseSnapshotOut,
    RunCreate,
    RunOut,
    RunTriggered,
    StepList,
    StepOut,
)
from app.tasks.engine_tasks import execute_run

STEPS_HARD_CAP = 2000  # 超过截断并在响应里标记,不静默丢行

router = APIRouter(prefix="/runs", tags=["runs"])
project_router = APIRouter(prefix="/projects/{project_id}/runs", tags=["runs"])


async def _get_run_or_404(session: AsyncSession, run_id: uuid.UUID) -> Run:
    run = await session.get(Run, run_id)
    if run is None:
        raise not_found("run", run_id)
    return run


@project_router.post("", status_code=202, response_model=RunTriggered)
async def trigger_run(
    project_id: int,
    payload: RunCreate,
    response: Response,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunTriggered:
    await get_project_or_404(session, project_id)

    case = await session.get(TestCase, payload.case_id)
    if (
        case is None
        or case.deleted_at is not None
        or case.project_id != project_id
    ):
        raise not_found("case", payload.case_id)  # 归属不符一律 404,不区分「不存在」与「不属于」

    environment = await session.get(Environment, payload.environment_id)
    if (
        environment is None
        or environment.deleted_at is not None
        or environment.project_id != project_id
    ):
        raise not_found("environment", payload.environment_id)

    run = Run(
        project_id=project_id,
        case_id=case.id,
        case_version=case.current_version,  # 锁定版本:改用例不影响旧报告还原
        environment_id=environment.id,
        trigger_mode="manual",
        triggered_by=user.id,
        status="pending",
    )
    session.add(run)
    # 先提交再投递:worker(真实或 eager)用独立会话读 run,未提交的插入对它不可见
    await session.commit()

    task = execute_run.delay(str(run.id))
    run.celery_task_id = task.id
    await session.flush()

    response.headers["Location"] = f"/api/v1/runs/{run.id}"
    return RunTriggered(run_id=run.id, status=run.status)


@router.get("/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunOut:
    run = await _get_run_or_404(session, run_id)
    return RunOut(
        run_id=run.id,
        project_id=run.project_id,
        case_id=run.case_id,
        case_version=run.case_version,
        environment_id=run.environment_id,
        trigger_mode=run.trigger_mode,
        status=run.status,
        duration_ms=run.duration_ms,
        summary=run.summary,
        created_at=run.created_at,
        started_at=run.started_at,
        finished_at=run.finished_at,
    )


@router.get("/{run_id}/steps", response_model=StepList)
async def list_run_steps(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StepList:
    await _get_run_or_404(session, run_id)  # run 不存在与无步骤区分开
    # 这张表禁止 SELECT *(数据模型 §9):显式列出明细需要的列,不抱走整行
    columns = (
        RunStepResult.id,
        RunStepResult.step_index,
        RunStepResult.step_name,
        RunStepResult.step_type,
        RunStepResult.status,
        RunStepResult.duration_ms,
        RunStepResult.request_method,
        RunStepResult.request_url,
        RunStepResult.request_headers,
        RunStepResult.request_body,
        RunStepResult.response_status,
        RunStepResult.response_headers,
        RunStepResult.response_size,
        RunStepResult.response_body,
        RunStepResult.response_body_truncated,
        RunStepResult.extracted_variables,
        RunStepResult.assertions,
        RunStepResult.assertion_total,
        RunStepResult.assertion_passed,
        RunStepResult.error_message,
    )
    result = await session.execute(
        select(*columns)
        .where(RunStepResult.run_id == run_id)
        .order_by(RunStepResult.step_index)
        .limit(STEPS_HARD_CAP + 1)
    )
    rows = result.all()
    truncated = len(rows) > STEPS_HARD_CAP
    rows = rows[:STEPS_HARD_CAP]

    items = []
    for row in rows:
        body_text = (
            bytes(row.response_body).decode("utf-8", errors="replace")
            if row.response_body is not None
            else None
        )
        items.append(
            StepOut(
                id=row.id,
                step_index=row.step_index,
                step_name=row.step_name,
                step_type=row.step_type,
                status=row.status,
                duration_ms=row.duration_ms,
                request=RequestSnapshotOut(
                    method=row.request_method or "",
                    url=row.request_url or "",
                    headers=row.request_headers or {},
                    body=row.request_body,
                )
                if row.request_method is not None
                else None,
                response=ResponseSnapshotOut(
                    status_code=row.response_status,
                    headers=row.response_headers,
                    body=body_text,
                    size=row.response_size,
                    truncated=row.response_body_truncated,
                )
                if row.response_status is not None
                else None,
                extracted_variables=row.extracted_variables or {},
                assertions=row.assertions or [],
                assertion_total=row.assertion_total,
                assertion_passed=row.assertion_passed,
                error_message=row.error_message,
            )
        )
    return StepList(items=items, count=len(items), truncated=truncated)


@router.post("/{run_id}/cancel", response_model=CancelAck)
async def cancel_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CancelAck:
    import redis as redis_py

    run = await _get_run_or_404(session, run_id)
    if run.status in RUN_TERMINAL_STATUSES:
        raise ConflictError(
            message="执行已结束,无法取消",
            code="run_already_finished",
            details={"run_id": str(run.id), "status": run.status},
        )

    # D3 顺序:先写带外取消标志,再改状态,避免 worker 读到空标志的竞态
    client = redis_py.Redis.from_url(get_settings().redis_url)
    client.set(f"run:cancel:{run.id}", 1, ex=3600)

    run.cancel_requested_at = func.now()
    run.status = "stopped" if run.status == "pending" else "stopping"
    await session.flush()
    return CancelAck(run_id=run.id, status=run.status)
