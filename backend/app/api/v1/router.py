"""v1 路由汇总。"""

from fastapi import APIRouter

from app.api.v1 import environments, projects, variables

api_router = APIRouter()
api_router.include_router(projects.router)
api_router.include_router(environments.project_router)
api_router.include_router(environments.router)
api_router.include_router(variables.project_router)
api_router.include_router(variables.router)
