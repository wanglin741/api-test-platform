"""run / run_step_result(数据模型文档 §9)。

run.id 用 UUID:对外暴露,写进 SSE 频道与 CI 回调,自增 ID 会泄漏业务量。
run_step_result 的响应体分流(≤64KB 内联 / 超限转对象存储)由 ResultSink 实现
决定;MVP 只内联,response_body_object_key 为预留列。
"""

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, fk_column, pk

RUN_TERMINAL_STATUSES = ("success", "failed", "error", "stopped")


class Run(Base, TimestampMixin):
    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'stopping', 'stopped', 'success', 'failed', 'error')",
            name="ck_run_status",
        ),
        CheckConstraint("trigger_mode IN ('manual', 'schedule', 'openapi', 'ci')", name="ck_run_trigger_mode"),
        Index("ix_run_project_id_created_at", "project_id", "created_at"),
        Index("ix_run_status", "status"),
        Index("ix_run_celery_task_id", "celery_task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[int] = fk_column("project")
    case_id: Mapped[int] = mapped_column(ForeignKey("test_case.id"), nullable=False)
    case_version: Mapped[int] = mapped_column(Integer, nullable=False)
    environment_id: Mapped[int] = fk_column("environment")
    trigger_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    triggered_by: Mapped[int | None] = mapped_column(ForeignKey("user.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class RunStepResult(Base, TimestampMixin):
    __tablename__ = "run_step_result"
    __table_args__ = (
        CheckConstraint("status IN ('passed', 'failed', 'error', 'skipped')", name="ck_run_step_result_status"),
        Index("ix_run_step_result_run_id_step_index", "run_id", "step_index"),
        Index("ix_run_step_result_created_at", "created_at"),
    )

    id: Mapped[int] = pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("run.id"), nullable=False
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    step_name: Mapped[str] = mapped_column(String(255), nullable=False)
    step_type: Mapped[str] = mapped_column(String(32), nullable=False, default="http")
    parent_index: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 循环/条件体预留
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request_method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    request_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    request_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    response_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    response_body_object_key: Mapped[str | None] = mapped_column(String(512), nullable=True)  # 预留:超限转对象存储
    response_body_sha256: Mapped[str | None] = mapped_column(CHAR(64), nullable=True)
    response_body_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    extracted_variables: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    assertions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    assertion_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    assertion_passed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
