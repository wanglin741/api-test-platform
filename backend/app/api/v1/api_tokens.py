"""API Token 管理(issue #38,API 设计「认证」节)。

明文仅在创建响应返回一次;列表不含哈希。撤销 = 软删,立即失效。
归属校验「不符即 404,不探测」,与 schedules 端点同形。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.api.v1.common import get_project_or_404
from app.core.errors import NotFoundError
from app.core.tokens import generate_token, hash_token
from app.models import ApiToken, User
from app.schemas.api_tokens import ApiTokenCreate, ApiTokenCreated, ApiTokenOut

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])
project_router = APIRouter(prefix="/projects/{project_id}/api-tokens", tags=["api-tokens"])


@project_router.post("", status_code=201, response_model=ApiTokenCreated)
async def create_api_token(
    project_id: int,
    payload: ApiTokenCreate,
    session: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ApiTokenCreated:
    await get_project_or_404(session, project_id)
    raw = generate_token()
    token = ApiToken(
        project_id=project_id,
        user_id=user.id,
        name=payload.name,
        token_hash=hash_token(raw),
        scopes=list(payload.scopes),
        expires_at=payload.expires_at,
    )
    session.add(token)
    await session.flush()
    await session.refresh(token)
    # 明文只在这一刻返回,此后库里只有哈希
    return ApiTokenCreated(**ApiTokenOut.model_validate(token).model_dump(), token=raw)


@project_router.get("", response_model=list[ApiTokenOut])
async def list_api_tokens(
    project_id: int,
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> list[ApiTokenOut]:
    await get_project_or_404(session, project_id)
    rows = await session.scalars(
        select(ApiToken)
        .where(ApiToken.project_id == project_id, ApiToken.alive())
        .order_by(ApiToken.id)
    )
    return [ApiTokenOut.model_validate(t) for t in rows]


@router.delete("/{token_id}", status_code=204)
async def revoke_api_token(
    token_id: int,
    session: AsyncSession = Depends(get_db),
    _user: User = Depends(get_current_user),
) -> None:
    token = await session.get(ApiToken, token_id)
    if token is None or token.deleted_at is not None:
        raise NotFoundError(
            "api_token 不存在", code="api_token_not_found", details={"token_id": token_id}
        )
    token.deleted_at = func.now()
    await session.flush()
