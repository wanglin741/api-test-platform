"""目录(folders)的请求与响应模型(issue #20)。scope 当前只有 case。"""

import datetime

from pydantic import BaseModel, ConfigDict, Field

SCOPE_CASE = "case"


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    parent_id: int | None = None
    scope: str = SCOPE_CASE
    sort_order: int = 0


class FolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)


class FolderMove(BaseModel):
    """移动:目标父节点(NULL = 顶层)+ 同级排序。环/深度/跨项目由端点校验。"""

    parent_id: int | None = None
    sort_order: int = 0


class FolderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    scope: str
    parent_id: int | None
    name: str
    sort_order: int
    created_at: datetime.datetime
    updated_at: datetime.datetime
