"""应用配置。API 进程与 worker 进程共用同一份 Settings。"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="APITEST_",
        extra="ignore",
    )

    # dev / test / prod
    env: str = "dev"

    # API 进程用 asyncpg,worker 进程用同步 psycopg(设计文档 D6)
    database_url: str = "postgresql+asyncpg://apitest:apitest@localhost:5432/apitest"
    database_url_sync: str = "postgresql+psycopg://apitest:apitest@localhost:5432/apitest"

    redis_url: str = "redis://localhost:6379/0"

    # 响应体内联存储上限,超出截断(设计文档 D4 / 执行引擎 §11)
    body_max_bytes: int = 64 * 1024

    # {{env.XXX}} 允许读取的进程环境变量前缀白名单(执行引擎 §3)
    env_var_whitelist_prefixes: tuple[str, ...] = ("TEST_",)


@lru_cache
def get_settings() -> Settings:
    return Settings()
