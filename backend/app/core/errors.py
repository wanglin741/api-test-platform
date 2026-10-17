"""统一错误格式 {"error": {code, message, details}}(API 设计文档 §1)。

客户端按 code 分支,不解析 message —— code 是稳定契约。
"""

from typing import Any


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(
        self,
        message: str | None = None,
        *,
        status_code: int | None = None,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code or self.__class__.status_code
        self.code = code or self.__class__.code
        self.message = message or self.code
        self.details = details or {}
        super().__init__(self.message)


class NotFoundError(AppError):
    status_code = 404
    code = "not_found"


class ConflictError(AppError):
    status_code = 409
    code = "conflict"


class ValidationFailedError(AppError):
    status_code = 422
    code = "validation_failed"


def not_found(resource: str, resource_id: object) -> NotFoundError:
    """资源不存在。code 为资源级稳定标识(如 case_not_found,API 设计 §1 示例);
    对外不区分「不存在」与「无权访问」(避免探测)。"""
    return NotFoundError(
        f"{resource} 不存在",
        code=f"{resource}_not_found",
        details={f"{resource}_id": resource_id},
    )
