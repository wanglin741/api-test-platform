"""FastAPI 依赖:数据库会话与当前用户。

get_current_user 是认证的唯一改动点 —— MVP 阶段直接返回种子用户,
接入真实认证时只需替换这个依赖的实现,各路由的字段照常写入。
"""

from collections.abc import AsyncIterator

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.errors import AppError
from app.models import User

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


async def get_current_user(session: AsyncSession = Depends(get_db)) -> User:
    result = await session.execute(select(User).where(User.username == SEED_USERNAME))
    user = result.scalar_one_or_none()
    if user is None:
        raise AppError(
            status_code=500,
            code="seed_user_missing",
            message="种子用户不存在,请确认迁移已执行",
        )
    return user
