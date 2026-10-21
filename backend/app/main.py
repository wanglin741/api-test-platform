"""FastAPI 应用入口。"""

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import AppError
from app.core.log import setup_logging
from app.db.session import make_async_engine, make_session_factory

setup_logging(get_settings().log_level)

logger = logging.getLogger(__name__)


def _error_body(code: str, message: str, details: dict | list | None = None) -> dict:
    return {"error": {"code": code, "message": message, "details": details}}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine: AsyncEngine = make_async_engine(settings.database_url)
    app.state.engine = engine
    app.state.session_factory = make_session_factory(engine)
    yield
    await engine.dispose()


app = FastAPI(title="API 测试平台", version="0.1.0", lifespan=lifespan)
app.include_router(api_router, prefix="/api/v1")


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(_error_body(exc.code, exc.message, exc.details)),
        headers=exc.headers or None,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=_error_body("validation_error", "请求参数校验失败", {"errors": jsonable_encoder(exc.errors())}),
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    code = "not_found" if exc.status_code == 404 else "http_error"
    return JSONResponse(
        status_code=exc.status_code,
        content=_error_body(code, str(exc.detail)),
    )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception) -> JSONResponse:
    # Starlette 错误中间件随后会重抛并由 uvicorn 记完整 traceback,这里只记一行摘要
    logger.error("%s %s -> 500: %r", request.method, request.url.path, exc)
    return JSONResponse(
        status_code=500,
        content=_error_body("internal_error", "服务器内部错误"),
    )


@app.get("/healthz", tags=["meta"])
async def healthz() -> dict:
    return {"status": "ok"}
