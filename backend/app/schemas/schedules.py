"""调度(schedules)的请求与响应模型(issue #36,数据模型 §10)。

next_run_at/last_run_* 由服务端维护,请求体不接受。
"""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScheduleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    plan_id: int
    cron_expression: str = Field(min_length=1, max_length=64)
    timezone: str = Field(min_length=1, max_length=64)
    environment_id: int | None = None


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    cron_expression: str | None = Field(default=None, min_length=1, max_length=64)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)
    environment_id: int | None = None

    @model_validator(mode="after")
    def _reject_explicit_null(self) -> "ScheduleUpdate":
        """未传字段用 None 表示缺省,但显式 null 是非法值。

        非空列(name/cron/timezone)收到显式 null 会在 flush 或 compute_next
        处炸成 500;提前挡成 422。environment_id 例外 —— 传 null 表示
        「清空覆盖环境」。
        """
        for field in ("name", "cron_expression", "timezone"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} 不能为 null")
        return self


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    plan_id: int
    cron_expression: str
    timezone: str
    enabled: bool
    environment_id: int | None
    next_run_at: datetime.datetime | None
    last_run_at: datetime.datetime | None
    last_run_id: uuid.UUID | None
    last_skip_at: datetime.datetime | None
    last_skip_reason: str | None
    created_at: datetime.datetime
    updated_at: datetime.datetime
