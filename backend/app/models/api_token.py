"""api_token(CI 集成,数据模型文档 §13,issue #38)。

只存 SHA-256 哈希:明文 `at_` 前缀串仅创建响应返回一次。撤销 = 软删
(alive() 过滤后查不到即 401,立即失效)。scopes 为 JSONB 数组,默认
["run:trigger", "report:read"](spec 决策 #12 最小权限)。
"""

import datetime

from sqlalchemy import DateTime, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk


class ApiToken(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "api_token"
    # 与迁移 c3f7a91b2d44 建出的索引保持一致:model 不声明会让后续
    # autogenerate 误判为多余索引并生成 drop
    __table_args__ = (Index("ix_api_token_project_id", "project_id"),)

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project", nullable=False)
    # 以该用户身份执行(数据模型 §13):权限边界跟随 user,不引入机器人账号
    user_id: Mapped[int] = fk_column("user", nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # SHA-256 hex 为 64 字符;唯一约束支撑按哈希直查认证
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    expires_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
