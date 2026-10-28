"""订单售后与退货管理服务（TASK-040）。

口径说明：
- 退货、换货保存后可继续维护；提交时必须绑定退货登记（R26）。
- 退货运费为空表示不退运费；填写 0.00 表示明确不退金额，但仍视为已填写。
- 退货确认后生成售后入库单，并将商品上架到退回仓库的可用库位，写入统一库存流水。
- 换货仅登记换货商品与补差信息，不自动扣减换货商品库存；换货发货沿用订单发货链路。
- 售后入库审核后生成退货成本冲回会计分录；退款金额作为售后单据字段留痕。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.order import (
    ExchangeOrder,
    ExchangeOrderItem,
    Order,
    OrderItem,
    OrderShipment,
    OrderShipmentItem,
    ReturnOrder,
    ReturnOrderItem,
    ReturnRegister,
)
from app.models.org import Staff
from app.models.platform import Shop
from app.models.product import Product, ProductSku
from app.models.stock import StockIn, StockInItem
from app.models.warehouse import Location, Warehouse
from app.services import stock_in_service, warehouse_service
from app.services.excel import Column, SheetSpec, export_data

DOC_TYPE_RETURN = "return_order"
DOC_TYPE_EXCHANGE = "exchange_order"
DOC_TYPE_REGISTER = "return_register"

STATUS_PENDING = "PENDING"
STATUS_SUBMITTED = "SUBMITTED"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_CANCELLED = "CANCELLED"

STATUS_LABELS = {
    STATUS_PENDING: "待提交",
    STATUS_SUBMITTED: "待确认",
    STATUS_CONFIRMED: "已确认",
    STATUS_CANCELLED: "已取消",
}

COMPENSATE_INCOME = "INCOME"
COMPENSATE_EXPENSE = "EXPENSE"
COMPENSATE_DIRECTIONS = {COMPENSATE_INCOME, COMPENSATE_EXPENSE}

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
ZERO_MONEY = Decimal("0.00")
MAX_ITEMS = 500

RETURN_FIELDS = {
    "order_id",
    "warehouse_id",
    "reason",
    "logistics_company",
    "tracking_no",
    "buyer_phone",
    "is_arrived",
    "freight",
    "is_rejected_sign",
    "return_person",
    "goods_status",
    "need_return_goods",
    "items",
}
RETURN_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "qty",
    "refund_amount",
}
EXCHANGE_FIELDS = {
    "order_id",
    "reason",
    "is_cod",
    "is_return",
    "compensate_order_no",
    "compensate_amount",
    "compensate_direction",
    "register_id",
    "items",
}
EXCHANGE_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "qty",
    "exchange_qty",
}
REGISTER_FIELDS = {
    "return_order_id",
    "exchange_order_id",
    "logistics_company",
    "tracking_no",
    "sender",
    "sender_phone",
    "product_info",
    "image",
}

RETURN_EXPORT_TEMPLATE = SheetSpec(
    name="after_sale_return_export",
    sheet_title="直营退货",
    columns=[
        Column("return_no", "退货编号", width=22),
        Column("order_no", "订单号", width=24),
        Column("order_status", "订单状态", width=14),
        Column("shop_name", "店铺名称", width=20),
        Column("product_info", "商品信息", width=36),
        Column("buyer_phone", "买家手机号", width=18),
        Column("status_label", "退货状态", width=14),
        Column("refund_amount", "退款金额", type="decimal", width=14),
        Column("goods_status", "商品状态", width=14),
        Column("logistics_company", "寄回快递", width=18),
        Column("tracking_no", "运单号", width=22),
        Column("stock_in_no", "退货入库单", width=22),
        Column("created_at", "创建时间", width=20),
        Column("audit_time", "审核时间", width=20),
    ],
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"不支持的字段：{', '.join(sorted(unknown))}",
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
        result = Decimal(str(value)).quantize(
            QTY_QUANT,
            rounding=ROUND_HALF_UP,
        )
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result <= ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _to_money(value: Any, label: str, *, allow_zero: bool = True) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < ZERO_MONEY or (not allow_zero and result == ZERO_MONEY):
        operator = "不能为负数" if allow_zero else "必须大于 0"
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}{operator}")
    return result


def _optional_money(value: Any, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    return _to_money(value, label)


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "是"}:
            return True
        if text in {"false", "0", "no", "n", "否", ""}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"{label}长度不能超过 {max_length}",
        )
    return text


def _order_or_404(db: Session, order_id: int) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise BizException(ErrorCode.NOT_FOUND, "订单不存在")
    return order


def _warehouse_or_404(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db,
        warehouse_id,
    )
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "退货仓库必须为实物仓")
    return warehouse


def get_return_or_404(db: Session, return_id: int) -> ReturnOrder:
    record = db.get(ReturnOrder, return_id)
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "退货单不存在")
    return record


def get_exchange_or_404(db: Session, exchange_id: int) -> ExchangeOrder:
    record = db.get(ExchangeOrder, exchange_id)
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "换货单不存在")
    return record


def get_register_or_404(db: Session, register_id: int) -> ReturnRegister:
    record = db.get(ReturnRegister, register_id)
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "退货登记不存在")
    return record


def _resolve_order_item(
    db: Session,
    *,
    order_id: int,
    product_id: int,
    sku_id: int | None,
) -> OrderItem:
    if sku_id is not None:
        item = db.scalar(
            select(OrderItem).where(
                OrderItem.order_id == order_id,
                OrderItem.product_id == product_id,
                OrderItem.sku_id == sku_id,
            )
        )
        if item is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "商品规格不属于该订单",
            )
    else:
        rows = list(
            db.scalars(
                select(OrderItem)
                .where(
                    OrderItem.order_id == order_id,
                    OrderItem.product_id == product_id,
                )
                .order_by(OrderItem.id)
            )
        )
        if not rows:
            raise BizException(ErrorCode.BAD_REQUEST, "商品不属于该订单")
        if len(rows) > 1:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "订单中该商品存在多个规格，请指定商品规格",
            )
        item = rows[0]
    if item.sku_id is None:
        raise BizException(
            ErrorCode.CONFLICT,
            "订单商品缺少商品规格，不能创建售后单",
        )
    return item


def _normalize_return_item(
    db: Session,
    *,
    order_id: int,
    raw: dict,
) -> dict[str, Any]:
    _check_unknown_fields(raw, RETURN_ITEM_FIELDS)
    product_id = _to_int_id(raw.get("product_id"), "商品")
    sku_id = _optional_int_id(raw.get("sku_id"), "商品规格")
    order_item = _resolve_order_item(
        db,
        order_id=order_id,
        product_id=product_id,
        sku_id=sku_id,
    )
    qty = _to_qty(raw.get("qty"), "退货数量")
    refund_value = raw.get("refund_amount")
    refund_amount = (
        round_money(qty * Decimal(order_item.price or 0))
        if refund_value in (None, "")
        else _to_money(refund_value, "退还金额")
    )
    return {
        "product_id": order_item.product_id,
        "sku_id": order_item.sku_id,
        "qty": qty,
        "refund_amount": refund_amount,
    }


def _normalize_exchange_item(
    db: Session,
    *,
    order_id: int,
    raw: dict,
) -> dict[str, Any]:
    _check_unknown_fields(raw, EXCHANGE_ITEM_FIELDS)
    product_id = _to_int_id(raw.get("product_id"), "商品")
    sku_id = _optional_int_id(raw.get("sku_id"), "商品规格")
    order_item = _resolve_order_item(
        db,
        order_id=order_id,
        product_id=product_id,
        sku_id=sku_id,
    )
    qty = (
        _to_qty(raw.get("qty"), "商品数量")
        if raw.get("qty") not in (None, "")
        else Decimal(order_item.qty)
    )
    exchange_qty = _to_qty(raw.get("exchange_qty"), "换货数量")
    return {
        "product_id": order_item.product_id,
        "sku_id": order_item.sku_id,
        "qty": qty,
        "exchange_qty": exchange_qty,
    }


def _normalize_items(
    db: Session,
    *,
    order_id: int,
    raw_items: Any,
    kind: str,
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        label = "退货商品" if kind == "return" else "换货商品"
        raise BizException(ErrorCode.BAD_REQUEST, f"至少添加一条{label}明细")
    if len(raw_items) > MAX_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"单张售后单最多 {MAX_ITEMS} 条商品明细",
        )
    normalizer = (
        _normalize_return_item
        if kind == "return"
        else _normalize_exchange_item
    )
    result: list[dict[str, Any]] = []
    keys: set[tuple[int, int]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "商品明细格式错误")
        item = normalizer(db, order_id=order_id, raw=raw)
        key = (item["product_id"], int(item["sku_id"]))
        if key in keys:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "同一商品规格不能重复",
            )
        keys.add(key)
        result.append(item)
    return result


def _qty_map(
    rows: list[tuple[int, int | None, Any]],
) -> dict[tuple[int, int], Decimal]:
    result: dict[tuple[int, int], Decimal] = {}
    for product_id, sku_id, qty in rows:
        if sku_id is None:
            continue
        key = (int(product_id), int(sku_id))
        result[key] = result.get(key, ZERO_QTY) + Decimal(qty or 0)
    return result


def _ordered_qty_map(
    db: Session,
    order_id: int,
) -> dict[tuple[int, int], Decimal]:
    rows = db.execute(
        select(
            OrderItem.product_id,
            OrderItem.sku_id,
            func.sum(OrderItem.qty),
        )
        .where(OrderItem.order_id == order_id)
        .group_by(OrderItem.product_id, OrderItem.sku_id)
    ).all()
    return _qty_map(rows)


def _shipped_qty_map(
    db: Session,
    order_id: int,
) -> dict[tuple[int, int], Decimal]:
    rows = db.execute(
        select(
            OrderItem.product_id,
            OrderItem.sku_id,
            func.sum(OrderShipmentItem.qty),
        )
        .join(OrderItem, OrderItem.id == OrderShipmentItem.order_item_id)
        .join(
            OrderShipment,
            OrderShipment.id == OrderShipmentItem.shipment_id,
        )
        .where(
            OrderShipment.order_id == order_id,
            OrderShipment.status != "CANCELLED",
        )
        .group_by(OrderItem.product_id, OrderItem.sku_id)
    ).all()
    return _qty_map(rows)


def _has_shipment(db: Session, order_id: int) -> bool:
    return (
        db.scalar(
            select(func.count(OrderShipment.id)).where(
                OrderShipment.order_id == order_id,
                OrderShipment.status != "CANCELLED",
            )
        )
        or 0
    ) > 0


def _active_return_qty_map(
    db: Session,
    order_id: int,
    *,
    exclude_return_id: int | None = None,
) -> dict[tuple[int, int], Decimal]:
    conditions = [
        ReturnOrder.order_id == order_id,
        ReturnOrder.status != STATUS_CANCELLED,
    ]
    if exclude_return_id is not None:
        conditions.append(ReturnOrder.id != exclude_return_id)
    rows = db.execute(
        select(
            ReturnOrderItem.product_id,
            ReturnOrderItem.sku_id,
            func.sum(ReturnOrderItem.qty),
        )
        .join(
            ReturnOrder,
            ReturnOrder.id == ReturnOrderItem.return_order_id,
        )
        .where(*conditions)
        .group_by(ReturnOrderItem.product_id, ReturnOrderItem.sku_id)
    ).all()
    return _qty_map(rows)


def _active_exchange_qty_map(
    db: Session,
    order_id: int,
    *,
    exclude_exchange_id: int | None = None,
) -> dict[tuple[int, int], Decimal]:
    conditions = [
        ExchangeOrder.order_id == order_id,
        ExchangeOrder.status != STATUS_CANCELLED,
    ]
    if exclude_exchange_id is not None:
        conditions.append(ExchangeOrder.id != exclude_exchange_id)
    rows = db.execute(
        select(
            ExchangeOrderItem.product_id,
            ExchangeOrderItem.sku_id,
            func.sum(ExchangeOrderItem.exchange_qty),
        )
        .join(
            ExchangeOrder,
            ExchangeOrder.id == ExchangeOrderItem.exchange_order_id,
        )
        .where(*conditions)
        .group_by(ExchangeOrderItem.product_id, ExchangeOrderItem.sku_id)
    ).all()
    return _qty_map(rows)


def _available_qty(
    db: Session,
    *,
    order: Order,
    product_id: int,
    sku_id: int,
    occupied: Decimal,
) -> Decimal:
    key = (product_id, sku_id)
    if _has_shipment(db, order.id):
        base = _shipped_qty_map(db, order.id).get(key, ZERO_QTY)
    else:
        base = _ordered_qty_map(db, order.id).get(key, ZERO_QTY)
    return max(base - occupied, ZERO_QTY)


def _validate_return_limits(
    db: Session,
    *,
    order: Order,
    items: list[dict[str, Any]],
    exclude_return_id: int | None = None,
) -> None:
    occupied = _active_return_qty_map(
        db,
        order.id,
        exclude_return_id=exclude_return_id,
    )
    for item in items:
        available = _available_qty(
            db,
            order=order,
            product_id=item["product_id"],
            sku_id=item["sku_id"],
            occupied=occupied.get(
                (item["product_id"], item["sku_id"]),
                ZERO_QTY,
            ),
        )
        if item["qty"] > available:
            product = db.get(Product, item["product_id"])
            label = product.name if product is not None else str(item["product_id"])
            raise BizException(
                ErrorCode.CONFLICT,
                f"{label}退货数量超过可退数量 {available}",
            )


def _validate_exchange_limits(
    db: Session,
    *,
    order: Order,
    items: list[dict[str, Any]],
    exclude_exchange_id: int | None = None,
) -> None:
    occupied = _active_exchange_qty_map(
        db,
        order.id,
        exclude_exchange_id=exclude_exchange_id,
    )
    for item in items:
        available = _available_qty(
            db,
            order=order,
            product_id=item["product_id"],
            sku_id=item["sku_id"],
            occupied=occupied.get(
                (item["product_id"], item["sku_id"]),
                ZERO_QTY,
            ),
        )
        if item["exchange_qty"] > available:
            product = db.get(Product, item["product_id"])
            label = product.name if product is not None else str(item["product_id"])
            raise BizException(
                ErrorCode.CONFLICT,
                f"{label}换货数量超过可换数量 {available}",
            )


def _return_items(db: Session, return_id: int) -> list[ReturnOrderItem]:
    return list(
        db.scalars(
            select(ReturnOrderItem)
            .where(ReturnOrderItem.return_order_id == return_id)
            .order_by(ReturnOrderItem.id)
        )
    )


def _exchange_items(
    db: Session,
    exchange_id: int,
) -> list[ExchangeOrderItem]:
    return list(
        db.scalars(
            select(ExchangeOrderItem)
            .where(ExchangeOrderItem.exchange_order_id == exchange_id)
            .order_by(ExchangeOrderItem.id)
        )
    )


def _return_register(
    db: Session,
    return_id: int,
) -> ReturnRegister | None:
    return db.scalar(
        select(ReturnRegister)
        .where(ReturnRegister.return_order_id == return_id)
        .order_by(ReturnRegister.id.desc())
        .limit(1)
    )


def _exchange_register(
    db: Session,
    exchange_id: int,
) -> ReturnRegister | None:
    return db.scalar(
        select(ReturnRegister)
        .where(ReturnRegister.exchange_order_id == exchange_id)
        .order_by(ReturnRegister.id.desc())
        .limit(1)
    )


def _product_info_for_return(db: Session, return_id: int) -> str | None:
    parts: list[str] = []
    for item in _return_items(db, return_id):
        product = db.get(Product, item.product_id)
        sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
        name = product.name if product is not None else str(item.product_id)
        if sku is not None and sku.spec_name:
            name = f"{name}/{sku.spec_name}"
        parts.append(f"{name} x {item.qty}")
    return "；".join(parts) or None


def _product_info_for_exchange(db: Session, exchange_id: int) -> str | None:
    parts: list[str] = []
    for item in _exchange_items(db, exchange_id):
        product = db.get(Product, item.product_id)
        sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
        name = product.name if product is not None else str(item.product_id)
        if sku is not None and sku.spec_name:
            name = f"{name}/{sku.spec_name}"
        parts.append(f"{name} 换货 x {item.exchange_qty}")
    return "；".join(parts) or None


def _recalculate_return(db: Session, record: ReturnOrder) -> None:
    items = _return_items(db, record.id)
    record.return_qty = sum((Decimal(item.qty or 0) for item in items), ZERO_QTY)
    item_amount = sum(
        (Decimal(item.refund_amount or 0) for item in items),
        ZERO_MONEY,
    )
    record.refund_amount = round_money(
        item_amount + (Decimal(record.freight) if record.freight is not None else 0)
    )


def _recalculate_exchange(db: Session, record: ExchangeOrder) -> None:
    record.exchange_qty = sum(
        (Decimal(item.exchange_qty or 0) for item in _exchange_items(db, record.id)),
        ZERO_QTY,
    )


def register_dict(
    db: Session,
    record: ReturnRegister,
) -> dict[str, Any]:
    target_type = None
    target_id = None
    target_no = None
    if record.return_order_id is not None:
        target = db.get(ReturnOrder, record.return_order_id)
        target_type = "RETURN"
        target_id = record.return_order_id
        target_no = target.return_no if target is not None else None
    elif record.exchange_order_id is not None:
        target = db.get(ExchangeOrder, record.exchange_order_id)
        target_type = "EXCHANGE"
        target_id = record.exchange_order_id
        target_no = target.exchange_no if target is not None else None
    return {
        "id": record.id,
        "return_order_id": record.return_order_id,
        "exchange_order_id": record.exchange_order_id,
        "target_type": target_type,
        "target_id": target_id,
        "target_no": target_no,
        "logistics_company": record.logistics_company,
        "tracking_no": record.tracking_no,
        "sender": record.sender,
        "sender_phone": record.sender_phone,
        "product_info": record.product_info,
        "image": record.image,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def return_item_dict(
    db: Session,
    item: ReturnOrderItem,
) -> dict[str, Any]:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_code": product.code if product is not None else None,
        "product_name": product.name if product is not None else None,
        "product_image": product.image if product is not None else None,
        "sku_id": item.sku_id,
        "spec_code": sku.spec_code if sku is not None else None,
        "spec_name": sku.spec_name if sku is not None else None,
        "qty": item.qty,
        "refund_amount": item.refund_amount,
    }


def exchange_item_dict(
    db: Session,
    item: ExchangeOrderItem,
) -> dict[str, Any]:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_code": product.code if product is not None else None,
        "product_name": product.name if product is not None else None,
        "product_image": product.image if product is not None else None,
        "sku_id": item.sku_id,
        "spec_code": sku.spec_code if sku is not None else None,
        "spec_name": sku.spec_name if sku is not None else None,
        "qty": item.qty,
        "exchange_qty": item.exchange_qty,
    }


def return_dict(
    db: Session,
    record: ReturnOrder,
    *,
    with_items: bool = True,
    with_register: bool = True,
) -> dict[str, Any]:
    order = db.get(Order, record.order_id)
    shop = db.get(Shop, record.shop_id)
    warehouse = db.get(Warehouse, record.warehouse_id) if record.warehouse_id else None
    stock_in = db.get(StockIn, record.stock_in_id) if record.stock_in_id else None
    items = _return_items(db, record.id) if with_items else []
    item_payloads = [return_item_dict(db, item) for item in items]
    product_info = "；".join(
        f"{item['product_name']} x {item['qty']}"
        for item in item_payloads
        if item["product_name"]
    )
    result: dict[str, Any] = {
        "id": record.id,
        "return_no": record.return_no,
        "order_id": record.order_id,
        "order_no": order.order_no if order is not None else None,
        "order_status": order.status if order is not None else None,
        "shop_id": record.shop_id,
        "shop_name": shop.name if shop is not None else None,
        "warehouse_id": record.warehouse_id,
        "warehouse_name": warehouse.name if warehouse is not None else None,
        "status": record.status,
        "status_label": STATUS_LABELS.get(record.status, record.status),
        "refund_amount": record.refund_amount,
        "return_qty": record.return_qty,
        "logistics_company": record.logistics_company,
        "tracking_no": record.tracking_no,
        "reason": record.reason,
        "buyer_phone": record.buyer_phone,
        "is_arrived": record.is_arrived,
        "freight": record.freight,
        "freight_refunded": record.freight is not None,
        "is_rejected_sign": record.is_rejected_sign,
        "return_person": record.return_person,
        "goods_status": record.goods_status,
        "need_return_goods": record.need_return_goods,
        "stock_in_id": record.stock_in_id,
        "stock_in_no": stock_in.stock_in_no if stock_in is not None else None,
        "stock_in_status": stock_in.status if stock_in is not None else None,
        "audit_time": record.audit_time,
        "product_info": product_info or None,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if with_items:
        result["items"] = item_payloads
    if with_register:
        register = _return_register(db, record.id)
        result["register"] = (
            register_dict(db, register) if register is not None else None
        )
    return result


def exchange_dict(
    db: Session,
    record: ExchangeOrder,
    *,
    with_items: bool = True,
    with_register: bool = True,
) -> dict[str, Any]:
    order = db.get(Order, record.order_id)
    shop = db.get(Shop, record.shop_id)
    items = _exchange_items(db, record.id) if with_items else []
    result: dict[str, Any] = {
        "id": record.id,
        "exchange_no": record.exchange_no,
        "order_id": record.order_id,
        "order_no": order.order_no if order is not None else None,
        "shop_id": record.shop_id,
        "shop_name": shop.name if shop is not None else None,
        "status": record.status,
        "status_label": STATUS_LABELS.get(record.status, record.status),
        "exchange_qty": record.exchange_qty,
        "reason": record.reason,
        "is_cod": record.is_cod,
        "is_return": record.is_return,
        "compensate_order_no": record.compensate_order_no,
        "compensate_amount": record.compensate_amount,
        "compensate_direction": record.compensate_direction,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if with_items:
        result["items"] = [exchange_item_dict(db, item) for item in items]
    if with_register:
        register = _exchange_register(db, record.id)
        result["register"] = (
            register_dict(db, register) if register is not None else None
        )
    return result


def list_returns(
    db: Session,
    *,
    order_id: int | None = None,
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    with_items: bool = True,
    with_register: bool = True,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if order_id is not None:
        conditions.append(ReturnOrder.order_id == order_id)
    if shop_id is not None:
        conditions.append(ReturnOrder.shop_id == shop_id)
    if warehouse_id is not None:
        conditions.append(ReturnOrder.warehouse_id == warehouse_id)
    if status:
        status_value = str(status).strip().upper()
        if status_value not in STATUS_LABELS:
            raise BizException(ErrorCode.BAD_REQUEST, "退货状态不合法")
        conditions.append(ReturnOrder.status == status_value)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                ReturnOrder.return_no.like(like),
                ReturnOrder.order_id.in_(
                    select(Order.id).where(Order.order_no.like(like))
                ),
                ReturnOrder.tracking_no.like(like),
                ReturnOrder.buyer_phone.like(like),
            )
        )
    if start_time is not None:
        conditions.append(ReturnOrder.created_at >= start_time)
    if end_time is not None:
        conditions.append(ReturnOrder.created_at <= end_time)
    total = db.scalar(select(func.count(ReturnOrder.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(ReturnOrder)
            .where(*conditions)
            .order_by(ReturnOrder.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [
            return_dict(db, row, with_items=with_items, with_register=with_register)
            for row in rows
        ],
    }


def list_exchanges(
    db: Session,
    *,
    order_id: int | None = None,
    shop_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    with_items: bool = True,
    with_register: bool = True,
) -> dict[str, Any]:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if order_id is not None:
        conditions.append(ExchangeOrder.order_id == order_id)
    if shop_id is not None:
        conditions.append(ExchangeOrder.shop_id == shop_id)
    if status:
        status_value = str(status).strip().upper()
        if status_value not in STATUS_LABELS:
            raise BizException(ErrorCode.BAD_REQUEST, "换货状态不合法")
        conditions.append(ExchangeOrder.status == status_value)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                ExchangeOrder.exchange_no.like(like),
                ExchangeOrder.order_id.in_(
                    select(Order.id).where(Order.order_no.like(like))
                ),
                ExchangeOrder.compensate_order_no.like(like),
            )
        )
    total = db.scalar(select(func.count(ExchangeOrder.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(ExchangeOrder)
            .where(*conditions)
            .order_by(ExchangeOrder.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [
            exchange_dict(db, row, with_items=with_items, with_register=with_register)
            for row in rows
        ],
    }


def create_return(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ReturnOrder:
    _check_unknown_fields(data, RETURN_FIELDS)
    order = _order_or_404(db, _to_int_id(data.get("order_id"), "订单"))
    warehouse = _warehouse_or_404(
        db,
        _to_int_id(data.get("warehouse_id"), "退回仓库"),
    )
    items = _normalize_items(
        db,
        order_id=order.id,
        raw_items=data.get("items"),
        kind="return",
    )
    _validate_return_limits(db, order=order, items=items)
    freight = _optional_money(data.get("freight"), "退回运费")
    record = ReturnOrder(
        return_no=generate_serial(SerialPrefix.RETURN_ORDER, dt=_now()),
        order_id=order.id,
        shop_id=order.shop_id,
        warehouse_id=warehouse.id,
        status=STATUS_PENDING,
        logistics_company=_optional_text(
            data.get("logistics_company"),
            "物流公司",
            64,
        ),
        tracking_no=_optional_text(data.get("tracking_no"), "物流单号", 64),
        reason=_optional_text(data.get("reason"), "退货原因", 128),
        buyer_phone=_optional_text(
            data.get("buyer_phone") or order.receiver_phone,
            "买家手机号",
            32,
        ),
        is_arrived=(
            _to_bool(data["is_arrived"], "是否到货")
            if "is_arrived" in data
            else False
        ),
        freight=freight,
        is_rejected_sign=(
            _to_bool(data["is_rejected_sign"], "是否拒签")
            if "is_rejected_sign" in data
            else False
        ),
        return_person=_optional_text(
            data.get("return_person"),
            "退货人",
            64,
        ),
        goods_status=_optional_text(
            data.get("goods_status"),
            "货物状态",
            32,
        ),
        need_return_goods=(
            _to_bool(data["need_return_goods"], "是否需寄回商品")
            if "need_return_goods" in data
            else False
        ),
    )
    db.add(record)
    db.flush()
    for item in items:
        db.add(ReturnOrderItem(return_order_id=record.id, **item))
    db.flush()
    _recalculate_return(db, record)
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE_RETURN,
        target_id=str(record.id),
        summary=f"新增退货单：{record.return_no}",
        detail={"order_id": order.id, "warehouse_id": warehouse.id},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _ensure_return_editable(record: ReturnOrder) -> None:
    if record.status != STATUS_PENDING:
        raise BizException(
            ErrorCode.CONFLICT,
            "退货单提交后不能修改",
        )


def update_return(
    db: Session,
    *,
    return_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ReturnOrder:
    _check_unknown_fields(data, RETURN_FIELDS)
    record = get_return_or_404(db, return_id)
    _ensure_return_editable(record)
    order_id = (
        _to_int_id(data["order_id"], "订单")
        if "order_id" in data
        else record.order_id
    )
    order = _order_or_404(db, order_id)
    warehouse_id = (
        _to_int_id(data["warehouse_id"], "退回仓库")
        if "warehouse_id" in data
        else record.warehouse_id
    )
    if warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "退回仓库不能为空")
    warehouse = _warehouse_or_404(db, warehouse_id)

    if "items" in data:
        items = _normalize_items(
            db,
            order_id=order.id,
            raw_items=data["items"],
            kind="return",
        )
        _validate_return_limits(
            db,
            order=order,
            items=items,
            exclude_return_id=record.id,
        )
        for item in _return_items(db, record.id):
            db.delete(item)
        db.flush()
        for item in items:
            db.add(ReturnOrderItem(return_order_id=record.id, **item))
        db.flush()
    else:
        current = [
            {
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "qty": item.qty,
            }
            for item in _return_items(db, record.id)
        ]
        if order.id != record.order_id:
            _validate_return_limits(
                db,
                order=order,
                items=current,
                exclude_return_id=record.id,
            )

    record.order_id = order.id
    record.shop_id = order.shop_id
    record.warehouse_id = warehouse.id
    for field, label, max_length in (
        ("reason", "退货原因", 128),
        ("logistics_company", "物流公司", 64),
        ("tracking_no", "物流单号", 64),
        ("buyer_phone", "买家手机号", 32),
        ("return_person", "退货人", 64),
        ("goods_status", "货物状态", 32),
    ):
        if field in data:
            setattr(record, field, _optional_text(data[field], label, max_length))
    for field, label in (
        ("is_arrived", "是否到货"),
        ("is_rejected_sign", "是否拒签"),
        ("need_return_goods", "是否需寄回商品"),
    ):
        if field in data:
            setattr(record, field, _to_bool(data[field], label))
    if "freight" in data:
        record.freight = _optional_money(data["freight"], "退回运费")
    db.flush()
    _recalculate_return(db, record)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE_RETURN,
        target_id=str(record.id),
        summary=f"修改退货单：{record.return_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    record = get_return_or_404(db, return_id)
    _ensure_return_editable(record)
    register = _return_register(db, record.id)
    if register is not None:
        db.delete(register)
    for item in _return_items(db, record.id):
        db.delete(item)
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE_RETURN,
        target_id=str(return_id),
        summary=f"删除退货单：{record.return_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def submit_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    commit: bool = True,
) -> ReturnOrder:
    record = get_return_or_404(db, return_id)
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前退货单状态不能提交")
    if not _return_items(db, record.id):
        raise BizException(ErrorCode.BAD_REQUEST, "退货单没有商品明细")
    if _return_register(db, record.id) is None:
        raise BizException(ErrorCode.CONFLICT, "退货单必须绑定退货登记后才能提交")
    record.status = STATUS_SUBMITTED
    write_audit(
        db,
        action="SUBMIT",
        target_type=DOC_TYPE_RETURN,
        target_id=str(record.id),
        summary=f"提交退货单：{record.return_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _inbound_location(
    db: Session,
    *,
    warehouse_id: int,
    location_id: int | None,
) -> Location:
    if location_id is not None:
        location = warehouse_service.get_location_or_404(db, location_id)
        if location.warehouse_id != warehouse_id:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "入库库位不属于退货仓库",
            )
        if not location.is_enabled:
            raise BizException(ErrorCode.CONFLICT, "入库库位已停用")
        if location.is_virtual:
            raise BizException(ErrorCode.CONFLICT, "虚拟库位不能用于退货入库")
        return location
    location = db.scalar(
        select(Location)
        .where(
            Location.warehouse_id == warehouse_id,
            Location.is_enabled.is_(True),
            Location.is_virtual.is_(False),
        )
        .order_by(Location.priority.desc(), Location.sort_order, Location.id)
        .limit(1)
    )
    if location is None:
        raise BizException(
            ErrorCode.CONFLICT,
            "退回仓库没有可用库位，无法完成退货入库",
        )
    return location


def _return_cost_price(
    db: Session,
    *,
    order_id: int,
    product_id: int,
    sku_id: int,
) -> Decimal:
    shipment_item = db.scalar(
        select(OrderShipmentItem)
        .join(
            OrderShipment,
            OrderShipment.id == OrderShipmentItem.shipment_id,
        )
        .join(OrderItem, OrderItem.id == OrderShipmentItem.order_item_id)
        .where(
            OrderShipment.order_id == order_id,
            OrderShipment.status != "CANCELLED",
            OrderItem.product_id == product_id,
            OrderItem.sku_id == sku_id,
            OrderShipmentItem.cost_price.is_not(None),
        )
        .order_by(OrderShipment.shipped_at.desc(), OrderShipment.id.desc())
        .limit(1)
    )
    if shipment_item is not None and shipment_item.cost_price is not None:
        return round_money(shipment_item.cost_price)
    sku = db.get(ProductSku, sku_id)
    product = db.get(Product, product_id)
    if sku is not None and sku.cost_price is not None:
        return round_money(sku.cost_price)
    if product is not None and product.cost_price is not None:
        return round_money(product.cost_price)
    raise BizException(
        ErrorCode.BAD_REQUEST,
        f"无法确定退货商品成本：{product.name if product else product_id}",
    )


def confirm_return(
    db: Session,
    *,
    return_id: int,
    location_id: int | None,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> ReturnOrder:
    record = get_return_or_404(db, return_id)
    if record.status not in {STATUS_PENDING, STATUS_SUBMITTED}:
        raise BizException(ErrorCode.CONFLICT, "当前退货单状态不能确认")
    if _return_register(db, record.id) is None:
        raise BizException(ErrorCode.CONFLICT, "退货单必须绑定退货登记后才能确认")
    if record.warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "退货单未设置退回仓库")
    warehouse = _warehouse_or_404(db, record.warehouse_id)
    location = _inbound_location(
        db,
        warehouse_id=warehouse.id,
        location_id=location_id,
    )
    items = _return_items(db, record.id)
    if not items:
        raise BizException(ErrorCode.BAD_REQUEST, "退货单没有商品明细")
    stock_items = [
        {
            "product_id": item.product_id,
            "sku_id": item.sku_id,
            "qty": item.qty,
            "cost_price": _return_cost_price(
                db,
                order_id=record.order_id,
                product_id=item.product_id,
                sku_id=int(item.sku_id),
            ),
        }
        for item in items
    ]
    try:
        stock_in = stock_in_service.create_stock_in(
            db,
            data={
                "warehouse_id": warehouse.id,
                "stock_in_type": "AFTER_SALE",
                "remark": f"售后退货入库:{record.return_no}",
                "items": stock_items,
            },
            staff=staff,
            commit=False,
        )
        stock_in.status = "APPROVED"
        stock_in.audit_status = "APPROVED"
        stock_in.auditor_id = staff.id
        stock_in.audit_time = _now()
        db.flush()
        rows = list(
            db.scalars(
                select(StockInItem)
                .where(StockInItem.stock_in_id == stock_in.id)
                .order_by(StockInItem.id)
            )
        )
        stock_in_service.putaway_stock(
            db,
            stock_in_id=stock_in.id,
            data={
                "items": [
                    {
                        "stock_in_item_id": row.id,
                        "location_id": location.id,
                        "qty": row.qty,
                    }
                    for row in rows
                ]
            },
            staff=staff,
            commit=False,
        )
        stock_in_service.generate_stock_in_voucher(
            db, stock_in=stock_in, staff=staff, commit=False
        )
        record.status = STATUS_CONFIRMED
        record.is_arrived = True
        record.stock_in_id = stock_in.id
        record.audit_time = _now()
        write_audit(
            db,
            action="CONFIRM",
            target_type=DOC_TYPE_RETURN,
            target_id=str(record.id),
            summary=f"确认退货并入库：{record.return_no}",
            reason=comment,
            detail={
                "stock_in_id": stock_in.id,
                "stock_in_no": stock_in.stock_in_no,
                "location_id": location.id,
                "location_code": location.code,
            },
            actor_id=staff.id,
            actor_name=staff.name,
        )
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return record


def cancel_return(
    db: Session,
    *,
    return_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> ReturnOrder:
    record = get_return_or_404(db, return_id)
    if record.status not in {STATUS_PENDING, STATUS_SUBMITTED}:
        raise BizException(ErrorCode.CONFLICT, "当前退货单状态不能取消")
    record.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type=DOC_TYPE_RETURN,
        target_id=str(record.id),
        summary=f"取消退货单：{record.return_no}",
        reason=_optional_text(reason, "取消原因", 255),
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def update_return_logistics(
    db: Session,
    *,
    return_id: int,
    logistics_company: str | None,
    tracking_no: str | None,
    staff: Staff,
    commit: bool = True,
) -> ReturnOrder:
    record = get_return_or_404(db, return_id)
    if record.status == STATUS_CANCELLED:
        raise BizException(ErrorCode.CONFLICT, "已取消退货单不能修改物流")
    record.logistics_company = _optional_text(
        logistics_company,
        "物流公司",
        64,
    )
    record.tracking_no = _optional_text(tracking_no, "运单号", 64)
    register = _return_register(db, record.id)
    if register is not None:
        register.logistics_company = record.logistics_company
        register.tracking_no = record.tracking_no
    write_audit(
        db,
        action="UPDATE_LOGISTICS",
        target_type=DOC_TYPE_RETURN,
        target_id=str(record.id),
        summary=f"修改退货物流：{record.return_no}",
        detail={
            "logistics_company": record.logistics_company,
            "tracking_no": record.tracking_no,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _normalize_compensation(
    db: Session,
    data: dict,
    *,
    existing: ExchangeOrder | None = None,
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "compensate_amount" in data:
        amount = _optional_money(data["compensate_amount"], "补差金额")
        values["compensate_amount"] = amount
    else:
        amount = existing.compensate_amount if existing is not None else None
    direction = (
        data.get("compensate_direction")
        if "compensate_direction" in data
        else (existing.compensate_direction if existing is not None else None)
    )
    direction = _optional_text(direction, "补差方向", 16)
    if direction is not None:
        direction = direction.upper()
        if direction not in COMPENSATE_DIRECTIONS:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "补差方向必须为 INCOME 或 EXPENSE",
            )
    if amount is not None and amount > ZERO_MONEY and direction is None:
        raise BizException(ErrorCode.BAD_REQUEST, "填写补差金额时必须选择收入或支出")
    if amount is None and direction is not None:
        raise BizException(ErrorCode.BAD_REQUEST, "选择补差方向时必须填写补差金额")
    values["compensate_direction"] = direction
    return values


def create_exchange(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ExchangeOrder:
    _check_unknown_fields(data, EXCHANGE_FIELDS)
    order = _order_or_404(db, _to_int_id(data.get("order_id"), "订单"))
    items = _normalize_items(
        db,
        order_id=order.id,
        raw_items=data.get("items"),
        kind="exchange",
    )
    _validate_exchange_limits(db, order=order, items=items)
    compensation = _normalize_compensation(db, data)
    record = ExchangeOrder(
        exchange_no=generate_serial(SerialPrefix.EXCHANGE_ORDER, dt=_now()),
        order_id=order.id,
        shop_id=order.shop_id,
        status=STATUS_PENDING,
        reason=_optional_text(data.get("reason"), "换货原因", 128),
        is_cod=(
            _to_bool(data["is_cod"], "是否到付")
            if "is_cod" in data
            else False
        ),
        is_return=(
            _to_bool(data["is_return"], "是否退货")
            if "is_return" in data
            else False
        ),
        compensate_order_no=_optional_text(
            data.get("compensate_order_no"),
            "关联补偿订单号",
            64,
        ),
        compensate_amount=compensation.get("compensate_amount"),
        compensate_direction=compensation.get("compensate_direction"),
    )
    db.add(record)
    db.flush()
    for item in items:
        db.add(ExchangeOrderItem(exchange_order_id=record.id, **item))
    db.flush()
    _recalculate_exchange(db, record)
    register_id = _optional_int_id(data.get("register_id"), "退货登记")
    if register_id is not None:
        attach_register(
            db,
            register_id=register_id,
            return_order_id=None,
            exchange_order_id=record.id,
            staff=staff,
            commit=False,
        )
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE_EXCHANGE,
        target_id=str(record.id),
        summary=f"新增换货单：{record.exchange_no}",
        detail={"order_id": order.id},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _ensure_exchange_editable(record: ExchangeOrder) -> None:
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "换货单提交后不能修改")


def update_exchange(
    db: Session,
    *,
    exchange_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ExchangeOrder:
    _check_unknown_fields(data, EXCHANGE_FIELDS)
    record = get_exchange_or_404(db, exchange_id)
    _ensure_exchange_editable(record)
    order_id = (
        _to_int_id(data["order_id"], "订单")
        if "order_id" in data
        else record.order_id
    )
    order = _order_or_404(db, order_id)
    if "items" in data:
        items = _normalize_items(
            db,
            order_id=order.id,
            raw_items=data["items"],
            kind="exchange",
        )
        _validate_exchange_limits(
            db,
            order=order,
            items=items,
            exclude_exchange_id=record.id,
        )
        for item in _exchange_items(db, record.id):
            db.delete(item)
        db.flush()
        for item in items:
            db.add(ExchangeOrderItem(exchange_order_id=record.id, **item))
        db.flush()
    record.order_id = order.id
    record.shop_id = order.shop_id
    if "reason" in data:
        record.reason = _optional_text(data["reason"], "换货原因", 128)
    if "is_cod" in data:
        record.is_cod = _to_bool(data["is_cod"], "是否到付")
    if "is_return" in data:
        record.is_return = _to_bool(data["is_return"], "是否退货")
    if "compensate_order_no" in data:
        record.compensate_order_no = _optional_text(
            data["compensate_order_no"],
            "关联补偿订单号",
            64,
        )
    compensation = _normalize_compensation(db, data, existing=record)
    for field, value in compensation.items():
        setattr(record, field, value)
    db.flush()
    _recalculate_exchange(db, record)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE_EXCHANGE,
        target_id=str(record.id),
        summary=f"修改换货单：{record.exchange_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_exchange(
    db: Session,
    *,
    exchange_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    record = get_exchange_or_404(db, exchange_id)
    _ensure_exchange_editable(record)
    register = _exchange_register(db, record.id)
    if register is not None:
        db.delete(register)
    for item in _exchange_items(db, record.id):
        db.delete(item)
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE_EXCHANGE,
        target_id=str(exchange_id),
        summary=f"删除换货单：{record.exchange_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def submit_exchange(
    db: Session,
    *,
    exchange_id: int,
    staff: Staff,
    commit: bool = True,
) -> ExchangeOrder:
    record = get_exchange_or_404(db, exchange_id)
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前换货单状态不能提交")
    if not _exchange_items(db, record.id):
        raise BizException(ErrorCode.BAD_REQUEST, "换货单没有商品明细")
    if _exchange_register(db, record.id) is None:
        raise BizException(ErrorCode.CONFLICT, "换货单必须绑定退货登记后才能提交")
    record.status = STATUS_SUBMITTED
    write_audit(
        db,
        action="SUBMIT",
        target_type=DOC_TYPE_EXCHANGE,
        target_id=str(record.id),
        summary=f"提交换货单：{record.exchange_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def cancel_exchange(
    db: Session,
    *,
    exchange_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> ExchangeOrder:
    record = get_exchange_or_404(db, exchange_id)
    if record.status not in {STATUS_PENDING, STATUS_SUBMITTED}:
        raise BizException(ErrorCode.CONFLICT, "当前换货单状态不能取消")
    record.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type=DOC_TYPE_EXCHANGE,
        target_id=str(record.id),
        summary=f"取消换货单：{record.exchange_no}",
        reason=_optional_text(reason, "取消原因", 255),
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _validate_register_target(
    db: Session,
    *,
    return_order_id: int | None,
    exchange_order_id: int | None,
    exclude_register_id: int | None = None,
) -> None:
    if return_order_id is not None and exchange_order_id is not None:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "退货登记不能同时绑定退货单和换货单",
        )
    if return_order_id is not None:
        record = get_return_or_404(db, return_order_id)
        if record.status in {STATUS_CONFIRMED, STATUS_CANCELLED}:
            raise BizException(
                ErrorCode.CONFLICT,
                "已确认或已取消退货单不能绑定退货登记",
            )
        conditions = [ReturnRegister.return_order_id == return_order_id]
        if exclude_register_id is not None:
            conditions.append(ReturnRegister.id != exclude_register_id)
        if db.scalar(select(ReturnRegister.id).where(*conditions).limit(1)):
            raise BizException(ErrorCode.CONFLICT, "退货单已绑定退货登记")
    if exchange_order_id is not None:
        record = get_exchange_or_404(db, exchange_order_id)
        if record.status != STATUS_PENDING:
            raise BizException(
                ErrorCode.CONFLICT,
                "当前换货单状态不能绑定退货登记",
            )
        conditions = [ReturnRegister.exchange_order_id == exchange_order_id]
        if exclude_register_id is not None:
            conditions.append(ReturnRegister.id != exclude_register_id)
        if db.scalar(select(ReturnRegister.id).where(*conditions).limit(1)):
            raise BizException(ErrorCode.CONFLICT, "换货单已绑定退货登记")


def create_register(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ReturnRegister:
    _check_unknown_fields(data, REGISTER_FIELDS)
    return_order_id = _optional_int_id(
        data.get("return_order_id"),
        "退货单",
    )
    exchange_order_id = _optional_int_id(
        data.get("exchange_order_id"),
        "换货单",
    )
    _validate_register_target(
        db,
        return_order_id=return_order_id,
        exchange_order_id=exchange_order_id,
    )
    product_info = _optional_text(data.get("product_info"), "商品信息", 2000)
    if product_info is None and return_order_id is not None:
        product_info = _product_info_for_return(db, return_order_id)
    if product_info is None and exchange_order_id is not None:
        product_info = _product_info_for_exchange(db, exchange_order_id)
    record = ReturnRegister(
        return_order_id=return_order_id,
        exchange_order_id=exchange_order_id,
        logistics_company=_optional_text(
            data.get("logistics_company"),
            "物流公司",
            64,
        ),
        tracking_no=_optional_text(data.get("tracking_no"), "物流单号", 64),
        sender=_optional_text(data.get("sender"), "寄件人", 64),
        sender_phone=_optional_text(data.get("sender_phone"), "寄件人手机号", 32),
        product_info=product_info,
        image=_optional_text(data.get("image"), "图片", 255),
    )
    db.add(record)
    db.flush()
    if return_order_id is not None:
        target_return = get_return_or_404(db, return_order_id)
        if not target_return.logistics_company:
            target_return.logistics_company = record.logistics_company
        if not target_return.tracking_no:
            target_return.tracking_no = record.tracking_no
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE_REGISTER,
        target_id=str(record.id),
        summary="新增退换货入库登记",
        detail={
            "return_order_id": return_order_id,
            "exchange_order_id": exchange_order_id,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def update_register(
    db: Session,
    *,
    register_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ReturnRegister:
    _check_unknown_fields(data, REGISTER_FIELDS)
    record = get_register_or_404(db, register_id)
    for field, label, max_length in (
        ("logistics_company", "物流公司", 64),
        ("tracking_no", "物流单号", 64),
        ("sender", "寄件人", 64),
        ("sender_phone", "寄件人手机号", 32),
        ("product_info", "商品信息", 2000),
        ("image", "图片", 255),
    ):
        if field in data:
            setattr(record, field, _optional_text(data[field], label, max_length))
    if record.return_order_id is not None:
        target = get_return_or_404(db, record.return_order_id)
        if "logistics_company" in data or "tracking_no" in data:
            target.logistics_company = record.logistics_company
            target.tracking_no = record.tracking_no
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE_REGISTER,
        target_id=str(record.id),
        summary="修改退换货入库登记",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def attach_register(
    db: Session,
    *,
    register_id: int,
    return_order_id: int | None,
    exchange_order_id: int | None,
    staff: Staff,
    commit: bool = True,
) -> ReturnRegister:
    record = get_register_or_404(db, register_id)
    if record.return_order_id is not None and (
        return_order_id is None or record.return_order_id != return_order_id
    ):
        raise BizException(ErrorCode.CONFLICT, "退货登记已绑定其它退货单")
    if record.exchange_order_id is not None and (
        exchange_order_id is None or record.exchange_order_id != exchange_order_id
    ):
        raise BizException(ErrorCode.CONFLICT, "退货登记已绑定其它换货单")
    _validate_register_target(
        db,
        return_order_id=return_order_id,
        exchange_order_id=exchange_order_id,
        exclude_register_id=record.id,
    )
    record.return_order_id = return_order_id
    record.exchange_order_id = exchange_order_id
    if return_order_id is not None:
        target = get_return_or_404(db, return_order_id)
        if not record.logistics_company:
            record.logistics_company = target.logistics_company
        if not record.tracking_no:
            record.tracking_no = target.tracking_no
        if not target.logistics_company:
            target.logistics_company = record.logistics_company
        if not target.tracking_no:
            target.tracking_no = record.tracking_no
        if not record.product_info:
            record.product_info = _product_info_for_return(db, target.id)
    if exchange_order_id is not None and not record.product_info:
        record.product_info = _product_info_for_exchange(db, exchange_order_id)
    write_audit(
        db,
        action="BIND",
        target_type=DOC_TYPE_REGISTER,
        target_id=str(record.id),
        summary="绑定退换货入库登记",
        detail={
            "return_order_id": return_order_id,
            "exchange_order_id": exchange_order_id,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_bound_register(
    db: Session,
    *,
    return_order_id: int | None = None,
    exchange_order_id: int | None = None,
    staff: Staff,
    commit: bool = True,
) -> None:
    if return_order_id is not None:
        record = _return_register(db, return_order_id)
    elif exchange_order_id is not None:
        record = _exchange_register(db, exchange_order_id)
    else:
        record = None
    if record is None:
        raise BizException(ErrorCode.NOT_FOUND, "未绑定退货登记")
    if return_order_id is not None:
        target = get_return_or_404(db, return_order_id)
        if target.status in {STATUS_CONFIRMED, STATUS_CANCELLED}:
            raise BizException(
                ErrorCode.CONFLICT,
                "已确认或已取消退货单不能删除退货登记",
            )
    if exchange_order_id is not None:
        target_exchange = get_exchange_or_404(db, exchange_order_id)
        if target_exchange.status != STATUS_PENDING:
            raise BizException(
                ErrorCode.CONFLICT,
                "换货单提交后不能删除退货登记",
            )
    register_id = record.id
    db.delete(record)
    write_audit(
        db,
        action="UNBIND",
        target_type=DOC_TYPE_REGISTER,
        target_id=str(register_id),
        summary="删除退换货入库登记绑定",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def delete_register(
    db: Session,
    *,
    register_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    record = get_register_or_404(db, register_id)
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE_REGISTER,
        target_id=str(register_id),
        summary="删除退换货入库登记",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def list_registers(
    db: Session,
    *,
    bound: bool | None = None,
) -> list[dict[str, Any]]:
    conditions: list[Any] = []
    if bound is True:
        conditions.append(
            or_(
                ReturnRegister.return_order_id.is_not(None),
                ReturnRegister.exchange_order_id.is_not(None),
            )
        )
    elif bound is False:
        conditions.extend(
            [
                ReturnRegister.return_order_id.is_(None),
                ReturnRegister.exchange_order_id.is_(None),
            ]
        )
    rows = list(
        db.scalars(
            select(ReturnRegister)
            .where(*conditions)
            .order_by(ReturnRegister.id.desc())
        )
    )
    return [register_dict(db, row) for row in rows]


def export_returns(
    db: Session,
    *,
    order_id: int | None = None,
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> bytes:
    listed = list_returns(
        db,
        order_id=order_id,
        shop_id=shop_id,
        warehouse_id=warehouse_id,
        status=status,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        page=1,
        page_size=200,
    )
    return export_data(
        RETURN_EXPORT_TEMPLATE,
        [
            {
                "return_no": row["return_no"],
                "order_no": row["order_no"],
                "order_status": row["order_status"],
                "shop_name": row["shop_name"],
                "product_info": row["product_info"],
                "buyer_phone": row["buyer_phone"],
                "status_label": row["status_label"],
                "refund_amount": row["refund_amount"],
                "goods_status": row["goods_status"],
                "logistics_company": row["logistics_company"],
                "tracking_no": row["tracking_no"],
                "stock_in_no": row["stock_in_no"],
                "created_at": row["created_at"],
                "audit_time": row["audit_time"],
            }
            for row in listed["items"]
        ],
    )
