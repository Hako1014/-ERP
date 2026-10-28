"""按公司校验登录和已认证请求的来源 IP。"""

from ipaddress import IPv6Address, ip_address, ip_network
from typing import Any

import jwt
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.core.errors import BizException, ErrorCode
from app.core.response import Result
from app.core.security import decode_token
from app.db.session import SessionLocal
from app.models.org import Staff
from app.models.rbac import IpRule


def is_ip_allowed(db: Session, company_id: int, source_ip: str | None) -> bool:
    rules = db.scalars(select(IpRule).where(IpRule.company_id == company_id)).all()
    if not rules:
        return True
    try:
        address = ip_address(source_ip or "")
        if isinstance(address, IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
    except ValueError:
        return False

    matches = []
    for rule in rules:
        try:
            network = ip_network(rule.ip_address, strict=False)
            if address in network:
                matches.append(rule.rule_type)
        except ValueError:
            continue
    if "DENY" in matches:
        return False
    if any(rule.rule_type == "ALLOW" for rule in rules):
        return "ALLOW" in matches
    return True


def require_allowed_ip(db: Session, company_id: int, source_ip: str | None) -> None:
    if not is_ip_allowed(db, company_id, source_ip):
        raise BizException(ErrorCode.FORBIDDEN, "当前 IP 不允许访问")


class IpAccessMiddleware:
    """对带有效员工令牌的请求执行 IP 规则；登录请求在认证后单独校验。"""

    def __init__(self, app, session_factory=SessionLocal):
        self.app = app
        self.session_factory = session_factory

    def _blocked(self, staff_id: int, source_ip: str | None) -> bool:
        with self.session_factory() as db:
            staff = db.get(Staff, staff_id)
            return staff is not None and not is_ip_allowed(db, staff.company_id, source_ip)

    async def __call__(self, scope: dict[str, Any], receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        if authorization.lower().startswith("bearer "):
            try:
                payload = decode_token(authorization[7:].strip())
                staff_id = payload.get("staff_id")
            except jwt.PyJWTError:
                staff_id = None
            if staff_id:
                source_ip = scope.get("client", (None, None))[0]
                if await run_in_threadpool(self._blocked, staff_id, source_ip):
                    response = JSONResponse(
                        status_code=403,
                        content=Result.fail(
                            ErrorCode.FORBIDDEN.value,
                            "当前 IP 不允许访问",
                        ).model_dump(),
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
