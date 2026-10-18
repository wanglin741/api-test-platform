"""test_case / test_case_version(数据模型文档 §7)。

用例本体只存元信息,DSL 正文整体放在 test_case_version.dsl(JSONB):
版本快照就是复制一个 JSONB 值,执行记录引用的 (case_id, version) 永远可还原。
"""

from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk


class TestCase(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "test_case"
    # name 走 naming convention 前缀:ck_%(table_name)s_%(constraint_name)s → ck_test_case_priority
    __table_args__ = (CheckConstraint("priority IN ('P0', 'P1', 'P2', 'P3')", name="priority"),)

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project")
    # 可空:NULL 即「未分类」(issue #20);目录删除拒绝非空,不会留下悬挂引用
    folder_id: Mapped[int | None] = mapped_column(
        ForeignKey("folder.id", name="fk_test_case_folder_id_folder"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str | None] = mapped_column(String(8), nullable=True)
    tags: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_test_case_created_by_user"), nullable=False)


class TestCaseVersion(Base, TimestampMixin):
    __tablename__ = "test_case_version"
    __table_args__ = (UniqueConstraint("test_case_id", "version"),)

    id: Mapped[int] = pk()
    test_case_id: Mapped[int] = mapped_column(
        ForeignKey("test_case.id", name="fk_test_case_version_test_case_id_test_case"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    dsl: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    change_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("user.id", name="fk_test_case_version_created_by_user"), nullable=False)
