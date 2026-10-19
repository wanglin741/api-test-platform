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

from app.api.deps import get_current_user, get_db, load_current_user
from app.api.v1.common import get_project_or_404
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, ValidationFailedError, not_found
from app.event_names import FINISHED_FILTER_EVENTS, RUN_FINISHED, RUN_SNAPSHOT, TERMINAL_EVENT
from app.models import Environment, Run, RUN_TERMINAL_STATUSES, RunStepResult, TestCase, TestPlan, TestPlanItem, User
from app.run_events import finished_payload, stream_key
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
        plan_id=run.plan_id,
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

    if (payload.case_id is None) == (payload.plan_id is None):
        raise ValidationFailedError(
            "case_id 与 plan_id 恰好二选一",
            code="run_target_invalid",
            details={"case_id": payload.case_id, "plan_id": payload.plan_id},
        )

    # ── 单用例触发(既有行为不变) ─────────────────────────────────
    if payload.case_id is not None:
        if payload.environment_id is None:
            raise ValidationFailedError(
                "单用例执行必须指定 environment_id", code="environment_required"
            )
        case = await session.get(TestCase, payload.case_id)
        if (
            case is None
            or case.deleted_at is not None
            or case.project_id != project_id
        ):
            raise not_found("case", payload.case_id)  # 归属不符一律 404,不区分「不存在」与「不属于」
        environment = await _validate_environment(session, project_id, payload.environment_id)
        run = Run(
            project_id=project_id,
            case_id=case.id,
            case_version=case.current_version,  # 锁定版本:改用例不影响旧报告还原
            environment_id=environment.id,
            trigger_mode="manual",
            triggered_by=user.id,
            status="pending",
        )
    # ── 计划触发 ───────────────────────────────────────────────────
    else:
        plan = await session.get(TestPlan, payload.plan_id)
        if plan is None or plan.deleted_at is not None or plan.project_id != project_id:
            raise not_found("plan", payload.plan_id)
        item_count = await session.scalar(
            select(func.count()).select_from(TestPlanItem).where(TestPlanItem.plan_id == plan.id)
        )
        if not item_count:
            raise ValidationFailedError(
                "计划没有用例,先在计划详情里编排清单",
                code="plan_items_empty",
                details={"plan_id": plan.id},
            )
        # 请求体的 environment_id 覆盖计划默认(数据模型 §8「运行时指定则覆盖」)
        environment_id = payload.environment_id or plan.environment_id
        if environment_id is None:
            raise ValidationFailedError(
                "计划未配置默认环境,触发时必须指定 environment_id",
                code="environment_required",
                details={"plan_id": plan.id},
            )
        environment = await _validate_environment(session, project_id, environment_id)
        run = Run(
            project_id=project_id,
            plan_id=plan.id,
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


async def _validate_environment(
    session: AsyncSession, project_id: int, environment_id: int
) -> Environment:
    environment = await session.get(Environment, environment_id)
    if (
        environment is None
        or environment.deleted_at is not None
        or environment.project_id != project_id
    ):
        raise not_found("environment", environment_id)
    return environment


@project_router.get("", response_model=RunPage)
async def list_project_runs(
    project_id: int,
    limit: int = Query(default=20, ge=1, le=100),
    cursor: str | None = Query(default=None),
    plan_id: int | None = Query(default=None),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> RunPage:
    await get_project_or_404(session, project_id)
    stmt = select(Run).where(Run.project_id == project_id)
    if plan_id is not None:
        # 报告列表按计划筛选(计划外键索引):计划详情页「最近执行」与
        # 列表页计划筛选共用
        stmt = stmt.where(Run.plan_id == plan_id)
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


async def _load_step_items(
    session: AsyncSession, run_id: uuid.UUID
) -> tuple[list[StepOut], bool]:
    """步骤明细加载:步骤明细接口与 run.snapshot 快照共用这一份实现。

    这张表禁止 SELECT *(数据模型 §9):显式列出明细需要的列,不抱走整行。
    """
    columns = (
        RunStepResult.id,
        RunStepResult.step_index,
        RunStepResult.step_name,
        RunStepResult.step_type,
        RunStepResult.status,
        RunStepResult.duration_ms,
        RunStepResult.case_index,
        RunStepResult.case_id,
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
        .order_by(RunStepResult.case_index, RunStepResult.step_index)
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
                case_index=row.case_index,
                case_id=row.case_id,
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
    return items, truncated


@router.get("/{run_id}/steps", response_model=StepList)
async def list_run_steps(
    run_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StepList:
    await _get_run_or_404(session, run_id)  # run 不存在与无步骤区分开
    items, truncated = await _load_step_items(session, run_id)
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


async def _ensure_run_stream_access(
    run_id: uuid.UUID,
    request: Request,
    events: str | None = Query(default=None),
) -> frozenset[str] | None:
    """流开始前的认证 + 404 判定 + 过滤参数校验,短会话即用即释。

    SSE 端点不能用 yield 依赖(get_db / get_current_user):其清理在流
    结束后才跑,会把数据库连接占住整个 SSE 生命周期。非 yield 依赖在
    响应开始前运行,认证失败 / 404 / 非法过滤值都在流开始前返回 ——
    生成器体里的异常无法变成 400。认证查询经 deps.load_current_user
    与 get_current_user 同源(认证唯一改动点不因本端点破例)。
    返回过滤白名单。
    """
    allowed = _resolve_event_filter(events)
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        await load_current_user(session)
        await _get_run_or_404(session, run_id)
    return allowed


# 读循环参数提为模块常量:集成测试缩短空读间隔以覆盖「worker 被强杀、
# 流里永无终态」的查库补偿路径,不必真等 60 秒
READ_BLOCK_SECONDS = 10.0
IDLE_DB_CHECK_SECONDS = 60.0


def _parse_stream_id(raw: str) -> tuple[int, int] | None:
    """「毫秒-序号」拆成数值对:游标比较必须按数值,按字符串比会错序。

    isascii() 先行:isdigit() 对非 ASCII 数字字符(如 '²')也为真,
    而 int() 会抛 ValueError —— 游标是外部输入,坏值必须走快照回退
    而不是在生成器体里炸掉整个流。
    """
    ms, sep, seq = raw.partition("-")
    if not sep or not (ms.isascii() and ms.isdigit()) or not (seq.isascii() and seq.isdigit()):
        return None
    return int(ms), int(seq)


async def _stream_edge(client: redis_async.Redis, key: str, *, first: bool) -> str | None:
    """流首(first)或流尾的消息 id;流不存在返回 None。"""
    entries = await (client.xrange(key, count=1) if first else client.xrevrange(key, count=1))
    return entries[0][0].decode() if entries else None


def _resolve_event_filter(events: str | None) -> frozenset[str] | None:
    """?events= 白名单过滤;非法取值立刻 400,不静默忽略。"""
    if events is None:
        return None
    if events != "finished":
        raise AppError(
            message="events 仅支持 finished",
            status_code=400,
            code="invalid_events_filter",
            details={"events": events},
        )
    return FINISHED_FILTER_EVENTS


def _snapshot_payload(run: Run, steps: list[StepOut], truncated: bool) -> dict:
    """run.snapshot 载荷:与报告详情接口同一份 schema 序列化(mode="json" 保日期可编码)。"""
    return {
        "run": _run_out(run).model_dump(mode="json"),
        "steps": [s.model_dump(mode="json") for s in steps],
        "steps_truncated": truncated,
    }


@router.get("/{run_id}/events", response_class=EventSourceResponse)
async def stream_run_events(
    run_id: uuid.UUID,
    request: Request,
    _allowed: frozenset[str] | None = Depends(_ensure_run_stream_access),
) -> AsyncIterator[ServerSentEvent]:
    """SSE 实时进度事件流(架构设计 §6 / API设计 §5)。

    起点判定:无 Last-Event-ID(首次连接)、流为空、或游标早于流首 ⇒
    快照路径 —— 先取流尾位置再读数据库(避免快照期间发布的事件被漏掉),
    发 run.snapshot;执行已终态则补发 run.finished 并结束连接。游标仍
    有效 ⇒ 重放路径,从游标之后续传。

    补偿:连续空读约 60 秒查一次库(worker 被强杀、流里永无终态的场景),
    已终态则补发 run.finished 并关闭,客户端不永久挂起。

    生成器端点由 FastAPI 路由层编码为 SSE、设置 text/event-stream 与
    Cache-Control / X-Accel-Buffering 两个头、自带 15 秒保活;每个连接
    自建异步 Redis 客户端(绑定当前事件循环,不跨连接复用),断开时
    finally 释放。
    """
    allowed = _allowed
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    client = redis_async.Redis.from_url(get_settings().redis_url)
    try:
        key = stream_key(run_id)

        last_id = "0"
        raw_cursor = request.headers.get("Last-Event-ID")
        cursor = None
        if raw_cursor and (parsed := _parse_stream_id(raw_cursor)) is not None:
            head = await _stream_edge(client, key, first=True)
            # 数值比较:游标 >= 流首才可信,早于流首说明该段已被裁剪,走快照
            if head is not None and parsed >= _parse_stream_id(head):
                cursor = raw_cursor

        if cursor is None:
            # 快照路径:流尾先于数据库读取取定,快照期间发布的事件都在其后,不漏
            tail = await _stream_edge(client, key, first=False)
            async with factory() as session:
                run = await _get_run_or_404(session, run_id)
                steps, truncated = await _load_step_items(session, run_id)
            # run.snapshot 恒放行过滤:它是客户端重新同步的唯一入口
            yield ServerSentEvent(
                raw_data=json.dumps(
                    _snapshot_payload(run, steps, truncated), ensure_ascii=False, default=str
                ),
                event=RUN_SNAPSHOT,
            )
            if run.status in RUN_TERMINAL_STATUSES:
                # 终态在流里可能已被裁剪:用数据库事实补发,连接自然收尾
                yield ServerSentEvent(
                    raw_data=json.dumps(
                        finished_payload(run.status, run.summary),
                        ensure_ascii=False,
                        default=str,
                    ),
                    event=RUN_FINISHED,
                )
                return
            last_id = tail or "0"
        else:
            last_id = cursor

        idle_seconds = 0.0
        while True:
            batches = await client.xread(
                {key: last_id}, block=int(READ_BLOCK_SECONDS * 1000), count=100
            )
            if not batches:
                idle_seconds += READ_BLOCK_SECONDS
                if idle_seconds >= IDLE_DB_CHECK_SECONDS:
                    idle_seconds = 0.0
                    async with factory() as session:
                        run = await session.get(Run, run_id)
                    if run is not None and run.status in RUN_TERMINAL_STATUSES:
                        yield ServerSentEvent(
                            raw_data=json.dumps(
                                finished_payload(run.status, run.summary),
                                ensure_ascii=False,
                                default=str,
                            ),
                            event=RUN_FINISHED,
                        )
                        return
                continue
            idle_seconds = 0.0

            for _stream, messages in batches:
                for message_id, fields in messages:
                    last_id = message_id
                    event = fields[b"event"].decode()
                    # data 已是 JSON 字符串,用 raw_data 原样透传,避免二次编码
                    if allowed is None or event in allowed:
                        yield ServerSentEvent(
                            raw_data=fields[b"data"].decode(),
                            event=event,
                            id=message_id.decode(),
                        )
                    if event == TERMINAL_EVENT:
                        return  # 终态即关:浏览器侧不再挂着连接
    finally:
        await client.aclose()
