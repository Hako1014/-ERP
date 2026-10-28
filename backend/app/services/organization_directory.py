"""按当前认证公司提供最小字段的组织只读目录。"""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import BizException, ErrorCode
from app.models.org import Department, Position, Staff


def list_departments(db: Session, company_id: int) -> list[dict]:
    rows = db.scalars(
        select(Department)
        .where(Department.company_id == company_id)
        .order_by(Department.sort_order, Department.id)
    ).all()
    department_ids = {row.id for row in rows}
    parent_by_id = {row.id: row.parent_id for row in rows}

    def has_company_cycle(department_id: int) -> bool:
        visited = set()
        cursor = department_id
        while cursor is not None:
            if cursor in visited:
                return True
            visited.add(cursor)
            cursor = parent_by_id.get(cursor)
        return False

    return [
        {
            "id": row.id,
            "name": row.name,
            "parent_id": row.parent_id if row.parent_id in department_ids and not has_company_cycle(row.id) else None,
            "sort_order": row.sort_order,
        }
        for row in rows
    ]


def _staff_query(db: Session, company_id: int, department_id: int | None, keyword: str | None):
    if department_id is not None and db.scalar(
        select(Department.id).where(
            Department.id == department_id,
            Department.company_id == company_id,
        )
    ) is None:
        raise BizException(ErrorCode.NOT_FOUND, "部门不存在")

    stmt = (
        select(Staff, Department.name, Position.name)
        .outerjoin(
            Department,
            (Department.id == Staff.department_id) & (Department.company_id == company_id),
        )
        .outerjoin(
            Position,
            (Position.id == Staff.position_id) & (Position.company_id == company_id),
        )
        .where(Staff.company_id == company_id, Staff.is_active.is_(True))
    )
    if department_id is not None:
        stmt = stmt.where(Staff.department_id == department_id)
    term = (keyword or "").strip()
    if term:
        escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        stmt = stmt.where(or_(Staff.name.ilike(pattern, escape="\\"), Department.name.ilike(pattern, escape="\\")))
    return stmt


def _person_contract(staff: Staff, department_name: str | None, position_name: str | None) -> dict:
    return {
        "staff_id": staff.id,
        "name": staff.name,
        "department_id": staff.department_id if department_name else None,
        "department_name": department_name,
        "position_id": staff.position_id if position_name else None,
        "position_name": position_name,
        "avatar_available": bool(staff.avatar_storage_key and staff.avatar_sha256),
    }


def list_people(
    db: Session,
    *,
    company_id: int,
    department_id: int | None,
    keyword: str | None,
    page: int,
    page_size: int,
) -> dict:
    stmt = _staff_query(db, company_id, department_id, keyword)
    total = db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = db.execute(
        stmt.order_by(Staff.name, Staff.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [_person_contract(staff, department_name, position_name) for staff, department_name, position_name in rows],
    }


def get_person(db: Session, *, company_id: int, staff_id: int) -> dict:
    row = db.execute(
        select(Staff, Department.name, Position.name)
        .outerjoin(
            Department,
            (Department.id == Staff.department_id) & (Department.company_id == company_id),
        )
        .outerjoin(
            Position,
            (Position.id == Staff.position_id) & (Position.company_id == company_id),
        )
        .where(Staff.id == staff_id, Staff.company_id == company_id, Staff.is_active.is_(True))
    ).first()
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "员工不存在")
    return _person_contract(*row)


def get_person_avatar(db: Session, *, company_id: int, staff_id: int) -> Staff:
    staff = db.scalar(
        select(Staff).where(
            Staff.id == staff_id,
            Staff.company_id == company_id,
            Staff.is_active.is_(True),
        )
    )
    if staff is None or not staff.avatar_storage_key or staff.avatar_media_type not in {
        "image/jpeg", "image/png", "image/webp",
    }:
        raise BizException(ErrorCode.NOT_FOUND, "员工头像不存在")
    return staff
