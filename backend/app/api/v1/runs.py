"""执行触发 / 状态查询 / 取消 / SSE 实时进度事件流。"""

import base64
import datetime
import json
import logging
import uuid
from collections.abc import AsyncIterator

import redis.asyncio as redis_async
from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.sse import EventSourceResponse, ServerSentEvent
from redis.exceptions import RedisError
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import idempotency as idem

from app.api.deps import (
    Principal,
    get_current_user,
    get_db,
    get_principal,
    require_token_project,
    require_token_scope,
    resolve_principal,
)
from app.api.v1.common import get_project_or_404
from app.core.config import get_settings
from app.core.errors import AppError, ConflictError, ValidationFailedError, not_found
from app.event_names import FINISHED_FILTER_EVENTS, RUN_FINISHED, RUN_SNAPSHOT, TERMINAL_EVENT
from app.junit_export import CaseResult, StepSummary, render_junit
from app.models import (
    Environment,
    Run,
    RUN_TERMINAL_STATUSES,
    RunStepResult,
    TestCase,
    TestPlan,
    User,
)
from app.quality_gate import evaluate_quality_gate
from app.run_builder import build_plan_run
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

logger = logging.getLogger(__name__)

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
        triggered_by=run.triggered_by,
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


async def _prepare_run(
    session: AsyncSession, project_id: int, payload: RunCreate, principal: Principal
) -> Run:
    """资源/权限校验并构造 run(不提交、不投递);幂等接入点在此函数之前。

    校验失败抛 AppError,调用方负责分别映射 4xx / 释放幂等锁。
    """
    await get_project_or_404(session, project_id)
    require_token_scope(principal, "run:trigger")
    require_token_project(principal, project_id)

    # trigger_mode 服务端判定,请求体不自报(spec 决策 #1):无 Bearer →
    # manual;API Token → ci 且 triggered_by 为空(归因走 api_token)
    if principal.token is not None:
        trigger_mode, triggered_by = "ci", None
    else:
        trigger_mode, triggered_by = "manual", principal.user.id

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
            trigger_mode=trigger_mode,
            triggered_by=triggered_by,
            status="pending",
        )
    # ── 计划触发 ───────────────────────────────────────────────────
    else:
        # 计划归属、清单非空、环境解析与 tick 共用同一构造器(spec #35:
        # 两种触发来源语义只写一遍);AppError 直接映射为既有 4xx 响应
        run = await session.run_sync(
            lambda s: build_plan_run(
                s,
                project_id=project_id,
                plan_id=payload.plan_id,
                environment_id=payload.environment_id,
                trigger_mode=trigger_mode,
                triggered_by=triggered_by,
            )
        )

    # 门禁配置持久化在 run 上(spec #35 决策 #10):worker 终态求值要取回。
    # 单用例与计划两条构造路径共用这一处赋值;定时 tick 不经过这里(无门禁)。
    if payload.quality_gate is not None:
        run.quality_gate = payload.quality_gate.model_dump(exclude_none=True)
    session.add(run)
    return run


async def _dispatch(session: AsyncSession, run: Run, response: Response) -> RunTriggered:
    """先提交再投递:worker(真实或 eager)用独立会话读 run,未提交的插入对它不可见。"""
    task = execute_run.delay(str(run.id))
    run.celery_task_id = task.id
    await session.flush()
    response.headers["Location"] = f"/api/v1/runs/{run.id}"
    return RunTriggered(run_id=run.id, status=run.status)


async def _mark_dispatch_failed(session: AsyncSession, run: Run) -> None:
    """投递失败补偿:落 error 终态,不留永久 pending(与调度 tick 同语义)。

    投递失败时 run 已被提交;标记 error 让重叠检查与 CI 轮询都能收敛,
    而不是对着一条永远不会执行的 pending 记录干等。
    """
    await session.rollback()
    stranded = await session.get(Run, run.id)
    if stranded is not None and stranded.status == "pending":
        stranded.status = "error"
        stranded.finished_at = func.now()
        stranded.summary = {
            "total": 0,
            "passed": 0,
            "failed": 0,
            "error": 1,
            "skipped": 0,
            "assertion_total": 0,
            "assertion_passed": 0,
            "dispatch_failed": True,
        }
        await session.commit()


def _validate_idempotency_header(value: str) -> None:
    if not value.strip() or len(value) > idem.MAX_KEY_LENGTH:
        raise ValidationFailedError(
            "Idempotency-Key 必须非空且不超过 255 个字符",
            code="idempotency_key_invalid",
        )


def _idempotency_unavailable() -> AppError:
    return AppError(
        status_code=503,
        code="idempotency_unavailable",
        message="幂等存储不可用,带幂等键的请求已拒绝",
    )


async def _trigger_idempotent(
    *,
    project_id: int,
    payload: RunCreate,
    response: Response,
    idempotency_key: str,
    session: AsyncSession,
    principal: Principal,
) -> RunTriggered:
    """带 Idempotency-Key 的触发:先抢锁,再做资源校验,提交后写 done。

    业务校验失败 abort(不消耗 key);Redis 异常 fail closed(503)。
    """
    key = idem.idempotency_key(project_id, principal, idempotency_key)
    fingerprint = idem.request_fingerprint(payload)
    client = None
    try:
        try:
            client = idem.make_client(get_settings())
            acquired = await idem.acquire(client, key, fingerprint)
        except (RedisError, ValueError) as exc:
            raise _idempotency_unavailable() from exc

        if acquired.state == "replay":
            response.headers["Idempotent-Replayed"] = "true"
            response.headers["Location"] = f"/api/v1/runs/{acquired.run_id}"
            return RunTriggered(run_id=acquired.run_id, status="pending")
        if acquired.state == "reused":
            raise ConflictError(
                message="Idempotency-Key 已用于不同的请求",
                code="idempotency_key_reused",
            )
        if acquired.state == "in_progress":
            raise ConflictError(
                message="相同幂等键的请求正在处理中,请稍后重试",
                code="idempotency_in_progress",
                headers={"Retry-After": str(idem.RETRY_AFTER_SECONDS)},
            )

        try:
            run = await _prepare_run(session, project_id, payload, principal)
            await session.commit()
        except BaseException:
            # 业务校验失败不消耗 key:释放本次持有的 pending,修正后同 key 可重试
            try:
                await idem.abort(client, key, acquired.nonce)
            except RedisError:
                logger.warning("幂等键 abort 失败: %s", key, exc_info=True)
            raise

        try:
            # 先投递、后写 done:done 键一旦写入会保留 24h,若先写再投递,
            # 投递失败时同 key 重试只会重放一条从未入队的 run
            result = await _dispatch(session, run, response)
        except Exception:
            logger.exception("run %s 投递失败", run.id)
            # 释放本次持有的 pending,让同 key 可重试(而不是重放死 run)
            try:
                await idem.abort(client, key, acquired.nonce)
            except RedisError:
                logger.warning("幂等键 abort 失败: %s", key, exc_info=True)
            await _mark_dispatch_failed(session, run)
            raise

        try:
            if not await idem.mark_done(client, key, fingerprint, acquired.nonce, run.id):
                logger.warning("幂等键 CAS 写 done 未生效(可能已过期): %s", key)
        except RedisError:
            # run 已投递:不能因 Redis 抖动把一次成功触发报成失败
            logger.warning("幂等键写 done 失败,本次仍按已创建返回: %s", key, exc_info=True)

        return result
    finally:
        if client is not None:
            await client.aclose()


@project_router.post("", status_code=202, response_model=RunTriggered)
async def trigger_run(
    project_id: int,
    payload: RunCreate,
    response: Response,
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
    idempotency_key: str | None = Header(
        default=None,
        alias="Idempotency-Key",
        description="幂等键:同 key 24h 内返回首次 run,同 key 不同请求体 409",
    ),
) -> RunTriggered:
    if idempotency_key is not None:
        _validate_idempotency_header(idempotency_key)
        return await _trigger_idempotent(
            project_id=project_id,
            payload=payload,
            response=response,
            idempotency_key=idempotency_key,
            session=session,
            principal=principal,
        )

    run = await _prepare_run(session, project_id, payload, principal)
    await session.commit()
    try:
        return await _dispatch(session, run, response)
    except Exception:
        logger.exception("run %s 投递失败", run.id)
        await _mark_dispatch_failed(session, run)
        raise


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
    principal: Principal = Depends(get_principal),
) -> RunPage:
    await get_project_or_404(session, project_id)
    require_token_scope(principal, "report:read")
    require_token_project(principal, project_id)
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
    principal: Principal = Depends(get_principal),
) -> RunOut:
    run = await _get_run_or_404(session, run_id)
    require_token_scope(principal, "report:read")
    require_token_project(principal, run.project_id)
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
    principal: Principal = Depends(get_principal),
) -> StepList:
    run = await _get_run_or_404(session, run_id)  # run 不存在与无步骤区分开
    require_token_scope(principal, "report:read")
    require_token_project(principal, run.project_id)
    items, truncated = await _load_step_items(session, run_id)
    return StepList(items=items, count=len(items), truncated=truncated)


def _group_step_summaries(rows) -> dict[int, list[StepSummary]]:
    """轻量步骤行按 case_index 分组;system-out 只要摘要,不碰请求响应体。"""
    grouped: dict[int, list[StepSummary]] = {}
    for row in rows:
        grouped.setdefault(row.case_index, []).append(
            StepSummary(
                name=row.step_name,
                status=row.status,
                duration_ms=row.duration_ms,
                assertion_total=row.assertion_total,
                assertion_passed=row.assertion_passed,
            )
        )
    return grouped


@router.get(
    "/{run_id}/export",
    response_class=Response,
    responses={
        200: {
            "content": {"application/xml": {}},
            "description": "JUnit XML 报告(附件 run-{id}.xml)",
        },
        409: {"description": "执行尚未结束(run_not_finished)"},
        422: {"description": "导出格式不支持(export_format_invalid)"},
    },
)
async def export_run(
    run_id: uuid.UUID,
    export_format: str | None = Query(
        default=None, alias="format", description="导出格式,仅支持 junit"
    ),
    session: AsyncSession = Depends(get_db),
    principal: Principal = Depends(get_principal),
) -> Response:
    """导出 JUnit XML(spec #35 决策 #8 / API设计 §7 导出)。

    权限与报告读取同源:report:read + Token 项目边界;非终态不导出半成品。
    名称按当前库中未删除的计划/用例解析,已删回退 `plan #id` / `用例 #id`。
    """
    run = await _get_run_or_404(session, run_id)
    require_token_scope(principal, "report:read")
    require_token_project(principal, run.project_id)

    if export_format != "junit":
        raise ValidationFailedError(
            "导出格式仅支持 junit",
            code="export_format_invalid",
            details={"format": export_format},
        )
    if run.status not in RUN_TERMINAL_STATUSES:
        raise ConflictError(
            "执行尚未结束,暂不能导出",
            code="run_not_finished",
            details={"run_id": str(run.id), "status": run.status},
        )

    # 显式列清单:system-out 只要步骤摘要,不读 response_body 等大字段
    rows = (
        await session.execute(
            select(
                RunStepResult.case_index,
                RunStepResult.step_name,
                RunStepResult.status,
                RunStepResult.duration_ms,
                RunStepResult.assertion_total,
                RunStepResult.assertion_passed,
            )
            .where(RunStepResult.run_id == run.id)
            .order_by(RunStepResult.case_index, RunStepResult.step_index)
        )
    ).all()
    steps_by_case = _group_step_summaries(rows)

    summary = run.summary or {}
    if run.plan_id is not None:
        # 计划 run:summary.cases 每条一个 testcase,classname = 计划名
        suite_name = (
            await session.scalar(
                select(TestPlan.name).where(
                    TestPlan.id == run.plan_id, TestPlan.deleted_at.is_(None)
                )
            )
        ) or f"plan #{run.plan_id}"
        entries = summary.get("cases") or []
        case_ids = [e.get("case_id") for e in entries if e.get("case_id") is not None]
        names: dict[int, str] = {}
        if case_ids:
            names = dict(
                (
                    await session.execute(
                        select(TestCase.id, TestCase.name).where(
                            TestCase.id.in_(case_ids), TestCase.deleted_at.is_(None)
                        )
                    )
                ).all()
            )
        cases = []
        for entry in entries:
            case_index = entry.get("case_index", 0)
            case_id = entry.get("case_id")
            case_steps = steps_by_case.get(case_index, [])
            cases.append(
                CaseResult(
                    classname=suite_name,
                    name=names.get(case_id) or f"用例 #{case_id}",
                    status=entry.get("status", run.status),
                    # 用例耗时 = 该用例步骤耗时之和(不是 run 时长)
                    time_ms=sum(step.duration_ms for step in case_steps),
                    steps=tuple(case_steps),
                )
            )
    else:
        # 单用例 run:一个 testcase,time 用 run 时长
        suite_name = (
            await session.scalar(
                select(TestCase.name).where(
                    TestCase.id == run.case_id, TestCase.deleted_at.is_(None)
                )
            )
            if run.case_id is not None
            else None
        ) or f"用例 #{run.case_id}"
        cases = [
            CaseResult(
                classname=suite_name,
                name=suite_name,
                status=run.status,
                time_ms=run.duration_ms or 0,
                steps=tuple(steps_by_case.get(0, [])),
            )
        ]

    xml = render_junit(
        suite_name=suite_name,
        cases=cases,
        timestamp=run.finished_at or run.started_at or run.created_at,
        # 键不存在 = 未配置门禁;不写 properties(spec 决策 #9)
        gate_passed=summary["gate_passed"] if "gate_passed" in summary else None,
    )
    return Response(
        content=xml,
        media_type="application/xml; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="run-{run.id}.xml"'},
    )


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
    if run.status == "stopped":
        # 排队中被取消也是终态(任务不会执行):门禁照常求值,不改状态
        run.summary = evaluate_quality_gate(run.quality_gate, run.summary)
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
    生成器体里的异常无法变成 400。认证经 deps.resolve_principal 与
    普通端点同源(认证唯一改动点不因本端点破例):Token 需要
    report:read 且 run 归属 Token 项目,无 Bearer 维持种子用户放行。
    返回过滤白名单。
    """
    allowed = _resolve_event_filter(events)
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        principal = await resolve_principal(session, request)
        run = await _get_run_or_404(session, run_id)
        require_token_scope(principal, "report:read")
        require_token_project(principal, run.project_id)
        if principal.token is not None:
            # 短会话不自动提交:last_used_at 的刷新要在这里落库
            await session.commit()
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
