"""变量管理:项目级(environment_id 为空)与环境级,均采用 PUT 整体替换。"""

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.core.errors import not_found
from app.api.v1.environments import _get_environment_or_404, _get_project_or_404
from app.models import User, Variable
from app.schemas.resources import VariableOut, VariableReplace

router = APIRouter(prefix="/environments", tags=["variables"])
project_router = APIRouter(prefix="/projects/{project_id}/variables", tags=["variables"])


async def _list_variables(
    session: AsyncSession, project_id: int, environment_id: int | None
) -> list[Variable]:
    result = await session.execute(
        select(Variable)
        .where(
            Variable.project_id == project_id,
            Variable.environment_id.is_(environment_id) if environment_id is None else Variable.environment_id == environment_id,
        )
        .order_by(Variable.id)
    )
    return list(result.scalars())


async def _replace_variables(
    session: AsyncSession, project_id: int, environment_id: int | None, payload: VariableReplace
) -> list[Variable]:
    await session.execute(
        delete(Variable).where(
            Variable.project_id == project_id,
            Variable.environment_id.is_(environment_id) if environment_id is None else Variable.environment_id == environment_id,
        )
    )
    variables = [
        Variable(project_id=project_id, environment_id=environment_id, **item.model_dump())
        for item in payload.items
    ]
    session.add_all(variables)
    await session.flush()
    return variables


@project_router.get("", response_model=list[VariableOut])
async def list_project_variables(
    project_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[VariableOut]:
    await _get_project_or_404(session, project_id)
    variables = await _list_variables(session, project_id, None)
    return [VariableOut.from_model(v) for v in variables]


@project_router.put("", response_model=list[VariableOut])
async def replace_project_variables(
    project_id: int,
    payload: VariableReplace,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[VariableOut]:
    await _get_project_or_404(session, project_id)
    variables = await _replace_variables(session, project_id, None, payload)
    return [VariableOut.from_model(v) for v in variables]


@router.get("/{environment_id}/variables", response_model=list[VariableOut])
async def list_environment_variables(
    environment_id: int,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[VariableOut]:
    environment = await _get_environment_or_404(session, environment_id)
    variables = await _list_variables(session, environment.project_id, environment_id)
    return [VariableOut.from_model(v) for v in variables]


@router.put("/{environment_id}/variables", response_model=list[VariableOut])
async def replace_environment_variables(
    environment_id: int,
    payload: VariableReplace,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[VariableOut]:
    environment = await _get_environment_or_404(session, environment_id)
    variables = await _replace_variables(session, environment.project_id, environment_id, payload)
    return [VariableOut.from_model(v) for v in variables]
