from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.rbac import Permission, Role, RolePermission, StaffRole


def list_roles(db: Session, company_id: int) -> list[Role]:
    return list(db.scalars(select(Role).where(Role.company_id == company_id)))


def list_permissions(db: Session) -> list[Permission]:
    return list(db.scalars(select(Permission)))


def list_role_permission_codes(db: Session, role_id: int) -> list[str]:
    stmt = (
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .where(RolePermission.role_id == role_id)
    )
    return list(db.scalars(stmt))


def list_staff_role_codes(db: Session, staff_id: int) -> list[str]:
    stmt = (
        select(Role.code)
        .join(StaffRole, StaffRole.role_id == Role.id)
        .where(StaffRole.staff_id == staff_id)
    )
    return list(db.scalars(stmt))
