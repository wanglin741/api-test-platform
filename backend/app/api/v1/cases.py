"""用例 CRUD + 版本快照。

保存即快照:POST 写 v1,带 dsl 的 PATCH 递增。dsl 是执行的唯一事实来源,
元信息(name/priority/tags)的更新不动版本。
"""

from fastapi import APIRouter, Depends, Query
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.errors import ValidationFailedError, not_found
from app.engine.dsl import parse_dsl
from app.models import Project, TestCase, TestCaseVersion, User
from app.schemas.cases import CaseCreate, CaseDetail, CaseOut, CasePage, CaseUpdate

router = APIRouter(prefix="/cases", tags=["cases"])
project_router = APIRouter(prefix="/projects/{project_id}/cases", tags=["cases"])


def _validate_dsl(dsl: dict) -> None:
    """DSL 结构校验,失败给 422 且错误信息定位到字段(loc 为 JSON 路径)。"""
    try:
        parse_dsl(dsl)
    except ValidationError as e:
        errors = [
            {"loc": list(err["loc"]), "msg": err["msg"], "type": err["type"]} for err in e.errors()
        ]
        raise ValidationFailedError(
            message="DSL 结构校验失败",
            code="dsl_invalid",
            details={"errors": errors},
        ) from e


async def _get_case_or_404(session: AsyncSession, case_id: int) -> TestCase:
    case = await session.get(TestCase, case_id)
    if case is None or case.deleted_at is not None:
        raise not_found("case", case_id)
    return case


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
    _validate_dsl(payload.dsl)
    case = TestCase(
        project_id=project_id,
        name=payload.name,
        description=payload.description,
        priority=payload.priority,
        tags=payload.tags,
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
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> CasePage:
    if page_size > 100:  # 超限钳制而非报错,保住客户端「一页拉全部」的写法
        page_size = 100
    conditions = [TestCase.project_id == project_id, TestCase.alive()]
    if keyword:
        conditions.append(TestCase.name.ilike(f"%{keyword}%"))
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
