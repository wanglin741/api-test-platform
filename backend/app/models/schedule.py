"""schedule(数据模型文档 §10,issue #36)。

一个计划可配多条调度;next_run_at 必须持久化(tick 主查询走 (enabled, next_run_at)
索引,「上次跑到哪」有据可查)。last_run_id 纯展示指针、不建外键,避免与
run.schedule_id 成循环外键(spec 决策 #11)。last_skip_at/last_skip_reason 只保留
最近一次跳过(audit_log 未建表前的最小承载,tick 任务落值)。
"""

import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk


class Schedule(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "schedule"
    __table_args__ = (
        Index("ix_schedule_enabled_next_run_at", "enabled", "next_run_at"),
        Index("ix_schedule_plan_id", "plan_id"),
        # 项目作用域的调度列表(数据模型 §10)
        Index("ix_schedule_project_id", "project_id"),
    )

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plan.id", name="fk_schedule_plan_id_test_plan"), nullable=False
    )
    cron_expression: Mapped[str] = mapped_column(String(64), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # 覆盖计划的默认环境;空则回退计划配置
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environment.id", name="fk_schedule_environment_id_environment"), nullable=True
    )
    next_run_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_run_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 纯展示指针,不建外键(spec 决策 #11)
    last_run_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    last_skip_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_skip_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("user.id", name="fk_schedule_created_by_user"), nullable=False
    )
