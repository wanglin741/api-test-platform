"""调度(schedules)的请求与响应模型(issue #36,数据模型 §10)。

next_run_at/last_run_* 由服务端维护,请求体不接受。
"""

import datetime
import uuid

from pydantic import BaseModel, ConfigDict, Field


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
