import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import BizException, ErrorCode
from app.core.response import Result

logger = logging.getLogger(__name__)

_STATUS_TO_CODE = {
    400: ErrorCode.BAD_REQUEST,
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    409: ErrorCode.CONFLICT,
    422: ErrorCode.VALIDATION_ERROR,
}


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(BizException)
    async def biz_exception_handler(request: Request, exc: BizException) -> JSONResponse:
        # 仅公开版本冲突的整数刷新提示；其他内部 detail 继续不进入错误响应。
        version_hint = None
        if (exc.code == ErrorCode.CONFLICT and isinstance(exc.detail, dict)
                and set(exc.detail) == {"version"} and type(exc.detail["version"]) is int):
            version_hint = exc.detail
        return JSONResponse(
            status_code=exc.http_status,
            content=Result.fail(exc.code.value, exc.message, data=version_hint).model_dump(),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = [
            {"loc": list(e.get("loc", [])), "msg": e.get("msg"), "type": e.get("type")}
            for e in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=Result.fail(
                ErrorCode.VALIDATION_ERROR.value,
                ErrorCode.VALIDATION_ERROR.default_message,
                data=errors,
            ).model_dump(),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        message = exc.detail if isinstance(exc.detail, str) else code.default_message
        return JSONResponse(
            status_code=exc.status_code,
            content=Result.fail(code.value, message).model_dump(),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("未处理的异常")
        return JSONResponse(
            status_code=500,
            content=Result.fail(
                ErrorCode.INTERNAL_ERROR.value,
                ErrorCode.INTERNAL_ERROR.default_message,
            ).model_dump(),
        )
