"""项目 / 环境 / 变量的请求与响应模型。"""

import datetime
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# 变量名即 {{name}} 占位符名,限制为合法标识符,避免解析歧义
VAR_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    settings: dict[str, Any] | None = None


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    settings: dict[str, Any]
    owner_id: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class EnvironmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str | None = None
    base_url: str | None = Field(default=None, max_length=512)
    default_headers: dict[str, str] = Field(default_factory=dict)
    auth: dict[str, Any] | None = None
    is_default: bool = False
    sort_order: int = 0


class EnvironmentUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    base_url: str | None = Field(default=None, max_length=512)
    default_headers: dict[str, str] | None = None
    auth: dict[str, Any] | None = None
    is_default: bool | None = None
    sort_order: int | None = None


class EnvironmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    name: str
    description: str | None
    base_url: str | None
    default_headers: dict[str, str]
    auth: dict[str, Any] | None
    is_default: bool
    sort_order: int
    created_at: datetime.datetime
    updated_at: datetime.datetime


class VariableIn(BaseModel):
    name: str = Field(min_length=1, max_length=128, pattern=VAR_NAME_PATTERN.pattern)
    value: str
    is_secret: bool = False
    description: str | None = Field(default=None, max_length=255)


class VariableReplace(BaseModel):
    """PUT 整体替换的请求体。"""

    items: list[VariableIn]


class VariableOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    value: str
    is_secret: bool
    description: str | None

    @classmethod
    def from_model(cls, var: Any) -> "VariableOut":
        """is_secret 的变量值不回传明文,统一打码为 ***。"""
        return cls(
            id=var.id,
            name=var.name,
            value="***" if var.is_secret else var.value,
            is_secret=var.is_secret,
            description=var.description,
        )
