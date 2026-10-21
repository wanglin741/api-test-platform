"""run / run_step_result(数据模型文档 §9)。

run.id 用 UUID:对外暴露,写进 SSE 频道与 CI 回调,自增 ID 会泄漏业务量。
run_step_result 的响应体分流(≤64KB 内联 / 超限转对象存储)由 ResultSink 实现
决定;MVP 只内联,response_body_object_key 为预留列。
"""

import datetime
import uuid

from sqlalchemy import (
    CHAR,
    BigInteger,
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
        # name 走 naming convention 前缀:ck_%(table_name)s_%(constraint_name)s → ck_run_status
        CheckConstraint(
            "status IN ('pending', 'running', 'stopping', 'stopped', 'success', 'failed', 'error')",
            name="status",
        ),
        CheckConstraint("trigger_mode IN ('manual', 'schedule', 'openapi', 'ci')", name="trigger_mode"),
        Index("ix_run_project_id_created_at", "project_id", "created_at"),
        # 计划维度收口:按计划拉最近执行(报告列表 plan_id 筛选、计划详情「最近执行」)
        Index("ix_run_plan_id_created_at", "plan_id", "created_at"),
        Index("ix_run_status", "status"),
        Index("ix_run_celery_task_id", "celery_task_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[int] = fk_column("project")
    # case_* 与 plan_id 二选一:单用例执行走 case_id,计划执行走 plan_id
    case_id: Mapped[int | None] = mapped_column(ForeignKey("test_case.id"), nullable=True)
    case_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_id: Mapped[int | None] = mapped_column(
        ForeignKey("test_plan.id", name="fk_run_plan_id_test_plan"), nullable=True, index=True
    )
    # 定时触发来源;调度删除后历史 run 仍可追溯(schedule 软删,引用不断)
    schedule_id: Mapped[int | None] = mapped_column(
        ForeignKey("schedule.id", name="fk_run_schedule_id_schedule"), nullable=True, index=True
    )
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
    # 质量门禁配置(spec #35 决策 #10):持久化在 run 上,worker 终态求值可取回;
    # 定时 run 不携带门禁
    quality_gate: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class RunStepResult(Base, TimestampMixin):
    __tablename__ = "run_step_result"
    __table_args__ = (
        CheckConstraint("status IN ('passed', 'failed', 'error', 'skipped')", name="status"),
        Index("ix_run_step_result_run_id_step_index", "run_id", "step_index"),
        Index("ix_run_step_result_run_id_case_step", "run_id", "case_index", "step_index"),
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
    # 用例归属:计划执行时 = (case_index, step_index);单用例执行保持缺省,
    # 既有查询不受影响。case_id 是冗余副本、不设外键 —— 用例删除后历史报告仍可还原
    case_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    case_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
