from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Result(BaseModel, Generic[T]):
    """统一响应结构：code=0 表示成功，非 0 为业务错误码。"""

    code: str = "0"
    message: str = "ok"
    data: T | None = None

    @classmethod
    def ok(cls, data: T | None = None, message: str = "ok") -> "Result[T]":
        return cls(code="0", message=message, data=data)

    @classmethod
    def fail(cls, code: str, message: str, data: T | None = None) -> "Result[T]":
        return cls(code=code, message=message, data=data)
