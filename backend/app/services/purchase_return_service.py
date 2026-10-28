"""采购退货单服务（TASK-027）。

实现采购退货单 CRUD、财务 + 商控/仓库两级审核、自动出库和退款记录，
并为 TASK-031 保留手工出库完成入口 ``complete_return_outbound``。

库存与退款口径：
- `auto_out=true`：末级审核通过后立即按库位扣减库存并写入出库流水。
- `auto_out=false`：仅生成待下架的采购退货出库单，由 TASK-031 完成下架。
- 出库完成后生成供应商退款收入单；真实供应商账户余额同步由
  TASK-046/TASK-064 负责。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Unit
from app.models.finance import Income
from app.models.org import Staff
from app.models.party import Supplier
from app.models.product import Product, ProductSku
from app.models.purchase import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchaseReturn,
    PurchaseReturnItem,
)
from app.models.stock import StockFlow, StockOut, StockOutItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.repositories.rbac_repo import list_staff_role_codes
from app.services import approval_service, party_service

DOC_TYPE = "purchase_return"
STOCK_OUT_TYPE = "PURCHASE_RETURN"
REFUND_INCOME_TYPE = "PURCHASE_RETURN_REFUND"

STATUS_PENDING = "PENDING"
STATUS_FINANCE_APPROVED = "FINANCE_APPROVED"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_TERMINATED = "TERMINATED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

STOCK_STATUS_PENDING = "PENDING"
STOCK_STATUS_APPROVED = "APPROVED"
STOCK_STATUS_TERMINATED = "TERMINATED"
OFF_SHELF_NONE = "NONE"
OFF_SHELF_DONE = "DONE"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
MAX_ITEMS = 500

RETURN_FIELDS = {
    "supplier_id",
    "order_id",
    "warehouse_id",
    "auto_out",
    "return_reason",
    "description",
    "items",
}

RETURN_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "unit_id",
    "unit_price",
    "return_qty",
    "amount",
}


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


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _to_qty(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(
            QTY_QUANT, rounding=ROUND_HALF_UP
        )
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
    if result <= Decimal("0.00"):
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


def get_return_or_404(
    db: Session, return_id: int, *, check_scope: bool = True,
    company_id: int | None = None,
) -> PurchaseReturn:
    stmt = select(PurchaseReturn).where(PurchaseReturn.id == return_id)
    if company_id is not None:
        stmt = stmt.where(PurchaseReturn.company_id == company_id)
    record = db.scalar(stmt)
    if record is None or (check_scope and not document_visible(db, record.created_by, DOC_TYPE, record.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购退货单不存在")
    return record


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    if product.is_disabled:
        raise BizException(ErrorCode.BAD_REQUEST, f"商品已禁用：{product.name}")
    return product


def _get_sku_or_404(
    db: Session, *, product_id: int, sku_id: int
) -> ProductSku:
    sku = db.get(ProductSku, sku_id)
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")
    if not sku.is_active:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格已停用")
    return sku


def _get_warehouse_or_404(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise BizException(ErrorCode.BAD_REQUEST, "退货仓库不存在")
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.BAD_REQUEST, "退货仓库已停用")
    return warehouse


def _get_unit_or_404(db: Session, unit_id: int) -> Unit:
    unit = db.get(Unit, unit_id)
    if unit is None:
        raise BizException(ErrorCode.BAD_REQUEST, "计量单位不存在")
    return unit


def _find_order_item(
    db: Session,
    *,
    order_id: int,
    product_id: int,
    sku_id: int,
) -> PurchaseOrderItem | None:
    return db.scalar(
        select(PurchaseOrderItem).where(
            PurchaseOrderItem.order_id == order_id,
            PurchaseOrderItem.product_id == product_id,
            PurchaseOrderItem.sku_id == sku_id,
        )
    )


def _resolve_supplier_order_warehouse(
    db: Session, data: dict
) -> tuple[Supplier, PurchaseOrder | None, Warehouse]:
    order_id = _optional_int_id(data.get("order_id"), "采购订单")
    order = db.get(PurchaseOrder, order_id) if order_id is not None else None
    if order_id is not None and order is None:
        raise BizException(ErrorCode.BAD_REQUEST, "采购订单不存在")
    if order is not None and order.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购订单审核通过后才能创建退货单")

    supplier_id = _optional_int_id(data.get("supplier_id"), "供应商")
    if supplier_id is None and order is not None:
        supplier_id = order.supplier_id
    if supplier_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "供应商不能为空")
    supplier = party_service.get_supplier_or_404(db, supplier_id)
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用，不能创建采购退货单")
    if order is not None and order.supplier_id != supplier.id:
        raise BizException(ErrorCode.BAD_REQUEST, "采购订单与供应商不一致")

    warehouse = _get_warehouse_or_404(
        db, _to_int_id(data.get("warehouse_id"), "退货仓库")
    )
    if order is not None and order.warehouse_id is not None:
        if order.warehouse_id != warehouse.id:
            raise BizException(ErrorCode.CONFLICT, "退货仓库与采购订单指定仓库不一致")
    return supplier, order, warehouse


def _resolve_price(
    db: Session,
    *,
    supplier_id: int,
    order: PurchaseOrder | None,
    product: Product,
    sku: ProductSku,
) -> Decimal:
    if order is not None:
        order_item = _find_order_item(
            db,
            order_id=order.id,
            product_id=product.id,
            sku_id=sku.id,
        )
        if order_item is not None and order_item.purchase_price is not None:
            return round_money(order_item.purchase_price)

    supplier_price = party_service.get_supplier_price(
        db, supplier_id=supplier_id, product_id=product.id, sku_id=sku.id
    )
    if supplier_price is not None:
        return round_money(supplier_price)
    if sku.cost_price is not None:
        return round_money(sku.cost_price)
    if product.cost_price is not None:
        return round_money(product.cost_price)
    raise BizException(
        ErrorCode.BAD_REQUEST, f"无法确定商品退货单价：{product.name}"
    )


def _normalize_item(
    db: Session,
    *,
    supplier_id: int,
    order: PurchaseOrder | None,
    data: dict,
) -> dict:
    _check_unknown_fields(data, RETURN_ITEM_FIELDS)
    product_id = _to_int_id(data.get("product_id"), "商品")
    sku_id = _to_int_id(data.get("sku_id"), "商品规格")
    product = _get_product_or_404(db, product_id)
    sku = _get_sku_or_404(db, product_id=product_id, sku_id=sku_id)

    unit_id = _optional_int_id(data.get("unit_id"), "计量单位")
    if unit_id is None:
        unit_id = product.main_unit_id
    if unit_id is not None:
        _get_unit_or_404(db, unit_id)

    return_qty = _to_qty(data.get("return_qty"), "退货数量")
    if data.get("unit_price") in (None, ""):
        unit_price = _resolve_price(
            db,
            supplier_id=supplier_id,
            order=order,
            product=product,
            sku=sku,
        )
    else:
        unit_price = _to_price(data["unit_price"], "退货单价")
    amount = round_money(return_qty * unit_price)
    return {
        "product_id": product.id,
        "sku_id": sku.id,
        "unit_id": unit_id,
        "unit_price": unit_price,
        "return_qty": return_qty,
        "amount": amount,
    }


def _normalize_items(
    db: Session,
    *,
    supplier_id: int,
    order: PurchaseOrder | None,
    raw_items: Any,
) -> list[dict]:
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少添加一条退货商品明细")
    if len(raw_items) > MAX_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"单张采购退货单最多 {MAX_ITEMS} 条明细"
        )

    result: list[dict] = []
    keys: set[tuple[int, int]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "退货商品明细格式错误")
        item = _normalize_item(
            db, supplier_id=supplier_id, order=order, data=raw
        )
        key = (item["product_id"], item["sku_id"])
        if key in keys:
            raise BizException(ErrorCode.BAD_REQUEST, "同一商品规格不能重复")
        keys.add(key)
        result.append(item)
    return result


def _previous_return_qty(
    db: Session,
    *,
    order_id: int,
    product_id: int,
    sku_id: int,
    exclude_return_id: int | None,
) -> Decimal:
    conditions = [
        PurchaseReturn.order_id == order_id,
        PurchaseReturnItem.product_id == product_id,
        PurchaseReturnItem.sku_id == sku_id,
        PurchaseReturn.status.notin_([STATUS_REJECTED, STATUS_TERMINATED]),
    ]
    if exclude_return_id is not None:
        conditions.append(PurchaseReturn.id != exclude_return_id)
    return (
        db.scalar(
            select(func.sum(PurchaseReturnItem.return_qty))
            .join(
                PurchaseReturn,
                PurchaseReturn.id == PurchaseReturnItem.return_id,
            )
            .where(*conditions)
        )
        or ZERO_QTY
    )


def _validate_order_limits(
    db: Session,
    *,
    order: PurchaseOrder | None,
    items: list[dict],
    exclude_return_id: int | None = None,
) -> None:
    if order is None:
        return
    for item in items:
        order_item = _find_order_item(
            db,
            order_id=order.id,
            product_id=item["product_id"],
            sku_id=item["sku_id"],
        )
        if order_item is None:
            raise BizException(ErrorCode.BAD_REQUEST, "退货商品不属于关联采购订单")
        already = _previous_return_qty(
            db,
            order_id=order.id,
            product_id=item["product_id"],
            sku_id=item["sku_id"],
            exclude_return_id=exclude_return_id,
        )
        available = order_item.arrived_qty - already
        if item["return_qty"] > available:
            product = db.get(Product, item["product_id"])
            label = product.name if product else str(item["product_id"])
            raise BizException(
                ErrorCode.CONFLICT,
                f"{label}退货数量超过可退数量 {available}",
            )


def _return_items(
    db: Session, return_id: int, *, company_id: int | None = None,
) -> list[PurchaseReturnItem]:
    conditions = [PurchaseReturnItem.return_id == return_id]
    if company_id is not None:
        conditions.append(PurchaseReturnItem.company_id == company_id)
    return list(
        db.scalars(
            select(PurchaseReturnItem)
            .where(*conditions)
            .order_by(PurchaseReturnItem.id)
        )
    )


def _recalculate(record: PurchaseReturn, items: list[PurchaseReturnItem]) -> None:
    record.total_qty = sum((item.return_qty for item in items), ZERO_QTY)
    record.refund_amount = sum(
        (item.amount for item in items if item.amount is not None),
        Decimal("0.00"),
    )


def _stock_out_dict(record: StockOut | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "stock_out_no": record.stock_out_no,
        "warehouse_id": record.warehouse_id,
        "stock_out_type": record.stock_out_type,
        "status": record.status,
        "audit_status": record.audit_status,
        "off_shelf_status": record.off_shelf_status,
        "total_qty": record.total_qty,
        "auditor_id": record.auditor_id,
        "audit_time": record.audit_time,
        "created_at": record.created_at,
    }


def _income_dict(record: Income | None) -> dict | None:
    if record is None:
        return None
    return {
        "id": record.id,
        "income_no": record.income_no,
        "supplier_id": record.supplier_id,
        "income_type": record.income_type,
        "amount": record.amount,
        "status": record.status,
        "audit_status": record.audit_status,
        "description": record.description,
        "created_at": record.created_at,
    }


def _find_stock_out(
    db: Session, return_id: int, *, company_id: int | None = None,
) -> StockOut | None:
    conditions = [
        StockOut.purchase_return_id == return_id,
        StockOut.status != STOCK_STATUS_TERMINATED,
    ]
    if company_id is not None:
        conditions.append(StockOut.company_id == company_id)
    return db.scalar(
        select(StockOut)
        .where(*conditions)
        .order_by(StockOut.id.desc())
    )


def _find_refund_income(
    db: Session, record: PurchaseReturn, *, company_id: int | None = None,
) -> Income | None:
    conditions = [
        Income.supplier_id == record.supplier_id,
        Income.income_type == REFUND_INCOME_TYPE,
        Income.description == f"采购退货退款:{record.return_no}",
        Income.status != "INVALID",
    ]
    if company_id is not None:
        conditions.append(Income.company_id == company_id)
    return db.scalar(
        select(Income)
        .where(*conditions)
        .order_by(Income.id.desc())
    )


def return_item_dict(
    db: Session, item: PurchaseReturnItem, *, company_id: int | None = None,
) -> dict:
    def related(model, row_id):
        if row_id is None:
            return None
        stmt = select(model).where(model.id == row_id)
        if company_id is not None:
            stmt = stmt.where(model.company_id == company_id)
        return db.scalar(stmt)

    product = related(Product, item.product_id)
    sku = related(ProductSku, item.sku_id)
    unit = related(Unit, item.unit_id)
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "art_no": product.art_no if product else None,
        "sku_id": item.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "unit_id": item.unit_id,
        "unit_name": unit.name if unit else None,
        "unit_price": item.unit_price,
        "return_qty": item.return_qty,
        "amount": item.amount,
    }


def return_dict(
    db: Session,
    record: PurchaseReturn,
    *,
    with_items: bool = True,
    with_approval: bool = False,
    company_id: int | None = None,
) -> dict:
    supplier_stmt = select(Supplier).where(Supplier.id == record.supplier_id)
    order_stmt = select(PurchaseOrder).where(PurchaseOrder.id == record.order_id) if record.order_id else None
    warehouse_stmt = select(Warehouse).where(Warehouse.id == record.warehouse_id) if record.warehouse_id else None
    if company_id is not None:
        supplier_stmt = supplier_stmt.where(Supplier.company_id == company_id)
        if order_stmt is not None:
            order_stmt = order_stmt.where(PurchaseOrder.company_id == company_id)
        if warehouse_stmt is not None:
            warehouse_stmt = warehouse_stmt.where(Warehouse.company_id == company_id)
    supplier = db.scalar(supplier_stmt)
    order = db.scalar(order_stmt) if order_stmt is not None else None
    warehouse = db.scalar(warehouse_stmt) if warehouse_stmt is not None else None
    creator = db.scalar(select(Staff).where(
        Staff.id == record.created_by,
        *([Staff.company_id == company_id] if company_id is not None else []),
    )) if record.created_by else None
    auditor = db.scalar(select(Staff).where(
        Staff.id == record.auditor_id,
        *([Staff.company_id == company_id] if company_id is not None else []),
    )) if record.auditor_id else None
    stock_out = _find_stock_out(db, record.id, company_id=company_id)
    income = _find_refund_income(db, record, company_id=company_id)

    if income is not None:
        refund_status = "RECORDED"
    elif stock_out is not None and stock_out.off_shelf_status == OFF_SHELF_DONE:
        refund_status = "PENDING"
    elif stock_out is not None:
        refund_status = "PENDING_OUTBOUND"
    else:
        refund_status = "NOT_CREATED"

    data = {
        "id": record.id,
        "return_no": record.return_no,
        "supplier_id": record.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "order_id": record.order_id,
        "order_no": order.order_no if order else None,
        "warehouse_id": record.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "warehouse_is_virtual": warehouse.is_virtual if warehouse else None,
        "auto_out": record.auto_out,
        "return_reason": record.return_reason,
        "description": record.description,
        "total_qty": record.total_qty,
        "returned_qty": record.returned_qty,
        "refund_amount": record.refund_amount,
        "refund_status": refund_status,
        "status": record.status,
        "audit_status": record.audit_status,
        "created_by": record.created_by,
        "created_by_name": creator.name if creator else None,
        "auditor_id": record.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": record.audit_time,
        "stock_out": _stock_out_dict(stock_out),
        "refund_income": _income_dict(income),
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if with_items:
        data["items"] = [
            return_item_dict(db, item, company_id=company_id)
            for item in _return_items(db, record.id, company_id=company_id)
        ]
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=DOC_TYPE, doc_id=record.id
        )
    return data


def list_returns(
    db: Session,
    *,
    supplier_id: int | None = None,
    order_id: int | None = None,
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
    conditions = scope_conditions(db, PurchaseReturn.created_by)
    if company_id is not None:
        conditions.append(PurchaseReturn.company_id == company_id)
    if supplier_id is not None:
        conditions.append(PurchaseReturn.supplier_id == supplier_id)
    if order_id is not None:
        conditions.append(PurchaseReturn.order_id == order_id)
    if warehouse_id is not None:
        conditions.append(PurchaseReturn.warehouse_id == warehouse_id)
    if status:
        conditions.append(PurchaseReturn.status == status.upper())
    if audit_status:
        conditions.append(PurchaseReturn.audit_status == audit_status.upper())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                PurchaseReturn.return_no.like(like),
                PurchaseReturn.supplier_id.in_(
                    select(Supplier.id).where(Supplier.name.like(like))
                ),
                PurchaseReturn.order_id.in_(
                    select(PurchaseOrder.id).where(PurchaseOrder.order_no.like(like))
                ),
                PurchaseReturn.warehouse_id.in_(
                    select(Warehouse.id).where(Warehouse.name.like(like))
                ),
            )
        )
    total = db.scalar(
        select(func.count(PurchaseReturn.id)).where(*conditions)
    ) or 0
    rows = list(
        db.scalars(
            select(PurchaseReturn)
            .where(*conditions)
            .order_by(PurchaseReturn.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            return_dict(db, row, with_items=False, with_approval=False)
            for row in rows
        ],
    }


def create_return(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> PurchaseReturn:
    _check_unknown_fields(data, RETURN_FIELDS)
    supplier, order, warehouse = _resolve_supplier_order_warehouse(db, data)
    items = _normalize_items(
        db,
        supplier_id=supplier.id,
        order=order,
        raw_items=data.get("items"),
    )
    _validate_order_limits(db, order=order, items=items)

    record = PurchaseReturn(
        return_no=generate_serial(SerialPrefix.PURCHASE_RETURN, dt=_now()),
        supplier_id=supplier.id,
        order_id=order.id if order else None,
        warehouse_id=warehouse.id,
        auto_out=_to_bool(data.get("auto_out", False), "是否自动出库"),
        return_reason=_optional_text(data.get("return_reason"), "退货原因", 128),
        description=_optional_text(data.get("description"), "退货描述", 255),
        total_qty=ZERO_QTY,
        refund_amount=Decimal("0.00"),
        returned_qty=ZERO_QTY,
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
    )
    db.add(record)
    db.flush()
    for item in items:
        db.add(PurchaseReturnItem(return_id=record.id, **item))
    db.flush()
    _recalculate(record, _return_items(db, record.id))
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"新增采购退货单：{record.return_no}",
        detail={
            "supplier_id": supplier.id,
            "order_id": record.order_id,
            "warehouse_id": warehouse.id,
            "auto_out": record.auto_out,
            "total_qty": str(record.total_qty),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _approval_state(db: Session, return_id: int) -> dict:
    return approval_service.get_state(
        db, doc_type=DOC_TYPE, doc_id=return_id
    )["state"]


def _ensure_editable(db: Session, record: PurchaseReturn) -> None:
    if record.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购退货单状态不允许修改")
    state = _approval_state(db, record.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "采购退货单已进入审批流程，不能修改")


def update_return(
    db: Session,
    *,
    return_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchaseReturn:
    _check_unknown_fields(data, RETURN_FIELDS)
    record = get_return_or_404(db, return_id)
    _ensure_editable(db, record)

    merged = {
        "supplier_id": data.get("supplier_id", record.supplier_id),
        "order_id": data.get("order_id", record.order_id),
        "warehouse_id": data.get("warehouse_id", record.warehouse_id),
    }
    if "order_id" in data and data["order_id"] in (None, ""):
        merged["order_id"] = None
    supplier, order, warehouse = _resolve_supplier_order_warehouse(db, merged)

    record.supplier_id = supplier.id
    record.order_id = order.id if order else None
    record.warehouse_id = warehouse.id
    if "auto_out" in data:
        record.auto_out = _to_bool(data["auto_out"], "是否自动出库")
    for field, label, max_length in (
        ("return_reason", "退货原因", 128),
        ("description", "退货描述", 255),
    ):
        if field in data:
            setattr(record, field, _optional_text(data[field], label, max_length))

    if "items" in data:
        items_data = _normalize_items(
            db,
            supplier_id=supplier.id,
            order=order,
            raw_items=data["items"],
        )
        _validate_order_limits(
            db, order=order, items=items_data, exclude_return_id=record.id
        )
        for item in _return_items(db, record.id):
            db.delete(item)
        db.flush()
        for item in items_data:
            db.add(PurchaseReturnItem(return_id=record.id, **item))
        db.flush()
    else:
        items_data = [
            {
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "return_qty": item.return_qty,
            }
            for item in _return_items(db, record.id)
        ]
        _validate_order_limits(
            db, order=order, items=items_data, exclude_return_id=record.id
        )

    _recalculate(record, _return_items(db, record.id))
    if record.audit_status == AUDIT_REJECTED:
        record.status = STATUS_PENDING
        record.audit_status = AUDIT_PENDING
        record.auditor_id = None
        record.audit_time = None
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"修改采购退货单：{record.return_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_return(
    db: Session, *, return_id: int, staff: Staff, commit: bool = True
) -> None:
    record = get_return_or_404(db, return_id)
    _ensure_editable(db, record)
    if _find_stock_out(db, record.id) is not None:
        raise BizException(ErrorCode.CONFLICT, "采购退货单已生成出库单，不能删除")
    if _find_refund_income(db, record) is not None:
        raise BizException(ErrorCode.CONFLICT, "采购退货单已生成退款记录，不能删除")
    for item in _return_items(db, record.id):
        db.delete(item)
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"删除采购退货单：{record.return_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(record: PurchaseReturn, state: dict) -> None:
    if state["status"] == "APPROVED":
        record.audit_status = AUDIT_APPROVED
    elif state["status"] == "REJECTED":
        record.audit_status = AUDIT_REJECTED
    else:
        record.audit_status = AUDIT_PENDING


def _ensure_two_step_flow(db: Session) -> None:
    flow = approval_service.resolve_flow(db, DOC_TYPE)
    if len(approval_service.steps_of(flow)) != 2:
        raise BizException(
            ErrorCode.CONFLICT,
            "采购退货审批流必须配置财务审核和商控/仓库审核两个环节",
        )


def submit_return(
    db: Session, *, return_id: int, staff: Staff, comment: str | None = None,
    commit: bool = True,
) -> PurchaseReturn:
    record = get_return_or_404(db, return_id)
    _ensure_editable(db, record)
    if not _return_items(db, record.id):
        raise BizException(ErrorCode.BAD_REQUEST, "采购退货单没有商品明细，不能提交")
    _ensure_two_step_flow(db)
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=record.id,
        doc_no=record.return_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    record.status = STATUS_PENDING
    _map_audit_status(record, state)
    record.auditor_id = None
    record.audit_time = None
    _commit(db, commit)
    return record


def _staff_roles(db: Session, staff: Staff) -> set[str]:
    return set(list_staff_role_codes(db, staff.id))


def _assert_finance_approver(db: Session, staff: Staff) -> None:
    if not _staff_roles(db, staff) & {"FINANCE", "ADMIN"}:
        raise BizException(
            ErrorCode.FORBIDDEN, "采购退货第一环节必须由财务审核"
        )


def _assert_outbound_approver(
    db: Session, record: PurchaseReturn, staff: Staff
) -> None:
    warehouse = _get_warehouse_or_404(db, record.warehouse_id)
    allowed = {"QUALITY", "ADMIN"} if warehouse.is_virtual else {"WAREHOUSE", "ADMIN"}
    if not _staff_roles(db, staff) & allowed:
        branch = "商控" if warehouse.is_virtual else "仓库"
        raise BizException(
            ErrorCode.FORBIDDEN, f"采购退货第二环节必须由{branch}人员审核"
        )


def _stock_rows(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    sku_id: int,
) -> list[StockLocation]:
    return list(
        db.scalars(
            select(StockLocation)
            .join(Location, Location.id == StockLocation.location_id)
            .where(
                Location.warehouse_id == warehouse_id,
                StockLocation.product_sku_id == sku_id,
                StockLocation.status.in_(["NORMAL", "DEFECTIVE"]),
                StockLocation.qty > ZERO_QTY,
            )
            .order_by(
                Location.priority,
                Location.sort_order,
                Location.id,
                StockLocation.id,
            )
            .with_for_update()
        )
    )


def _allocate_stock(
    db: Session,
    *,
    warehouse_id: int,
    item: PurchaseReturnItem,
) -> list[tuple[StockLocation, Decimal]]:
    if item.sku_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "自动出库必须指定商品规格")
    required = item.return_qty
    allocations: list[tuple[StockLocation, Decimal]] = []
    for row in _stock_rows(
        db,
        warehouse_id=warehouse_id,
        product_id=item.product_id,
        sku_id=item.sku_id,
    ):
        if required <= ZERO_QTY:
            break
        picked = min(row.qty, required)
        allocations.append((row, picked))
        required -= picked
    if required > ZERO_QTY:
        product = db.get(Product, item.product_id)
        label = product.name if product else str(item.product_id)
        raise BizException(ErrorCode.CONFLICT, f"{label}库存不足，无法自动出库")
    return allocations


def _create_stock_out(
    db: Session, record: PurchaseReturn
) -> StockOut:
    stock_out = StockOut(
        stock_out_no=generate_serial(SerialPrefix.STOCK_OUT, dt=_now()),
        warehouse_id=record.warehouse_id,
        stock_out_type=STOCK_OUT_TYPE,
        purchase_return_id=record.id,
        status=STOCK_STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        off_shelf_status=OFF_SHELF_NONE,
        total_qty=record.total_qty,
        remark=f"采购退货出库:{record.return_no}",
        created_by=record.created_by,
    )
    db.add(stock_out)
    db.flush()
    for item in _return_items(db, record.id):
        db.add(
            StockOutItem(
                stock_out_id=stock_out.id,
                product_id=item.product_id,
                sku_id=item.sku_id,
                location_id=None,
                qty=item.return_qty,
                cost_price=item.unit_price,
            )
        )
    db.flush()
    return stock_out


def _complete_stock_out(
    db: Session,
    *,
    record: PurchaseReturn,
    stock_out: StockOut,
    staff: Staff,
) -> None:
    if stock_out.off_shelf_status == OFF_SHELF_DONE:
        return
    for row in db.scalars(
        select(StockOutItem).where(StockOutItem.stock_out_id == stock_out.id)
    ):
        db.delete(row)
    db.flush()

    order = db.get(PurchaseOrder, record.order_id) if record.order_id else None
    for item in _return_items(db, record.id):
        for stock_row, qty in _allocate_stock(
            db, warehouse_id=record.warehouse_id, item=item
        ):
            stock_row.qty -= qty
            db.add(
                StockOutItem(
                    stock_out_id=stock_out.id,
                    product_id=item.product_id,
                    sku_id=item.sku_id,
                    location_id=stock_row.location_id,
                    qty=qty,
                    cost_price=item.unit_price,
                )
            )
            total_cost = round_money(qty * item.unit_price)
            db.add(
                StockFlow(
                    warehouse_id=record.warehouse_id,
                    product_id=item.product_id,
                    sku_id=item.sku_id,
                    location_id=stock_row.location_id,
                    direction="OUT",
                    biz_type=STOCK_OUT_TYPE,
                    qty=qty,
                    cost_price=item.unit_price,
                    total_cost=total_cost,
                    source_no=stock_out.stock_out_no,
                    order_no=order.order_no if order else None,
                    flow_time=_now(),
                    remark=f"采购退货:{record.return_no}",
                )
            )
    stock_out.status = STOCK_STATUS_APPROVED
    stock_out.audit_status = AUDIT_APPROVED
    stock_out.off_shelf_status = OFF_SHELF_DONE
    stock_out.auditor_id = staff.id
    stock_out.audit_time = _now()
    record.returned_qty = record.total_qty
    db.flush()


def _ensure_refund_income(
    db: Session, record: PurchaseReturn, staff: Staff
) -> Income:
    existing = _find_refund_income(db, record)
    if existing is not None:
        return existing
    income = Income(
        income_no=generate_serial(SerialPrefix.INCOME, dt=_now()),
        supplier_id=record.supplier_id,
        income_type=REFUND_INCOME_TYPE,
        amount=record.refund_amount,
        description=f"采购退货退款:{record.return_no}",
        status=STATUS_APPROVED,
        audit_status=AUDIT_APPROVED,
        created_by=staff.id,
    )
    db.add(income)
    db.flush()
    write_audit(
        db,
        action="REFUND",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"采购退货退款入供应商账户：{record.return_no}",
        detail={
            "income_id": income.id,
            "income_no": income.income_no,
            "supplier_id": record.supplier_id,
            "amount": str(record.refund_amount),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    return income


def _ensure_stock_out(
    db: Session,
    *,
    record: PurchaseReturn,
    staff: Staff,
    complete: bool,
) -> StockOut:
    stock_out = _find_stock_out(db, record.id)
    if stock_out is None:
        stock_out = _create_stock_out(db, record)
    if complete:
        _complete_stock_out(
            db, record=record, stock_out=stock_out, staff=staff
        )
        _ensure_refund_income(db, record, staff)
    return stock_out


def approve_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseReturn:
    record = get_return_or_404(db, return_id, check_scope=False)
    if record.status not in {STATUS_PENDING, STATUS_FINANCE_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购退货单状态不能审核")
    try:
        state = _approval_state(db, record.id)
        if state["current_step"] == 1:
            _assert_finance_approver(db, staff)
        elif state["current_step"] == 2:
            _assert_outbound_approver(db, record, staff)

        new_state = approval_service.approve(
            db,
            doc_type=DOC_TYPE,
            doc_id=record.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(record, new_state)
        if new_state["status"] == "APPROVED":
            record.status = STATUS_APPROVED
            record.auditor_id = staff.id
            record.audit_time = _now()
            _ensure_stock_out(
                db, record=record, staff=staff, complete=record.auto_out
            )
        else:
            record.status = STATUS_FINANCE_APPROVED
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return record


def reject_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseReturn:
    record = get_return_or_404(db, return_id, check_scope=False)
    if record.status not in {STATUS_PENDING, STATUS_FINANCE_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购退货单状态不能审核")
    state = _approval_state(db, record.id)
    if state["current_step"] == 1:
        _assert_finance_approver(db, staff)
    elif state["current_step"] == 2:
        _assert_outbound_approver(db, record, staff)
    new_state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=record.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    record.status = STATUS_REJECTED
    _map_audit_status(record, new_state)
    record.auditor_id = staff.id
    record.audit_time = _now()
    _commit(db, commit)
    return record


def _terminate_stock_out(db: Session, record: PurchaseReturn) -> None:
    stock_out = _find_stock_out(db, record.id)
    if stock_out is None:
        return
    if stock_out.off_shelf_status == OFF_SHELF_DONE:
        raise BizException(ErrorCode.CONFLICT, "采购退货已出库，不能弃审")
    stock_out.status = STOCK_STATUS_TERMINATED
    stock_out.audit_status = AUDIT_REJECTED


def _invalidate_refund_income(db: Session, record: PurchaseReturn) -> None:
    income = _find_refund_income(db, record)
    if income is None:
        return
    income.status = "INVALID"
    income.audit_status = AUDIT_REJECTED


def unaudit_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> PurchaseReturn:
    record = get_return_or_404(db, return_id, check_scope=False)
    if record.status != STATUS_APPROVED or record.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购退货单尚未审核通过，不能弃审")
    if record.returned_qty > ZERO_QTY:
        raise BizException(ErrorCode.CONFLICT, "采购退货单已出库，不能弃审")
    try:
        _terminate_stock_out(db, record)
        _invalidate_refund_income(db, record)
        state = approval_service.unaudit(
            db,
            doc_type=DOC_TYPE,
            doc_id=record.id,
            staff=staff,
            reason=reason,
            step=step,
            commit=False,
        )
        record.status = STATUS_FINANCE_APPROVED
        _map_audit_status(record, state)
        record.auditor_id = None
        record.audit_time = None
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return record


def terminate_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    reason: str,
    commit: bool = True,
) -> PurchaseReturn:
    record = get_return_or_404(db, return_id)
    _ensure_editable(db, record)
    if record.returned_qty > ZERO_QTY:
        raise BizException(ErrorCode.CONFLICT, "采购退货单已出库，不能终止")
    record.status = STATUS_TERMINATED
    write_audit(
        db,
        action="TERMINATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"终止采购退货单：{record.return_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def complete_return_outbound(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    commit: bool = True,
) -> PurchaseReturn:
    """TASK-031 完成手工下架后调用，扣减库存并生成退款收入单。"""
    record = get_return_or_404(db, return_id)
    if (
        record.status != STATUS_APPROVED
        or record.audit_status != AUDIT_APPROVED
    ):
        raise BizException(ErrorCode.CONFLICT, "采购退货单审核通过后才能完成出库")
    if record.returned_qty >= record.total_qty:
        return record
    try:
        _ensure_stock_out(
            db, record=record, staff=staff, complete=True
        )
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return record


def finalize_manual_return_outbound(
    db: Session,
    *,
    return_id: int,
    stock_out_id: int,
    staff: Staff,
    commit: bool = True,
) -> PurchaseReturn:
    """TASK-031 选择库位完成下架后，统一登记退货完成和退款。"""
    record = get_return_or_404(db, return_id)
    if (
        record.status != STATUS_APPROVED
        or record.audit_status != AUDIT_APPROVED
    ):
        raise BizException(ErrorCode.CONFLICT, "采购退货单审核通过后才能完成出库")
    stock_out = _find_stock_out(db, record.id)
    if stock_out is None or stock_out.id != stock_out_id:
        raise BizException(ErrorCode.CONFLICT, "采购退货出库单不存在或已终止")
    if stock_out.off_shelf_status != OFF_SHELF_DONE:
        raise BizException(ErrorCode.CONFLICT, "采购退货出库单尚未完成下架")

    record.returned_qty = record.total_qty
    _ensure_refund_income(db, record, staff)
    db.flush()
    _commit(db, commit)
    return record


def get_return_stock_out(
    db: Session, return_id: int, *, company_id: int | None = None,
) -> dict | None:
    record = get_return_or_404(db, return_id, company_id=company_id)
    return _stock_out_dict(_find_stock_out(db, record.id, company_id=company_id))


def get_return_refund(
    db: Session, return_id: int, *, company_id: int | None = None,
) -> dict | None:
    record = get_return_or_404(db, return_id, company_id=company_id)
    return _income_dict(_find_refund_income(db, record, company_id=company_id))
