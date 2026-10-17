"""engine / session 工厂。API 用 asyncpg,worker 用同步 psycopg(D6)。"""

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import sessionmaker


def make_async_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


def make_sync_session_factory(database_url: str) -> sessionmaker[Any]:
    """worker 进程用(同步驱动);应用启动时不创建。"""
    from sqlalchemy import create_engine

    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


async def get_db(session_factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """请求级会话:成功提交,异常回滚。"""
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
