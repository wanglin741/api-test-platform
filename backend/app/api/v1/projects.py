"""项目 CRUD。"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.errors import not_found
from app.models import Project, User
from app.schemas.resources import ProjectCreate, ProjectOut, ProjectUpdate

router = APIRouter(prefix="/projects", tags=["projects"])


async def _get_project_or_404(session: AsyncSession, project_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise not_found("project", project_id)
    return project


@router.post("", status_code=201, response_model=ProjectOut)
async def create_project(
    payload: ProjectCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = Project(name=payload.name, description=payload.description, settings=payload.settings, owner_id=user.id)
    session.add(project)
    await session.flush()
    # server_default 列(created_at 等)在 flush 后需显式取回,否则响应序列化触发同步 IO
    await session.refresh(project)
    return project


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[Project]:
    result = await session.execute(
        select(Project).where(Project.deleted_at.is_(None)).order_by(Project.id)
    )
    return list(result.scalars())


@router.get("/{project_id}", response_model=ProjectOut)
async def get_project(
    project_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    return await _get_project_or_404(session, project_id)


@router.patch("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    payload: ProjectUpdate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Project:
    project = await _get_project_or_404(session, project_id)
    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(project, field, value)
    await session.flush()
    await session.refresh(project)
    return project


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    project = await _get_project_or_404(session, project_id)
    project.deleted_at = func.now()
    await session.flush()
