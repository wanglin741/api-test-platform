"""用例 DSL 的 Pydantic 定义与解析。

可执行用例是一份 JSON 文档,`dsl_version` 必填且当前只接受 1 ——
它是引擎的演进契约,未来 DSL 结构变化时按版本分派解析逻辑。

MVP 范围(spec #1):步骤类型仅 `http`;`extract` / `assert` / `retry`
字段在此接受并建模,分别由 T3(提取与断言)、T4(重试)接线。
未知字段忽略(`auth` 等暂不实现的结构可以出现在文档里而不炸校验)。

本模块不 import 任何框架。
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]
BodyType = Literal["json", "raw", "form-data", "urlencoded"]


class _EngineModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class RequestBody(_EngineModel):
    type: BodyType
    content: Any = None


class RequestSpec(_EngineModel):
    method: HttpMethod
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    params: dict[str, str] = Field(default_factory=dict)
    body: RequestBody | None = None
    timeout_ms: int | None = None  # 不填则继承用例 config.timeout_ms
    follow_redirects: bool = True
    verify_ssl: bool = True


class ExtractSpec(_EngineModel):
    """提取器。提取结果写入运行时变量 `name`。"""

    name: str
    type: Literal["jsonpath", "header", "status_code"]
    # jsonpath → JSONPath 表达式;header → 响应头名(大小写不敏感);status_code 不需要
    expression: str | None = None
    required: bool = True  # 提取失败:false → 变量不注入;true → 步骤 failed
    scope: Literal["case"] = "case"


class AssertSpec(_EngineModel):
    """断言。单步内所有断言全部执行,不短路。"""

    type: Literal["status_code", "jsonpath", "response_time_ms"]
    operator: Literal[
        "eq", "ne", "gt", "gte", "lt", "lte", "contains", "regex_match"
    ]
    expected: Any = None
    expression: str | None = None  # jsonpath 断言的取值表达式
    message: str | None = None  # 失败时展示给用户的自定义信息


class RetrySpec(_EngineModel):
    """步骤级重试。仅对网络类 error 生效,断言 failed 不重试(T4)。"""

    times: int = Field(default=0, ge=0)
    interval_ms: int = Field(default=1000, ge=0)
    on: tuple[Literal["error"], ...] = ("error",)


class StepSpec(_EngineModel):
    id: str
    name: str
    type: Literal["http"]
    enabled: bool = True
    request: RequestSpec
    extract: list[ExtractSpec] = Field(default_factory=list)
    assertions: list[AssertSpec] = Field(default_factory=list, alias="assert")
    retry: RetrySpec = Field(default_factory=RetrySpec)


class ConfigSpec(_EngineModel):
    variables: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 10_000  # 单步默认超时,可被步骤覆盖
    fail_fast: bool = False
    max_duration_ms: int | None = None  # 用例总时长约束(T4 接线)


class CaseDSL(_EngineModel):
    dsl_version: Literal[1]
    config: ConfigSpec = Field(default_factory=ConfigSpec)
    steps: list[StepSpec] = Field(min_length=1)


def parse_dsl(data: dict[str, Any]) -> CaseDSL:
    """从 JSON dict 加载 DSL。结构非法抛 pydantic.ValidationError(带字段定位)。"""
    return CaseDSL.model_validate(data)
