"""folder 表(数据模型文档 §4,选型见 ADR-0001:独立目录表而非统一节点表)。

目录树只查这一张小表;用例永远是叶子,经 test_case.folder_id 弱挂载。
「未分类」= folder_id IS NULL,不落哨兵行。
"""

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk


class Folder(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "folder"
    # PG 对 NULL 视为互异:parent_id 为空的顶层行不受该约束管,顶层同名冲突由应用层预检
    __table_args__ = (UniqueConstraint("project_id", "scope", "parent_id", "name"),)

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project")
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="case")
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("folder.id", ondelete="CASCADE"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)
