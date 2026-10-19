"""test_plan / test_plan_item(数据模型文档 §8,#29)。

计划 = 有序用例清单 + 执行配置。一次计划执行对应一条 run(§3.3 定案),
条目只是清单的投影 —— 用例版本在触发时才锁定,清单行不存版本。
"""

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, SoftDeleteMixin, TimestampMixin, fk_column, pk

EXECUTE_MODES = ("serial", "parallel")
ITEM_TYPE_CASE = "case"


class TestPlan(Base, TimestampMixin, SoftDeleteMixin):
    __tablename__ = "test_plan"
    # 同 test_case.priority:业务枚举走 VARCHAR + CHECK,不用 PG 原生 ENUM
    __table_args__ = (
        CheckConstraint(f"execute_mode IN {EXECUTE_MODES}", name="execute_mode"),
        CheckConstraint("concurrency >= 1", name="concurrency_positive"),
    )

    id: Mapped[int] = pk()
    project_id: Mapped[int] = fk_column("project")
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 可空:触发时请求体可再覆盖(数据模型 §8「运行时指定则覆盖」)
    environment_id: Mapped[int | None] = mapped_column(
        ForeignKey("environment.id", name="fk_test_plan_environment_id_environment"), nullable=True
    )
    execute_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="serial")
    concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 计划级重试:本阶段只建列不实现(spec Out of Scope),等真实反馈定语义
    retry_on_fail: Mapped[bool] = mapped_column(nullable=False, default=False)
    retry_times: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stop_on_fail: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_by: Mapped[int] = mapped_column(
        ForeignKey("user.id", name="fk_test_plan_created_by_user"), nullable=False
    )


class TestPlanItem(Base, TimestampMixin):
    __tablename__ = "test_plan_item"
    __table_args__ = (
        UniqueConstraint("plan_id", "item_type", "item_id"),
        CheckConstraint(f"item_type IN ('{ITEM_TYPE_CASE}')", name="item_type"),
    )

    id: Mapped[int] = pk()
    plan_id: Mapped[int] = mapped_column(
        ForeignKey("test_plan.id", name="fk_test_plan_item_plan_id_test_plan"),
        nullable=False,
        index=True,
    )
    # 保留列以备「计划内嵌子计划」;当前仅 case。item_id 不设外键
    # (数据模型 §8 的取舍:保留类型列的同时放弃数据库级引用校验,
    #  归属校验由端点负责 —— 跨项目/已删用例一律 404)
    item_type: Mapped[str] = mapped_column(String(16), nullable=False, default=ITEM_TYPE_CASE)
    item_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
