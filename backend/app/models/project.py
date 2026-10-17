"""project 表(数据模型文档 §3)。settings 里放项目级配置开关(D4)。"""

from typing import Any

from sqlalchemy import JSON, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk


class Project(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "project"

    id: Mapped[int] = pk()
    name: Mapped[str] = mapped_column(nullable=False)  # VARCHAR(128)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[int] = fk_column("user")
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
