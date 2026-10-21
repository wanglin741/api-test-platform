"""迁移可重放:upgrade head → downgrade base → upgrade head。"""

from tests.integration.conftest import _run_alembic


def test_migration_replay(database: None) -> None:
    _run_alembic("base")
    _run_alembic("head")

    import os

    import psycopg

    sync_url = os.environ["APITEST_DATABASE_URL_SYNC"].replace("+psycopg", "")
    with psycopg.connect(sync_url) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        }
        assert {
            "user",
            "project",
            "environment",
            "variable",
            "folder",
            "test_case",
            "test_case_version",
            "run",
            "run_step_result",
            "schedule",
            "api_token",
            "alembic_version",
        } <= tables
        # 门禁配置持久化在 run 上(#40);重放后列必须存在
        run_columns = {
            row[0]
            for row in conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name = 'run'"
            ).fetchall()
        }
        assert "quality_gate" in run_columns
        # 重放后种子用户必须存在(get_current_user 依赖它)
        row = conn.execute(
            "SELECT username, is_superuser FROM \"user\" WHERE username = 'admin'"
        ).fetchone()
        assert row == ("admin", True)
