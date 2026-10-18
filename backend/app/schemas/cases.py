"""用例与版本快照的请求/响应模型。dsl 为引擎 DSL 的 JSON 文档,结构由 parse_dsl 校验。"""

import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.runs import RequestSnapshotOut, ResponseSnapshotOut


class CaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    tags: list[str] = Field(default_factory=list)
    # NULL = 未分类(issue #20)
    folder_id: int | None = None
    dsl: dict[str, Any]
    change_note: str | None = Field(default=None, max_length=255)


class CaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    priority: Literal["P0", "P1", "P2", "P3"] | None = None
    tags: list[str] | None = None
    # 显式传 null 表示移出目录(移回未分类);exclude_unset 区分「没传」与「传了 null」
    folder_id: int | None = None
    dsl: dict[str, Any] | None = None
    change_note: str | None = Field(default=None, max_length=255)


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    folder_id: int | None
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


class DebugAssertionOut(BaseModel):
    """单条断言的执行结果(结构与引擎 AssertionResult 对齐)。"""

    type: str
    operator: str
    expected: Any
    actual: Any
    passed: bool
    message: str | None = None


class StepDebugOut(BaseModel):
    """单步调试结果:同步执行一步,返回变量替换后的请求与真实响应快照。

    快照模型与报告端点共用(RequestSnapshotOut/ResponseSnapshotOut),前端渲染组件得以复用。
    """

    step_id: str
    step_name: str
    status: str
    duration_ms: float | None = None
    attempts: int = 1
    error_message: str | None = None
    request: RequestSnapshotOut | None = None
    response: ResponseSnapshotOut | None = None
    extracted: dict[str, Any] = Field(default_factory=dict)
    assertions: list[DebugAssertionOut] = Field(default_factory=list)
