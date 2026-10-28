"""MCP 查询执行边界：独立只读会话、配额、超时、脱敏错误与强制审计。"""

import json
import logging
import threading
from typing import Literal
from contextlib import contextmanager
from time import monotonic
from uuid import uuid4

import anyio
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from redis.exceptions import RedisError
from sqlalchemy import event, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.audit import write_audit
from app.core.data_scope import clear_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.permissions import visible_modules
from app.services.mcp_credentials import ACCESS_TOOL, REGISTERED_TOOLS, authenticate_credential, utc_now
from app.services.mcp_limits import QueryLimitExceeded
from app.services.mcp_tool_registry import ToolEnvelope, get_tool, visible_tool_names

logger = logging.getLogger(__name__)


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class QueryPage(BaseModel):
    """后续工具复用的分页限制；身份工具不接受分页参数。"""
    model_config = ConfigDict(extra="forbid", strict=True)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)


class AccessData(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    staff_id: int
    company_id: int
    permissions: list[str]
    modules: list[str]
    data_scope: Literal["SELF", "DEPARTMENT", "COMPANY"]
    tools: list[str]


class ScopeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    type: Literal["SELF", "DEPARTMENT", "COMPANY"]


class AccessResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["1.0"]
    request_id: str
    queried_at: str
    business_timezone: Literal["Asia/Shanghai"]
    effective_filters: EmptyArguments
    scope_summary: ScopeSummary
    data: AccessData
    warnings: list[str]


class RuntimeFailure(Exception):
    def __init__(self, code: str):
        self.code = code


_MESSAGES = {
    "UNAUTHENTICATED": "查询凭据无效或账号不可用",
    "FORBIDDEN": "当前权限不允许查询",
    "INVALID_ARGUMENT": "查询参数不符合工具定义",
    "UNKNOWN_TOOL": "未注册的查询工具",
    "RATE_LIMITED": "查询频率或并发达到上限",
    "TIMEOUT": "查询超时，请稍后重试",
    "CANCELLED": "查询已取消",
    "RESULT_TOO_LARGE": "结果超过大小限制",
    "DEPENDENCY_UNAVAILABLE": "查询依赖暂不可用",
    "INTERNAL_ERROR": "查询暂未完成，请使用请求编号排查",
}


def error_result(code: str, request_id: str) -> CallToolResult:
    data = {"code": code, "message": _MESSAGES[code], "request_id": request_id}
    return CallToolResult(isError=True, structuredContent=data,
                          content=[TextContent(type="text", text=json.dumps(data, ensure_ascii=False))])


def _reject_flush(session, context, instances):
    raise RuntimeFailure("INTERNAL_ERROR")


class McpRuntime:
    def __init__(self, *, read_factory, audit_factory, token: str, limits,
                 timeout_seconds: float = 10, max_result_bytes: int = 262144):
        self.read_factory = read_factory
        self.audit_factory = audit_factory
        self._token = token
        self.limits = limits
        self.timeout_seconds = timeout_seconds
        self.max_result_bytes = max_result_bytes
        self._threads = anyio.CapacityLimiter(4)

    @contextmanager
    def _read_session(self):
        with self.read_factory() as db:
            event.listen(db, "before_flush", _reject_flush)
            try:
                # MySQL事务级只读，禁止ORM及原始SQL持久化写入。
                if db.get_bind().dialect.name == "mysql":
                    db.execute(text("SET TRANSACTION READ ONLY"))
                yield db
            finally:
                clear_scope_policy(db)
                db.rollback()

    def _audit(self, *, request_id: str, name: str, actor_id: int | None,
               company_id: int | None, code: str, started: float, rows: int = 0):
        with self.audit_factory() as db:
            write_audit(
                db, action="MCP_READ" if code == "OK" else "MCP_READ_DENIED",
                target_type="mcp_tool", target_id=name if name in REGISTERED_TOOLS else "unregistered",
                actor_id=actor_id, summary="MCP只读查询",
                detail={"request_id": request_id, "company_id": company_id,
                        "tool": name if name in REGISTERED_TOOLS else "unregistered",
                        "code": code, "elapsed_ms": int((monotonic() - started) * 1000),
                        "rows": rows if code == "OK" else 0},
            )
            db.commit()

    def _identity_data(self, identity):
        return {
            "staff_id": identity.staff_id, "company_id": identity.company_id,
            "permissions": list(identity.permissions),
            "modules": visible_modules(set(identity.permissions)),
            "data_scope": identity.policy.scope_type, "tools": visible_tool_names(identity),
        }

    def _execute(self, name, arguments, discovery, request_id, cancelled):
        started = monotonic()
        actor_id = company_id = None
        rows = 0
        lease = None
        code, result = "INTERNAL_ERROR", None
        try:
            if not self._token.startswith("erp_mcp_") or len(self._token) > 128:
                raise BizException(ErrorCode.UNAUTHORIZED)
            with self._read_session() as db:
                identity = authenticate_credential(db, self._token)
                actor_id, company_id = identity.staff_id, identity.company_id
                lease = self.limits.acquire(company_id, actor_id)
                if cancelled.is_set():
                    raise RuntimeFailure("CANCELLED")
                if discovery:
                    if name != ACCESS_TOOL or ACCESS_TOOL not in identity.tools:
                        raise RuntimeFailure("FORBIDDEN")
                    EmptyArguments.model_validate(arguments)
                    result = visible_tool_names(identity)
                elif name == ACCESS_TOOL:
                    if ACCESS_TOOL not in identity.tools:
                        raise RuntimeFailure("FORBIDDEN")
                    EmptyArguments.model_validate(arguments)
                    result = {
                        "schema_version": "1.0", "request_id": request_id,
                        "queried_at": utc_now().isoformat() + "Z",
                        "business_timezone": "Asia/Shanghai",
                        "effective_filters": {},
                        "scope_summary": {"type": identity.policy.scope_type},
                        "data": self._identity_data(identity), "warnings": [],
                    }
                    result = AccessResult.model_validate(result).model_dump(mode="json")
                    rows = 1
                else:
                    spec = get_tool(name)
                    if spec is None:
                        raise RuntimeFailure("UNKNOWN_TOOL")
                    if name not in visible_tool_names(identity):
                        raise RuntimeFailure("FORBIDDEN")
                    parsed = spec.arguments.model_validate(arguments)
                    output = spec.handler(db, parsed, identity)
                    result = ToolEnvelope.model_validate({
                        "schema_version": "1.0", "request_id": request_id,
                        "queried_at": utc_now().isoformat() + "Z",
                        "business_timezone": "Asia/Shanghai",
                        "effective_filters": output.effective_filters,
                        "scope_summary": {"type": identity.policy.scope_type},
                        "page": output.page, "data": output.data,
                        "warnings": list(output.warnings),
                    }).model_dump(mode="json")
                    rows = output.rows
                if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > self.max_result_bytes:
                    raise RuntimeFailure("RESULT_TOO_LARGE")
            if cancelled.is_set():
                raise RuntimeFailure("CANCELLED")
            if monotonic() - started >= self.timeout_seconds:
                raise RuntimeFailure("TIMEOUT")
            code = "OK"
        except BizException as exc:
            if exc.code == ErrorCode.UNAUTHORIZED:
                code = "UNAUTHENTICATED"
            elif exc.code == ErrorCode.BAD_REQUEST:
                code = "INVALID_ARGUMENT"
            else:
                # 缺失实体与越权统一响应，避免按ID枚举不可见业务数据。
                code = "FORBIDDEN"
        except ValidationError:
            code = "INVALID_ARGUMENT"
        except QueryLimitExceeded:
            code = "RATE_LIMITED"
        except RuntimeFailure as exc:
            code = exc.code
        except (SQLAlchemyError, RedisError, OSError):
            code = "DEPENDENCY_UNAVAILABLE"
        except Exception as exc:
            # 禁止记录异常原文：数据库参数与依赖异常可能携带秘密。
            logger.error("MCP查询内部失败 type=%s attribute=%s",
                         type(exc).__name__, getattr(exc, "name", None))
            code = "INTERNAL_ERROR"
        finally:
            if lease is not None:
                try:
                    self.limits.release(lease)
                except Exception:
                    code = "DEPENDENCY_UNAVAILABLE"
            try:
                self._audit(request_id=request_id, name=name, actor_id=actor_id,
                            company_id=company_id, code=code, started=started, rows=rows)
            except Exception:
                # 审计失败时不返回查询结果，stderr仅含安全编号。
                logger.error("MCP审计不可用 request_id=%s", request_id)
                code = "DEPENDENCY_UNAVAILABLE"
        if code != "OK":
            return error_result(code, request_id)
        return result

    async def execute(self, name: str, arguments: dict, *, discovery: bool = False):
        request_id = str(uuid4())
        cancelled = threading.Event()
        try:
            with anyio.fail_after(self.timeout_seconds):
                result = await anyio.to_thread.run_sync(
                    lambda: self._execute(name, arguments, discovery, request_id, cancelled),
                    abandon_on_cancel=True, limiter=self._threads,
                )
        except TimeoutError:
            cancelled.set()
            return error_result("TIMEOUT", request_id)
        except Exception:
            cancelled.set()
            return error_result("INTERNAL_ERROR", request_id)
        except BaseException:
            cancelled.set()
            raise
        if discovery or isinstance(result, CallToolResult):
            return result
        return CallToolResult(
            structuredContent=result,
            content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False))],
        )
