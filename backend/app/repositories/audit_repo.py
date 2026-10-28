from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.audit_log import AuditLog


def list_by_target(
    db: Session, target_type: str, target_id: str, limit: int = 100
) -> list[AuditLog]:
    """按目标单据查询留痕时间线（按时间倒序）。"""
    stmt = (
        select(AuditLog)
        .where(AuditLog.target_type == target_type, AuditLog.target_id == target_id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))
