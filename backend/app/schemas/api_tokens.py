"""API Token 的请求与响应模型(issue #38)。

明文 `token` 字段只在创建响应(ApiTokenCreated)出现,列表用 ApiTokenOut
(不含哈希与明文)。scopes 用 Literal 钉死合法值:未知 scope 422。
"""

import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Scope = Literal["run:trigger", "report:read"]


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    scopes: list[Scope] = Field(default=["run:trigger", "report:read"])
    expires_at: datetime.datetime | None = None


class ApiTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    scopes: list[str]
    expires_at: datetime.datetime | None
    last_used_at: datetime.datetime | None
    created_at: datetime.datetime


class ApiTokenCreated(ApiTokenOut):
    # 一次性明文:仅创建响应携带,任何列表/详情都不再返回
    token: str
