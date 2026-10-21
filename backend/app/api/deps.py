"""FastAPI 依赖:数据库会话与当前用户。

认证的唯一改动点(spec #35 决策 #1/#12):resolve_principal 按凭据前缀分派 ——
- 无 Bearer / 非 `at_` 凭据 → 种子用户(浏览器兼容,既有前端不回归);
- `Bearer at_...` → API Token:查 SHA-256 哈希、校验软删/过期、刷新
  last_used_at;未知/过期/已撤销 → 401 invalid_token。

SSE 端点不能挂 yield 依赖(get_db 的清理在流结束后才跑,会把连接占住
整个 SSE 生命周期),但认证查询经同一个 resolve_principal 与普通端点
同源 —— 认证分派不因流端点破例。
"""

import datetime
from collections.abc import AsyncIterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.core.tokens import TOKEN_PREFIX, hash_token
from app.models import ApiToken, User

SEED_USERNAME = "admin"


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def _seed_user(session: AsyncSession) -> User:
    result = await session.execute(select(User).where(User.username == SEED_USERNAME))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(
            status_code=500,
            code="seed_user_missing",
            message="种子用户不存在,请确认迁移已执行",
        )
    return user


@dataclass
class Principal:
    """请求主体:浏览器(种子用户)或 API Token。

    Token 请求经 token 携带 scopes 与项目边界;user 仅作为归属身份
    (run 的 triggered_by 对 Token 触发保持 NULL,归因走 api_token)。
    """

    user: User
    token: ApiToken | None = None


async def resolve_principal(session: AsyncSession, request: Request) -> Principal:
    header = request.headers.get("authorization") or ""
    scheme, _, credential = header.partition(" ")
    if scheme.lower() != "bearer" or not credential.startswith(TOKEN_PREFIX):
        return Principal(user=await _seed_user(session))

    token = (
        await session.scalars(
            select(ApiToken).where(
                ApiToken.token_hash == hash_token(credential), ApiToken.alive()
            )
        )
    ).first()
    if token is None or (
        token.expires_at is not None
        and token.expires_at <= datetime.datetime.now(datetime.UTC)
    ):
        raise AppError(
            status_code=401,
            code="invalid_token",
            message="API Token 无效、已过期或已撤销",
        )
    token.last_used_at = func.now()
    await session.flush()
    user = await session.get(User, token.user_id)
    if user is None:
        raise AppError(
            status_code=500,
            code="token_user_missing",
            message="Token 绑定的用户不存在",
        )
    return Principal(user=user, token=token)


def require_token_scope(principal: Principal, scope: str) -> None:
    """Token 请求的 scope 校验;浏览器(种子用户)请求不受限。"""
    if principal.token is not None and scope not in (principal.token.scopes or []):
        raise AppError(
            status_code=403,
            code="insufficient_scope",
            message=f"Token 缺少 {scope} 权限",
            details={"required_scope": scope},
        )


def require_token_project(principal: Principal, project_id: int) -> None:
    """Token 的项目边界;跨项目一律 403,不探测。"""
    if principal.token is not None and principal.token.project_id != project_id:
        raise AppError(
            status_code=403,
            code="token_project_mismatch",
            message="Token 不属于该项目",
            details={"project_id": project_id},
        )


async def get_current_user(request: Request, session: AsyncSession = Depends(get_db)) -> User:
    """非 run 端点统一挂这个依赖:Token 到这里即 403 —— 仅 run 端点
    (触发 / 报告读取)接受 Token(spec 决策 #12),其余端点一律拒绝,
    未完成的权限体系不外露。"""
    principal = await resolve_principal(session, request)
    if principal.token is not None:
        raise AppError(
            status_code=403,
            code="token_not_allowed",
            message="API Token 仅用于执行触发与报告读取",
        )
    return principal.user


async def get_principal(request: Request, session: AsyncSession = Depends(get_db)) -> Principal:
    """run 端点的认证依赖:接受 Token,scope / 项目边界由端点自行校验。"""
    return await resolve_principal(session, request)
