"""Settings 冒烟测试:配置类可加载、环境变量覆盖生效。"""

from app.core.config import Settings


def test_settings_defaults() -> None:
    s = Settings(_env_file=None)

    assert s.env == "dev"
    assert s.database_url.startswith("postgresql+asyncpg://")
    assert s.database_url_sync.startswith("postgresql+psycopg://")
    assert s.redis_url.startswith("redis://")
    assert s.body_max_bytes == 64 * 1024
    assert s.env_var_whitelist_prefixes == ("TEST_",)


def test_settings_env_override(monkeypatch) -> None:
    monkeypatch.setenv("APITEST_DATABASE_URL", "postgresql+asyncpg://u:p@h:1/db")
    monkeypatch.setenv("APITEST_BODY_MAX_BYTES", "1024")

    s = Settings(_env_file=None)

    assert s.database_url == "postgresql+asyncpg://u:p@h:1/db"
    assert s.body_max_bytes == 1024
