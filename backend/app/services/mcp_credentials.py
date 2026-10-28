"""凭据管理与逐次认证；不依赖令牌中缓存的角色。"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import ScopePolicy, initialize_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.permissions import MODULES
from app.core.query_access import (
    CAPABILITIES,
    MCP_GRANT_KEY,
    QueryGrant,
    authorize_query,
    current_actor_access,
)
from app.models.mcp_credential import McpCredential
from app.models.org import Staff

ACCESS_TOOL = "erp_get_my_access"
REGISTERED_TOOLS = frozenset({
    ACCESS_TOOL,
    *(
        tool
        for capability in CAPABILITIES.values()
        for tool in (
            (capability.tool,)
            if isinstance(capability.tool, str)
            else tuple(capability.tool or ())
        )
    ),
})


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _current_permissions(db: Session, staff: Staff) -> set[str]:
    return set(current_actor_access(db, staff.id)[2])


def credential_contract(row: McpCredential) -> dict:
    return {
        "id": row.id, "name": row.name,
        "permission_scopes": row.permission_scopes, "tool_scopes": row.tool_scopes,
        "expires_at": row.expires_at.isoformat() + "Z" if row.expires_at else None,
        "revoked_at": row.revoked_at.isoformat() + "Z" if row.revoked_at else None,
        "created_at": row.created_at.isoformat() + "Z",
    }


def issue_credential(
    db: Session, staff: Staff, *, name: str, permission_scopes: list[str],
    tool_scopes: list[str], expires_days: int | None = 7,
) -> dict:
    # 员工行锁串行化个人签发，避免多并发突破活跃令牌数限制。
    staff = db.scalar(select(Staff).where(Staff.id == staff.id).with_for_update())
    if staff is None:
        raise BizException(ErrorCode.UNAUTHORIZED)
    current = _current_permissions(db, staff)
    scopes, tools = set(permission_scopes), set(tool_scopes)
    valid = {f"module:{module}" for module in MODULES}
    if (
        not name.strip() or len(name) > 64
        or (expires_days is not None and not 1 <= expires_days <= 30)
        or not scopes or not scopes <= valid or ACCESS_TOOL not in tools
        or not tools <= REGISTERED_TOOLS
        or ("*" not in current and not scopes <= current)
    ):
        raise BizException(ErrorCode.FORBIDDEN, "凭据范围或参数不允许")
    now = utc_now()
    count = db.scalar(select(func.count(McpCredential.id)).where(
        McpCredential.staff_id == staff.id, McpCredential.revoked_at.is_(None),
        or_(McpCredential.expires_at.is_(None), McpCredential.expires_at > now),
    ))
    if count >= 10:
        raise BizException(ErrorCode.CONFLICT, "最多保留10个有效查询凭据")
    token = "erp_mcp_" + secrets.token_urlsafe(32)
    row = McpCredential(
        staff_id=staff.id, company_id=staff.company_id, name=name.strip(),
        token_digest=_digest(token), permission_scopes=sorted(scopes),
        tool_scopes=sorted(tools),
        expires_at=now + timedelta(days=expires_days) if expires_days is not None else None,
    )
    db.add(row)
    db.flush()
    write_audit(db, action="MCP_TOKEN_ISSUE", target_type="mcp_credential",
                target_id=str(row.id), actor_id=staff.id,
                summary="签发个人查询凭据", detail={"scopes": sorted(scopes), "tools": sorted(tools)})
    db.commit()
    return {**credential_contract(row), "token": token}


def list_credentials(db: Session, staff: Staff) -> list[dict]:
    _current_permissions(db, staff)
    rows = db.scalars(select(McpCredential).where(
        McpCredential.staff_id == staff.id, McpCredential.company_id == staff.company_id,
    ).order_by(McpCredential.id.desc()).limit(100))
    return [credential_contract(row) for row in rows]


def revoke_credential(db: Session, staff: Staff, credential_id: int) -> None:
    _current_permissions(db, staff)
    row = db.scalar(select(McpCredential).where(
        McpCredential.id == credential_id, McpCredential.staff_id == staff.id,
        McpCredential.company_id == staff.company_id,
    ).with_for_update())
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND)
    if row.revoked_at is None:
        row.revoked_at = utc_now()
        write_audit(db, action="MCP_TOKEN_REVOKE", target_type="mcp_credential",
                    target_id=str(row.id), actor_id=staff.id, summary="撤销个人查询凭据")
        db.commit()


@dataclass(frozen=True)
class McpIdentity:
    staff_id: int
    company_id: int
    permissions: tuple[str, ...]
    roles: tuple[str, ...]
    tools: tuple[str, ...]
    policy: ScopePolicy


def authenticate_credential(db: Session, token: str) -> McpIdentity:
    db.info.pop(MCP_GRANT_KEY, None)
    if not isinstance(token, str) or not token.startswith("erp_mcp_") or len(token) > 128:
        raise BizException(ErrorCode.UNAUTHORIZED, "查询凭据无效")
    row = db.scalar(select(McpCredential).where(
        McpCredential.token_digest == _digest(token),
    ).execution_options(populate_existing=True))
    if (
        row is None or row.revoked_at is not None
        or (row.expires_at is not None and row.expires_at <= utc_now())
    ):
        raise BizException(ErrorCode.UNAUTHORIZED, "查询凭据无效")
    staff = db.get(Staff, row.staff_id, populate_existing=True)
    if staff is None or staff.company_id != row.company_id:
        raise BizException(ErrorCode.UNAUTHORIZED, "查询凭据无效")
    staff, roles, current = current_actor_access(db, staff.id)
    effective = set(row.permission_scopes) if "*" in current else set(row.permission_scopes) & current
    if not effective:
        raise BizException(ErrorCode.FORBIDDEN, "查询权限已撤销")
    policy = initialize_scope_policy(db, staff)
    tools = frozenset(set(row.tool_scopes) & REGISTERED_TOOLS)
    db.info[MCP_GRANT_KEY] = QueryGrant(frozenset(effective), tools)
    authorize_query(db, "access")
    return McpIdentity(staff.id, staff.company_id, tuple(sorted(effective)),
                       tuple(sorted(roles)), tuple(sorted(tools)), policy)
