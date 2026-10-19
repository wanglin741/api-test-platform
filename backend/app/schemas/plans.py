"""计划(plans)的请求与响应模型(issue #29,数据模型 §8)。

item_count 不在表上,查询时聚合;PlanOut 用它承载列表/详情统一形状。
"""

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PlanCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    environment_id: int | None = None
    execute_mode: Literal["serial", "parallel"] = "serial"
    concurrency: int = Field(default=1, ge=1)
    stop_on_fail: bool = False


class PlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    environment_id: int | None = None
    execute_mode: Literal["serial", "parallel"] | None = None
    concurrency: int | None = Field(default=None, ge=1)
    stop_on_fail: bool | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    environment_id: int | None
    execute_mode: str
    concurrency: int
    stop_on_fail: bool
    item_count: int = 0
    created_at: datetime.datetime
    updated_at: datetime.datetime


class PlanItemIn(BaseModel):
    case_id: int


class PlanItemsPut(BaseModel):
    items: list[PlanItemIn]


class PlanItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_type: str
    case_id: int
    case_name: str
    sort_order: int
