"""采购入库与商品上架服务（TASK-030）。

业务流程：
1. 创建入库单，可关联已审核采购订单；
2. 入库单提交并审核通过后，采购订单回写到货数量，商品进入上架月台；
3. 仓库选择库位执行上架，增加库位库存并写入库存流水。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.finance import Voucher
from app.models.org import Staff
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseOrder, PurchaseOrderItem
from app.models.stock import Putaway, StockFlow, StockIn, StockInItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import (
    approval_service,
    purchase_order_service,
    voucher_service,
    warehouse_service,
)

DOC_TYPE = "stock_in"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

STOCK_IN_TYPES = {
    "PURCHASE",
    "MANUAL",
    "AFTER_SALE",
    "TRANSFER",
    "CHECK",
    "PUSH",
    "ADJUST",
}

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0")
MAX_STOCK_IN_ITEMS = 500

STOCK_IN_FIELDS = {
    "warehouse_id",
    "purchase_order_id",
    "stock_in_type",
    "remark",
    "items",
}

PURCHASE_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "qty",
    "cost_price",
    "barcode",
}

PUTAWAY_FIELDS = {"stock_in_item_id", "location_id", "qty"}


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


def _to_money(value: Any, label: str) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < 0:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为负数")
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


def _validate_stock_in_type(value: Any) -> str:
    stock_in_type = str(value or "PURCHASE").strip().upper()
    if stock_in_type not in STOCK_IN_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"入库类型仅支持 {', '.join(sorted(STOCK_IN_TYPES))}",
        )
    return stock_in_type


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    if product.is_disabled:
        raise BizException(ErrorCode.BAD_REQUEST, f"商品已禁用：{product.name}")
    return product


def _validate_sku(
    db: Session, *, product_id: int, sku_id: int | None
) -> ProductSku:
    if sku_id is None:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "库位库存按商品规格管理，入库明细必须选择商品规格",
        )
    sku = db.get(ProductSku, sku_id)
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")
    if not sku.is_active:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格已停用")
    return sku


def _purchase_order_items(
    db: Session, purchase_order_id: int
) -> dict[tuple[int, int], PurchaseOrderItem]:
    rows = list(
        db.scalars(
            select(PurchaseOrderItem)
            .where(PurchaseOrderItem.order_id == purchase_order_id)
            .order_by(PurchaseOrderItem.id)
        )
    )
    result: dict[tuple[int, int], PurchaseOrderItem] = {}
    for row in rows:
        if row.sku_id is None:
            continue
        result[(row.product_id, row.sku_id)] = row
    return result


def _get_purchase_order_or_404(
    db: Session, purchase_order_id: int
) -> PurchaseOrder:
    order = db.get(PurchaseOrder, purchase_order_id)
    if order is None:
        raise BizException(ErrorCode.NOT_FOUND, "采购订单不存在")
    return order


def _inbound_warehouse(
    db: Session,
    *,
    warehouse_id: int,
    purchase_order: PurchaseOrder | None = None,
) -> Warehouse:
    warehouse = warehouse_service.ensure_warehouse_selectable(db, warehouse_id)
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "入库仓库必须为实物仓")
    if purchase_order is not None:
        purchase_order_service.ensure_inbound_warehouse(
            db,
            order=purchase_order,
            warehouse_id=warehouse.id,
        )
    return warehouse


def _normalize_item(
    db: Session,
    *,
    data: dict,
    default_cost_price: Decimal | None = None,
) -> dict[str, Any]:
    _check_unknown_fields(data, PURCHASE_ITEM_FIELDS)
    product_id = _to_int_id(data.get("product_id"), "商品")
    sku_id = _optional_int_id(data.get("sku_id"), "商品规格")
    product = _get_product_or_404(db, product_id)
    sku = _validate_sku(db, product_id=product.id, sku_id=sku_id)
    qty = _to_qty(data.get("qty"), "入库数量")

    if data.get("cost_price") not in (None, ""):
        cost_price = _to_money(data["cost_price"], "入库成本")
    elif default_cost_price is not None:
        cost_price = _to_money(default_cost_price, "入库成本")
    elif sku.cost_price is not None:
        cost_price = _to_money(sku.cost_price, "入库成本")
    elif product.cost_price is not None:
        cost_price = _to_money(product.cost_price, "入库成本")
    else:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"无法确定商品入库成本：{product.name}",
        )

    barcode = _optional_text(data.get("barcode"), "条形码", 64) or sku.barcode
    return {
        "product_id": product.id,
        "sku_id": sku.id,
        "qty": qty,
        "cost_price": cost_price,
        "amount": round_money(qty * cost_price),
        "barcode": barcode,
    }


def _normalize_items(
    db: Session,
    *,
    raw_items: Any,
    purchase_order: PurchaseOrder | None = None,
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少添加一条入库商品明细")
    if len(raw_items) > MAX_STOCK_IN_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"单张入库单最多 {MAX_STOCK_IN_ITEMS} 条明细",
        )

    order_item_map = (
        _purchase_order_items(db, purchase_order.id)
        if purchase_order is not None
        else {}
    )
    result: list[dict[str, Any]] = []
    keys: set[tuple[int, int]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "入库商品明细格式错误")
        product_id = _to_int_id(raw.get("product_id"), "商品")
        sku_id = _optional_int_id(raw.get("sku_id"), "商品规格")
        default_cost = None
        if purchase_order is not None:
            if sku_id is None:
                raise BizException(
                    ErrorCode.BAD_REQUEST,
                    "关联采购订单时，入库明细必须选择商品规格",
                )
            order_item = order_item_map.get((product_id, sku_id))
            if order_item is None:
                raise BizException(
                    ErrorCode.BAD_REQUEST,
                    "入库商品规格不在关联采购订单中",
                )
            default_cost = order_item.purchase_price
        item = _normalize_item(
            db,
            data=raw,
            default_cost_price=default_cost,
        )
        key = (item["product_id"], item["sku_id"])
        if key in keys:
            raise BizException(ErrorCode.BAD_REQUEST, "同一商品规格不能重复")
        keys.add(key)
        result.append(item)

    if purchase_order is not None:
        for item in result:
            order_item = order_item_map[(item["product_id"], item["sku_id"])]
            outstanding = order_item.qty - order_item.arrived_qty
            if item["qty"] > outstanding:
                raise BizException(
                    ErrorCode.CONFLICT,
                    f"入库数量超过采购订单未到货数量：{order_item.id}",
                )
    return result


def _stock_in_items(db: Session, stock_in_id: int) -> list[StockInItem]:
    return list(
        db.scalars(
            select(StockInItem)
            .where(StockInItem.stock_in_id == stock_in_id)
            .order_by(StockInItem.id)
        )
    )


def _recalculate_stock_in(db: Session, stock_in: StockIn) -> None:
    stock_in.total_qty = sum(
        (item.qty for item in _stock_in_items(db, stock_in.id)),
        ZERO_QTY,
    )
    db.flush()


def get_stock_in_or_404(db: Session, stock_in_id: int, *, check_scope: bool = True) -> StockIn:
    stock_in = db.get(StockIn, stock_in_id)
    if stock_in is None or (check_scope and not document_visible(db, stock_in.created_by, DOC_TYPE, stock_in.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购入库单不存在")
    return stock_in


def stock_in_item_dict(db: Session, item: StockInItem) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    putaway_qty = (
        db.scalar(
            select(func.sum(Putaway.qty)).where(
                Putaway.source_type == "STOCK_IN",
                Putaway.source_id == item.stock_in_id,
                Putaway.product_id == item.product_id,
                Putaway.sku_id == item.sku_id,
            )
        )
        or ZERO_QTY
    )
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "sku_id": item.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "qty": item.qty,
        "cost_price": item.cost_price,
        "amount": item.amount,
        "barcode": item.barcode,
        "putaway_qty": Decimal(putaway_qty),
        "pending_putaway_qty": max(Decimal(item.qty) - Decimal(putaway_qty), ZERO_QTY),
    }


def _approval_state(db: Session, stock_in_id: int) -> dict:
    return approval_service.get_state(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_in_id,
    )["state"]


def stock_in_dict(
    db: Session,
    stock_in: StockIn,
    *,
    with_items: bool = False,
    with_approval: bool = False,
) -> dict:
    warehouse = db.get(Warehouse, stock_in.warehouse_id)
    purchase_order = (
        db.get(PurchaseOrder, stock_in.purchase_order_id)
        if stock_in.purchase_order_id
        else None
    )
    created_by = db.get(Staff, stock_in.created_by) if stock_in.created_by else None
    auditor = db.get(Staff, stock_in.auditor_id) if stock_in.auditor_id else None
    items = _stock_in_items(db, stock_in.id)
    putaway_qty = (
        db.scalar(
            select(func.sum(Putaway.qty)).where(
                Putaway.source_type == "STOCK_IN",
                Putaway.source_id == stock_in.id,
            )
        )
        or ZERO_QTY
    )
    result = {
        "id": stock_in.id,
        "stock_in_no": stock_in.stock_in_no,
        "warehouse_id": stock_in.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "stock_in_type": stock_in.stock_in_type,
        "purchase_order_id": stock_in.purchase_order_id,
        "purchase_order_no": purchase_order.order_no if purchase_order else None,
        "status": stock_in.status,
        "audit_status": stock_in.audit_status,
        "total_qty": stock_in.total_qty,
        "putaway_qty": Decimal(putaway_qty),
        "pending_putaway_qty": max(
            Decimal(stock_in.total_qty) - Decimal(putaway_qty),
            ZERO_QTY,
        ),
        "remark": stock_in.remark,
        "created_by": stock_in.created_by,
        "created_by_name": created_by.name if created_by else None,
        "auditor_id": stock_in.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": stock_in.audit_time,
        "created_at": stock_in.created_at,
        "updated_at": stock_in.updated_at,
        "item_count": len(items),
    }
    if with_items:
        result["items"] = [stock_in_item_dict(db, item) for item in items]
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=DOC_TYPE,
            doc_id=stock_in.id,
        )
    return result


def list_stock_ins(
    db: Session,
    *,
    warehouse_id: int | None = None,
    purchase_order_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, StockIn.created_by)
    if warehouse_id is not None:
        conditions.append(StockIn.warehouse_id == warehouse_id)
    if purchase_order_id is not None:
        conditions.append(StockIn.purchase_order_id == purchase_order_id)
    if status:
        conditions.append(StockIn.status == status.upper())
    if audit_status:
        conditions.append(StockIn.audit_status == audit_status.upper())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                StockIn.stock_in_no.like(like),
                PurchaseOrder.order_no.like(like),
            )
        )

    total = (
        db.scalar(
            select(func.count(StockIn.id))
            .outerjoin(PurchaseOrder, PurchaseOrder.id == StockIn.purchase_order_id)
            .where(*conditions)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(StockIn)
            .outerjoin(PurchaseOrder, PurchaseOrder.id == StockIn.purchase_order_id)
            .where(*conditions)
            .order_by(StockIn.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [stock_in_dict(db, row) for row in rows],
    }


def _stock_in_values(
    db: Session,
    *,
    data: dict,
    existing: StockIn | None = None,
) -> tuple[dict[str, Any], PurchaseOrder | None]:
    _check_unknown_fields(data, STOCK_IN_FIELDS)
    values: dict[str, Any] = {}

    if existing is None or "warehouse_id" in data:
        warehouse_id = _optional_int_id(data.get("warehouse_id"), "入库仓库")
        if warehouse_id is None:
            raise BizException(ErrorCode.BAD_REQUEST, "入库仓库不能为空")
        values["warehouse_id"] = warehouse_id
    else:
        warehouse_id = existing.warehouse_id

    purchase_order_id = (
        _optional_int_id(data.get("purchase_order_id"), "采购订单")
        if "purchase_order_id" in data
        else (existing.purchase_order_id if existing is not None else None)
    )
    purchase_order = (
        _get_purchase_order_or_404(db, purchase_order_id)
        if purchase_order_id is not None
        else None
    )
    if "purchase_order_id" in data:
        values["purchase_order_id"] = purchase_order_id

    _inbound_warehouse(
        db,
        warehouse_id=warehouse_id,
        purchase_order=purchase_order,
    )

    if existing is None or "stock_in_type" in data:
        stock_in_type = _validate_stock_in_type(
            data.get(
                "stock_in_type",
                existing.stock_in_type if existing is not None else "PURCHASE",
            )
        )
        if purchase_order is not None and stock_in_type != "PURCHASE":
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "关联采购订单时，入库类型必须为 PURCHASE",
            )
        values["stock_in_type"] = stock_in_type
    elif purchase_order is not None and existing.stock_in_type != "PURCHASE":
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "关联采购订单时，入库类型必须为 PURCHASE",
        )

    if "remark" in data:
        values["remark"] = _optional_text(data["remark"], "备注", 255)
    return values, purchase_order


def create_stock_in(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> StockIn:
    values, purchase_order = _stock_in_values(db, data=data)
    items = _normalize_items(
        db,
        raw_items=data.get("items"),
        purchase_order=purchase_order,
    )
    stock_in = StockIn(
        stock_in_no=generate_serial(SerialPrefix.STOCK_IN, dt=_now()),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
        total_qty=sum((item["qty"] for item in items), ZERO_QTY),
        **values,
    )
    db.add(stock_in)
    db.flush()
    for item in items:
        db.add(StockInItem(stock_in_id=stock_in.id, **item))
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(stock_in.id),
        summary=f"新增采购入库单：{stock_in.stock_in_no}",
        detail={
            "warehouse_id": stock_in.warehouse_id,
            "purchase_order_id": stock_in.purchase_order_id,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return stock_in


def _ensure_editable(db: Session, stock_in: StockIn) -> None:
    if stock_in.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前入库单状态不允许修改")
    state = _approval_state(db, stock_in.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "入库单已进入审批流程，不能修改")


def update_stock_in(
    db: Session,
    *,
    stock_in_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockIn:
    stock_in = get_stock_in_or_404(db, stock_in_id)
    _ensure_editable(db, stock_in)
    values, purchase_order = _stock_in_values(
        db,
        data=data,
        existing=stock_in,
    )
    for field, value in values.items():
        setattr(stock_in, field, value)

    if "items" in data:
        items = _normalize_items(
            db,
            raw_items=data["items"],
            purchase_order=purchase_order,
        )
        for item in _stock_in_items(db, stock_in.id):
            db.delete(item)
        db.flush()
        for item in items:
            db.add(StockInItem(stock_in_id=stock_in.id, **item))
        db.flush()
    elif purchase_order is not None:
        current_items = _stock_in_items(db, stock_in.id)
        order_item_map = _purchase_order_items(db, purchase_order.id)
        for item in current_items:
            order_item = order_item_map.get((item.product_id, item.sku_id))
            if order_item is None:
                raise BizException(
                    ErrorCode.BAD_REQUEST,
                    "入库商品规格不在关联采购订单中",
                )
            if item.qty > order_item.qty - order_item.arrived_qty:
                raise BizException(
                    ErrorCode.CONFLICT,
                    f"入库数量超过采购订单未到货数量：{order_item.id}",
                )

    _recalculate_stock_in(db, stock_in)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(stock_in.id),
        summary=f"修改采购入库单：{stock_in.stock_in_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return stock_in


def delete_stock_in(
    db: Session, *, stock_in_id: int, staff: Staff, commit: bool = True
) -> None:
    stock_in = get_stock_in_or_404(db, stock_in_id)
    _ensure_editable(db, stock_in)
    if db.scalar(
        select(func.count(Putaway.id)).where(
            Putaway.source_type == "STOCK_IN",
            Putaway.source_id == stock_in.id,
        )
    ):
        raise BizException(ErrorCode.CONFLICT, "入库单已有上架记录，不能删除")
    for item in _stock_in_items(db, stock_in.id):
        db.delete(item)
    stock_in_no = stock_in.stock_in_no
    db.delete(stock_in)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(stock_in.id),
        summary=f"删除采购入库单：{stock_in_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(stock_in: StockIn, state: dict) -> None:
    status = state["status"]
    if status == "APPROVED":
        stock_in.audit_status = AUDIT_APPROVED
    elif status == "REJECTED":
        stock_in.audit_status = AUDIT_REJECTED
    else:
        stock_in.audit_status = AUDIT_PENDING


def submit_stock_in(
    db: Session, *, stock_in_id: int, staff: Staff, commit: bool = True
) -> StockIn:
    stock_in = get_stock_in_or_404(db, stock_in_id)
    _ensure_editable(db, stock_in)
    if not _stock_in_items(db, stock_in.id):
        raise BizException(ErrorCode.BAD_REQUEST, "入库单没有商品明细，不能提交")
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_in.id,
        doc_no=stock_in.stock_in_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(stock_in, state)
    stock_in.status = STATUS_PENDING
    stock_in.auditor_id = None
    stock_in.audit_time = None
    _commit(db, commit)
    return stock_in


def _arrival_payload(
    db: Session, stock_in: StockIn
) -> tuple[PurchaseOrder, list[dict[str, Any]]]:
    if stock_in.purchase_order_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "入库单未关联采购订单")
    order = _get_purchase_order_or_404(db, stock_in.purchase_order_id)
    order_item_map = _purchase_order_items(db, order.id)
    payload: list[dict[str, Any]] = []
    for item in _stock_in_items(db, stock_in.id):
        order_item = order_item_map.get((item.product_id, item.sku_id))
        if order_item is None:
            raise BizException(
                ErrorCode.CONFLICT,
                "入库商品规格不在关联采购订单中",
            )
        outstanding = order_item.qty - order_item.arrived_qty
        if item.qty > outstanding:
            raise BizException(
                ErrorCode.CONFLICT,
                f"入库数量超过采购订单未到货数量：{order_item.id}",
            )
        payload.append({"order_item_id": order_item.id, "qty": item.qty})
    return order, payload


def approve_stock_in(
    db: Session,
    *,
    stock_in_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> StockIn:
    stock_in = get_stock_in_or_404(db, stock_in_id, check_scope=False)
    if stock_in.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前入库单状态不能审核")
    state_before = _approval_state(db, stock_in.id)
    is_final_step = (
        state_before["current_step"] is not None
        and state_before["current_step"] == state_before["total_steps"]
    )
    arrival_payload: list[dict[str, Any]] | None = None
    if is_final_step and stock_in.purchase_order_id is not None:
        _, arrival_payload = _arrival_payload(db, stock_in)

    state = approval_service.approve(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_in.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(stock_in, state)
    if state["status"] == "APPROVED":
        order = None
        if arrival_payload is not None:
            order = purchase_order_service.record_arrival(
                db,
                order_id=stock_in.purchase_order_id,
                warehouse_id=stock_in.warehouse_id,
                items=arrival_payload,
                staff=staff,
                commit=False,
            )
        stock_in.status = STATUS_APPROVED
        stock_in.auditor_id = staff.id
        stock_in.audit_time = _now()
        generate_stock_in_voucher(
            db, stock_in=stock_in, staff=staff, commit=False
        )
        if order is not None and staff.company_id:
            from app.services.notification_service import enqueue_purchase_order_arrival

            enqueue_purchase_order_arrival(
                db,
                company_id=staff.company_id,
                stock_in_id=stock_in.id,
                order=order,
            )
    else:
        stock_in.status = STATUS_PENDING
    _commit(db, commit)
    return stock_in


def generate_stock_in_voucher(
    db: Session,
    *,
    stock_in: StockIn,
    staff: Staff,
    commit: bool = False,
) -> Voucher | None:
    """按入库类型生成公司级会计分录；调拨入库不生成凭证。"""
    if stock_in.stock_in_type == "TRANSFER":
        return None
    amount = sum(
        (
            Decimal(item.amount or ZERO_QTY)
            for item in _stock_in_items(db, stock_in.id)
        ),
        ZERO_QTY,
    )
    if amount <= ZERO_QTY:
        return None
    return voucher_service.create_stock_in_voucher(
        db,
        stock_in_type=stock_in.stock_in_type,
        stock_in_no=stock_in.stock_in_no,
        amount=amount,
        staff=staff,
        serial_generator=generate_serial,
        commit=commit,
    )


def reject_stock_in(
    db: Session,
    *,
    stock_in_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> StockIn:
    stock_in = get_stock_in_or_404(db, stock_in_id, check_scope=False)
    if stock_in.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前入库单状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_in.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(stock_in, state)
    stock_in.status = STATUS_REJECTED
    stock_in.auditor_id = staff.id
    stock_in.audit_time = _now()
    _commit(db, commit)
    return stock_in


def _putaway_quantity(
    db: Session, *, stock_in_id: int, product_id: int, sku_id: int | None
) -> Decimal:
    value = (
        db.scalar(
            select(func.sum(Putaway.qty)).where(
                Putaway.source_type == "STOCK_IN",
                Putaway.source_id == stock_in_id,
                Putaway.product_id == product_id,
                Putaway.sku_id == sku_id,
            )
        )
        or ZERO_QTY
    )
    return Decimal(value)


def list_pending_putaways(
    db: Session,
    *,
    warehouse_id: int | None = None,
    keyword: str | None = None,
) -> list[dict]:
    """按仓库 + 商品规格汇总已审核待上架和已上架数量。"""
    putaway_totals = (
        select(
            Putaway.source_id.label("source_id"),
            Putaway.product_id.label("product_id"),
            Putaway.sku_id.label("sku_id"),
            func.sum(Putaway.qty).label("putaway_qty"),
        )
        .where(Putaway.source_type == "STOCK_IN")
        .group_by(
            Putaway.source_id,
            Putaway.product_id,
            Putaway.sku_id,
        )
        .subquery()
    )
    conditions = [StockIn.status == STATUS_APPROVED]
    if warehouse_id is not None:
        conditions.append(StockIn.warehouse_id == warehouse_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Product.name.like(like),
                Product.code.like(like),
                ProductSku.spec_code.like(like),
            )
        )
    stmt = (
        select(
            StockIn.warehouse_id,
            StockInItem.product_id,
            StockInItem.sku_id,
            func.sum(StockInItem.qty).label("received_qty"),
            func.sum(func.coalesce(putaway_totals.c.putaway_qty, 0)).label(
                "putaway_qty"
            ),
        )
        .join(StockIn, StockIn.id == StockInItem.stock_in_id)
        .join(Product, Product.id == StockInItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockInItem.sku_id)
        .outerjoin(
            putaway_totals,
            and_(
                putaway_totals.c.source_id == StockIn.id,
                putaway_totals.c.product_id == StockInItem.product_id,
                putaway_totals.c.sku_id == StockInItem.sku_id,
            ),
        )
        .where(*conditions)
        .group_by(
            StockIn.warehouse_id,
            StockInItem.product_id,
            StockInItem.sku_id,
        )
        .order_by(StockIn.warehouse_id, StockInItem.product_id, StockInItem.sku_id)
    )

    result: list[dict] = []
    for row in db.execute(stmt):
        received_qty = Decimal(row.received_qty)
        putaway_qty = Decimal(row.putaway_qty or 0)
        pending_qty = max(received_qty - putaway_qty, ZERO_QTY)
        if pending_qty <= ZERO_QTY:
            continue
        warehouse = db.get(Warehouse, row.warehouse_id)
        product = db.get(Product, row.product_id)
        sku = db.get(ProductSku, row.sku_id) if row.sku_id else None
        result.append(
            {
                "warehouse_id": row.warehouse_id,
                "warehouse_name": warehouse.name if warehouse else None,
                "product_id": row.product_id,
                "product_name": product.name if product else None,
                "product_code": product.code if product else None,
                "sku_id": row.sku_id,
                "spec_name": sku.spec_name if sku else None,
                "spec_code": sku.spec_code if sku else None,
                "received_qty": received_qty,
                "putaway_qty": putaway_qty,
                "pending_putaway_qty": pending_qty,
            }
        )
    return result


def _validate_putaway_location(
    db: Session, *, warehouse_id: int, location_id: int
) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise BizException(ErrorCode.NOT_FOUND, "库位不存在")
    if location.warehouse_id != warehouse_id:
        raise BizException(ErrorCode.BAD_REQUEST, "库位不属于入库仓库")
    if not location.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "库位已停用，不能上架")
    if location.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟库位不能用于实际入库上架")
    return location


def _upsert_stock_location(
    db: Session,
    *,
    location_id: int,
    sku_id: int,
    qty: Decimal,
    status: str,
) -> StockLocation:
    stock_location = db.scalar(
        select(StockLocation).where(
            StockLocation.location_id == location_id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == status,
        )
    )
    if stock_location is None:
        stock_location = StockLocation(
            location_id=location_id,
            product_sku_id=sku_id,
            qty=qty,
            status=status,
        )
        db.add(stock_location)
    else:
        stock_location.qty += qty
    db.flush()
    return stock_location


def putaway_stock(
    db: Session,
    *,
    stock_in_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> list[Putaway]:
    _check_unknown_fields(data, {"items"})
    stock_in = get_stock_in_or_404(db, stock_in_id)
    if stock_in.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "入库单审核通过后才能上架")
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "上架明细不能为空")

    warehouse = db.get(Warehouse, stock_in.warehouse_id)
    stock_status = (
        "DEFECTIVE"
        if warehouse is not None and warehouse.warehouse_type == "DEFECTIVE"
        else "NORMAL"
    )
    item_map = {item.id: item for item in _stock_in_items(db, stock_in.id)}
    remaining = {
        item.id: item.qty
        - _putaway_quantity(
            db,
            stock_in_id=stock_in.id,
            product_id=item.product_id,
            sku_id=item.sku_id,
        )
        for item in item_map.values()
    }
    purchase_order = (
        db.get(PurchaseOrder, stock_in.purchase_order_id)
        if stock_in.purchase_order_id
        else None
    )

    created: list[Putaway] = []
    created_details: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "上架明细格式错误")
        _check_unknown_fields(raw, PUTAWAY_FIELDS)
        item_id = _to_int_id(raw.get("stock_in_item_id"), "入库明细")
        location_id = _to_int_id(raw.get("location_id"), "上架库位")
        qty = _to_qty(raw.get("qty"), "上架数量")
        item = item_map.get(item_id)
        if item is None:
            raise BizException(ErrorCode.BAD_REQUEST, "入库明细不属于当前入库单")

        pending = remaining[item.id]
        if qty > pending:
            raise BizException(
                ErrorCode.CONFLICT,
                "上架数量超过待上架数量",
            )
        _validate_putaway_location(
            db,
            warehouse_id=stock_in.warehouse_id,
            location_id=location_id,
        )
        warehouse_service.ensure_location_capacity(
            db,
            location_id,
            added_qty=qty,
        )
        _upsert_stock_location(
            db,
            location_id=location_id,
            sku_id=item.sku_id,
            qty=qty,
            status=stock_status,
        )
        product = db.get(Product, item.product_id)
        putaway = Putaway(
            warehouse_id=stock_in.warehouse_id,
            source_type="STOCK_IN",
            source_id=stock_in.id,
            location_id=location_id,
            product_id=item.product_id,
            sku_id=item.sku_id,
            qty=qty,
            operator_id=staff.id,
        )
        db.add(putaway)
        db.flush()
        created.append(putaway)
        created_details.append(
            {
                "stock_in_item_id": item.id,
                "location_id": location_id,
                "qty": str(qty),
            }
        )
        remaining[item.id] = pending - qty

        total_cost = (
            round_money(qty * item.cost_price)
            if item.cost_price is not None
            else None
        )
        db.add(
            StockFlow(
                warehouse_id=stock_in.warehouse_id,
                product_id=item.product_id,
                sku_id=item.sku_id,
                location_id=location_id,
                unit_id=product.main_unit_id if product else None,
                direction="IN",
                biz_type=(
                    "PURCHASE_IN"
                    if purchase_order is not None
                    else (
                        "AFTER_SALE"
                        if stock_in.stock_in_type == "AFTER_SALE"
                        else "MANUAL_IN"
                    )
                ),
                qty=qty,
                cost_price=item.cost_price,
                total_cost=total_cost,
                source_no=stock_in.stock_in_no,
                order_no=purchase_order.order_no if purchase_order else None,
                flow_time=_now(),
                remark="采购入库上架",
            )
        )
        db.flush()

    write_audit(
        db,
        action="PUTAWAY",
        target_type=DOC_TYPE,
        target_id=str(stock_in.id),
        summary=f"采购入库上架：{stock_in.stock_in_no}",
        detail={
            "items": created_details
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return created


def putaway_dict(db: Session, putaway: Putaway) -> dict:
    stock_in = (
        db.get(StockIn, putaway.source_id)
        if putaway.source_type == "STOCK_IN"
        else None
    )
    warehouse = db.get(Warehouse, putaway.warehouse_id)
    location = db.get(Location, putaway.location_id)
    product = db.get(Product, putaway.product_id)
    sku = db.get(ProductSku, putaway.sku_id) if putaway.sku_id else None
    operator = db.get(Staff, putaway.operator_id) if putaway.operator_id else None
    purchase_order = (
        db.get(PurchaseOrder, stock_in.purchase_order_id)
        if stock_in is not None and stock_in.purchase_order_id
        else None
    )
    return {
        "id": putaway.id,
        "source_type": putaway.source_type,
        "source_id": putaway.source_id,
        "stock_in_no": stock_in.stock_in_no if stock_in else None,
        "purchase_order_no": purchase_order.order_no if purchase_order else None,
        "warehouse_id": putaway.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "location_id": putaway.location_id,
        "location_code": location.code if location else None,
        "product_id": putaway.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "sku_id": putaway.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "qty": putaway.qty,
        "operator_id": putaway.operator_id,
        "operator_name": operator.name if operator else None,
        "created_at": putaway.created_at,
    }


def list_putaways(
    db: Session,
    *,
    warehouse_id: int | None = None,
    location_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    stock_in_id: int | None = None,
) -> list[Putaway]:
    conditions = []
    if warehouse_id is not None:
        conditions.append(Putaway.warehouse_id == warehouse_id)
    if location_id is not None:
        conditions.append(Putaway.location_id == location_id)
    if product_id is not None:
        conditions.append(Putaway.product_id == product_id)
    if sku_id is not None:
        conditions.append(Putaway.sku_id == sku_id)
    if stock_in_id is not None:
        conditions.extend(
            [
                Putaway.source_type == "STOCK_IN",
                Putaway.source_id == stock_in_id,
            ]
        )
    return list(
        db.scalars(
            select(Putaway)
            .where(*conditions)
            .order_by(Putaway.id.desc())
        )
    )
