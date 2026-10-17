"""全平台唯一 SQLAlchemy Base 与公共 Mixin(数据模型文档 §1 总体约定)。

- 业务实体 BIGINT 自增主键
- 软删除统一 SoftDeleteMixin
- 时间戳由数据库默认值生成,不信任应用时钟
- naming convention 必配,否则约束名不稳定,Alembic 后续 ALTER 会出错
"""

import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column
from sqlalchemy.sql.elements import ColumnElement

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    # 业务表枚举一律 VARCHAR + CHECK,不用 PG 原生 ENUM(§1)
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class SoftDeleteMixin:
    """deleted_at 非空即视为已删除。

    查询必须经 alive() 取「未删」谓词,不允许在 service/路由手写
    deleted_at 条件 —— 漏一处就是数据泄漏(数据模型文档 §1)。
    """

    @declared_attr
    def deleted_at(cls) -> Mapped[datetime.datetime | None]:
        return mapped_column(DateTime(timezone=True), nullable=True)

    @classmethod
    def alive(cls) -> ColumnElement[bool]:
        return cls.deleted_at.is_(None)


def pk() -> Mapped[int]:
    return mapped_column(BigInteger, primary_key=True, autoincrement=True)


def fk_column(referred_table: str, nullable: bool = False) -> Mapped[int]:
    """显式 FK 约束(数据模型文档 §1:与 MeterSphere 的零外键相反)。"""
    return mapped_column(
        BigInteger, ForeignKey(f"{referred_table}.id"), nullable=nullable
    )
