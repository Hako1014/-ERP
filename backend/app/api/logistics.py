from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import require_permission
from app.core.response import Result
from app.db.session import get_db
from app.models.org import Staff
from app.services import logistics_service

router = APIRouter(tags=["物流工具"])

read_logistics = require_permission("module:settings", "module:order")
manage_settings = require_permission("settings:manage")


class LogisticsCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    company: str | None = Field(default=None, max_length=64)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=255)
    is_default: bool = False
    is_enabled: bool = True


class LogisticsUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    company: str | None = Field(default=None, max_length=64)
    contact: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)
    address: str | None = Field(default=None, max_length=255)
    is_default: bool | None = None
    is_enabled: bool | None = None


class FreightTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    logistics_id: int
    charge_type: str = Field(default="BY_COUNT", max_length=16)
    first_count: int = 1
    continue_count: int = 1
    first_weight: Decimal | None = None
    continue_weight: Decimal | None = None
    base_fee: Decimal = Decimal("0")
    continue_fee: Decimal = Decimal("0")
    deduct_weight: Decimal | None = None
    operation_fee: Decimal = Decimal("0")
    consumable_fee: Decimal = Decimal("0")
    ewaybill_fee: Decimal = Decimal("0")
    tax_fee: Decimal = Decimal("0")
    tax_point: Decimal | None = None


class FreightTemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    logistics_id: int | None = None
    charge_type: str | None = Field(default=None, max_length=16)
    first_count: int | None = None
    continue_count: int | None = None
    first_weight: Decimal | None = None
    continue_weight: Decimal | None = None
    base_fee: Decimal | None = None
    continue_fee: Decimal | None = None
    deduct_weight: Decimal | None = None
    operation_fee: Decimal | None = None
    consumable_fee: Decimal | None = None
    ewaybill_fee: Decimal | None = None
    tax_fee: Decimal | None = None
    tax_point: Decimal | None = None


class AreaRuleCreateRequest(BaseModel):
    logistics_id: int
    province: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=32)
    match_weight_min: Decimal | None = None
    match_weight_max: Decimal | None = None
    freight_template_id: int | None = None


class AreaRuleUpdateRequest(BaseModel):
    logistics_id: int | None = None
    province: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, max_length=32)
    match_weight_min: Decimal | None = None
    match_weight_max: Decimal | None = None
    freight_template_id: int | None = None


class PrintTemplateCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    template_type: str = Field(..., min_length=1, max_length=32)
    logistics_id: int | None = None
    content: str | None = None
    paper_width: Decimal | None = None
    paper_height: Decimal | None = None
    is_default: bool = False


class PrintTemplateUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    template_type: str | None = Field(default=None, min_length=1, max_length=32)
    logistics_id: int | None = None
    content: str | None = None
    paper_width: Decimal | None = None
    paper_height: Decimal | None = None
    is_default: bool | None = None


def _payload(model: BaseModel) -> dict:
    return model.model_dump(exclude_unset=True)


# --------------------------------------------------------------------------- #
# 物流方式
# --------------------------------------------------------------------------- #


@router.get("/logistics", response_model=Result[list[dict]])
def list_logistics(
    keyword: str | None = None,
    is_enabled: bool | None = None,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    rows = logistics_service.list_logistics(
        db, keyword=keyword, is_enabled=is_enabled
    )
    return Result.ok([logistics_service.logistics_dict(item) for item in rows])


@router.post("/logistics", response_model=Result[dict])
def create_logistics(
    req: LogisticsCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.create_logistics(db, data=_payload(req), staff=staff)
    return Result.ok(logistics_service.logistics_dict(row), message="物流方式已创建")


@router.get("/logistics/{logistics_id}", response_model=Result[dict])
def get_logistics(
    logistics_id: int,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    row = logistics_service.get_logistics_or_404(db, logistics_id)
    return Result.ok(logistics_service.logistics_dict(row))


@router.put("/logistics/{logistics_id}", response_model=Result[dict])
def update_logistics(
    logistics_id: int,
    req: LogisticsUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.update_logistics(
        db, logistics_id=logistics_id, data=_payload(req), staff=staff
    )
    return Result.ok(logistics_service.logistics_dict(row), message="物流方式已更新")


@router.delete("/logistics/{logistics_id}", response_model=Result[dict])
def delete_logistics(
    logistics_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    logistics_service.delete_logistics(
        db, logistics_id=logistics_id, staff=staff
    )
    return Result.ok(message="物流方式已删除")


@router.post("/logistics/{logistics_id}/enable", response_model=Result[dict])
def enable_logistics(
    logistics_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.set_logistics_enabled(
        db, logistics_id=logistics_id, enabled=True, staff=staff
    )
    return Result.ok(logistics_service.logistics_dict(row), message="物流方式已启用")


@router.post("/logistics/{logistics_id}/disable", response_model=Result[dict])
def disable_logistics(
    logistics_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.set_logistics_enabled(
        db, logistics_id=logistics_id, enabled=False, staff=staff
    )
    return Result.ok(logistics_service.logistics_dict(row), message="物流方式已停用")


@router.post("/logistics/{logistics_id}/default", response_model=Result[dict])
def set_default_logistics(
    logistics_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.set_default_logistics(
        db, logistics_id=logistics_id, staff=staff
    )
    return Result.ok(logistics_service.logistics_dict(row), message="已设为默认物流")


# --------------------------------------------------------------------------- #
# 运费模板
# --------------------------------------------------------------------------- #


@router.get("/freight-templates", response_model=Result[list[dict]])
def list_freight_templates(
    logistics_id: int | None = None,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    rows = logistics_service.list_freight_templates(
        db, logistics_id=logistics_id
    )
    return Result.ok(
        [logistics_service.freight_template_dict(db, item) for item in rows]
    )


@router.post("/freight-templates", response_model=Result[dict])
def create_freight_template(
    req: FreightTemplateCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.create_freight_template(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(
        logistics_service.freight_template_dict(db, row), message="运费模板已创建"
    )


@router.get(
    "/freight-templates/{template_id}/calculate",
    response_model=Result[dict],
)
def calculate_freight(
    template_id: int,
    count: int | None = Query(default=None, ge=1),
    weight: Decimal | None = Query(default=None, gt=0),
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    return Result.ok(
        logistics_service.calculate_freight(
            db, template_id=template_id, count=count, weight=weight
        )
    )


@router.get("/freight-templates/{template_id}", response_model=Result[dict])
def get_freight_template(
    template_id: int,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    row = logistics_service.get_freight_template_or_404(db, template_id)
    return Result.ok(logistics_service.freight_template_dict(db, row))


@router.put("/freight-templates/{template_id}", response_model=Result[dict])
def update_freight_template(
    template_id: int,
    req: FreightTemplateUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.update_freight_template(
        db, template_id=template_id, data=_payload(req), staff=staff
    )
    return Result.ok(
        logistics_service.freight_template_dict(db, row), message="运费模板已更新"
    )


@router.delete("/freight-templates/{template_id}", response_model=Result[dict])
def delete_freight_template(
    template_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    logistics_service.delete_freight_template(
        db, template_id=template_id, staff=staff
    )
    return Result.ok(message="运费模板已删除")


# --------------------------------------------------------------------------- #
# 区域物流规则
# --------------------------------------------------------------------------- #


@router.get("/area-rules/match", response_model=Result[dict])
def match_area_rule(
    logistics_id: int,
    province: str | None = None,
    city: str | None = None,
    weight: Decimal | None = Query(default=None, ge=0),
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    return Result.ok(
        logistics_service.match_area_rule(
            db,
            logistics_id=logistics_id,
            province=province,
            city=city,
            weight=weight,
        )
    )


@router.get("/area-rules", response_model=Result[list[dict]])
def list_area_rules(
    logistics_id: int | None = None,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    rows = logistics_service.list_area_rules(db, logistics_id=logistics_id)
    return Result.ok([logistics_service.area_rule_dict(db, item) for item in rows])


@router.post("/area-rules", response_model=Result[dict])
def create_area_rule(
    req: AreaRuleCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.create_area_rule(db, data=_payload(req), staff=staff)
    return Result.ok(logistics_service.area_rule_dict(db, row), message="区域物流规则已创建")


@router.get("/area-rules/{rule_id}", response_model=Result[dict])
def get_area_rule(
    rule_id: int,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    row = logistics_service.get_area_rule_or_404(db, rule_id)
    return Result.ok(logistics_service.area_rule_dict(db, row))


@router.put("/area-rules/{rule_id}", response_model=Result[dict])
def update_area_rule(
    rule_id: int,
    req: AreaRuleUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.update_area_rule(
        db, rule_id=rule_id, data=_payload(req), staff=staff
    )
    return Result.ok(logistics_service.area_rule_dict(db, row), message="区域物流规则已更新")


@router.delete("/area-rules/{rule_id}", response_model=Result[dict])
def delete_area_rule(
    rule_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    logistics_service.delete_area_rule(db, rule_id=rule_id, staff=staff)
    return Result.ok(message="区域物流规则已删除")


# --------------------------------------------------------------------------- #
# 打印模板
# --------------------------------------------------------------------------- #


@router.get("/print-templates", response_model=Result[list[dict]])
def list_print_templates(
    logistics_id: int | None = None,
    template_type: str | None = None,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    rows = logistics_service.list_print_templates(
        db, logistics_id=logistics_id, template_type=template_type
    )
    return Result.ok([logistics_service.print_template_dict(item) for item in rows])


@router.post("/print-templates", response_model=Result[dict])
def create_print_template(
    req: PrintTemplateCreateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.create_print_template(
        db, data=_payload(req), staff=staff
    )
    return Result.ok(logistics_service.print_template_dict(row), message="打印模板已创建")


@router.get("/print-templates/{template_id}", response_model=Result[dict])
def get_print_template(
    template_id: int,
    staff: Staff = Depends(read_logistics),
    db: Session = Depends(get_db),
):
    row = logistics_service.get_print_template_or_404(db, template_id)
    return Result.ok(logistics_service.print_template_dict(row))


@router.put("/print-templates/{template_id}", response_model=Result[dict])
def update_print_template(
    template_id: int,
    req: PrintTemplateUpdateRequest,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    row = logistics_service.update_print_template(
        db, template_id=template_id, data=_payload(req), staff=staff
    )
    return Result.ok(logistics_service.print_template_dict(row), message="打印模板已更新")


@router.delete("/print-templates/{template_id}", response_model=Result[dict])
def delete_print_template(
    template_id: int,
    staff: Staff = Depends(manage_settings),
    db: Session = Depends(get_db),
):
    logistics_service.delete_print_template(
        db, template_id=template_id, staff=staff
    )
    return Result.ok(message="打印模板已删除")
