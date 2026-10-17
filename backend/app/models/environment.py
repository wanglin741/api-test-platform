"""environment 表(数据模型文档 §5)。UNIQUE (project_id, name)。"""

from typing import Any

from sqlalchemy import JSON, Boolean, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk


class Environment(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "environment"
    __table_args__ = (UniqueConstraint("project_id", "name"),)

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project")
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    default_headers: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    auth: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
