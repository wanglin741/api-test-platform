"""执行触发 / 状态查询 / 取消 / SSE 实时进度事件流。"""

import base64
import datetime
import json
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import SEED_USERNAME, get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, ValidationFailedError, not_found
from app.event_names import TERMINAL_EVENT
from app.models import Environment, Run, RUN_TERMINAL_STATUSES, RunStepResult, TestCase, User
from app.run_events import stream_key
from app.schemas.runs import (
    CancelAck,
    RequestSnapshotOut,
    ResponseSnapshotOut,
    RunCreate,
    RunOut,
    RunPage,
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


def _run_out(run: Run) -> RunOut:
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


def _encode_cursor(run: Run) -> str:
    raw = json.dumps({"c": run.created_at.isoformat(), "i": str(run.id)})
    return base64.urlsafe_b64encode(raw.encode()).decode()


def _decode_cursor(cursor: str) -> tuple[datetime.datetime, uuid.UUID]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return datetime.datetime.fromisoformat(data["c"]), uuid.UUID(data["i"])
    except Exception as exc:
        raise ValidationFailedError(
            message="无效的执行列表游标",
            code="invalid_cursor",
        ) from exc


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


@project_router.get("", response_model=RunPage)
async def list_project_runs(
    project_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunPage:
    await get_project_or_404(session, project_id)
    stmt = select(Run).where(Run.project_id == project_id)
    if cursor is not None:
        created_at, run_id = _decode_cursor(cursor)
        # 行值比较同时锚定时间与 id:created_at 相同的相邻行不重不漏
        stmt = stmt.where(tuple_(Run.created_at, Run.id) < tuple_(created_at, run_id))
    stmt = stmt.order_by(Run.created_at.desc(), Run.id.desc()).limit(limit + 1)
    rows = list((await session.scalars(stmt)).all())
    # 多取一条只为探测「还有下一页」;游标取自本页最后一行
    has_more = len(rows) > limit
    rows = rows[:limit]
    return RunPage(
        items=[_run_out(r) for r in rows],
        next_cursor=_encode_cursor(rows[-1]) if has_more and rows else None,
    )


@router.get("/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunOut:
    run = await _get_run_or_404(session, run_id)
    return _run_out(run)


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


async def _ensure_run_stream_access(run_id: uuid.UUID, request: Request) -> None:
    """流开始前的认证 + 404 判定,短会话即用即释。

    SSE 端点不能用 yield 依赖(get_db / get_current_user):其清理在流
    结束后才跑,会把数据库连接占住整个 SSE 生命周期。非 yield 依赖在
    响应开始前运行,认证失败 / 404 都在流开始前返回。认证查询与
    get_current_user 保持同一缝(SEED_USERNAME),真实认证落地时同步换。
    """
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        result = await session.execute(select(User).where(User.username == SEED_USERNAME))
        if result.scalar_one_or_none() is None:
            raise AppError(
                status_code=500,
                code="seed_user_missing",
                message="种子用户不存在,请确认迁移已执行",
            )
        await _get_run_or_404(session, run_id)


@router.get("/{run_id}/events", response_class=EventSourceResponse)
async def stream_run_events(
    run_id: uuid.UUID,
    _access: None = Depends(_ensure_run_stream_access),
) -> AsyncIterator[ServerSentEvent]:
    """SSE 实时进度事件流(架构设计 §6 / API设计 §5)。

    #23 骨架:从流首重放,收到执行终态事件即关闭 —— 已结束的执行重放
    完全部事件后自然收尾,运行中的执行推到终态为止。快照补偿 /
    Last-Event-ID 续传 / ?events= 过滤在后续票落地。

    生成器端点由 FastAPI 路由层编码为 SSE、设置 text/event-stream 与
    Cache-Control / X-Accel-Buffering 两个头、自带 15 秒保活;每个连接
    自建异步 Redis 客户端(绑定当前事件循环,不跨连接复用),断开时
    finally 释放。
    """
    client = redis_async.Redis.from_url(get_settings().redis_url)
    try:
        key = stream_key(run_id)
        last_id = "0"
        while True:
            batches = await client.xread({key: last_id}, block=10_000, count=100)
            for _stream, messages in batches or ():
                for message_id, fields in messages:
                    last_id = message_id
                    event = fields[b"event"].decode()
                    # data 已是 JSON 字符串,用 raw_data 原样透传,避免二次编码
                    yield ServerSentEvent(
                        raw_data=fields[b"data"].decode(),
                        event=event,
                        id=message_id.decode(),
                    )
                    if event == TERMINAL_EVENT:
                        return  # 终态即关:浏览器侧不再挂着连接
    finally:
        await client.aclose()
