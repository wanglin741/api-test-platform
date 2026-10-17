"""用例与版本快照的请求/响应模型。dsl 为引擎 DSL 的 JSON 文档,结构由 parse_dsl 校验。"""

import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    tags: list[str] = Field(default_factory=list)
    dsl: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=255)


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    tags: list[str] | None = None
    dsl: dict[str, Any] | None = None
    change_note: str | None = Field(default=None, max_length=255)


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    priority: str | None
    tags: list[Any]
    current_version: int
    created_by: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class CaseDetail(CaseOut):
    dsl: dict[str, Any]
    change_note: str | None = None


class CasePage(BaseModel):
    items: list[CaseOut]
    total: int
    page: int
    page_size: int
