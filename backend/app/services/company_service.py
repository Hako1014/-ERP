"""公司设置服务（TASK-022）。

覆盖公司信息、部门、职务、员工登录账户、密码重置和 IP 访问规则。
IP 的实际登录拦截在 TASK-061 落地，本模块只负责规则维护。
"""

from ipaddress import ip_address, ip_network
from typing import Any

from sqlalchemy import delete, func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.security import hash_password, validate_new_password
from app.db.base import Base
from app.models.org import Company, Department, Position, Staff
from app.models.rbac import DataScope, IpRule, StaffRole

COMPANY_FIELDS = {"full_name", "short_name", "legal_person", "phone", "tax_no"}
DEPARTMENT_FIELDS = {"name", "parent_id", "sort_order"}
POSITION_FIELDS = {"name"}
STAFF_BASE_FIELDS = {
    "name",
    "staff_no",
    "login_account",
    "phone",
    "department_id",
    "position_id",
    "gender",
    "region",
    "remark",
    "is_distribution",
    "is_active",
}
STAFF_CREATE_FIELDS = STAFF_BASE_FIELDS | {"password"}
IP_RULE_FIELDS = {"rule_type", "ip_address", "remark"}
IP_RULE_TYPES = {"ALLOW", "DENY"}

_STAFF_CHILD_TABLES = {"staff_role", "data_scope"}


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _require_text(value: Any, label: str, max_length: int) -> str:
    if value is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    if len(text) > max_length:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}")
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _require_text(value, label, max_length)


def _to_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc


def _optional_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int(value, label)


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}"
        )


# --------------------------------------------------------------------------- #
# 公司信息
# --------------------------------------------------------------------------- #


def get_company(db: Session, company_id: int) -> Company | None:
    return db.get(Company, company_id)


def get_company_or_404(db: Session, company_id: int) -> Company:
    company = db.get(Company, company_id)
    if company is None:
        raise BizException(ErrorCode.NOT_FOUND, "公司不存在")
    return company


def company_dict(company: Company) -> dict:
    return {
        "id": company.id,
        "full_name": company.full_name,
        "short_name": company.short_name,
        "legal_person": company.legal_person,
        "phone": company.phone,
        "tax_no": company.tax_no,
    }


def update_company(
    db: Session, *, company_id: int, data: dict, staff: Staff, commit: bool = True
) -> Company:
    _check_unknown_fields(data, COMPANY_FIELDS)
    company = get_company_or_404(db, company_id)

    if "full_name" in data:
        company.full_name = _require_text(data["full_name"], "公司全称", 128)
    for field, label, max_length in (
        ("short_name", "公司简称", 64),
        ("legal_person", "公司法人", 64),
        ("phone", "手机号码", 32),
        ("tax_no", "企业税号", 64),
    ):
        if field in data:
            setattr(company, field, _optional_text(data[field], label, max_length))

    write_audit(
        db,
        action="UPDATE",
        target_type="company",
        target_id=str(company.id),
        summary=f"修改公司信息：{company.full_name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return company


# --------------------------------------------------------------------------- #
# 部门
# --------------------------------------------------------------------------- #


def department_dict(department: Department) -> dict:
    return {
        "id": department.id,
        "name": department.name,
        "parent_id": department.parent_id,
        "sort_order": department.sort_order,
    }


def get_department_or_404(
    db: Session, department_id: int, company_id: int
) -> Department:
    department = db.get(Department, department_id)
    if department is None or department.company_id != company_id:
        raise BizException(ErrorCode.NOT_FOUND, "部门不存在")
    return department


def _validate_department_parent(
    db: Session,
    *,
    company_id: int,
    parent_id: int | None,
    department_id: int | None = None,
) -> None:
    if parent_id is None:
        return
    if department_id is not None and parent_id == department_id:
        raise BizException(ErrorCode.BAD_REQUEST, "上级部门不能选择当前部门")

    parent = get_department_or_404(db, parent_id, company_id)
    visited = {department_id} if department_id is not None else set()
    cursor: Department | None = parent
    while cursor is not None:
        if cursor.id in visited:
            raise BizException(ErrorCode.BAD_REQUEST, "部门层级不能形成循环")
        visited.add(cursor.id)
        cursor = db.get(Department, cursor.parent_id) if cursor.parent_id else None


def list_departments(db: Session, company_id: int) -> list[Department]:
    return list(
        db.scalars(
            select(Department)
            .where(Department.company_id == company_id)
            .order_by(Department.sort_order, Department.id)
        )
    )


def create_department(
    db: Session, *, company_id: int, data: dict, staff: Staff, commit: bool = True
) -> Department:
    _check_unknown_fields(data, DEPARTMENT_FIELDS)
    name = _require_text(data.get("name"), "部门名称", 64)
    parent_id = _optional_id(data.get("parent_id"), "上级部门")
    sort_order = _to_int(data.get("sort_order", 0), "排序")
    _validate_department_parent(db, company_id=company_id, parent_id=parent_id)

    department = Department(
        company_id=company_id,
        name=name,
        parent_id=parent_id,
        sort_order=sort_order,
    )
    db.add(department)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="department",
        target_id=str(department.id),
        summary=f"新增部门：{department.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return department


def update_department(
    db: Session,
    *,
    department_id: int,
    company_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Department:
    _check_unknown_fields(data, DEPARTMENT_FIELDS)
    department = get_department_or_404(db, department_id, company_id)

    parent_id = department.parent_id
    if "parent_id" in data:
        parent_id = _optional_id(data["parent_id"], "上级部门")
    _validate_department_parent(
        db,
        company_id=company_id,
        parent_id=parent_id,
        department_id=department.id,
    )

    if "name" in data:
        department.name = _require_text(data["name"], "部门名称", 64)
    if "parent_id" in data:
        department.parent_id = parent_id
    if "sort_order" in data:
        department.sort_order = _to_int(data["sort_order"], "排序")

    write_audit(
        db,
        action="UPDATE",
        target_type="department",
        target_id=str(department.id),
        summary=f"修改部门：{department.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return department


def delete_department(
    db: Session,
    *,
    department_id: int,
    company_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    department = get_department_or_404(db, department_id, company_id)
    blockers: list[str] = []
    if db.scalar(
        select(func.count(Department.id)).where(Department.parent_id == department.id)
    ):
        blockers.append("下级部门")
    if db.scalar(select(func.count(Staff.id)).where(Staff.department_id == department.id)):
        blockers.append("员工")
    if db.scalar(
        select(func.count(DataScope.id)).where(DataScope.department_id == department.id)
    ):
        blockers.append("数据权限")
    if blockers:
        raise BizException(
            ErrorCode.CONFLICT,
            f"部门已被 {','.join(blockers)} 使用，不能删除",
        )

    db.delete(department)
    write_audit(
        db,
        action="DELETE",
        target_type="department",
        target_id=str(department.id),
        summary=f"删除部门：{department.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 职务
# --------------------------------------------------------------------------- #


def position_dict(position: Position) -> dict:
    return {"id": position.id, "name": position.name}


def get_position_or_404(db: Session, position_id: int, company_id: int) -> Position:
    position = db.get(Position, position_id)
    if position is None or position.company_id != company_id:
        raise BizException(ErrorCode.NOT_FOUND, "职务不存在")
    return position


def list_positions(db: Session, company_id: int) -> list[Position]:
    return list(
        db.scalars(
            select(Position)
            .where(Position.company_id == company_id)
            .order_by(Position.id)
        )
    )


def create_position(
    db: Session, *, company_id: int, data: dict, staff: Staff, commit: bool = True
) -> Position:
    _check_unknown_fields(data, POSITION_FIELDS)
    position = Position(
        company_id=company_id,
        name=_require_text(data.get("name"), "职务名称", 64),
    )
    db.add(position)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="position",
        target_id=str(position.id),
        summary=f"新增职务：{position.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return position


def update_position(
    db: Session,
    *,
    position_id: int,
    company_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Position:
    _check_unknown_fields(data, POSITION_FIELDS)
    position = get_position_or_404(db, position_id, company_id)
    if "name" in data:
        position.name = _require_text(data["name"], "职务名称", 64)
    write_audit(
        db,
        action="UPDATE",
        target_type="position",
        target_id=str(position.id),
        summary=f"修改职务：{position.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return position


def delete_position(
    db: Session,
    *,
    position_id: int,
    company_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    position = get_position_or_404(db, position_id, company_id)
    if db.scalar(select(func.count(Staff.id)).where(Staff.position_id == position.id)):
        raise BizException(ErrorCode.CONFLICT, "职务已被员工使用，不能删除")
    db.delete(position)
    write_audit(
        db,
        action="DELETE",
        target_type="position",
        target_id=str(position.id),
        summary=f"删除职务：{position.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 员工
# --------------------------------------------------------------------------- #


def staff_dict(db: Session, staff: Staff) -> dict:
    department = db.get(Department, staff.department_id) if staff.department_id else None
    position = db.get(Position, staff.position_id) if staff.position_id else None
    return {
        "id": staff.id,
        "company_id": staff.company_id,
        "name": staff.name,
        "staff_no": staff.staff_no,
        "login_account": staff.login_account,
        "phone": staff.phone,
        "department_id": staff.department_id,
        "department_name": department.name if department else None,
        "position_id": staff.position_id,
        "position_name": position.name if position else None,
        "gender": staff.gender,
        "region": staff.region,
        "remark": staff.remark,
        "is_distribution": staff.is_distribution,
        "is_active": staff.is_active,
        "has_password": bool(staff.password_hash),
    }


def get_staff_or_404(db: Session, staff_id: int, company_id: int) -> Staff:
    staff = db.get(Staff, staff_id)
    if staff is None or staff.company_id != company_id:
        raise BizException(ErrorCode.NOT_FOUND, "员工不存在")
    return staff


def _validate_staff_org(
    db: Session, *, company_id: int, department_id: int | None, position_id: int | None
) -> None:
    if department_id is not None:
        get_department_or_404(db, department_id, company_id)
    if position_id is not None:
        get_position_or_404(db, position_id, company_id)


def _validate_staff_unique(
    db: Session, values: dict, *, exclude_id: int | None = None
) -> None:
    for field, label in (
        ("staff_no", "工号"),
        ("login_account", "登录账户"),
        ("phone", "手机号码"),
    ):
        value = values.get(field)
        if value is None:
            continue
        stmt = select(Staff.id).where(getattr(Staff, field) == value)
        if exclude_id is not None:
            stmt = stmt.where(Staff.id != exclude_id)
        if db.scalar(stmt) is not None:
            raise BizException(ErrorCode.CONFLICT, f"{label}已存在：{value}")


def _staff_values(
    db: Session,
    *,
    company_id: int,
    data: dict,
    allow_password: bool,
    exclude_id: int | None = None,
    require_name: bool = False,
) -> dict:
    allowed = STAFF_CREATE_FIELDS if allow_password else STAFF_BASE_FIELDS
    _check_unknown_fields(data, allowed)
    values: dict[str, Any] = {}

    if require_name or "name" in data:
        values["name"] = _require_text(data.get("name"), "员工姓名", 64)
    for field, label, max_length in (
        ("staff_no", "工号", 32),
        ("login_account", "登录账户", 64),
        ("phone", "手机号码", 32),
        ("gender", "性别", 8),
        ("region", "地区", 64),
        ("remark", "备注", 255),
    ):
        if field in data:
            values[field] = _optional_text(data[field], label, max_length)

    for field, label in (("department_id", "部门"), ("position_id", "职务")):
        if field in data:
            values[field] = _optional_id(data[field], label)
    for field, label in (("is_distribution", "分销业务"), ("is_active", "是否启用")):
        if field in data:
            values[field] = _to_bool(data[field], label)

    if allow_password and "password" in data:
        password = data["password"]
        if password is None or not str(password):
            raise BizException(ErrorCode.BAD_REQUEST, "初始密码不能为空")
        validate_new_password(str(password))
        values["password_hash"] = hash_password(str(password))

    _validate_staff_unique(db, values, exclude_id=exclude_id)
    _validate_staff_org(
        db,
        company_id=company_id,
        department_id=values.get("department_id"),
        position_id=values.get("position_id"),
    )
    return values


def create_staff(
    db: Session, *, company_id: int, data: dict, staff: Staff, commit: bool = True
) -> Staff:
    values = _staff_values(
        db, company_id=company_id, data=data, allow_password=True, require_name=True
    )
    values.setdefault("is_distribution", False)
    values.setdefault("is_active", True)
    employee = Staff(company_id=company_id, **values)
    db.add(employee)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="staff",
        target_id=str(employee.id),
        summary=f"新增员工：{employee.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return employee


def update_staff(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Staff:
    employee = get_staff_or_404(db, staff_id, company_id)
    values = _staff_values(
        db,
        company_id=company_id,
        data=data,
        allow_password=False,
        exclude_id=employee.id,
    )
    for field, value in values.items():
        setattr(employee, field, value)
    write_audit(
        db,
        action="UPDATE",
        target_type="staff",
        target_id=str(employee.id),
        summary=f"修改员工：{employee.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return employee


def list_staff(
    db: Session,
    *,
    company_id: int,
    keyword: str | None = None,
    department_id: int | None = None,
    position_id: int | None = None,
    is_active: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = [Staff.company_id == company_id]
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Staff.name.like(like),
                Staff.staff_no.like(like),
                Staff.login_account.like(like),
                Staff.phone.like(like),
            )
        )
    if department_id is not None:
        conditions.append(Staff.department_id == department_id)
    if position_id is not None:
        conditions.append(Staff.position_id == position_id)
    if is_active is not None:
        conditions.append(Staff.is_active.is_(is_active))

    total = db.scalar(select(func.count(Staff.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Staff)
            .where(*conditions)
            .order_by(Staff.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [staff_dict(db, row) for row in rows],
    }


def set_staff_active(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    active: bool,
    staff: Staff,
    commit: bool = True,
) -> Staff:
    employee = get_staff_or_404(db, staff_id, company_id)
    employee.is_active = active
    write_audit(
        db,
        action="ENABLE" if active else "DISABLE",
        target_type="staff",
        target_id=str(employee.id),
        summary=f"{'启用' if active else '停用'}员工：{employee.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return employee


def reset_staff_password(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    new_password: str,
    staff: Staff,
    commit: bool = True,
) -> Staff:
    password = str(new_password)
    validate_new_password(password)
    employee = get_staff_or_404(db, staff_id, company_id)
    employee.password_hash = hash_password(password)
    write_audit(
        db,
        action="RESET_PASSWORD",
        target_type="staff",
        target_id=str(employee.id),
        summary=f"重置员工密码：{employee.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return employee


def get_staff_data_scope(db: Session, *, staff_id: int, company_id: int) -> dict:
    employee = get_staff_or_404(db, staff_id, company_id)
    row = db.scalars(
        select(DataScope).where(DataScope.staff_id == employee.id).order_by(DataScope.id.desc())
    ).first()
    return {"staff_id": employee.id, "scope_type": row.scope_type if row else "SELF"}


def set_staff_data_scope(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    scope_type: str,
    staff: Staff,
    commit: bool = True,
) -> dict:
    employee = get_staff_or_404(db, staff_id, company_id)
    if scope_type not in {"SELF", "DEPARTMENT", "COMPANY"}:
        raise BizException(ErrorCode.BAD_REQUEST, "数据范围仅支持本人、本部门或全公司")
    db.execute(delete(DataScope).where(DataScope.staff_id == employee.id))
    db.add(
        DataScope(
            staff_id=employee.id,
            scope_type=scope_type,
            department_id=employee.department_id if scope_type == "DEPARTMENT" else None,
        )
    )
    write_audit(
        db,
        action="UPDATE",
        target_type="data_scope",
        target_id=str(employee.id),
        summary=f"设置员工数据范围：{employee.name} {scope_type}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {"staff_id": employee.id, "scope_type": scope_type}


def _staff_referenced_tables(db: Session, staff_id: int) -> list[str]:
    used: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name in _STAFF_CHILD_TABLES:
            continue
        for column in table.columns:
            if not any(fk.target_fullname == "staff.id" for fk in column.foreign_keys):
                continue
            stmt = select(literal(1)).select_from(table).where(column == staff_id).limit(1)
            if db.execute(stmt).first() is not None:
                used.append(table.name)
                break
    return used


def delete_staff(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    employee = get_staff_or_404(db, staff_id, company_id)
    if employee.id == staff.id:
        raise BizException(ErrorCode.BAD_REQUEST, "不能删除当前登录员工")
    referenced = _staff_referenced_tables(db, employee.id)
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"员工已被 {','.join(sorted(referenced))} 引用，不能删除，请改为停用",
        )
    db.execute(delete(StaffRole).where(StaffRole.staff_id == employee.id))
    db.execute(delete(DataScope).where(DataScope.staff_id == employee.id))
    db.delete(employee)
    write_audit(
        db,
        action="DELETE",
        target_type="staff",
        target_id=str(employee.id),
        summary=f"删除员工：{employee.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# IP 访问规则
# --------------------------------------------------------------------------- #


def ip_rule_dict(rule: IpRule) -> dict:
    return {
        "id": rule.id,
        "rule_type": rule.rule_type,
        "ip_address": rule.ip_address,
        "remark": rule.remark,
    }


def get_ip_rule_or_404(db: Session, rule_id: int, company_id: int) -> IpRule:
    rule = db.get(IpRule, rule_id)
    if rule is None or rule.company_id != company_id:
        raise BizException(ErrorCode.NOT_FOUND, "IP 规则不存在")
    return rule


def list_ip_rules(db: Session, company_id: int) -> list[IpRule]:
    return list(
        db.scalars(
            select(IpRule)
            .where(IpRule.company_id == company_id)
            .order_by(IpRule.id.desc())
        )
    )


def _validate_rule_type(value: Any) -> str:
    rule_type = _require_text(value, "规则类型", 8).upper()
    if rule_type not in IP_RULE_TYPES:
        raise BizException(ErrorCode.BAD_REQUEST, "规则类型仅支持 ALLOW 或 DENY")
    return rule_type


def _validate_ip_address(value: Any) -> str:
    text = _require_text(value, "IP 地址", 64)
    try:
        if "/" in text:
            ip_network(text, strict=False)
        else:
            ip_address(text)
    except ValueError as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "IP 地址或网段格式不正确") from exc
    return text


def _validate_ip_rule_unique(
    db: Session,
    *,
    company_id: int,
    rule_type: str,
    ip_address: str,
    exclude_id: int | None = None,
) -> None:
    stmt = select(IpRule.id).where(
        IpRule.company_id == company_id,
        IpRule.rule_type == rule_type,
        IpRule.ip_address == ip_address,
    )
    if exclude_id is not None:
        stmt = stmt.where(IpRule.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, "相同 IP 规则已存在")


def create_ip_rule(
    db: Session, *, company_id: int, data: dict, staff: Staff, commit: bool = True
) -> IpRule:
    _check_unknown_fields(data, IP_RULE_FIELDS)
    rule_type = _validate_rule_type(data.get("rule_type"))
    ip_value = _validate_ip_address(data.get("ip_address"))
    _validate_ip_rule_unique(
        db, company_id=company_id, rule_type=rule_type, ip_address=ip_value
    )
    rule = IpRule(
        company_id=company_id,
        rule_type=rule_type,
        ip_address=ip_value,
        remark=_optional_text(data.get("remark"), "备注", 255),
    )
    db.add(rule)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="ip_rule",
        target_id=str(rule.id),
        summary=f"新增 IP 规则：{rule.rule_type} {rule.ip_address}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return rule


def update_ip_rule(
    db: Session,
    *,
    rule_id: int,
    company_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> IpRule:
    _check_unknown_fields(data, IP_RULE_FIELDS)
    rule = get_ip_rule_or_404(db, rule_id, company_id)
    rule_type = (
        _validate_rule_type(data["rule_type"]) if "rule_type" in data else rule.rule_type
    )
    ip_value = (
        _validate_ip_address(data["ip_address"])
        if "ip_address" in data
        else rule.ip_address
    )
    _validate_ip_rule_unique(
        db,
        company_id=company_id,
        rule_type=rule_type,
        ip_address=ip_value,
        exclude_id=rule.id,
    )
    rule.rule_type = rule_type
    rule.ip_address = ip_value
    if "remark" in data:
        rule.remark = _optional_text(data["remark"], "备注", 255)
    write_audit(
        db,
        action="UPDATE",
        target_type="ip_rule",
        target_id=str(rule.id),
        summary=f"修改 IP 规则：{rule.rule_type} {rule.ip_address}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return rule


def delete_ip_rule(
    db: Session,
    *,
    rule_id: int,
    company_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    rule = get_ip_rule_or_404(db, rule_id, company_id)
    db.delete(rule)
    write_audit(
        db,
        action="DELETE",
        target_type="ip_rule",
        target_id=str(rule.id),
        summary=f"删除 IP 规则：{rule.rule_type} {rule.ip_address}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
