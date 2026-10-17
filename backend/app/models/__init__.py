"""ORM 模型汇总,方便 Alembic 的 target_metadata 与全量 import。"""

from app.models.base import Base
from app.models.environment import Environment
from app.models.project import Project
from app.models.run import Run, RunStepResult
from app.models.test_case import TestCase, TestCaseVersion
from app.models.user import User
from app.models.variable import Variable

__all__ = [
    "Base",
    "Environment",
    "Project",
    "Run",
    "RunStepResult",
    "TestCase",
    "TestCaseVersion",
    "User",
    "Variable",
]
