"""环境 CRUD。环境归属项目;项目被软删后,其环境对外一律 404。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.errors import ConflictError, not_found
from app.models import Environment, Project, User
from app.schemas.resources import EnvironmentCreate, EnvironmentOut, EnvironmentUpdate

router = APIRouter(prefix="/environments", tags=["environments"])
project_router = APIRouter(prefix="/projects/{project_id}/environments", tags=["environments"])


async def _get_environment_or_404(
    session: AsyncSession, environment_id: int, *, require_active_project: bool = True
) -> Environment:
    result = await session.execute(
        select(Environment, Project).join(Project, Environment.project_id == Project.id).where(
            Environment.id == environment_id, Environment.alive()
        )
    )
    row = result.first()
    if row is None:
        raise not_found("environment", environment_id)
    environment, project = row
    if require_active_project and project.deleted_at is not None:
        raise not_found("environment", environment_id)
    return environment


@project_router.post("", status_code=201, response_model=EnvironmentOut)
async def create_environment(
    project_id: int,
    payload: EnvironmentCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Environment:
    await get_project_or_404(session, project_id)
    # 表级 UNIQUE (project_id, name) 含软删行 —— 已删环境的名额不释放,与数据库约束保持一致
    conflict = await session.execute(
        select(Environment.id).where(
            Environment.project_id == project_id,
            Environment.name == payload.name,
        )
    )
    if conflict.scalar_one_or_none() is not None:
        raise ConflictError(
            code="environment_name_conflict",
            message=f"项目 {project_id} 下已存在同名环境 {payload.name!r}",
            details={"project_id": project_id, "name": payload.name},
        )
    environment = Environment(project_id=project_id, **payload.model_dump())
    session.add(environment)
    await session.flush()
    await session.refresh(environment)  # 取回 server_default 列
    return environment


@project_router.get("", response_model=list[EnvironmentOut])
async def list_environments(
    project_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Environment]:
    await get_project_or_404(session, project_id)
    result = await session.execute(
        select(Environment)
        .where(Environment.project_id == project_id, Environment.alive())
        .order_by(Environment.sort_order, Environment.id)
    )
    return list(result.scalars())


@router.get("/{environment_id}", response_model=EnvironmentOut)
async def get_environment(
    environment_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Environment:
    return await _get_environment_or_404(session, environment_id)


@router.patch("/{environment_id}", response_model=EnvironmentOut)
async def update_environment(
    environment_id: int,
    payload: EnvironmentUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Environment:
    environment = await _get_environment_or_404(session, environment_id)
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] != environment.name:
        conflict = await session.execute(
            select(Environment.id).where(
                Environment.project_id == environment.project_id,
                Environment.name == data["name"],
                Environment.id != environment.id,
            )
        )
        if conflict.scalar_one_or_none() is not None:
            raise ConflictError(
                code="environment_name_conflict",
                message=f"项目 {environment.project_id} 下已存在同名环境 {data['name']!r}",
                details={"project_id": environment.project_id, "name": data["name"]},
            )
    for field, value in data.items():
        setattr(environment, field, value)
    await session.flush()
    await session.refresh(environment)  # 取回 server_default 列
    return environment


@router.delete("/{environment_id}", status_code=204)
async def delete_environment(
    environment_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    environment = await _get_environment_or_404(session, environment_id)
    environment.deleted_at = func.now()
    await session.flush()
