"""采购订单服务（TASK-025）。

覆盖计划转订单、采购订单 CRUD、财务审批、附件、Excel 导入导出、PDF
以及到货/差异数量回写。入库单通过 ``record_arrival`` 更新明细到货数量，
服务同时校验入库仓库必须与采购订单指定仓库一致。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import apply_owner_scope, document_visible, require_scope_policy, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Brand, Unit
from app.models.org import Staff
from app.models.party import Supplier
from app.models.product import Product, ProductSku
from app.models.purchase import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasePlan,
    PurchasePlanItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, party_service, purchase_plan_service
from app.services.excel import (
    Column,
    ParseResult,
    SheetSpec,
    export_data,
    parse_data,
    register_template,
)
from app.services.pdf import PdfDocument, PdfTable, render_pdf

DOC_TYPE = "purchase_order"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_TERMINATED = "TERMINATED"
STATUS_RECEIVING = "RECEIVING"
STATUS_RECEIVED = "RECEIVED"
STATUS_CLOSED = "CLOSED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0")
MAX_ATTACHMENTS = 50
MAX_ORDER_ITEMS = 500

ORDER_FIELDS = {
    "supplier_id",
    "warehouse_id",
    "purchaser_id",
    "description",
    "attachments",
    "items",
}

PURCHASE_ORDER_TEMPLATE = SheetSpec(
    name="purchase_order",
    sheet_title="采购订单",
    columns=[
        Column("supplier_code", "供应商编码", required=True, width=20),
        Column("warehouse_code", "入库仓库编码", width=20),
        Column("purchaser_phone", "采购人员手机号", width=18),
        Column("description", "采购描述", width=30),
        Column("product_code", "商品编码", required=True, width=20),
        Column("sku_code", "规格编码", width=20),
        Column("purchase_price", "采购价", type="decimal", width=14),
        Column("qty", "采购数量", required=True, type="decimal", width=14),
        Column("attachments", "附件地址（多个用|分隔）", width=40),
    ],
)

PURCHASE_ORDER_EXPORT_TEMPLATE = SheetSpec(
    name="purchase_order_export",
    sheet_title="采购订单",
    columns=[
        Column("order_no", "采购订单号", width=22),
        Column("plan_no", "采购计划编号", width=22),
        Column("supplier_name", "供应商", width=24),
        Column("warehouse_name", "入库仓库", width=18),
        Column("total_qty", "采购总数量", type="decimal", width=14),
        Column("arrived_qty", "到货数量", type="decimal", width=14),
        Column("difference_qty", "差异数量", type="decimal", width=14),
        Column("total_amount", "采购金额", type="decimal", width=14),
        Column("status", "单据状态", width=16),
        Column("audit_status", "审核状态", width=16),
        Column("purchaser_name", "采购员", width=16),
        Column("created_at", "制单时间", width=22),
        Column("description", "采购描述", width=30),
    ],
)

register_template(PURCHASE_ORDER_TEMPLATE.name, PURCHASE_ORDER_TEMPLATE)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}"
        )


def _to_int_id(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc
    if result < 1:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_int_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int_id(value, label)


def _to_qty(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result <= ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _to_price(value: Any, label: str) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result <= 0:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}")
    return text


def _normalize_attachments(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    parsed = value
    if isinstance(value, str):
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            loaded = [
                part.strip()
                for part in value.replace("\n", "|").replace(",", "|").split("|")
                if part.strip()
            ]
        parsed = loaded
    if not isinstance(parsed, (list, tuple)):
        raise BizException(ErrorCode.BAD_REQUEST, "附件必须为地址数组")
    result: list[str] = []
    for raw in parsed:
        if raw in (None, ""):
            continue
        url = str(raw).strip()
        if not url:
            continue
        if len(url) > 255:
            raise BizException(ErrorCode.BAD_REQUEST, "附件地址长度不能超过 255")
        if url not in result:
            result.append(url)
    if len(result) > MAX_ATTACHMENTS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"采购订单附件不能超过 {MAX_ATTACHMENTS} 个"
        )
    return result


def _attachments_from_db(order: PurchaseOrder) -> list[str]:
    if not order.attachments:
        return []
    try:
        return _normalize_attachments(order.attachments)
    except BizException:
        return []


def _attachments_to_db(value: Any) -> str | None:
    attachments = _normalize_attachments(value)
    return json.dumps(attachments, ensure_ascii=False) if attachments else None


def get_order_or_404(
    db: Session,
    order_id: int,
    *,
    check_scope: bool = True,
    company_id: int | None = None,
) -> PurchaseOrder:
    stmt = select(PurchaseOrder).where(PurchaseOrder.id == order_id)
    if company_id is not None:
        stmt = stmt.where(PurchaseOrder.company_id == company_id)
    order = db.scalar(stmt)
    if order is None or (check_scope and not document_visible(db, order.created_by, DOC_TYPE, order.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购订单不存在")
    return order


def _get_staff_or_404(db: Session, staff_id: int) -> Staff:
    staff = db.get(Staff, staff_id)
    if staff is None or not staff.is_active:
        raise BizException(ErrorCode.BAD_REQUEST, "采购人员不存在或已停用")
    return staff


def _validate_warehouse(
    db: Session, warehouse_id: int | None
) -> Warehouse | None:
    if warehouse_id is None:
        return None
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise BizException(ErrorCode.BAD_REQUEST, "入库仓库不存在")
    if not warehouse.is_enabled or warehouse.is_virtual:
        raise BizException(ErrorCode.BAD_REQUEST, "入库仓库必须为启用的实物仓")
    if warehouse.lock_type != "NONE":
        raise BizException(ErrorCode.CONFLICT, "入库仓库已锁定，不能选择")
    return warehouse


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    if product.is_disabled:
        raise BizException(ErrorCode.BAD_REQUEST, f"商品已禁用：{product.name}")
    return product


def _validate_sku(
    db: Session, product_id: int, sku_id: int | None
) -> ProductSku | None:
    if sku_id is None:
        return None
    sku = db.get(ProductSku, sku_id)
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")
    if not sku.is_active:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格已停用")
    return sku


def _normalize_item(
    db: Session, *, supplier_id: int, data: dict
) -> dict:
    _check_unknown_fields(
        data, {"product_id", "sku_id", "qty", "purchase_price"}
    )
    product_id = _to_int_id(data.get("product_id"), "商品")
    sku_id = _optional_int_id(data.get("sku_id"), "规格")
    product = _get_product_or_404(db, product_id)
    _validate_sku(db, product_id, sku_id)
    qty = _to_qty(data.get("qty"), "采购数量")
    if "purchase_price" in data and data.get("purchase_price") not in (None, ""):
        purchase_price = _to_price(data["purchase_price"], "采购价")
    else:
        purchase_price = purchase_plan_service.resolve_purchase_price(
            db, supplier_id=supplier_id, product=product, sku_id=sku_id
        )
    if purchase_price is None:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"无法确定商品采购价：{product.name}"
        )
    purchase_price = round_money(purchase_price)
    return {
        "product_id": product.id,
        "sku_id": sku_id,
        "brand_id": product.brand_id,
        "unit_id": product.main_unit_id,
        "purchase_price": purchase_price,
        "qty": qty,
        "in_transit_qty": qty,
        "arrived_qty": ZERO_QTY,
        "amount": round_money(qty * purchase_price),
    }


def _normalize_items(
    db: Session, *, supplier_id: int, raw_items: Any
) -> list[dict]:
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少添加一条采购商品明细")
    if len(raw_items) > MAX_ORDER_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"单张采购订单最多 {MAX_ORDER_ITEMS} 条明细"
        )
    result: list[dict] = []
    keys: set[tuple[int, int | None]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "采购商品明细格式错误")
        item = _normalize_item(db, supplier_id=supplier_id, data=raw)
        key = (item["product_id"], item["sku_id"])
        if key in keys:
            raise BizException(ErrorCode.BAD_REQUEST, "同一商品规格不能重复")
        keys.add(key)
        result.append(item)
    return result


def _item_key(item: PurchaseOrderItem | dict) -> tuple[int, int | None]:
    if isinstance(item, dict):
        return item["product_id"], item["sku_id"]
    return item.product_id, item.sku_id


def _item_qty(item: PurchaseOrderItem | dict) -> Decimal:
    return item["qty"] if isinstance(item, dict) else item.qty


def _plan_item_map(plan_id: int, db: Session) -> dict[tuple[int, int | None], PurchasePlanItem]:
    rows = db.scalars(
        select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan_id)
    )
    return {(row.product_id, row.sku_id): row for row in rows}


def _apply_plan_delta(
    db: Session,
    *,
    plan: PurchasePlan,
    old_items: Iterable[PurchaseOrderItem | dict],
    new_items: Iterable[PurchaseOrderItem | dict],
) -> None:
    if plan.status == "FINISHED":
        raise BizException(ErrorCode.CONFLICT, "关联采购计划已完成，不能调整采购订单")

    old_map = {_item_key(item): _item_qty(item) for item in old_items}
    new_map = {_item_key(item): _item_qty(item) for item in new_items}
    plan_map = _plan_item_map(plan.id, db)
    changes: list[tuple[PurchasePlanItem, Decimal]] = []
    for key in set(old_map) | set(new_map):
        delta = new_map.get(key, ZERO_QTY) - old_map.get(key, ZERO_QTY)
        if delta == ZERO_QTY:
            continue
        plan_item = plan_map.get(key)
        if plan_item is None:
            raise BizException(ErrorCode.BAD_REQUEST, "采购订单明细不属于关联采购计划")
        target = plan_item.purchased_qty + delta
        if target < ZERO_QTY:
            raise BizException(ErrorCode.CONFLICT, "采购计划已采购数量不能为负数")
        if target > plan_item.planned_qty:
            product = db.get(Product, key[0])
            label = product.name if product else str(key[0])
            raise BizException(
                ErrorCode.CONFLICT, f"{label}转采购数量超过计划剩余数量"
            )
        changes.append((plan_item, target))

    for plan_item, target in changes:
        plan_item.purchased_qty = target
    db.flush()


def _recalculate_order(db: Session, order: PurchaseOrder) -> None:
    items = list(
        db.scalars(
            select(PurchaseOrderItem).where(PurchaseOrderItem.order_id == order.id)
        )
    )
    order.total_qty = sum((item.qty for item in items), ZERO_QTY)
    order.total_amount = sum(
        (item.amount for item in items if item.amount is not None), ZERO_QTY
    )
    order.arrived_qty = sum((item.arrived_qty for item in items), ZERO_QTY)
    for item in items:
        item.in_transit_qty = max(item.qty - item.arrived_qty, ZERO_QTY)

    if order.audit_status == AUDIT_APPROVED and order.status not in {
        STATUS_TERMINATED,
        STATUS_CLOSED,
    }:
        if order.total_qty > ZERO_QTY and order.arrived_qty >= order.total_qty:
            order.status = STATUS_RECEIVED
        elif order.arrived_qty > ZERO_QTY:
            order.status = STATUS_RECEIVING
        else:
            order.status = STATUS_APPROVED
    db.flush()


def _order_items(
    db: Session, order_id: int, *, company_id: int | None = None,
) -> list[PurchaseOrderItem]:
    conditions = [PurchaseOrderItem.order_id == order_id]
    if company_id is not None:
        conditions.append(PurchaseOrderItem.company_id == company_id)
    return list(
        db.scalars(
            select(PurchaseOrderItem)
            .where(*conditions)
            .order_by(PurchaseOrderItem.id)
        )
    )


def order_item_dict(
    db: Session,
    item: PurchaseOrderItem,
    *,
    company_id: int | None = None,
) -> dict:
    def company_row(model, row_id):
        if row_id is None:
            return None
        stmt = select(model).where(model.id == row_id)
        if company_id is not None and hasattr(model, "company_id"):
            stmt = stmt.where(model.company_id == company_id)
        return db.scalar(stmt)

    product = company_row(Product, item.product_id)
    sku = company_row(ProductSku, item.sku_id)
    brand = company_row(Brand, item.brand_id)
    unit = company_row(Unit, item.unit_id)
    difference_qty = item.qty - item.arrived_qty
    current_stock = ZERO_QTY
    if item.sku_id is not None:
        current_stock = (
            db.scalar(
                select(func.sum(StockLocation.qty))
                .join(Location, Location.id == StockLocation.location_id)
                .join(Warehouse, Warehouse.id == Location.warehouse_id)
                .where(
                    StockLocation.product_sku_id == item.sku_id,
                    StockLocation.status == "NORMAL",
                    Warehouse.is_virtual.is_(False),
                    Warehouse.is_enabled.is_(True),
                    *([StockLocation.company_id == company_id] if company_id is not None else []),
                    *([Location.company_id == company_id] if company_id is not None else []),
                    *([Warehouse.company_id == company_id] if company_id is not None else []),
                )
            )
            or ZERO_QTY
        )
    in_transit_stock = (
        db.scalar(
            select(
                func.sum(PurchaseOrderItem.qty - PurchaseOrderItem.arrived_qty)
            )
            .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
            .where(
                PurchaseOrderItem.product_id == item.product_id,
                PurchaseOrderItem.sku_id.is_(None)
                if item.sku_id is None
                else PurchaseOrderItem.sku_id == item.sku_id,
                PurchaseOrderItem.qty > PurchaseOrderItem.arrived_qty,
                PurchaseOrder.id != item.order_id,
                PurchaseOrder.audit_status == AUDIT_APPROVED,
                PurchaseOrder.status.in_([STATUS_APPROVED, STATUS_RECEIVING]),
                *([PurchaseOrder.company_id == company_id] if company_id is not None else []),
                *([PurchaseOrderItem.company_id == company_id] if company_id is not None else []),
                *scope_conditions(db, PurchaseOrder.created_by),
            )
        )
        or ZERO_QTY
    )
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "art_no": product.art_no if product else None,
        "sku_id": item.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "brand_id": item.brand_id,
        "brand_name": brand.name if brand else None,
        "unit_id": item.unit_id,
        "unit_name": unit.name if unit else None,
        "purchase_price": item.purchase_price,
        "qty": item.qty,
        "in_transit_qty": max(item.qty - item.arrived_qty, ZERO_QTY),
        "current_stock": Decimal(current_stock),
        "in_transit_stock": Decimal(in_transit_stock),
        "arrived_qty": item.arrived_qty,
        "difference_qty": difference_qty,
        "amount": item.amount,
    }


def _arrival_status(order: PurchaseOrder) -> str:
    if order.arrived_qty <= ZERO_QTY:
        return "PENDING"
    if order.arrived_qty < order.total_qty:
        return "PARTIAL"
    return "FULL"


def order_dict(
    db: Session,
    order: PurchaseOrder,
    *,
    with_items: bool = True,
    with_approval: bool = False,
    company_id: int | None = None,
) -> dict:
    company_id = company_id or require_scope_policy(db).company_id
    if order.company_id != company_id:
        raise BizException(ErrorCode.NOT_FOUND, "采购订单不存在")

    def company_row(model, row_id):
        if row_id is None:
            return None
        stmt = select(model).where(model.id == row_id)
        if hasattr(model, "company_id"):
            stmt = stmt.where(model.company_id == company_id)
        return db.scalar(stmt)

    supplier = company_row(Supplier, order.supplier_id)
    warehouse = company_row(Warehouse, order.warehouse_id)
    purchaser = company_row(Staff, order.purchaser_id)
    auditor = company_row(Staff, order.auditor_id)
    plan = company_row(PurchasePlan, order.plan_id)
    if plan and not document_visible(db, plan.created_by, "purchase_plan", plan.id):
        plan = None
    if purchaser and purchaser.company_id != company_id:
        purchaser = None
    if auditor and auditor.company_id != company_id:
        auditor = None
    difference_qty = order.total_qty - order.arrived_qty
    data = {
        "id": order.id,
        "order_no": order.order_no,
        "plan_id": plan.id if plan else None,
        "plan_no": plan.plan_no if plan else None,
        "supplier_id": order.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "warehouse_id": order.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "purchaser_id": purchaser.id if purchaser else None,
        "purchaser_name": purchaser.name if purchaser else None,
        "status": order.status,
        "audit_status": order.audit_status,
        "arrival_status": _arrival_status(order),
        "total_qty": order.total_qty,
        "arrived_qty": order.arrived_qty,
        "difference_qty": difference_qty,
        "total_amount": order.total_amount,
        "prepay_amount": order.prepay_amount,
        "description": order.description,
        "attachments": _attachments_from_db(order),
        "inbound_warehouse_locked": order.warehouse_id is not None,
        "created_by": order.created_by,
        "auditor_id": auditor.id if auditor else None,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": order.audit_time,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
    if with_items:
        data["items"] = [
            order_item_dict(db, item, company_id=company_id)
            for item in _order_items(db, order.id, company_id=company_id)
        ]
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=DOC_TYPE, doc_id=order.id
        )
    return data


def list_orders(
    db: Session,
    *,
    plan_id: int | None = None,
    supplier_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    company_id: int | None = None,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if company_id is not None:
        conditions.append(PurchaseOrder.company_id == company_id)
    if plan_id is not None:
        purchase_plan_service.get_plan_or_404(db, plan_id)
        conditions.append(PurchaseOrder.plan_id == plan_id)
    if supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == supplier_id)
    if warehouse_id is not None:
        conditions.append(PurchaseOrder.warehouse_id == warehouse_id)
    if status:
        conditions.append(PurchaseOrder.status == status.upper())
    if audit_status:
        conditions.append(PurchaseOrder.audit_status == audit_status.upper())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                PurchaseOrder.order_no.like(like),
                PurchaseOrder.supplier_id.in_(
                    select(Supplier.id).where(Supplier.name.like(like))
                ),
                PurchaseOrder.plan_id.in_(
                    apply_owner_scope(
                        select(PurchasePlan.id).where(PurchasePlan.plan_no.like(like)),
                        PurchasePlan.created_by, db,
                    )
                ),
            )
        )
    total = db.scalar(
        apply_owner_scope(
            select(func.count(PurchaseOrder.id)).where(*conditions),
            PurchaseOrder.created_by,
            db,
        )
    ) or 0
    rows = list(
        db.scalars(
            apply_owner_scope(
                select(PurchaseOrder)
                .where(*conditions)
                .order_by(PurchaseOrder.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size),
                PurchaseOrder.created_by,
                db,
            )
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            order_dict(
                db, row, with_items=False, with_approval=False,
                company_id=company_id,
            )
            for row in rows
        ],
    }


def _create_order_record(
    db: Session,
    *,
    supplier: Supplier,
    warehouse: Warehouse | None,
    purchaser: Staff,
    description: str | None,
    attachments: list[str],
    items: list[dict],
    staff: Staff,
    plan_id: int | None = None,
    commit: bool = True,
) -> PurchaseOrder:
    order = PurchaseOrder(
        company_id=staff.company_id,
        order_no=generate_serial(SerialPrefix.PURCHASE_ORDER, dt=_now()),
        plan_id=plan_id,
        supplier_id=supplier.id,
        warehouse_id=warehouse.id if warehouse else None,
        purchaser_id=purchaser.id,
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        total_qty=ZERO_QTY,
        total_amount=Decimal("0.00"),
        arrived_qty=ZERO_QTY,
        prepay_amount=Decimal("0.00"),
        description=description,
        attachments=_attachments_to_db(attachments),
        created_by=staff.id,
    )
    db.add(order)
    db.flush()
    for item in items:
        db.add(PurchaseOrderItem(company_id=order.company_id, order_id=order.id, **item))
    db.flush()
    _recalculate_order(db, order)
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"新增采购订单：{order.order_no}",
        detail={
            "supplier_id": supplier.id,
            "warehouse_id": warehouse.id if warehouse else None,
            "item_count": len(items),
            "plan_id": plan_id,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def create_order(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> PurchaseOrder:
    _check_unknown_fields(data, ORDER_FIELDS)
    supplier_id = _to_int_id(data.get("supplier_id"), "供应商")
    supplier = party_service.get_supplier_or_404(db, supplier_id)
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用，不能创建采购订单")
    warehouse = _validate_warehouse(
        db, _optional_int_id(data.get("warehouse_id"), "入库仓库")
    )
    purchaser = (
        _get_staff_or_404(db, _to_int_id(data["purchaser_id"], "采购人员"))
        if data.get("purchaser_id") not in (None, "")
        else staff
    )
    items = _normalize_items(db, supplier_id=supplier.id, raw_items=data.get("items"))
    return _create_order_record(
        db,
        supplier=supplier,
        warehouse=warehouse,
        purchaser=purchaser,
        description=_optional_text(data.get("description"), "采购描述", 255),
        attachments=_normalize_attachments(data.get("attachments")),
        items=items,
        staff=staff,
        commit=commit,
    )


def create_order_from_plan(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchaseOrder:
    _check_unknown_fields(
        data,
        {
            "plan_id",
            "warehouse_id",
            "purchaser_id",
            "description",
            "attachments",
            "items",
        },
    )
    plan_id = _to_int_id(data.get("plan_id"), "采购计划")
    plan = db.get(PurchasePlan, plan_id)
    if plan is None:
        raise BizException(ErrorCode.NOT_FOUND, "采购计划不存在")
    if plan.audit_status != AUDIT_APPROVED or plan.status != "IN_PROGRESS":
        raise BizException(ErrorCode.CONFLICT, "仅审核通过的进行中采购计划可以转采购订单")

    supplier = party_service.get_supplier_or_404(db, plan.supplier_id)
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用，不能转采购订单")
    warehouse = _validate_warehouse(
        db, _optional_int_id(data.get("warehouse_id"), "入库仓库")
    )
    purchaser = (
        _get_staff_or_404(db, _to_int_id(data["purchaser_id"], "采购人员"))
        if data.get("purchaser_id") not in (None, "")
        else staff
    )
    plan_items = _plan_item_map(plan.id, db)
    raw_items = data.get("items")
    if raw_items is None:
        raw_items = [
            {
                "plan_item_id": item.id,
                "qty": item.planned_qty - item.purchased_qty,
            }
            for item in plan_items.values()
            if item.planned_qty > item.purchased_qty
        ]
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "采购计划没有可转单的剩余数量")

    allocations: list[tuple[PurchasePlanItem, Decimal]] = []
    items: list[dict] = []
    keys: set[tuple[int, int | None]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "转单明细格式错误")
        _check_unknown_fields(raw, {"plan_item_id", "qty"})
        plan_item_id = _to_int_id(raw.get("plan_item_id"), "采购计划明细")
        plan_item = next(
            (item for item in plan_items.values() if item.id == plan_item_id), None
        )
        if plan_item is None:
            raise BizException(ErrorCode.BAD_REQUEST, "采购计划明细不属于当前计划")
        remaining = plan_item.planned_qty - plan_item.purchased_qty
        qty = (
            _to_qty(raw.get("qty"), "转采购数量")
            if raw.get("qty") not in (None, "")
            else remaining
        )
        if qty > remaining:
            raise BizException(ErrorCode.CONFLICT, "转采购数量超过计划剩余数量")
        key = (plan_item.product_id, plan_item.sku_id)
        if key in keys:
            raise BizException(ErrorCode.BAD_REQUEST, "同一商品规格不能重复转单")
        keys.add(key)
        product = _get_product_or_404(db, plan_item.product_id)
        _validate_sku(db, plan_item.product_id, plan_item.sku_id)
        purchase_price = (
            round_money(plan_item.purchase_price)
            if plan_item.purchase_price is not None
            else purchase_plan_service.resolve_purchase_price(
                db,
                supplier_id=supplier.id,
                product=product,
                sku_id=plan_item.sku_id,
            )
        )
        if purchase_price is None:
            raise BizException(
                ErrorCode.BAD_REQUEST, f"无法确定商品采购价：{product.name}"
            )
        items.append(
            {
                "product_id": plan_item.product_id,
                "sku_id": plan_item.sku_id,
                "brand_id": product.brand_id,
                "unit_id": product.main_unit_id,
                "purchase_price": purchase_price,
                "qty": qty,
                "in_transit_qty": qty,
                "arrived_qty": ZERO_QTY,
                "amount": round_money(qty * purchase_price),
            }
        )
        allocations.append((plan_item, plan_item.purchased_qty + qty))

    for plan_item, new_purchased_qty in allocations:
        plan_item.purchased_qty = new_purchased_qty
    db.flush()
    return _create_order_record(
        db,
        supplier=supplier,
        warehouse=warehouse,
        purchaser=purchaser,
        description=_optional_text(data.get("description"), "采购描述", 255),
        attachments=_normalize_attachments(data.get("attachments")),
        items=items,
        staff=staff,
        plan_id=plan.id,
        commit=commit,
    )


def _approval_state(db: Session, order_id: int) -> dict:
    return approval_service.get_state(
        db, doc_type=DOC_TYPE, doc_id=order_id
    )["state"]


def _ensure_editable(db: Session, order: PurchaseOrder) -> None:
    if order.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前采购订单状态不允许修改")
    if order.arrived_qty > ZERO_QTY:
        raise BizException(ErrorCode.CONFLICT, "采购订单已有到货记录，不能修改")
    state = _approval_state(db, order.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "采购订单已进入审批流程，不能修改")


def update_order(
    db: Session,
    *,
    order_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchaseOrder:
    _check_unknown_fields(data, ORDER_FIELDS)
    order = get_order_or_404(db, order_id)
    _ensure_editable(db, order)

    supplier = db.get(Supplier, order.supplier_id)
    if "supplier_id" in data:
        supplier = party_service.get_supplier_or_404(
            db, _to_int_id(data["supplier_id"], "供应商")
        )
        if not supplier.is_active:
            raise BizException(ErrorCode.CONFLICT, "供应商已停用")
        order.supplier_id = supplier.id
    if "warehouse_id" in data:
        warehouse = _validate_warehouse(
            db, _optional_int_id(data["warehouse_id"], "入库仓库")
        )
        order.warehouse_id = warehouse.id if warehouse else None
    if "purchaser_id" in data:
        order.purchaser_id = _get_staff_or_404(
            db, _to_int_id(data["purchaser_id"], "采购人员")
        ).id
    if "description" in data:
        order.description = _optional_text(data["description"], "采购描述", 255)
    if "attachments" in data:
        order.attachments = _attachments_to_db(data["attachments"])

    if "items" in data:
        if supplier is None:
            raise BizException(ErrorCode.BAD_REQUEST, "供应商不存在")
        new_items = _normalize_items(
            db, supplier_id=supplier.id, raw_items=data["items"]
        )
        old_items = _order_items(db, order.id)
        if order.plan_id:
            plan = db.get(PurchasePlan, order.plan_id)
            if plan is None:
                raise BizException(ErrorCode.CONFLICT, "关联采购计划不存在")
            _apply_plan_delta(
                db, plan=plan, old_items=old_items, new_items=new_items
            )
        for item in old_items:
            db.delete(item)
        db.flush()
        for item in new_items:
            db.add(PurchaseOrderItem(company_id=order.company_id, order_id=order.id, **item))
        db.flush()

    _recalculate_order(db, order)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"修改采购订单：{order.order_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def _release_plan_allocations(db: Session, order: PurchaseOrder) -> None:
    if not order.plan_id:
        return
    plan = db.get(PurchasePlan, order.plan_id)
    if plan is None:
        return
    if plan.status == "FINISHED":
        raise BizException(ErrorCode.CONFLICT, "关联采购计划已完成，不能释放采购数量")
    plan_map = _plan_item_map(plan.id, db)
    changes: list[tuple[PurchasePlanItem, Decimal]] = []
    for item in _order_items(db, order.id):
        plan_item = plan_map.get((item.product_id, item.sku_id))
        if plan_item is None:
            continue
        target = plan_item.purchased_qty - item.qty
        if target < ZERO_QTY:
            raise BizException(ErrorCode.CONFLICT, "采购计划已采购数量不能为负数")
        changes.append((plan_item, target))
    for plan_item, target in changes:
        plan_item.purchased_qty = target
    db.flush()


def delete_order(
    db: Session, *, order_id: int, staff: Staff, commit: bool = True
) -> None:
    order = get_order_or_404(db, order_id)
    _ensure_editable(db, order)
    _release_plan_allocations(db, order)
    for item in _order_items(db, order.id):
        db.delete(item)
    db.delete(order)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"删除采购订单：{order.order_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(order: PurchaseOrder, state: dict) -> None:
    status = state["status"]
    if status == "APPROVED":
        order.audit_status = AUDIT_APPROVED
    elif status == "REJECTED":
        order.audit_status = AUDIT_REJECTED
    else:
        order.audit_status = AUDIT_PENDING


def submit_order(
    db: Session, *, order_id: int, staff: Staff, commit: bool = True
) -> PurchaseOrder:
    order = get_order_or_404(db, order_id)
    _ensure_editable(db, order)
    if not _order_items(db, order.id):
        raise BizException(ErrorCode.BAD_REQUEST, "采购订单没有商品明细，不能提交")
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        doc_no=order.order_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(order, state)
    order.auditor_id = None
    order.audit_time = None
    _commit(db, commit)
    return order


def approve_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseOrder:
    # 审批人可审核他人单据；审批引擎负责校验当前环节授权。
    order = get_order_or_404(db, order_id, check_scope=False)
    if order.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购订单状态不能审核")
    state = approval_service.approve(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(order, state)
    if state["status"] == "APPROVED":
        order.status = STATUS_APPROVED
        order.auditor_id = staff.id
        order.audit_time = _now()
    _recalculate_order(db, order)
    _commit(db, commit)
    return order


def reject_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseOrder:
    order = get_order_or_404(db, order_id, check_scope=False)
    if order.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前采购订单状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(order, state)
    order.auditor_id = staff.id
    order.audit_time = _now()
    db.flush()
    _commit(db, commit)
    return order


def unaudit_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> PurchaseOrder:
    order = get_order_or_404(db, order_id, check_scope=False)
    if order.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购订单尚未审核通过，不能弃审")
    if order.arrived_qty > ZERO_QTY:
        raise BizException(ErrorCode.CONFLICT, "采购订单已有到货记录，不能弃审")
    state = approval_service.unaudit(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        staff=staff,
        reason=reason,
        step=step,
        commit=False,
    )
    _map_audit_status(order, state)
    order.status = STATUS_PENDING
    order.auditor_id = None
    order.audit_time = None
    for item in _order_items(db, order.id):
        item.in_transit_qty = ZERO_QTY
    db.flush()
    _commit(db, commit)
    return order


def terminate_order(
    db: Session, *, order_id: int, staff: Staff, reason: str, commit: bool = True
) -> PurchaseOrder:
    order = get_order_or_404(db, order_id)
    if order.status in {STATUS_TERMINATED, STATUS_CLOSED, STATUS_RECEIVED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购订单不能终止")
    if order.arrived_qty > ZERO_QTY:
        raise BizException(ErrorCode.CONFLICT, "采购订单已有到货记录，不能终止")
    _release_plan_allocations(db, order)
    order.status = STATUS_TERMINATED
    for item in _order_items(db, order.id):
        item.in_transit_qty = ZERO_QTY
    write_audit(
        db,
        action="TERMINATE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"终止采购订单：{order.order_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def ensure_inbound_warehouse(
    db: Session, *, order: PurchaseOrder, warehouse_id: int
) -> Warehouse:
    if order.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购订单审核通过后才能入库")
    if order.status in {STATUS_TERMINATED, STATUS_CLOSED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购订单不能入库")
    warehouse = _validate_warehouse(db, warehouse_id)
    if warehouse is None:
        raise BizException(ErrorCode.BAD_REQUEST, "必须指定入库仓库")
    if order.warehouse_id is not None and order.warehouse_id != warehouse.id:
        raise BizException(ErrorCode.CONFLICT, "入库仓库与采购订单指定仓库不一致")
    return warehouse


def record_arrival(
    db: Session,
    *,
    order_id: int,
    warehouse_id: int,
    items: list[dict],
    staff: Staff,
    commit: bool = True,
) -> PurchaseOrder:
    """入库单审核后回写到货数量；参数 ``items`` 中的 qty 为本次新增到货量。"""
    # 入库单已完成权限与审批校验；跨单据回写不受采购订单制单人范围限制。
    order = get_order_or_404(db, order_id, check_scope=False)
    ensure_inbound_warehouse(db, order=order, warehouse_id=warehouse_id)
    if not isinstance(items, list) or not items:
        raise BizException(ErrorCode.BAD_REQUEST, "到货明细不能为空")

    order_items = {item.id: item for item in _order_items(db, order.id)}
    normalized: list[tuple[PurchaseOrderItem, Decimal]] = []
    seen: set[int] = set()
    for raw in items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "到货明细格式错误")
        _check_unknown_fields(raw, {"order_item_id", "qty"})
        item_id = _to_int_id(raw.get("order_item_id"), "采购订单明细")
        if item_id in seen:
            raise BizException(ErrorCode.BAD_REQUEST, "到货明细不能重复")
        seen.add(item_id)
        item = order_items.get(item_id)
        if item is None:
            raise BizException(ErrorCode.BAD_REQUEST, "到货明细不属于当前采购订单")
        normalized.append((item, _to_qty(raw.get("qty"), "本次到货数量")))

    for item, qty in normalized:
        item.arrived_qty += qty
    db.flush()
    _recalculate_order(db, order)
    write_audit(
        db,
        action="ARRIVAL",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"采购订单到货回写：{order.order_no}",
        detail={
            "warehouse_id": warehouse_id,
            "items": [
                {"order_item_id": item.id, "arrived_qty": str(qty)}
                for item, qty in normalized
            ],
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def add_attachment_urls(
    db: Session,
    *,
    order_id: int,
    urls: list[str],
    staff: Staff,
    commit: bool = True,
) -> PurchaseOrder:
    order = get_order_or_404(db, order_id)
    if order.status in {STATUS_TERMINATED, STATUS_CLOSED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购订单不能上传附件")
    attachments = _attachments_from_db(order)
    for url in _normalize_attachments(urls):
        if url not in attachments:
            attachments.append(url)
    if len(attachments) > MAX_ATTACHMENTS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"采购订单附件不能超过 {MAX_ATTACHMENTS} 个"
        )
    order.attachments = _attachments_to_db(attachments)
    write_audit(
        db,
        action="ATTACH",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"上传采购订单附件：{order.order_no}",
        detail={"attachment_count": len(_normalize_attachments(urls))},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def export_orders(
    db: Session,
    *,
    plan_id: int | None = None,
    supplier_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
) -> bytes:
    rows: list[dict] = []
    page = 1
    while True:
        listed = list_orders(
            db,
            plan_id=plan_id,
            supplier_id=supplier_id,
            warehouse_id=warehouse_id,
            status=status,
            audit_status=audit_status,
            keyword=keyword,
            page=page,
            page_size=200,
        )
        rows.extend(listed["items"])
        if len(rows) >= listed["total"] or not listed["items"]:
            break
        page += 1
    return export_data(PURCHASE_ORDER_EXPORT_TEMPLATE, rows)


def import_orders(
    db: Session, *, file_bytes: bytes, staff: Staff, commit: bool = True
) -> dict:
    parsed: ParseResult = parse_data(file_bytes, PURCHASE_ORDER_TEMPLATE)
    errors = list(parsed.errors)
    if errors and not parsed.rows:
        return {"created": 0, "item_count": 0, "errors": errors}
    if not parsed.rows:
        return {
            "created": 0,
            "item_count": 0,
            "errors": [{"row": 0, "field": "", "message": "没有可导入的数据"}],
        }

    supplier = None
    warehouse_id: int | None = None
    purchaser_id = staff.id
    description: str | None = None
    attachments: list[str] = []
    items: list[dict] = []

    for index, row in enumerate(parsed.rows, start=2):
        try:
            supplier_code = str(row.get("supplier_code") or "").strip()
            row_supplier = db.scalar(
                select(Supplier).where(Supplier.code == supplier_code)
            )
            if row_supplier is None:
                raise BizException(
                    ErrorCode.BAD_REQUEST, f"供应商编码不存在：{supplier_code}"
                )
            if not row_supplier.is_active:
                raise BizException(ErrorCode.BAD_REQUEST, "供应商已停用")

            warehouse_code = str(row.get("warehouse_code") or "").strip()
            row_warehouse_id = None
            if warehouse_code:
                warehouse = db.scalar(
                    select(Warehouse).where(Warehouse.code == warehouse_code)
                )
                if warehouse is None:
                    raise BizException(
                        ErrorCode.BAD_REQUEST, f"仓库编码不存在：{warehouse_code}"
                    )
                _validate_warehouse(db, warehouse.id)
                row_warehouse_id = warehouse.id

            purchaser_phone = str(row.get("purchaser_phone") or "").strip()
            row_purchaser_id = purchaser_id
            if purchaser_phone:
                row_purchaser = _get_staff_or_404(
                    db,
                    _to_int_id(
                        db.scalar(
                            select(Staff.id).where(Staff.phone == purchaser_phone)
                        ),
                        "采购人员",
                    ),
                )
                row_purchaser_id = row_purchaser.id

            row_description = _optional_text(
                row.get("description"), "采购描述", 255
            )
            row_attachments = _normalize_attachments(row.get("attachments"))
            product_code = str(row.get("product_code") or "").strip()
            product = db.scalar(select(Product).where(Product.code == product_code))
            if product is None:
                raise BizException(
                    ErrorCode.BAD_REQUEST, f"商品编码不存在：{product_code}"
                )
            sku_code = str(row.get("sku_code") or "").strip()
            sku_id = None
            if sku_code:
                sku = db.scalar(
                    select(ProductSku).where(ProductSku.spec_code == sku_code)
                )
                if sku is None or sku.product_id != product.id:
                    raise BizException(
                        ErrorCode.BAD_REQUEST, f"规格编码不属于商品：{sku_code}"
                    )
                sku_id = sku.id
        except BizException as exc:
            errors.append({"row": index, "field": "", "message": exc.message})
            continue

        if supplier is None:
            supplier = row_supplier
            warehouse_id = row_warehouse_id
            purchaser_id = row_purchaser_id
            description = row_description
            attachments = row_attachments
        else:
            if row_supplier.id != supplier.id:
                errors.append(
                    {"row": index, "field": "supplier_code", "message": "同一文件只能包含一个供应商"}
                )
                continue
            if row_warehouse_id != warehouse_id:
                errors.append(
                    {"row": index, "field": "warehouse_code", "message": "同一文件入库仓库必须一致"}
                )
                continue
            if row_purchaser_id != purchaser_id:
                errors.append(
                    {"row": index, "field": "purchaser_phone", "message": "同一文件采购人员必须一致"}
                )
                continue
            if row_description != description:
                errors.append(
                    {"row": index, "field": "description", "message": "同一文件采购描述必须一致"}
                )
                continue
            if row_attachments != attachments:
                errors.append(
                    {"row": index, "field": "attachments", "message": "同一文件附件必须一致"}
                )
                continue

        items.append(
            {
                "product_id": product.id,
                "sku_id": sku_id,
                "qty": row.get("qty"),
                "purchase_price": row.get("purchase_price"),
            }
        )

    if errors:
        return {"created": 0, "item_count": 0, "errors": errors}
    if supplier is None:
        return {
            "created": 0,
            "item_count": 0,
            "errors": [{"row": 0, "field": "", "message": "没有有效数据"}],
        }

    try:
        normalized_items = _normalize_items(
            db, supplier_id=supplier.id, raw_items=items
        )
        order = _create_order_record(
            db,
            supplier=supplier,
            warehouse=_validate_warehouse(db, warehouse_id),
            purchaser=_get_staff_or_404(db, purchaser_id),
            description=description,
            attachments=attachments,
            items=normalized_items,
            staff=staff,
            commit=False,
        )
    except BizException as exc:
        db.rollback()
        return {
            "created": 0,
            "item_count": 0,
            "errors": [{"row": 0, "field": "", "message": exc.message}],
        }

    write_audit(
        db,
        action="IMPORT",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"导入采购订单：{order.order_no}",
        detail={"item_count": len(normalized_items)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "created": 1,
        "item_count": len(normalized_items),
        "order_id": order.id,
        "order_no": order.order_no,
        "errors": [],
    }


def build_order_pdf(db: Session, order_id: int) -> bytes:
    order = get_order_or_404(db, order_id)
    detail = order_dict(db, order, with_items=True, with_approval=False)
    rows = [
        [
            item["product_code"],
            item["product_name"],
            item["spec_name"] or "",
            item["unit_name"] or "",
            item["purchase_price"],
            item["qty"],
            item["amount"],
        ]
        for item in detail["items"]
    ]
    return render_pdf(
        PdfDocument(
            title="采购订单",
            fields=[
                ("采购订单号", detail["order_no"]),
                ("采购计划编号", detail["plan_no"] or ""),
                ("供应商", detail["supplier_name"] or ""),
                ("入库仓库", detail["warehouse_name"] or ""),
                ("采购人员", detail["purchaser_name"] or ""),
                ("采购描述", detail["description"] or ""),
            ],
            tables=[
                PdfTable(
                    headers=[
                        "商品编码",
                        "商品名称",
                        "规格",
                        "单位",
                        "采购价",
                        "采购数量",
                        "金额",
                    ],
                    rows=rows,
                )
            ],
            footers=[
                ("采购总数量", detail["total_qty"]),
                ("采购金额", detail["total_amount"]),
            ],
        )
    )
