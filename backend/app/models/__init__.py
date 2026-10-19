"""ORM 模型汇总,方便 Alembic 的 target_metadata 与全量 import。"""

from app.models.base import Base
from app.models.environment import Environment
from app.models.folder import Folder
from app.models.project import Project
from app.models.run import RUN_TERMINAL_STATUSES, Run, RunStepResult
from app.models.test_case import TestCase, TestCaseVersion
from app.models.test_plan import TestPlan, TestPlanItem
from app.models.user import User
from app.models.variable import Variable

__all__ = [
    "Base",
    "Environment",
    "Folder",
    "Project",
    "RUN_TERMINAL_STATUSES",
    "Run",
    "RunStepResult",
    "TestCase",
    "TestCaseVersion",
    "TestPlan",
    "TestPlanItem",
    "User",
    "Variable",
]
