"""执行引擎公共入口。

纯逻辑库,不依赖 FastAPI / Celery / ORM(设计文档《执行引擎》§1)。
外部只应 import 这里的 `execute` 与结果/上下文结构。
"""

from app.engine.context import EngineContext
from app.engine.plan_runner import PlanCase, PlanSummary, execute_plan
from app.engine.results import AssertionResult, RequestSnapshot, ResponseSnapshot, RunSummary, StepResult
from app.engine.runner import execute

__all__ = [
    "AssertionResult",
    "EngineContext",
    "PlanCase",
    "PlanSummary",
    "RequestSnapshot",
    "ResponseSnapshot",
    "RunSummary",
    "StepResult",
    "execute",
    "execute_plan",
]
