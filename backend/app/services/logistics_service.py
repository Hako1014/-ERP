"""物流工具服务（TASK-023）。

覆盖物流方式、按件/按重运费模板、区域物流规则和打印模板维护。
真实物流下单、面单和轨迹对接在 TASK-066 落地。
"""

from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, literal, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money, to_decimal
from app.db.base import Base
from app.models.order import PrintTemplate
from app.models.org import Staff
from app.models.platform import AreaRule, FreightTemplate, Logistics

LOGISTICS_FIELDS = {
    "name",
    "company",
    "contact",
    "phone",
    "address",
    "is_default",
    "is_enabled",
}
FREIGHT_TEMPLATE_FIELDS = {
    "name",
    "logistics_id",
    "charge_type",
    "first_count",
    "continue_count",
    "first_weight",
    "continue_weight",
    "base_fee",
    "continue_fee",
    "deduct_weight",
    "operation_fee",
    "consumable_fee",
    "ewaybill_fee",
    "tax_fee",
    "tax_point",
}
AREA_RULE_FIELDS = {
    "logistics_id",
    "province",
    "city",
    "match_weight_min",
    "match_weight_max",
    "freight_template_id",
}
PRINT_TEMPLATE_FIELDS = {
    "name",
    "template_type",
    "logistics_id",
    "content",
    "paper_width",
    "paper_height",
    "is_default",
}
CHARGE_TYPES = {"BY_COUNT", "BY_WEIGHT"}

WEIGHT_QUANT = Decimal("0.001")
PAPER_QUANT = Decimal("0.01")


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


def _to_int(value: Any, label: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc


def _optional_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int(value, label)


def _to_money(value: Any, label: str) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < 0:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为负数")
    return result


def _to_decimal(
    value: Any,
    label: str,
    *,
    quant: Decimal,
    allow_zero: bool = True,
) -> Decimal:
    try:
        result = to_decimal(value).quantize(quant, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数字") from exc
    if result < 0 or (not allow_zero and result == 0):
        message = f"{label}不能小于 0" if allow_zero else f"{label}必须大于 0"
        raise BizException(ErrorCode.BAD_REQUEST, message)
    return result


def _optional_weight(value: Any, label: str) -> Decimal | None:
    if value in (None, "", 0, Decimal("0")):
        return None
    return _to_decimal(value, label, quant=WEIGHT_QUANT)


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}"
        )


# --------------------------------------------------------------------------- #
# 物流方式
# --------------------------------------------------------------------------- #


def logistics_dict(logistics: Logistics) -> dict:
    return {
        "id": logistics.id,
        "name": logistics.name,
        "company": logistics.company,
        "contact": logistics.contact,
        "phone": logistics.phone,
        "address": logistics.address,
        "is_default": logistics.is_default,
        "is_enabled": logistics.is_enabled,
    }


def get_logistics_or_404(
    db: Session, logistics_id: int, *, company_id: int | None = None,
) -> Logistics:
    stmt = select(Logistics).where(Logistics.id == logistics_id)
    if company_id is not None:
        stmt = stmt.where(Logistics.company_id == company_id)
    logistics = db.scalar(stmt)
    if logistics is None:
        raise BizException(ErrorCode.NOT_FOUND, "物流方式不存在")
    return logistics


def list_logistics(
    db: Session,
    *,
    keyword: str | None = None,
    is_enabled: bool | None = None,
) -> list[Logistics]:
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            Logistics.name.like(like) | Logistics.company.like(like)
        )
    if is_enabled is not None:
        conditions.append(Logistics.is_enabled.is_(is_enabled))
    return list(
        db.scalars(
            select(Logistics)
            .where(*conditions)
            .order_by(Logistics.is_default.desc(), Logistics.id)
        )
    )


def _validate_logistics_values(data: dict, *, require_name: bool) -> dict:
    _check_unknown_fields(data, LOGISTICS_FIELDS)
    values: dict[str, Any] = {}
    if require_name or "name" in data:
        values["name"] = _require_text(data.get("name"), "物流方式名称", 64)
    for field, label, max_length in (
        ("company", "物流公司", 64),
        ("contact", "联系人", 64),
        ("phone", "联系方式", 32),
        ("address", "联系地址", 255),
    ):
        if field in data:
            values[field] = _optional_text(data[field], label, max_length)
    for field, label in (("is_default", "默认"), ("is_enabled", "状态")):
        if field in data:
            values[field] = _to_bool(data[field], label)
    return values


def _reset_default_logistics(db: Session, *, keep_id: int) -> None:
    for row in db.scalars(select(Logistics).where(Logistics.id != keep_id)):
        row.is_default = False


def create_logistics(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Logistics:
    values = _validate_logistics_values(data, require_name=True)
    values.setdefault("is_default", False)
    values.setdefault("is_enabled", True)
    logistics = Logistics(**values)
    db.add(logistics)
    db.flush()
    if logistics.is_default:
        logistics.is_enabled = True
        _reset_default_logistics(db, keep_id=logistics.id)
    write_audit(
        db,
        action="CREATE",
        target_type="logistics",
        target_id=str(logistics.id),
        summary=f"新增物流方式：{logistics.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return logistics


def update_logistics(
    db: Session, *, logistics_id: int, data: dict, staff: Staff, commit: bool = True
) -> Logistics:
    logistics = get_logistics_or_404(db, logistics_id)
    values = _validate_logistics_values(data, require_name=False)
    for field, value in values.items():
        setattr(logistics, field, value)
    if logistics.is_default:
        logistics.is_enabled = True
        _reset_default_logistics(db, keep_id=logistics.id)
    write_audit(
        db,
        action="UPDATE",
        target_type="logistics",
        target_id=str(logistics.id),
        summary=f"修改物流方式：{logistics.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return logistics


def set_logistics_enabled(
    db: Session,
    *,
    logistics_id: int,
    enabled: bool,
    staff: Staff,
    commit: bool = True,
) -> Logistics:
    logistics = get_logistics_or_404(db, logistics_id)
    logistics.is_enabled = enabled
    if not enabled:
        logistics.is_default = False
    write_audit(
        db,
        action="ENABLE" if enabled else "DISABLE",
        target_type="logistics",
        target_id=str(logistics.id),
        summary=f"{'启用' if enabled else '停用'}物流方式：{logistics.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return logistics


def set_default_logistics(
    db: Session, *, logistics_id: int, staff: Staff, commit: bool = True
) -> Logistics:
    logistics = get_logistics_or_404(db, logistics_id)
    logistics.is_default = True
    logistics.is_enabled = True
    _reset_default_logistics(db, keep_id=logistics.id)
    write_audit(
        db,
        action="SET_DEFAULT",
        target_type="logistics",
        target_id=str(logistics.id),
        summary=f"设置默认物流方式：{logistics.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return logistics


def _referenced_tables(db: Session, logistics_id: int) -> list[str]:
    used: list[str] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not any(fk.target_fullname == "logistics.id" for fk in column.foreign_keys):
                continue
            stmt = (
                select(literal(1))
                .select_from(table)
                .where(column == logistics_id)
                .limit(1)
            )
            if db.execute(stmt).first() is not None:
                used.append(table.name)
                break
    return used


def delete_logistics(
    db: Session, *, logistics_id: int, staff: Staff, commit: bool = True
) -> None:
    logistics = get_logistics_or_404(db, logistics_id)
    referenced = _referenced_tables(db, logistics.id)
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"物流方式已被 {','.join(sorted(referenced))} 引用，不能删除，请改为停用",
        )
    db.delete(logistics)
    write_audit(
        db,
        action="DELETE",
        target_type="logistics",
        target_id=str(logistics.id),
        summary=f"删除物流方式：{logistics.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 运费模板
# --------------------------------------------------------------------------- #


def freight_template_dict(db: Session, template: FreightTemplate) -> dict:
    logistics = db.get(Logistics, template.logistics_id)
    return {
        "id": template.id,
        "name": template.name,
        "logistics_id": template.logistics_id,
        "logistics_name": logistics.name if logistics else None,
        "charge_type": template.charge_type,
        "first_count": template.first_count,
        "continue_count": template.continue_count,
        "first_weight": template.first_weight,
        "continue_weight": template.continue_weight,
        "base_fee": template.base_fee,
        "continue_fee": template.continue_fee,
        "deduct_weight": template.deduct_weight,
        "operation_fee": template.operation_fee,
        "consumable_fee": template.consumable_fee,
        "ewaybill_fee": template.ewaybill_fee,
        "tax_fee": template.tax_fee,
        "tax_point": template.tax_point,
    }


def get_freight_template_or_404(
    db: Session, template_id: int, *, company_id: int | None = None,
) -> FreightTemplate:
    stmt = select(FreightTemplate).where(FreightTemplate.id == template_id)
    if company_id is not None:
        stmt = stmt.where(FreightTemplate.company_id == company_id)
    template = db.scalar(stmt)
    if template is None:
        raise BizException(ErrorCode.NOT_FOUND, "运费模板不存在")
    return template


def _freight_values(
    db: Session,
    data: dict,
    *,
    existing: FreightTemplate | None = None,
) -> dict:
    _check_unknown_fields(data, FREIGHT_TEMPLATE_FIELDS)
    values: dict[str, Any] = {}

    if existing is None or "name" in data:
        values["name"] = _require_text(data.get("name"), "运费模板名称", 64)
    if existing is None or "logistics_id" in data:
        logistics_id = _optional_id(data.get("logistics_id"), "物流方式")
        if logistics_id is None:
            raise BizException(ErrorCode.BAD_REQUEST, "物流方式不能为空")
        get_logistics_or_404(db, logistics_id)
        values["logistics_id"] = logistics_id

    charge_type = str(
        data.get("charge_type", existing.charge_type if existing else "BY_COUNT")
    ).upper()
    if charge_type not in CHARGE_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST, "计费方式仅支持 BY_COUNT / BY_WEIGHT"
        )
    values["charge_type"] = charge_type

    if charge_type == "BY_COUNT":
        first_count = _to_int(
            data.get("first_count", existing.first_count if existing else 1),
            "首件数量",
        )
        continue_count = _to_int(
            data.get("continue_count", existing.continue_count if existing else 1),
            "续件数量",
        )
        if first_count <= 0 or continue_count <= 0:
            raise BizException(ErrorCode.BAD_REQUEST, "首件和续件数量必须大于 0")
        values.update(
            {
                "first_count": first_count,
                "continue_count": continue_count,
                "first_weight": None,
                "continue_weight": None,
                "deduct_weight": None,
            }
        )
    else:
        first_weight = _to_decimal(
            data.get("first_weight", existing.first_weight if existing else None),
            "首重",
            quant=WEIGHT_QUANT,
            allow_zero=False,
        )
        continue_weight = _to_decimal(
            data.get(
                "continue_weight",
                existing.continue_weight if existing else None,
            ),
            "续重",
            quant=WEIGHT_QUANT,
            allow_zero=False,
        )
        deduct_weight = _to_decimal(
            data.get(
                "deduct_weight",
                existing.deduct_weight if existing and existing.deduct_weight is not None else 0,
            ),
            "扣减重量",
            quant=WEIGHT_QUANT,
        )
        values.update(
            {
                "first_count": 1,
                "continue_count": 1,
                "first_weight": first_weight,
                "continue_weight": continue_weight,
                "deduct_weight": deduct_weight,
            }
        )

    for field, label in (
        ("base_fee", "首费"),
        ("continue_fee", "续费"),
        ("operation_fee", "操作费"),
        ("consumable_fee", "耗材费"),
        ("ewaybill_fee", "电子面单费"),
        ("tax_fee", "税费"),
    ):
        default = getattr(existing, field) if existing is not None else Decimal("0")
        values[field] = _to_money(data.get(field, default), label)

    tax_point_value = data.get(
        "tax_point", existing.tax_point if existing else None
    )
    tax_point = (
        _to_decimal(tax_point_value, "税点", quant=Decimal("0.01"))
        if tax_point_value not in (None, "")
        else None
    )
    if tax_point is not None and tax_point > 100:
        raise BizException(ErrorCode.BAD_REQUEST, "税点不能超过 100")
    values["tax_point"] = tax_point
    return values


def create_freight_template(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> FreightTemplate:
    values = _freight_values(db, data)
    template = FreightTemplate(**values)
    db.add(template)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="freight_template",
        target_id=str(template.id),
        summary=f"新增运费模板：{template.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return template


def update_freight_template(
    db: Session,
    *,
    template_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> FreightTemplate:
    template = get_freight_template_or_404(db, template_id)
    values = _freight_values(db, data, existing=template)
    for field, value in values.items():
        setattr(template, field, value)
    write_audit(
        db,
        action="UPDATE",
        target_type="freight_template",
        target_id=str(template.id),
        summary=f"修改运费模板：{template.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return template


def list_freight_templates(
    db: Session, *, logistics_id: int | None = None
) -> list[FreightTemplate]:
    conditions = []
    if logistics_id is not None:
        get_logistics_or_404(db, logistics_id)
        conditions.append(FreightTemplate.logistics_id == logistics_id)
    return list(
        db.scalars(
            select(FreightTemplate)
            .where(*conditions)
            .order_by(FreightTemplate.id)
        )
    )


def delete_freight_template(
    db: Session, *, template_id: int, staff: Staff, commit: bool = True
) -> None:
    template = get_freight_template_or_404(db, template_id)
    if db.scalar(
        select(func.count(AreaRule.id)).where(
            AreaRule.freight_template_id == template.id
        )
    ):
        raise BizException(ErrorCode.CONFLICT, "运费模板已被区域物流规则引用，不能删除")
    db.delete(template)
    write_audit(
        db,
        action="DELETE",
        target_type="freight_template",
        target_id=str(template.id),
        summary=f"删除运费模板：{template.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def calculate_freight(
    db: Session,
    *,
    template_id: int,
    count: int | None = None,
    weight: Any = None,
    company_id: int | None = None,
) -> dict:
    template = get_freight_template_or_404(
        db, template_id, company_id=company_id,
    )
    if template.charge_type == "BY_COUNT":
        if count is None:
            raise BizException(ErrorCode.BAD_REQUEST, "按件计费必须传入件数")
        billed_count = _to_int(count, "件数")
        if billed_count <= 0:
            raise BizException(ErrorCode.BAD_REQUEST, "件数必须大于 0")
        charge_weight = None
        additional_units = 0
        if billed_count > template.first_count:
            remaining = billed_count - template.first_count
            additional_units = (remaining + template.continue_count - 1) // template.continue_count
        quantity_label = "件数"
        quantity_value = billed_count
    else:
        if weight is None:
            raise BizException(ErrorCode.BAD_REQUEST, "按重计费必须传入重量")
        gross_weight = _to_decimal(
            weight, "重量", quant=WEIGHT_QUANT, allow_zero=False
        )
        charge_weight = max(
            gross_weight - (template.deduct_weight or Decimal("0")),
            Decimal("0"),
        )
        additional_units = 0
        if charge_weight > template.first_weight:
            remaining = charge_weight - template.first_weight
            additional_units = int(
                (remaining / template.continue_weight).to_integral_value(
                    rounding=ROUND_CEILING
                )
            )
        quantity_label = "重量"
        quantity_value = gross_weight

    base_fee = round_money(template.base_fee)
    additional_fee = round_money(Decimal(additional_units) * template.continue_fee)
    freight_fee = round_money(base_fee + additional_fee)
    operation_fee = round_money(template.operation_fee)
    consumable_fee = round_money(template.consumable_fee)
    ewaybill_fee = round_money(template.ewaybill_fee)
    fixed_tax_fee = round_money(template.tax_fee)
    tax_point = template.tax_point or Decimal("0")
    tax_base = round_money(
        freight_fee + operation_fee + consumable_fee + ewaybill_fee
    )
    percentage_tax = round_money(tax_base * tax_point / Decimal("100"))
    tax_amount = round_money(percentage_tax + fixed_tax_fee)
    total_fee = round_money(tax_base + tax_amount)

    return {
        "template_id": template.id,
        "template_name": template.name,
        "charge_type": template.charge_type,
        "quantity_label": quantity_label,
        "quantity": quantity_value,
        "charge_weight": charge_weight,
        "additional_units": additional_units,
        "base_fee": base_fee,
        "additional_fee": additional_fee,
        "freight_fee": freight_fee,
        "operation_fee": operation_fee,
        "consumable_fee": consumable_fee,
        "ewaybill_fee": ewaybill_fee,
        "tax_point": tax_point,
        "percentage_tax": percentage_tax,
        "fixed_tax_fee": fixed_tax_fee,
        "tax_amount": tax_amount,
        "total_fee": total_fee,
    }


# --------------------------------------------------------------------------- #
# 区域物流规则
# --------------------------------------------------------------------------- #


def area_rule_dict(
    db: Session, rule: AreaRule, *, company_id: int | None = None,
) -> dict:
    logistics_stmt = select(Logistics).where(Logistics.id == rule.logistics_id)
    template_stmt = select(FreightTemplate).where(
        FreightTemplate.id == rule.freight_template_id,
    ) if rule.freight_template_id else None
    if company_id is not None:
        logistics_stmt = logistics_stmt.where(Logistics.company_id == company_id)
        if template_stmt is not None:
            template_stmt = template_stmt.where(FreightTemplate.company_id == company_id)
    logistics = db.scalar(logistics_stmt)
    template = db.scalar(template_stmt) if template_stmt is not None else None
    return {
        "id": rule.id,
        "logistics_id": rule.logistics_id,
        "logistics_name": logistics.name if logistics else None,
        "province": rule.province,
        "city": rule.city,
        "match_weight_min": rule.match_weight_min,
        "match_weight_max": rule.match_weight_max,
        "freight_template_id": rule.freight_template_id,
        "freight_template_name": template.name if template else None,
    }


def get_area_rule_or_404(
    db: Session, rule_id: int, *, company_id: int | None = None,
) -> AreaRule:
    stmt = select(AreaRule).where(AreaRule.id == rule_id)
    if company_id is not None:
        stmt = stmt.where(AreaRule.company_id == company_id)
    rule = db.scalar(stmt)
    if rule is None:
        raise BizException(ErrorCode.NOT_FOUND, "区域物流规则不存在")
    return rule


def _area_rule_values(
    db: Session, data: dict, *, existing: AreaRule | None = None
) -> dict:
    _check_unknown_fields(data, AREA_RULE_FIELDS)
    values: dict[str, Any] = {}

    if existing is None or "logistics_id" in data:
        logistics_id = _optional_id(data.get("logistics_id"), "物流方式")
        if logistics_id is None:
            raise BizException(ErrorCode.BAD_REQUEST, "物流方式不能为空")
        get_logistics_or_404(db, logistics_id)
        values["logistics_id"] = logistics_id

    if "province" in data:
        values["province"] = _optional_text(data["province"], "省", 32)
    if "city" in data:
        values["city"] = _optional_text(data["city"], "市", 32)
    if "match_weight_min" in data:
        values["match_weight_min"] = _optional_weight(
            data["match_weight_min"], "匹配重量下限"
        )
    if "match_weight_max" in data:
        values["match_weight_max"] = _optional_weight(
            data["match_weight_max"], "匹配重量上限"
        )
    if "freight_template_id" in data:
        template_id = _optional_id(data["freight_template_id"], "运费模板")
        values["freight_template_id"] = template_id

    merged = {
        "logistics_id": values.get(
            "logistics_id", existing.logistics_id if existing else None
        ),
        "match_weight_min": values.get(
            "match_weight_min",
            existing.match_weight_min if existing else None,
        ),
        "match_weight_max": values.get(
            "match_weight_max",
            existing.match_weight_max if existing else None,
        ),
        "freight_template_id": values.get(
            "freight_template_id",
            existing.freight_template_id if existing else None,
        ),
    }
    min_weight = merged["match_weight_min"]
    max_weight = merged["match_weight_max"]
    if (
        min_weight is not None
        and max_weight is not None
        and min_weight > max_weight
    ):
        raise BizException(ErrorCode.BAD_REQUEST, "匹配重量下限不能大于上限")

    template_id = merged["freight_template_id"]
    if template_id is not None:
        template = get_freight_template_or_404(db, template_id)
        if template.logistics_id != merged["logistics_id"]:
            raise BizException(
                ErrorCode.BAD_REQUEST, "运费模板不属于所选物流方式"
            )
    return values


def create_area_rule(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> AreaRule:
    values = _area_rule_values(db, data)
    rule = AreaRule(**values)
    db.add(rule)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="area_rule",
        target_id=str(rule.id),
        summary=f"新增区域物流规则：{rule.province or '全部'}{rule.city or ''}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return rule


def update_area_rule(
    db: Session,
    *,
    rule_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> AreaRule:
    rule = get_area_rule_or_404(db, rule_id)
    values = _area_rule_values(db, data, existing=rule)
    for field, value in values.items():
        setattr(rule, field, value)
    write_audit(
        db,
        action="UPDATE",
        target_type="area_rule",
        target_id=str(rule.id),
        summary=f"修改区域物流规则：{rule.province or '全部'}{rule.city or ''}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return rule


def list_area_rules(
    db: Session, *, logistics_id: int | None = None,
    company_id: int | None = None,
) -> list[AreaRule]:
    conditions = []
    if company_id is not None:
        conditions.append(AreaRule.company_id == company_id)
    if logistics_id is not None:
        get_logistics_or_404(db, logistics_id, company_id=company_id)
        conditions.append(AreaRule.logistics_id == logistics_id)
    return list(
        db.scalars(select(AreaRule).where(*conditions).order_by(AreaRule.id))
    )


def delete_area_rule(
    db: Session, *, rule_id: int, staff: Staff, commit: bool = True
) -> None:
    rule = get_area_rule_or_404(db, rule_id)
    db.delete(rule)
    write_audit(
        db,
        action="DELETE",
        target_type="area_rule",
        target_id=str(rule.id),
        summary=f"删除区域物流规则：{rule.province or '全部'}{rule.city or ''}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def match_area_rule(
    db: Session,
    *,
    logistics_id: int,
    province: str | None = None,
    city: str | None = None,
    weight: Any = None,
    company_id: int | None = None,
) -> dict:
    get_logistics_or_404(db, logistics_id, company_id=company_id)
    province_value = _optional_text(province, "省", 32)
    city_value = _optional_text(city, "市", 32)
    weight_value = (
        _to_decimal(weight, "重量", quant=WEIGHT_QUANT)
        if weight not in (None, "")
        else None
    )

    best: AreaRule | None = None
    best_score = -1
    for rule in db.scalars(
        select(AreaRule)
        .where(
            AreaRule.logistics_id == logistics_id,
            *([AreaRule.company_id == company_id] if company_id is not None else []),
        )
        .order_by(AreaRule.id)
    ):
        if rule.province is not None and rule.province != province_value:
            continue
        if rule.city is not None and rule.city != city_value:
            continue
        if rule.match_weight_min is not None and (
            weight_value is None or weight_value < rule.match_weight_min
        ):
            continue
        if rule.match_weight_max is not None and (
            weight_value is None or weight_value > rule.match_weight_max
        ):
            continue

        score = 0
        if rule.province is not None:
            score += 1
        if rule.city is not None:
            score += 2
        if score > best_score:
            best = rule
            best_score = score

    if best is None:
        return {
            "matched": False,
            "logistics_id": logistics_id,
            "province": province_value,
            "city": city_value,
            "weight": weight_value,
            "area_rule": None,
            "freight_template": None,
        }

    template = (
        get_freight_template_or_404(db, best.freight_template_id)
        if best.freight_template_id
        else None
    )
    return {
        "matched": True,
        "logistics_id": logistics_id,
        "province": province_value,
        "city": city_value,
        "weight": weight_value,
        "area_rule": area_rule_dict(db, best),
        "freight_template": (
            freight_template_dict(db, template) if template is not None else None
        ),
    }


# --------------------------------------------------------------------------- #
# 打印模板
# --------------------------------------------------------------------------- #


def print_template_dict(template: PrintTemplate) -> dict:
    return {
        "id": template.id,
        "name": template.name,
        "template_type": template.template_type,
        "logistics_id": template.logistics_id,
        "content": template.content,
        "paper_width": template.paper_width,
        "paper_height": template.paper_height,
        "is_default": template.is_default,
    }


def get_print_template_or_404(
    db: Session, template_id: int, *, company_id: int | None = None,
) -> PrintTemplate:
    stmt = select(PrintTemplate).where(PrintTemplate.id == template_id)
    if company_id is not None:
        stmt = stmt.where(PrintTemplate.company_id == company_id)
    template = db.scalar(stmt)
    if template is None:
        raise BizException(ErrorCode.NOT_FOUND, "打印模板不存在")
    return template


def _print_template_values(
    db: Session, data: dict, *, existing: PrintTemplate | None = None
) -> dict:
    _check_unknown_fields(data, PRINT_TEMPLATE_FIELDS)
    values: dict[str, Any] = {}
    if existing is None or "name" in data:
        values["name"] = _require_text(data.get("name"), "模板名称", 64)
    if existing is None or "template_type" in data:
        values["template_type"] = _require_text(
            data.get("template_type"), "模板类型", 32
        )
    if "logistics_id" in data:
        logistics_id = _optional_id(data["logistics_id"], "物流方式")
        if logistics_id is not None:
            get_logistics_or_404(db, logistics_id)
        values["logistics_id"] = logistics_id
    if "content" in data:
        values["content"] = (
            str(data["content"]) if data["content"] not in (None, "") else None
        )
    for field, label in (("paper_width", "纸张宽度"), ("paper_height", "纸张高度")):
        if field in data:
            value = data[field]
            values[field] = (
                _to_decimal(value, label, quant=PAPER_QUANT, allow_zero=False)
                if value not in (None, "")
                else None
            )
    if "is_default" in data:
        values["is_default"] = _to_bool(data["is_default"], "默认模板")
    return values


def _reset_default_print_templates(
    db: Session,
    *,
    template_type: str,
    logistics_id: int | None,
    keep_id: int,
) -> None:
    stmt = select(PrintTemplate).where(
        PrintTemplate.template_type == template_type,
        PrintTemplate.id != keep_id,
    )
    stmt = (
        stmt.where(PrintTemplate.logistics_id.is_(None))
        if logistics_id is None
        else stmt.where(PrintTemplate.logistics_id == logistics_id)
    )
    for row in db.scalars(stmt):
        row.is_default = False


def create_print_template(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> PrintTemplate:
    values = _print_template_values(db, data)
    values.setdefault("logistics_id", None)
    values.setdefault("is_default", False)
    template = PrintTemplate(**values)
    db.add(template)
    db.flush()
    if template.is_default:
        _reset_default_print_templates(
            db,
            template_type=template.template_type,
            logistics_id=template.logistics_id,
            keep_id=template.id,
        )
    write_audit(
        db,
        action="CREATE",
        target_type="print_template",
        target_id=str(template.id),
        summary=f"新增打印模板：{template.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return template


def update_print_template(
    db: Session,
    *,
    template_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PrintTemplate:
    template = get_print_template_or_404(db, template_id)
    values = _print_template_values(db, data, existing=template)
    for field, value in values.items():
        setattr(template, field, value)
    if template.is_default:
        _reset_default_print_templates(
            db,
            template_type=template.template_type,
            logistics_id=template.logistics_id,
            keep_id=template.id,
        )
    write_audit(
        db,
        action="UPDATE",
        target_type="print_template",
        target_id=str(template.id),
        summary=f"修改打印模板：{template.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return template


def list_print_templates(
    db: Session,
    *,
    logistics_id: int | None = None,
    template_type: str | None = None,
) -> list[PrintTemplate]:
    conditions = []
    if logistics_id is not None:
        get_logistics_or_404(db, logistics_id)
        conditions.append(PrintTemplate.logistics_id == logistics_id)
    if template_type:
        conditions.append(PrintTemplate.template_type == template_type)
    return list(
        db.scalars(
            select(PrintTemplate)
            .where(*conditions)
            .order_by(PrintTemplate.id)
        )
    )


def delete_print_template(
    db: Session, *, template_id: int, staff: Staff, commit: bool = True
) -> None:
    template = get_print_template_or_404(db, template_id)
    db.delete(template)
    write_audit(
        db,
        action="DELETE",
        target_type="print_template",
        target_id=str(template.id),
        summary=f"删除打印模板：{template.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
