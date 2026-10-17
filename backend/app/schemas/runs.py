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


class RequestSnapshotOut(BaseModel):
    method: str
    url: str
    headers: dict[str, str]
    body: str | None = None


class ResponseSnapshotOut(BaseModel):
    status_code: int | None = None
    headers: dict[str, str] | None = None
    # BYTEA 解码为文本回传;截断约定通过 truncated 标识
    body: str | None = None
    size: int | None = None
    truncated: bool = False


class StepOut(BaseModel):
    id: int
    step_index: int
    step_name: str
    step_type: str
    status: str
    duration_ms: int
    request: RequestSnapshotOut | None = None
    response: ResponseSnapshotOut | None = None
    extracted_variables: dict[str, Any]
    assertions: list[dict[str, Any]]
    assertion_total: int
    assertion_passed: int
    error_message: str | None = None


class StepList(BaseModel):
    """run_step_result 不分页;超过上限截断并标记。"""

    items: list[StepOut]
    count: int
    truncated: bool
