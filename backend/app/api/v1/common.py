"""路由间共享的资源取用助手。"""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import not_found
from app.models import Project


async def get_project_or_404(session: AsyncSession, project_id: int) -> Project:
    project = await session.get(Project, project_id)
    if project is None or project.deleted_at is not None:
        raise not_found("project", project_id)
    return project
