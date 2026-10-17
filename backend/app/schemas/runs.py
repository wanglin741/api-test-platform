"""执行触发的请求/响应模型。"""

import datetime
import uuid
from typing import Any

from pydantic import BaseModel, Field


class RunCreate(BaseModel):
    case_id: int
    environment_id: int


class RunTriggered(BaseModel):
    run_id: uuid.UUID
    status: str


class RunOut(BaseModel):
    run_id: uuid.UUID
    project_id: int
    case_id: int
    case_version: int
    environment_id: int
    trigger_mode: str
    status: str
    duration_ms: int | None = None
    summary: dict[str, Any] | None = None
    created_at: datetime.datetime
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class CancelAck(BaseModel):
    run_id: uuid.UUID
    status: str
