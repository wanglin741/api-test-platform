"""用例 CRUD + 版本快照 + 单步调试。

保存即快照:POST 写 v1,带 dsl 的 PATCH 递增。dsl 是执行的唯一事实来源,
元信息(name/priority/tags)的更新不动版本。
"""

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.config import get_settings
from app.core.errors import ValidationFailedError, not_found
from app.engine.context import EngineContext
from app.engine.defaults import InMemorySink
from app.engine.dsl import parse_dsl
from app.engine.results import StepResult, response_size
from app.engine.steps.http import run_http_step
from app.engine.variables import VariableScope
from app.models import Environment, Folder, Project, TestCase, TestCaseVersion, User, Variable
from app.schemas.cases import (
    CaseCreate,
    CaseDetail,
    CaseOut,
    CasePage,
    CaseUpdate,
    DebugAssertionOut,
    StepDebugOut,
)
from app.schemas.runs import RequestSnapshotOut, ResponseSnapshotOut

router = APIRouter(prefix="/cases", tags=["cases"])
project_router = APIRouter(prefix="/projects/{project_id}/cases", tags=["cases"])


def _parse_dsl_or_422(dsl: dict):
    try:
        return parse_dsl(dsl)
    except ValidationError as e:
        errors = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in e.errors()
        ]
        raise ValidationFailedError(
            message="DSL 结构校验失败",
            code="dsl_invalid",
            details={"errors": errors},
        ) from e


def _validate_dsl(dsl: dict) -> None:
    """DSL 结构校验,失败给 422 且错误信息定位到字段(loc 为 JSON 路径)。"""
    _parse_dsl_or_422(dsl)


async def _get_case_or_404(session: AsyncSession, case_id: int) -> TestCase:
    case = await session.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise not_found("case", case_id)
    return case


UNCATEGORIZED_FOLDER_ID = 0  # 列表过滤的哨兵值:folder_id=0 即「未分类」(folder_id IS NULL)


async def _validate_folder_id(
    session: AsyncSession, project_id: int, folder_id: int | None
) -> None:
    if folder_id is None:
        return
    folder = await session.get(Folder, folder_id)
    if folder is None or folder.deleted_at is not None or folder.project_id != project_id:
        raise not_found("folder", folder_id)


async def _folder_filter_ids(session: AsyncSession, project_id: int, folder_id: int) -> set[int]:
    """目录过滤的目标集合:该目录 + 其全部后代目录(选中目录即看整棵子树的用例)。

    目录不存在 / 跨项目 / 已删 → 404(调用方据此判 404,这里返回 None)。
    """
    folders = (
        await session.scalars(
            select(Folder).where(Folder.project_id == project_id, Folder.alive())
        )
    ).all()
    children: dict[int | None, list[int]] = {}
    for folder in folders:
        children.setdefault(folder.parent_id, []).append(folder.id)
    if folder_id not in {f.id for f in folders}:
        return set()  # 空集合 = 目标目录不在本项目在用目录中,由调用方判 404
    ids: set[int] = set()
    stack = [folder_id]
    while stack:
        current = stack.pop()
        ids.add(current)
        stack.extend(children.get(current, []))
    return ids


async def _get_current_version_row(session: AsyncSession, case: TestCase) -> TestCaseVersion:
    result = await session.execute(
        select(TestCaseVersion).where(
            TestCaseVersion.test_case_id == case.id,
            TestCaseVersion.version == case.current_version,
        )
    )
    version = result.scalar_one_or_none()
    if version is None:  # 不应发生:current_version 与快照行同事务写入
        raise not_found("case_version", case.current_version)
    return version


def _detail(case: TestCase, version: TestCaseVersion) -> CaseDetail:
    return CaseDetail(
        **CaseOut.model_validate(case).model_dump(),
        dsl=version.dsl,
        change_note=version.change_note,
    )


@project_router.post("", status_code=201, response_model=CaseDetail)
async def create_case(
    project_id: int,
    payload: CaseCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CaseDetail:
    await get_project_or_404(session, project_id)
    await _validate_folder_id(session, project_id, payload.folder_id)
    _validate_dsl(payload.dsl)
    case = TestCase(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        priority=payload.priority,
        tags=payload.tags,
        folder_id=payload.folder_id,
        current_version=1,
        created_by=user.id,
    )
    session.add(case)
    await session.flush()
    version = TestCaseVersion(
        test_case_id=case.id,
        version=1,
        dsl=payload.dsl,
        change_note=payload.change_note,
        created_by=user.id,
    )
    session.add(version)
    await session.flush()
    await session.refresh(case)  # 取回 server_default 列
    return _detail(case, version)


@project_router.get("", response_model=CasePage)
async def list_cases(
    project_id: int,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1),
    keyword: str | None = Query(default=None, max_length=128),
    # 目录过滤:0 = 未分类(folder_id IS NULL),N = 该目录及其子目录下的用例
    folder_id: int | None = Query(default=None, ge=0),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CasePage:
    if page_size > 100:  # 超限钳制而非报错,保住客户端「一页拉全部」的写法
        page_size = 100
    conditions = [TestCase.project_id == project_id, TestCase.alive()]
    if keyword:
        conditions.append(TestCase.name.ilike(f"%{keyword}%"))
    if folder_id == UNCATEGORIZED_FOLDER_ID:
        conditions.append(TestCase.folder_id.is_(None))
    elif folder_id is not None:
        ids = await _folder_filter_ids(session, project_id, folder_id)
        if not ids:  # 目录不存在 / 跨项目 / 已删
            raise not_found("folder", folder_id)
        conditions.append(TestCase.folder_id.in_(ids))
    total = await session.scalar(select(func.count()).select_from(TestCase).where(*conditions))
    result = await session.execute(
        select(TestCase)
        .where(*conditions)
        .order_by(TestCase.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list(result.scalars())
    return CasePage(
        items=[CaseOut.model_validate(c) for c in items],
        total=total or 0,
        page=page,
        page_size=page_size,
    )


@router.get("/{case_id}", response_model=CaseDetail)
async def get_case(
    case_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CaseDetail:
    case = await _get_case_or_404(session, case_id)
    version = await _get_current_version_row(session, case)
    return _detail(case, version)


@router.patch("/{case_id}", response_model=CaseDetail)
async def update_case(
    case_id: int,
    payload: CaseUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CaseDetail:
    case = await _get_case_or_404(session, case_id)
    data = payload.model_dump(exclude_unset=True)
    new_dsl = data.pop("dsl", None)
    change_note = data.pop("change_note", None)
    if "folder_id" in data:
        # 显式传 null = 移回未分类,同样要过校验(null 直接放行)
        await _validate_folder_id(session, case.project_id, data["folder_id"])
    for field, value in data.items():
        setattr(case, field, value)

    if new_dsl is not None:
        _validate_dsl(new_dsl)
        version = TestCaseVersion(
            test_case_id=case.id,
            version=case.current_version + 1,
            dsl=new_dsl,
            change_note=change_note,
            created_by=user.id,
        )
        session.add(version)
        case.current_version = version.version
    else:
        # 元信息更新不动版本;change_note 只在产生新快照时有意义,此处忽略
        version = await _get_current_version_row(session, case)

    await session.flush()
    await session.refresh(case)
    return _detail(case, version)


@router.delete("/{case_id}", status_code=204)
async def delete_case(
    case_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    case = await _get_case_or_404(session, case_id)
    case.deleted_at = func.now()
    await session.flush()


async def _load_scope_variables(
    session: AsyncSession, project_id: int, environment_id: int
) -> tuple[dict, dict]:
    """加载项目级/环境级变量(含 base_url 兜底)。

    与 app.tasks.engine_tasks._load_variables 语义一致,那边是同步 Session(worker),
    这边是 AsyncSession(API)—— 引擎的变量合成规则见执行引擎设计,两处都不得自创。
    """
    project_vars: dict = {}
    env_vars: dict = {}
    rows = (
        await session.scalars(select(Variable).where(Variable.project_id == project_id))
    ).all()
    for var in rows:
        if var.environment_id is None:
            project_vars[var.name] = var.value
        elif var.environment_id == environment_id:
            env_vars[var.name] = var.value
    environment = await session.get(Environment, environment_id)
    if environment is not None and environment.base_url:
        env_vars.setdefault("base_url", environment.base_url)
    return project_vars, env_vars


@router.post("/{case_id}/steps/{step_id}/debug", response_model=StepDebugOut)
async def debug_step(
    case_id: int,
    step_id: str,
    environment_id: int = Query(...),
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StepDebugOut:
    """单步调试:同步执行一步并返回快照(不走 Celery,一次请求一次返回)。

    只执行目标步骤本身,不跑前序步骤 —— 提取变量的作用域是单步的,与报告语义区分。
    """
    case = await _get_case_or_404(session, case_id)
    version = await _get_current_version_row(session, case)
    dsl = _parse_dsl_or_422(version.dsl)

    step = next((s for s in dsl.steps if s.id == step_id), None)
    if step is None:
        raise not_found("step", step_id)

    environment = await session.get(Environment, environment_id)
    if (
        environment is None
        or environment.deleted_at is not None
        or environment.project_id != case.project_id
    ):
        raise not_found("environment", environment_id)  # 归属不符一律 404,同 trigger_run

    project_vars, env_vars = await _load_scope_variables(session, case.project_id, environment.id)
    scope = VariableScope(
        project=project_vars, environment=env_vars, case=dsl.config.variables
    )
    settings = get_settings()
    context = EngineContext(
        sink=InMemorySink(),
        # run_http_step 直接使用显式传入的 client,factory/sink 是 EngineContext 的必填契约位
        http_client_factory=lambda verify: httpx.AsyncClient(follow_redirects=True, verify=verify),
        body_max_bytes=settings.body_max_bytes,  # 与报告链路同一截断阈值
    )
    async with httpx.AsyncClient(follow_redirects=True, verify=step.request.verify_ssl) as client:
        try:
            result = await run_http_step(step, scope, context, client, dsl.config.timeout_ms)
        except Exception as exc:
            # 引擎只兜底 TransportError;调试要给步骤级反馈而非 500:
            # 坏 JSONPath 表达式、非法 URL、重定向循环等都属于用户的步骤问题
            result = StepResult(
                step_id=step.id,
                step_name=step.name,
                step_index=0,
                status="error",
                error_message=f"{type(exc).__name__}: {exc}",
            )

    return StepDebugOut(
        step_id=result.step_id,
        step_name=result.step_name,
        status=result.status,
        duration_ms=result.duration_ms,
        attempts=result.attempts,
        error_message=result.error_message,
        request=RequestSnapshotOut(
            method=result.request.method,
            url=result.request.url,
            headers=result.request.headers,
            query=result.request.query,
            body=result.request.body,
        )
        if result.request is not None
        else None,
        response=ResponseSnapshotOut(
            status_code=result.response.status_code,
            headers=result.response.headers,
            body=result.response.body,
            size=response_size(result.response),
            truncated=result.response.truncated,
        )
        if result.response is not None
        else None,
        extracted=result.extracted,
        assertions=[
            DebugAssertionOut(
                type=a.type,
                operator=a.operator,
                expected=a.expected,
                actual=a.actual,
                passed=a.passed,
                message=a.message,
            )
            for a in result.assertions
        ],
    )
