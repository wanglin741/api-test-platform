"""v1 路由汇总。"""

from fastapi import APIRouter

from app.api.v1 import cases, environments, projects, runs, variables

api_router = APIRouter()
api_router.include_router(projects.router)
api_router.include_router(cases.project_router)
api_router.include_router(cases.router)
api_router.include_router(runs.project_router)
api_router.include_router(runs.router)
api_router.include_router(environments.project_router)
api_router.include_router(environments.router)
api_router.include_router(variables.project_router)
api_router.include_router(variables.router)
