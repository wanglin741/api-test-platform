"""variable 表(数据模型文档 §5)。

environment_id 为 NULL 表示项目级变量;UNIQUE (project_id, environment_id, name)。
注意:PG 里 UNIQUE 对 NULL 不去重,项目级变量 (environment_id IS NULL) 的唯一性
由应用层在整体替换时保证。
is_secret 只保证不出现在 API 响应与报告,值仍以明文落 PG(文档已声明该边界)。
"""

from sqlalchemy import Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, fk_column, pk


class Variable(Base, TimestampMixin):
    __tablename__ = "variable"
    __table_args__ = (UniqueConstraint("project_id", "environment_id", "name"),)

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project")
    environment_id: Mapped[int | None] = fk_column("environment", nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_secret: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
