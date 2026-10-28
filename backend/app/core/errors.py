from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """业务错误码，统一为 5 位字符串。"""

    BAD_REQUEST = "40000"
    UNAUTHORIZED = "40100"
    FORBIDDEN = "40300"
    NOT_FOUND = "40400"
    CONFLICT = "40900"
    AI_SESSION_EXPIRED = "40901"
    AI_SESSION_INVALID = "40902"
    AI_SESSION_VERSION_CONFLICT = "40903"
    VALIDATION_ERROR = "42200"
    AI_RATE_LIMITED = "42901"
    AI_INSUFFICIENT_BALANCE = "40201"
    AI_UNCONFIGURED = "50301"
    AI_AUTH_FAILED = "50201"
    AI_PROVIDER_ERROR = "50202"
    AI_TIMEOUT = "50400"
    INTERNAL_ERROR = "50000"

    @property
    def default_message(self) -> str:
        return _DEFAULT_MESSAGES[self]

    @property
    def http_status(self) -> int:
        return _HTTP_STATUS[self]


class BizException(Exception):
    """业务异常，由全局异常处理器统一转换为 Result 响应。"""

    def __init__(self, code: ErrorCode, message: str | None = None, detail: Any = None):
        self.code = code
        self.message = message or code.default_message
        self.detail = detail
        super().__init__(self.message)

    @property
    def http_status(self) -> int:
        return self.code.http_status


_DEFAULT_MESSAGES = {
    ErrorCode.BAD_REQUEST: "请求参数错误",
    ErrorCode.UNAUTHORIZED: "未登录或登录已过期",
    ErrorCode.FORBIDDEN: "无权限访问",
    ErrorCode.NOT_FOUND: "资源不存在",
    ErrorCode.CONFLICT: "数据冲突",
    ErrorCode.AI_SESSION_EXPIRED: "AI 会话已过期",
    ErrorCode.AI_SESSION_INVALID: "AI 会话无效",
    ErrorCode.AI_SESSION_VERSION_CONFLICT: "AI 会话已更新",
    ErrorCode.VALIDATION_ERROR: "参数校验失败",
    ErrorCode.AI_RATE_LIMITED: "AI 服务请求过于频繁，请稍后重试",
    ErrorCode.AI_INSUFFICIENT_BALANCE: "AI 服务额度不足，请联系管理员",
    ErrorCode.AI_UNCONFIGURED: "AI 服务未配置，请联系管理员",
    ErrorCode.AI_AUTH_FAILED: "AI 服务认证失败，请联系管理员检查配置",
    ErrorCode.AI_PROVIDER_ERROR: "AI 服务暂时异常，请稍后重试",
    ErrorCode.AI_TIMEOUT: "AI 服务响应超时，请稍后重试",
    ErrorCode.INTERNAL_ERROR: "系统内部错误",
}

_HTTP_STATUS = {
    ErrorCode.BAD_REQUEST: 400,
    ErrorCode.UNAUTHORIZED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.CONFLICT: 409,
    ErrorCode.AI_SESSION_EXPIRED: 409,
    ErrorCode.AI_SESSION_INVALID: 409,
    ErrorCode.AI_SESSION_VERSION_CONFLICT: 409,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.AI_RATE_LIMITED: 429,
    ErrorCode.AI_INSUFFICIENT_BALANCE: 402,
    ErrorCode.AI_UNCONFIGURED: 503,
    ErrorCode.AI_AUTH_FAILED: 502,
    ErrorCode.AI_PROVIDER_ERROR: 502,
    ErrorCode.AI_TIMEOUT: 504,
    ErrorCode.INTERNAL_ERROR: 500,
}
