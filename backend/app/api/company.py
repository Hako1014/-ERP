from typing import Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import company_service

router = APIRouter(tags=["公司设置"])

read_settings = require_permission("module:settings")
manage_settings = require_permission("settings:manage")


class CompanyUpdateRequest(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=128)
    short_name: str | None = Field(default=None, max_length=64)
    legal_person: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    tax_no: str | None = Field(default=None, max_length=64)


class DepartmentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    parent_id: int | None = None
    sort_order: int = 0


class DepartmentUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    parent_id: int | None = None
    sort_order: int | None = None


class PositionCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class PositionUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)


class StaffCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    staff_no: str | None = Field(default=None, max_length=32)
    login_account: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    password: str | None = Field(default=None, min_length=8, max_length=64)
    department_id: int | None = None
    position_id: int | None = None
    gender: str | None = Field(default=None, max_length=8)
    region: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    is_distribution: bool = False
    is_active: bool = True


class StaffUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    staff_no: str | None = Field(default=None, max_length=32)
    login_account: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    department_id: int | None = None
    position_id: int | None = None
    gender: str | None = Field(default=None, max_length=8)
    region: str | None = Field(default=None, max_length=64)
    remark: str | None = Field(default=None, max_length=255)
    is_distribution: bool | None = None
    is_active: bool | None = None


class ResetPasswordRequest(BaseModel):
    new_password: str = Field(..., min_length=8, max_length=64)


class DataScopeRequest(BaseModel):
    scope_type: Literal["SELF", "DEPARTMENT", "COMPANY"]


class IpRuleCreateRequest(BaseModel):
    rule_type: Literal["ALLOW", "DENY"]
    ip_address: str = Field(..., min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)


class IpRuleUpdateRequest(BaseModel):
    rule_type: Literal["ALLOW", "DENY"] | None = None
    ip_address: str | None = Field(default=None, min_length=1, max_length=64)
    remark: str | None = Field(default=None, max_length=255)


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- #
# 公司信息
# --------------------------------------------------------------------------- #


@router.get("/company", response_model=Result[dict])
def get_company(
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    company = company_service.get_company_or_404(db, staff.company_id)
    return Result.ok(company_service.company_dict(company))


@router.put("/company", response_model=Result[dict])
def update_company(
    req: CompanyUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    company = company_service.update_company(
        db, company_id=staff.company_id, data=_payload(req), staff=staff
    )
    return Result.ok(company_service.company_dict(company), message="公司信息已更新")


# --------------------------------------------------------------------------- #
# 部门
# --------------------------------------------------------------------------- #


@router.get("/departments", response_model=Result[list[dict]])
def list_departments(
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    departments = company_service.list_departments(db, staff.company_id)
    return Result.ok([company_service.department_dict(item) for item in departments])


@router.post("/departments", response_model=Result[dict])
def create_department(
    req: DepartmentCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    department = company_service.create_department(
        db, company_id=staff.company_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        company_service.department_dict(department), message="部门已创建"
    )


@router.get("/departments/{department_id}", response_model=Result[dict])
def get_department(
    department_id: int,
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    department = company_service.get_department_or_404(
        db, department_id, staff.company_id
    )
    return Result.ok(company_service.department_dict(department))


@router.put("/departments/{department_id}", response_model=Result[dict])
def update_department(
    department_id: int,
    req: DepartmentUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    department = company_service.update_department(
        db,
        department_id=department_id,
        company_id=staff.company_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(
        company_service.department_dict(department), message="部门已更新"
    )


@router.delete("/departments/{department_id}", response_model=Result[dict])
def delete_department(
    department_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    company_service.delete_department(
        db,
        department_id=department_id,
        company_id=staff.company_id,
        staff=staff,
    )
    return Result.ok(message="部门已删除")


# --------------------------------------------------------------------------- #
# 职务
# --------------------------------------------------------------------------- #


@router.get("/positions", response_model=Result[list[dict]])
def list_positions(
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    positions = company_service.list_positions(db, staff.company_id)
    return Result.ok([company_service.position_dict(item) for item in positions])


@router.post("/positions", response_model=Result[dict])
def create_position(
    req: PositionCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    position = company_service.create_position(
        db, company_id=staff.company_id, data=_payload(req), staff=staff
    )
    return Result.ok(company_service.position_dict(position), message="职务已创建")


@router.get("/positions/{position_id}", response_model=Result[dict])
def get_position(
    position_id: int,
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    position = company_service.get_position_or_404(db, position_id, staff.company_id)
    return Result.ok(company_service.position_dict(position))


@router.put("/positions/{position_id}", response_model=Result[dict])
def update_position(
    position_id: int,
    req: PositionUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    position = company_service.update_position(
        db,
        position_id=position_id,
        company_id=staff.company_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(company_service.position_dict(position), message="职务已更新")


@router.delete("/positions/{position_id}", response_model=Result[dict])
def delete_position(
    position_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    company_service.delete_position(
        db,
        position_id=position_id,
        company_id=staff.company_id,
        staff=staff,
    )
    return Result.ok(message="职务已删除")


# --------------------------------------------------------------------------- #
# 员工
# --------------------------------------------------------------------------- #


@router.get("/staff", response_model=Result[dict])
def list_staff(
    keyword: str | None = None,
    department_id: int | None = None,
    position_id: int | None = None,
    is_active: bool | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    return Result.ok(
        company_service.list_staff(
            db,
            company_id=staff.company_id,
            keyword=keyword,
            department_id=department_id,
            position_id=position_id,
            is_active=is_active,
            page=page,
            page_size=page_size,
        )
    )


@router.post("/staff", response_model=Result[dict])
def create_staff(
    req: StaffCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    employee = company_service.create_staff(
        db, company_id=staff.company_id, data=_payload(req), staff=staff
    )
    return Result.ok(company_service.staff_dict(db, employee), message="员工已创建")


@router.get("/staff/{staff_id}", response_model=Result[dict])
def get_staff(
    staff_id: int,
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    employee = company_service.get_staff_or_404(db, staff_id, staff.company_id)
    return Result.ok(company_service.staff_dict(db, employee))


@router.put("/staff/{staff_id}", response_model=Result[dict])
def update_staff(
    staff_id: int,
    req: StaffUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    employee = company_service.update_staff(
        db,
        staff_id=staff_id,
        company_id=staff.company_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(company_service.staff_dict(db, employee), message="员工已更新")


@router.delete("/staff/{staff_id}", response_model=Result[dict])
def delete_staff(
    staff_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    company_service.delete_staff(
        db, staff_id=staff_id, company_id=staff.company_id, staff=staff
    )
    return Result.ok(message="员工已删除")


@router.post("/staff/{staff_id}/enable", response_model=Result[dict])
def enable_staff(
    staff_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    employee = company_service.set_staff_active(
        db,
        staff_id=staff_id,
        company_id=staff.company_id,
        active=True,
        staff=staff,
    )
    return Result.ok(company_service.staff_dict(db, employee), message="员工已启用")


@router.post("/staff/{staff_id}/disable", response_model=Result[dict])
def disable_staff(
    staff_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    employee = company_service.set_staff_active(
        db,
        staff_id=staff_id,
        company_id=staff.company_id,
        active=False,
        staff=staff,
    )
    return Result.ok(company_service.staff_dict(db, employee), message="员工已停用")


@router.post("/staff/{staff_id}/reset-password", response_model=Result[dict])
def reset_staff_password(
    staff_id: int,
    req: ResetPasswordRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    company_service.reset_staff_password(
        db,
        staff_id=staff_id,
        company_id=staff.company_id,
        new_password=req.new_password,
        staff=staff,
    )
    return Result.ok(message="密码已重置")


@router.get("/staff/{staff_id}/data-scope", response_model=Result[dict])
def get_staff_data_scope(
    staff_id: int,
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    return Result.ok(
        company_service.get_staff_data_scope(
            db, staff_id=staff_id, company_id=staff.company_id
        )
    )


@router.put("/staff/{staff_id}/data-scope", response_model=Result[dict])
def set_staff_data_scope(
    staff_id: int,
    req: DataScopeRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    return Result.ok(
        company_service.set_staff_data_scope(
            db,
            staff_id=staff_id,
            company_id=staff.company_id,
            scope_type=req.scope_type,
            staff=staff,
        ),
        message="数据范围已更新",
    )


# --------------------------------------------------------------------------- #
# IP 访问规则
# --------------------------------------------------------------------------- #


@router.get("/ip-rules", response_model=Result[list[dict]])
def list_ip_rules(
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    rules = company_service.list_ip_rules(db, staff.company_id)
    return Result.ok([company_service.ip_rule_dict(item) for item in rules])


@router.post("/ip-rules", response_model=Result[dict])
def create_ip_rule(
    req: IpRuleCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    rule = company_service.create_ip_rule(
        db, company_id=staff.company_id, data=_payload(req), staff=staff
    )
    return Result.ok(company_service.ip_rule_dict(rule), message="IP 规则已创建")


@router.get("/ip-rules/{rule_id}", response_model=Result[dict])
def get_ip_rule(
    rule_id: int,
    staff: Staff = Depends(read_settings),
    db: Session = Depends(get_db),
):
    rule = company_service.get_ip_rule_or_404(db, rule_id, staff.company_id)
    return Result.ok(company_service.ip_rule_dict(rule))


@router.put("/ip-rules/{rule_id}", response_model=Result[dict])
def update_ip_rule(
    rule_id: int,
    req: IpRuleUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    rule = company_service.update_ip_rule(
        db,
        rule_id=rule_id,
        company_id=staff.company_id,
        data=_payload(req),
        staff=staff,
    )
    return Result.ok(company_service.ip_rule_dict(rule), message="IP 规则已更新")


@router.delete("/ip-rules/{rule_id}", response_model=Result[dict])
def delete_ip_rule(
    rule_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    company_service.delete_ip_rule(
        db, rule_id=rule_id, company_id=staff.company_id, staff=staff
    )
    return Result.ok(message="IP 规则已删除")
