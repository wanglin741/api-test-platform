"""CRUD 集成测试:真实 PostgreSQL(compose),测试库 apitest_test。

在导入任何 app 模块之前把 Settings 指向测试库,避免误伤开发库 apitest。
"""

import os

_TEST_DB = "apitest_test"
os.environ.setdefault(
    "APITEST_DATABASE_URL",
    f"postgresql+asyncpg://apitest:apitest@localhost:5432/{_TEST_DB}",
)
os.environ.setdefault(
    "APITEST_DATABASE_URL_SYNC",
    f"postgresql+psycopg://apitest:apitest@localhost:5432/{_TEST_DB}",
)

from pathlib import Path

import psycopg
import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient

BACKEND_DIR = Path(__file__).resolve().parents[2]


def _run_alembic(target: str) -> None:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(cfg, target)


@pytest.fixture(scope="session", autouse=True)
def database() -> None:
    """建测试库(若缺)并执行迁移;会话结束后清库,保证可重复运行。"""
    admin = "postgresql://apitest:apitest@localhost:5432/postgres"
    with psycopg.connect(admin, autocommit=True) as conn:
        exists = conn.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (_TEST_DB,)
        ).fetchone()
        if exists is None:
            conn.execute(f'CREATE DATABASE {_TEST_DB}')
    _run_alembic("head")
    yield
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (_TEST_DB,),
        )
        conn.execute(f'DROP DATABASE {_TEST_DB}')


@pytest.fixture(autouse=True)
def clean_tables(database: None) -> None:
    """每个用例后清业务表(保留种子用户),用例之间互不可见。"""
    yield
    sync_url = os.environ["APITEST_DATABASE_URL_SYNC"].replace("+psycopg", "")
    with psycopg.connect(sync_url, autocommit=True) as conn:
        conn.execute(
            "TRUNCATE run_step_result, run, test_case_version, test_case, variable, environment, project RESTART IDENTITY CASCADE"
        )


@pytest.fixture(scope="module")
def local_server():
    """本地真实 HTTP 服务(真实 socket),供执行链路与验收场景作被测目标。"""
    import threading

    from tests.e2e.server import E2EServer

    server = E2EServer()
    host, port = server.server_address[:2]
    url = f"http://{host}:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield url
    server.shutdown()
    server.server_close()


@pytest.fixture(autouse=True)
def eager_celery():
    """Celery eager 模式:任务在进程内同步执行,不依赖 broker。"""
    from app.tasks.celery_app import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = False
    celery_app.conf.task_eager_propagates = False


@pytest.fixture
async def client():
    from app.core.config import get_settings
    from app.db.session import make_async_engine, make_session_factory
    from app.main import app

    # httpx 的 ASGITransport 不触发 lifespan,这里手动装配等价状态
    engine = make_async_engine(get_settings().database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http
    await engine.dispose()
