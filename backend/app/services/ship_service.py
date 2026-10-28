"""扫描发货与订单包裹服务（TASK-039）。

口径说明：
- 扫描码支持快递单号、订单号；商品码优先匹配规格条码，其次商品条码。
- 每次扫描立即按仓库库位优先级扣减库存，并写入采购出库同口径的 `stock_out` 与
  `stock_flow`，业务类型为 `ORDER_SALE`，确保库存查询与成本核算可追溯。
- 一个订单允许生成多个包裹。订单全部数量扫描完成后，所有待发货包裹一并转为已发货。
- 重量以克为单位，商品重量按千克换算；未传实际重量时不阻断，传入后按误差区间校验。
- 真实平台回传由 TASK-065 对接，本任务只维护回传状态和留痕。
"""

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
    Order,
    OrderItem,
    OrderShipment,
    OrderShipmentItem,
    Waybill,
)
from app.models.org import Staff
from app.models.product import Product, ProductSku
from app.models.stock import StockFlow, StockOut, StockOutItem
from app.models.warehouse import Location, StockLocation
from app.services import stock_query_service, warehouse_service

DOC_TYPE = "order_shipment"
STOCK_OUT_TYPE = "ORDER_SALE"

STATUS_SCANNING = "SCANNING"
STATUS_SHIPPED = "SHIPPED"
STATUS_CANCELLED = "CANCELLED"
SHIPMENT_STATUSES = {STATUS_SCANNING, STATUS_SHIPPED, STATUS_CANCELLED}

SHIPPING_TYPE_EXPRESS = "EXPRESS"
SHIPPING_TYPE_SELF_PICKUP = "SELF_PICKUP"
SHIPPING_TYPES = {SHIPPING_TYPE_EXPRESS, SHIPPING_TYPE_SELF_PICKUP}

SOURCE_SCAN = "SCAN"
SOURCE_ONE_CLICK = "ONE_CLICK"
SOURCE_SELF_PICKUP = "SELF_PICKUP"
SOURCES = {SOURCE_SCAN, SOURCE_ONE_CLICK, SOURCE_SELF_PICKUP}

SCAN_SOURCE_WAYBILL = "WAYBILL"
SCAN_SOURCE_ORDER = "ORDER"

SYNC_PENDING = "PENDING"
SYNC_SYNCED = "SYNCED"
SYNC_FAILED = "FAILED"
SYNC_SKIPPED = "SKIPPED"
SYNC_STATUSES = {SYNC_PENDING, SYNC_SYNCED, SYNC_FAILED, SYNC_SKIPPED}

QTY_QUANT = Decimal("0.001")
WEIGHT_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _to_int(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc
    if result < 1:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_int(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int(value, label)


def _to_qty(value: Any, label: str = "数量") -> Decimal:
    try:
        result = Decimal(str(value)).quantize(
            QTY_QUANT,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result <= ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_weight(value: Any, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value)).quantize(
            WEIGHT_QUANT,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法重量") from exc
    if result < ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能小于 0")
    return result


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y", "是"}:
            return True
        if text in {"false", "0", "no", "n", "否"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _required_text(value: Any, label: str, max_length: int) -> str:
    if value in (None, ""):
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    if len(text) > max_length:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"{label}长度不能超过 {max_length}",
        )
    return text


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


def get_shipment_or_404(db: Session, shipment_id: int) -> OrderShipment:
    shipment = db.get(OrderShipment, shipment_id)
    if shipment is None:
        raise BizException(ErrorCode.NOT_FOUND, "发货包裹不存在")
    return shipment


def _shipment_items(
    db: Session,
    shipment_id: int,
) -> list[OrderShipmentItem]:
    return list(
        db.scalars(
            select(OrderShipmentItem)
            .where(OrderShipmentItem.shipment_id == shipment_id)
            .order_by(OrderShipmentItem.id)
        )
    )


def _shipment_item_dict(
    db: Session,
    item: OrderShipmentItem,
) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    order_item = db.get(OrderItem, item.order_item_id)
    return {
        "id": item.id,
        "order_item_id": item.order_item_id,
        "product_id": item.product_id,
        "product_code": product.code if product else None,
        "product_name": product.name if product else None,
        "sku_id": item.sku_id,
        "spec_code": sku.spec_code if sku else None,
        "spec_name": sku.spec_name if sku else None,
        "qty": item.qty,
        "cost_price": item.cost_price,
        "barcode": item.barcode,
        "is_gift": order_item.is_gift if order_item else False,
    }


def shipment_dict(
    db: Session,
    shipment: OrderShipment,
    *,
    with_items: bool = True,
) -> dict:
    order = db.get(Order, shipment.order_id)
    stock_out = (
        db.get(StockOut, shipment.stock_out_id)
        if shipment.stock_out_id
        else None
    )
    shipper = db.get(Staff, shipment.shipped_by) if shipment.shipped_by else None
    result = {
        "id": shipment.id,
        "shipment_no": shipment.shipment_no,
        "order_id": shipment.order_id,
        "order_no": order.order_no if order else None,
        "warehouse_id": shipment.warehouse_id,
        "stock_out_id": shipment.stock_out_id,
        "stock_out_no": stock_out.stock_out_no if stock_out else None,
        "package_no": shipment.package_no,
        "tracking_no": shipment.tracking_no,
        "shipping_type": shipment.shipping_type,
        "source": shipment.source,
        "status": shipment.status,
        "expected_weight_g": shipment.expected_weight_g,
        "actual_weight_g": shipment.actual_weight_g,
        "weight_tolerance_g": shipment.weight_tolerance_g,
        "weight_out_of_range": shipment.weight_out_of_range,
        "weight_overridden": shipment.weight_overridden,
        "platform_sync_status": shipment.platform_sync_status,
        "platform_sync_message": shipment.platform_sync_message,
        "shipped_by": shipment.shipped_by,
        "shipped_by_name": shipper.name if shipper else None,
        "shipped_at": shipment.shipped_at,
        "remark": shipment.remark,
        "created_at": shipment.created_at,
        "updated_at": shipment.updated_at,
    }
    if with_items:
        result["items"] = [
            _shipment_item_dict(db, item)
            for item in _shipment_items(db, shipment.id)
        ]
    return result


def list_shipments(
    db: Session,
    *,
    order_id: int | None = None,
    status: str | None = None,
    tracking_no: str | None = None,
    platform_sync_status: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if order_id is not None:
        conditions.append(OrderShipment.order_id == order_id)
    if status:
        status_value = str(status).strip().upper()
        if status_value not in SHIPMENT_STATUSES:
            raise BizException(ErrorCode.BAD_REQUEST, "发货状态不合法")
        conditions.append(OrderShipment.status == status_value)
    if tracking_no:
        conditions.append(
            OrderShipment.tracking_no.like(f"%{tracking_no.strip()}%")
        )
    if platform_sync_status:
        sync_value = str(platform_sync_status).strip().upper()
        if sync_value not in SYNC_STATUSES:
            raise BizException(ErrorCode.BAD_REQUEST, "平台回传状态不合法")
        conditions.append(OrderShipment.platform_sync_status == sync_value)
    if start_time is not None:
        conditions.append(OrderShipment.created_at >= start_time)
    if end_time is not None:
        conditions.append(OrderShipment.created_at <= end_time)
    total = db.scalar(
        select(func.count(OrderShipment.id)).where(*conditions)
    ) or 0
    rows = list(
        db.scalars(
            select(OrderShipment)
            .where(*conditions)
            .order_by(OrderShipment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [shipment_dict(db, row) for row in rows],
    }


def _order_items(db: Session, order_id: int) -> list[OrderItem]:
    return list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order_id)
            .order_by(OrderItem.id)
        )
    )


def _validate_order_for_shipping(
    db: Session,
    order: Order,
) -> None:
    if order.audit_status != "APPROVED":
        raise BizException(ErrorCode.CONFLICT, "订单审核通过后才能发货")
    if order.status != "PENDING":
        raise BizException(ErrorCode.CONFLICT, "订单当前状态不能发货")
    if order.warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "订单未设置发货仓库")
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, order.warehouse_id
    )
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟仓不能作为发货仓库")
    if not _order_items(db, order.id):
        raise BizException(ErrorCode.CONFLICT, "订单没有可发货商品")


def _resolve_scan_order(
    db: Session,
    scan_code: str,
) -> tuple[Order, str, str | None]:
    waybill = db.scalar(
        select(Waybill)
        .where(Waybill.waybill_no == scan_code)
        .order_by(Waybill.id.desc())
        .limit(1)
    )
    if waybill is not None:
        order = db.get(Order, waybill.order_id)
        if order is None:
            raise BizException(ErrorCode.NOT_FOUND, "运单关联订单不存在")
        return order, SCAN_SOURCE_WAYBILL, waybill.waybill_no

    order = db.scalar(select(Order).where(Order.order_no == scan_code))
    if order is not None:
        return order, SCAN_SOURCE_ORDER, order.tracking_no

    order = db.scalar(
        select(Order)
        .where(Order.tracking_no == scan_code)
        .order_by(Order.id.desc())
        .limit(1)
    )
    if order is not None:
        return order, SCAN_SOURCE_ORDER, order.tracking_no
    raise BizException(
        ErrorCode.NOT_FOUND,
        f"未找到快递单号或订单号：{scan_code}",
    )


def _shipped_qty_map(
    db: Session,
    order_id: int,
) -> dict[int, Decimal]:
    rows = db.execute(
        select(
            OrderShipmentItem.order_item_id,
            func.coalesce(func.sum(OrderShipmentItem.qty), 0),
        )
        .join(
            OrderShipment,
            OrderShipment.id == OrderShipmentItem.shipment_id,
        )
        .where(
            OrderShipment.order_id == order_id,
            OrderShipment.status != STATUS_CANCELLED,
        )
        .group_by(OrderShipmentItem.order_item_id)
    )
    return {
        int(order_item_id): Decimal(qty or 0)
        for order_item_id, qty in rows
    }


def _remaining_qty_map(
    db: Session,
    order: Order,
) -> dict[int, Decimal]:
    shipped = _shipped_qty_map(db, order.id)
    return {
        item.id: max(
            Decimal(item.qty or 0) - shipped.get(item.id, ZERO_QTY),
            ZERO_QTY,
        )
        for item in _order_items(db, order.id)
    }


def _resolve_order_item_by_barcode(
    db: Session,
    *,
    order: Order,
    barcode: str,
) -> tuple[OrderItem, str]:
    sku = db.scalar(
        select(ProductSku)
        .where(
            or_(
                ProductSku.barcode == barcode,
                ProductSku.spec_code == barcode,
            )
        )
        .order_by(ProductSku.id)
        .limit(1)
    )
    product_id: int | None = sku.product_id if sku is not None else None
    if sku is None:
        product = db.scalar(
            select(Product)
            .where(
                or_(
                    Product.barcode == barcode,
                    Product.code == barcode,
                )
            )
            .order_by(Product.id)
            .limit(1)
        )
        product_id = product.id if product is not None else None
    if product_id is None:
        raise BizException(ErrorCode.NOT_FOUND, f"商品条码不存在：{barcode}")

    matched = [
        item
        for item in _order_items(db, order.id)
        if item.product_id == product_id
        and (sku is None or item.sku_id == sku.id)
    ]
    if not matched:
        raise BizException(
            ErrorCode.CONFLICT,
            f"商品条码不属于当前订单：{barcode}",
        )
    if len(matched) > 1:
        raise BizException(
            ErrorCode.CONFLICT,
            f"商品条码匹配到多条订单明细，请扫描规格条码：{barcode}",
        )
    return matched[0], barcode


def _find_or_create_shipment(
    db: Session,
    *,
    order: Order,
    tracking_no: str | None,
    package_no: int | None,
    shipping_type: str,
    source: str,
    staff: Staff,
) -> OrderShipment:
    if shipping_type not in SHIPPING_TYPES:
        raise BizException(ErrorCode.BAD_REQUEST, "发货类型不合法")
    if source not in SOURCES:
        raise BizException(ErrorCode.BAD_REQUEST, "发货来源不合法")

    shipment: OrderShipment | None = None
    if tracking_no:
        other_order = db.scalar(
            select(OrderShipment)
            .where(
                OrderShipment.tracking_no == tracking_no,
                OrderShipment.order_id != order.id,
                OrderShipment.status != STATUS_CANCELLED,
            )
            .order_by(OrderShipment.id.desc())
            .limit(1)
        )
        if other_order is not None:
            raise BizException(
                ErrorCode.CONFLICT,
                f"运单号已绑定其它订单：{tracking_no}",
            )
        shipped = db.scalar(
            select(OrderShipment)
            .where(
                OrderShipment.tracking_no == tracking_no,
                OrderShipment.status == STATUS_SHIPPED,
            )
            .order_by(OrderShipment.id.desc())
            .limit(1)
        )
        if shipped is not None:
            raise BizException(
                ErrorCode.CONFLICT,
                f"运单号已发货：{tracking_no}",
            )
        shipment = db.scalar(
            select(OrderShipment)
            .where(
                OrderShipment.order_id == order.id,
                OrderShipment.tracking_no == tracking_no,
                OrderShipment.status == STATUS_SCANNING,
            )
            .order_by(OrderShipment.id.desc())
            .limit(1)
        )
    elif package_no is not None:
        shipment = db.scalar(
            select(OrderShipment)
            .where(
                OrderShipment.order_id == order.id,
                OrderShipment.package_no == package_no,
                OrderShipment.status == STATUS_SCANNING,
            )
            .order_by(OrderShipment.id.desc())
            .limit(1)
        )
    else:
        shipment = db.scalar(
            select(OrderShipment)
            .where(
                OrderShipment.order_id == order.id,
                OrderShipment.status == STATUS_SCANNING,
            )
            .order_by(OrderShipment.id.desc())
            .limit(1)
        )

    if shipment is not None:
        if tracking_no and shipment.tracking_no not in (None, tracking_no):
            raise BizException(ErrorCode.CONFLICT, "包裹已绑定其它运单号")
        if tracking_no and shipment.tracking_no is None:
            shipment.tracking_no = tracking_no
        return shipment

    max_package_no = db.scalar(
        select(func.coalesce(func.max(OrderShipment.package_no), 0)).where(
            OrderShipment.order_id == order.id
        )
    ) or 0
    resolved_package_no = package_no or int(max_package_no) + 1
    existing_package = db.scalar(
        select(OrderShipment).where(
            OrderShipment.order_id == order.id,
            OrderShipment.package_no == resolved_package_no,
        )
    )
    if existing_package is not None:
        raise BizException(
            ErrorCode.CONFLICT,
            f"订单已存在第 {resolved_package_no} 个包裹记录",
        )
    shipment = OrderShipment(
        shipment_no=generate_serial(SerialPrefix.SHIPMENT, dt=_now()),
        order_id=order.id,
        warehouse_id=order.warehouse_id,
        package_no=resolved_package_no,
        tracking_no=tracking_no,
        shipping_type=shipping_type,
        source=source,
        status=STATUS_SCANNING,
        platform_sync_status=SYNC_PENDING,
        platform_sync_message="平台回传待 TASK-065 对接",
    )
    db.add(shipment)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(shipment.id),
        summary=(
            f"创建订单包裹：{order.order_no} 第 {shipment.package_no} 包"
        ),
        detail={
            "order_id": order.id,
            "tracking_no": tracking_no,
            "shipping_type": shipping_type,
            "source": source,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    return shipment


def _ensure_stock_out(
    db: Session,
    *,
    order: Order,
    shipment: OrderShipment,
    staff: Staff,
) -> StockOut:
    if shipment.stock_out_id:
        stock_out = db.get(StockOut, shipment.stock_out_id)
        if stock_out is None:
            raise BizException(ErrorCode.NOT_FOUND, "发货出库单不存在")
        return stock_out
    stock_out = StockOut(
        stock_out_no=generate_serial(SerialPrefix.STOCK_OUT, dt=_now()),
        warehouse_id=shipment.warehouse_id,
        stock_out_type=STOCK_OUT_TYPE,
        purchase_return_id=None,
        status="APPROVED",
        audit_status="APPROVED",
        off_shelf_status="DONE",
        total_qty=ZERO_QTY,
        remark=f"订单发货:{order.order_no}",
        created_by=staff.id,
        auditor_id=staff.id,
        audit_time=_now(),
    )
    db.add(stock_out)
    db.flush()
    shipment.stock_out_id = stock_out.id
    return stock_out


def _resolve_cost_price(product: Product, sku: ProductSku | None) -> Decimal | None:
    if sku is not None and sku.cost_price is not None:
        return round_money(sku.cost_price)
    if product.cost_price is not None:
        return round_money(product.cost_price)
    return None


def _deduct_stock(
    db: Session,
    *,
    order: Order,
    shipment: OrderShipment,
    order_item: OrderItem,
    qty: Decimal,
    staff: Staff,
) -> None:
    if order_item.sku_id is None:
        raise BizException(
            ErrorCode.CONFLICT,
            "订单明细未指定商品规格，不能执行库存扣减",
        )
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, shipment.warehouse_id
    )
    stock_status = (
        "DEFECTIVE"
        if warehouse.warehouse_type == "DEFECTIVE"
        else "NORMAL"
    )
    rows = list(
        db.execute(
            select(StockLocation, Location)
            .join(Location, Location.id == StockLocation.location_id)
            .where(
                Location.warehouse_id == shipment.warehouse_id,
                StockLocation.product_sku_id == order_item.sku_id,
                StockLocation.status == stock_status,
                StockLocation.qty > ZERO_QTY,
            )
            .order_by(
                Location.priority.desc(),
                Location.sort_order,
                Location.code,
                StockLocation.id,
            )
            .with_for_update()
        )
    )
    available = sum((Decimal(row.qty or 0) for row, _ in rows), ZERO_QTY)
    sellable = stock_query_service.available_qty(
        db,
        warehouse_id=shipment.warehouse_id,
        product_id=order_item.product_id,
        sku_id=order_item.sku_id,
        status=stock_status,
    )
    if available < qty or sellable < qty:
        product = db.get(Product, order_item.product_id)
        sku = db.get(ProductSku, order_item.sku_id) if order_item.sku_id else None
        label = (
            sku.spec_code
            if sku is not None
            else (product.code if product is not None else order_item.product_id)
        )
        available_for_label = max(min(available, sellable), ZERO_QTY)
        raise BizException(
            ErrorCode.CONFLICT,
            f"库存不足：{label} 可用 {available_for_label}，需要 {qty}",
        )

    product = db.get(Product, order_item.product_id)
    sku = db.get(ProductSku, order_item.sku_id) if order_item.sku_id else None
    if product is None:
        raise BizException(ErrorCode.NOT_FOUND, "订单商品不存在")
    cost_price = _resolve_cost_price(product, sku)
    stock_out = _ensure_stock_out(
        db,
        order=order,
        shipment=shipment,
        staff=staff,
    )
    remaining = qty
    allocations: list[tuple[StockLocation, Location, Decimal]] = []
    for stock_row, location in rows:
        if remaining <= ZERO_QTY:
            break
        allocated = min(Decimal(stock_row.qty or 0), remaining)
        if allocated <= ZERO_QTY:
            continue
        allocations.append((stock_row, location, allocated))
        remaining -= allocated
    if remaining > ZERO_QTY:
        raise BizException(ErrorCode.CONFLICT, "库存分配失败，请重试")

    sku_barcode = sku.barcode if sku is not None else None
    for stock_row, location, allocated in allocations:
        stock_row.qty -= allocated
        db.add(
            StockOutItem(
                stock_out_id=stock_out.id,
                product_id=order_item.product_id,
                sku_id=order_item.sku_id,
                location_id=location.id,
                qty=allocated,
                cost_price=cost_price,
                barcode=sku_barcode,
            )
        )
        db.add(
            StockFlow(
                warehouse_id=shipment.warehouse_id,
                product_id=order_item.product_id,
                sku_id=order_item.sku_id,
                location_id=location.id,
                unit_id=product.main_unit_id,
                direction="OUT",
                biz_type=STOCK_OUT_TYPE,
                qty=allocated,
                cost_price=cost_price,
                total_cost=(
                    round_money(allocated * cost_price)
                    if cost_price is not None
                    else None
                ),
                source_no=shipment.shipment_no,
                order_no=order.order_no,
                flow_time=_now(),
                remark=(
                    f"订单发货:{shipment.tracking_no or order.order_no}"
                ),
            )
        )
    stock_out.total_qty = Decimal(stock_out.total_qty or 0) + qty
    db.flush()


def _add_shipment_item(
    db: Session,
    *,
    shipment: OrderShipment,
    order_item: OrderItem,
    qty: Decimal,
    barcode: str | None,
) -> OrderShipmentItem:
    product = db.get(Product, order_item.product_id)
    sku = db.get(ProductSku, order_item.sku_id) if order_item.sku_id else None
    row = db.scalar(
        select(OrderShipmentItem).where(
            OrderShipmentItem.shipment_id == shipment.id,
            OrderShipmentItem.order_item_id == order_item.id,
        )
    )
    if row is None:
        row = OrderShipmentItem(
            shipment_id=shipment.id,
            order_item_id=order_item.id,
            product_id=order_item.product_id,
            sku_id=order_item.sku_id,
            qty=qty,
            cost_price=(
                _resolve_cost_price(product, sku)
                if product is not None
                else None
            ),
            barcode=barcode,
        )
        db.add(row)
    else:
        row.qty = Decimal(row.qty or 0) + qty
        if barcode:
            row.barcode = barcode
    db.flush()
    return row


def _expected_shipment_weight_g(
    db: Session,
    *,
    shipment: OrderShipment,
    extra_order_item: OrderItem | None = None,
    extra_qty: Decimal = ZERO_QTY,
) -> Decimal | None:
    rows: list[tuple[OrderItem, Decimal]] = []
    for shipment_item in _shipment_items(db, shipment.id):
        order_item = db.get(OrderItem, shipment_item.order_item_id)
        if order_item is not None:
            rows.append((order_item, Decimal(shipment_item.qty or 0)))
    if extra_order_item is not None and extra_qty > ZERO_QTY:
        for index, (order_item, qty) in enumerate(rows):
            if order_item.id == extra_order_item.id:
                rows[index] = (order_item, qty + extra_qty)
                break
        else:
            rows.append((extra_order_item, extra_qty))

    expected = ZERO_QTY
    has_weight = False
    for order_item, qty in rows:
        product = db.get(Product, order_item.product_id)
        if product is None or product.weight is None:
            continue
        has_weight = True
        expected += Decimal(product.weight) * Decimal("1000") * qty
    return expected.quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP) if has_weight else None


def _validate_weight_values(
    *,
    expected_weight_g: Decimal | None,
    actual_weight_g: Decimal | None,
    tolerance_g: Decimal | None,
    allow_override: bool,
) -> None:
    if actual_weight_g is None:
        return
    tolerance = tolerance_g or ZERO_QTY
    if tolerance < ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, "重量误差区间不能小于 0")
    if expected_weight_g is None:
        return
    difference = abs(actual_weight_g - expected_weight_g)
    if difference > tolerance and not allow_override:
        raise BizException(
            ErrorCode.CONFLICT,
            (
                f"重量超出误差区间：应为 {expected_weight_g}g，"
                f"实际 {actual_weight_g}g，误差 {difference}g，"
                f"允许 {tolerance}g"
            ),
        )


def _validate_shipment_weight(
    db: Session,
    *,
    shipment: OrderShipment,
    extra_order_item: OrderItem | None = None,
    extra_qty: Decimal = ZERO_QTY,
    actual_weight_g: Decimal | None,
    tolerance_g: Decimal | None,
    allow_override: bool,
) -> None:
    expected = _expected_shipment_weight_g(
        db,
        shipment=shipment,
        extra_order_item=extra_order_item,
        extra_qty=extra_qty,
    )
    _validate_weight_values(
        expected_weight_g=expected,
        actual_weight_g=actual_weight_g,
        tolerance_g=tolerance_g,
        allow_override=allow_override,
    )


def _record_shipment_weight(
    db: Session,
    *,
    shipment: OrderShipment,
    actual_weight_g: Decimal | None,
    tolerance_g: Decimal | None,
    allow_override: bool,
) -> None:
    expected = _expected_shipment_weight_g(db, shipment=shipment)
    shipment.expected_weight_g = expected
    if actual_weight_g is None:
        return
    shipment.actual_weight_g = actual_weight_g
    shipment.weight_tolerance_g = tolerance_g or ZERO_QTY
    if expected is None:
        shipment.weight_out_of_range = False
        shipment.weight_overridden = False
        return
    difference = abs(actual_weight_g - expected)
    out_of_range = difference > (tolerance_g or ZERO_QTY)
    shipment.weight_out_of_range = out_of_range
    shipment.weight_overridden = out_of_range and allow_override


def _maybe_complete_order(
    db: Session,
    *,
    order: Order,
    staff: Staff,
) -> tuple[bool, int]:
    remaining = _remaining_qty_map(db, order)
    if any(qty > ZERO_QTY for qty in remaining.values()):
        return False, 0
    shipments = list(
        db.scalars(
            select(OrderShipment)
            .where(
                OrderShipment.order_id == order.id,
                OrderShipment.status == STATUS_SCANNING,
            )
            .order_by(OrderShipment.package_no, OrderShipment.id)
        )
    )
    if not shipments:
        return False, 0
    shipped_at = _now()
    for shipment in shipments:
        if shipment.expected_weight_g is None:
            shipment.expected_weight_g = _expected_shipment_weight_g(
                db, shipment=shipment
            )
        shipment.status = STATUS_SHIPPED
        shipment.shipped_by = staff.id
        shipment.shipped_at = shipped_at
        if shipment.platform_sync_status == SYNC_PENDING:
            shipment.platform_sync_message = "平台回传待 TASK-065 对接"

    tracking_numbers = [
        shipment.tracking_no
        for shipment in shipments
        if shipment.tracking_no
    ]
    order.status = "SHIPPED"
    order.ship_time = shipped_at
    if tracking_numbers:
        order.tracking_no = tracking_numbers[0]
    write_audit(
        db,
        action="SHIP",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"订单发货完成：{order.order_no}",
        detail={
            "order_id": order.id,
            "package_count": len(shipments),
            "packages": [
                {
                    "shipment_id": shipment.id,
                    "shipment_no": shipment.shipment_no,
                    "package_no": shipment.package_no,
                    "tracking_no": shipment.tracking_no,
                    "platform_sync_status": shipment.platform_sync_status,
                }
                for shipment in shipments
            ],
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()
    return True, len(shipments)


def _scan_item(
    db: Session,
    *,
    order: Order,
    scan_code: str,
    scan_source: str,
    barcode: str,
    qty: Decimal,
    tracking_no: str | None,
    package_no: int | None,
    actual_weight_g: Decimal | None,
    tolerance_g: Decimal | None,
    allow_weight_override: bool,
    remark: str | None,
    staff: Staff,
) -> tuple[OrderShipment, bool, int]:
    order_item, resolved_barcode = _resolve_order_item_by_barcode(
        db,
        order=order,
        barcode=barcode,
    )
    remaining = _remaining_qty_map(db, order).get(
        order_item.id, ZERO_QTY
    )
    if qty > remaining:
        raise BizException(
            ErrorCode.CONFLICT,
            f"扫描数量超过待发数量，剩余 {remaining}",
        )
    shipment = _find_or_create_shipment(
        db,
        order=order,
        tracking_no=tracking_no,
        package_no=package_no,
        shipping_type=SHIPPING_TYPE_EXPRESS,
        source=SOURCE_SCAN,
        staff=staff,
    )
    _validate_shipment_weight(
        db,
        shipment=shipment,
        extra_order_item=order_item,
        extra_qty=qty,
        actual_weight_g=actual_weight_g,
        tolerance_g=tolerance_g,
        allow_override=allow_weight_override,
    )
    _deduct_stock(
        db,
        order=order,
        shipment=shipment,
        order_item=order_item,
        qty=qty,
        staff=staff,
    )
    _add_shipment_item(
        db,
        shipment=shipment,
        order_item=order_item,
        qty=qty,
        barcode=resolved_barcode,
    )
    if actual_weight_g is not None:
        _record_shipment_weight(
            db,
            shipment=shipment,
            actual_weight_g=actual_weight_g,
            tolerance_g=tolerance_g,
            allow_override=allow_weight_override,
        )
    shipment.remark = remark or shipment.remark
    completed, package_count = _maybe_complete_order(
        db,
        order=order,
        staff=staff,
    )
    if not completed:
        write_audit(
            db,
            action="SCAN",
            target_type=DOC_TYPE,
            target_id=str(shipment.id),
            summary=(
                f"扫描发货：{order.order_no} 第 {shipment.package_no} 包"
            ),
            detail={
                "scan_code": scan_code,
                "scan_source": scan_source,
                "barcode": resolved_barcode,
                "qty": str(qty),
                "tracking_no": shipment.tracking_no,
            },
            actor_id=staff.id,
            actor_name=staff.name,
        )
    return shipment, completed, package_count


def _ship_remaining_items(
    db: Session,
    *,
    order: Order,
    tracking_no: str | None,
    package_no: int | None,
    shipping_type: str,
    source: str,
    actual_weight_g: Decimal | None,
    tolerance_g: Decimal | None,
    allow_weight_override: bool,
    remark: str | None,
    staff: Staff,
) -> tuple[OrderShipment, bool, int]:
    remaining = _remaining_qty_map(db, order)
    pending = [
        (db.get(OrderItem, order_item_id), qty)
        for order_item_id, qty in remaining.items()
        if qty > ZERO_QTY
    ]
    if not pending:
        raise BizException(ErrorCode.CONFLICT, "订单没有待发货商品")
    shipment = _find_or_create_shipment(
        db,
        order=order,
        tracking_no=tracking_no,
        package_no=package_no,
        shipping_type=shipping_type,
        source=source,
        staff=staff,
    )
    expected_weight = _expected_shipment_weight_g(db, shipment=shipment)
    has_weight = expected_weight is not None
    for order_item, qty in pending:
        if order_item is None:
            raise BizException(ErrorCode.NOT_FOUND, "订单明细不存在")
        if order_item.sku_id is None:
            raise BizException(
                ErrorCode.CONFLICT,
                "订单明细未指定商品规格，不能执行库存扣减",
            )
        product = db.get(Product, order_item.product_id)
        if product is not None and product.weight is not None:
            has_weight = True
            expected_weight = Decimal(expected_weight or 0) + (
                Decimal(product.weight) * Decimal("1000") * qty
            )
    if has_weight and expected_weight is not None:
        expected_weight = expected_weight.quantize(
            WEIGHT_QUANT,
            rounding=ROUND_HALF_UP,
        )
    _validate_weight_values(
        expected_weight_g=expected_weight,
        actual_weight_g=actual_weight_g,
        tolerance_g=tolerance_g,
        allow_override=allow_weight_override,
    )
    for order_item, qty in pending:
        if order_item is None:
            raise BizException(ErrorCode.NOT_FOUND, "订单明细不存在")
        sku = db.get(ProductSku, order_item.sku_id) if order_item.sku_id else None
        _deduct_stock(
            db,
            order=order,
            shipment=shipment,
            order_item=order_item,
            qty=qty,
            staff=staff,
        )
        _add_shipment_item(
            db,
            shipment=shipment,
            order_item=order_item,
            qty=qty,
            barcode=db.get(ProductSku, order_item.sku_id).barcode
            if sku is not None
            else None,
        )
    _record_shipment_weight(
        db,
        shipment=shipment,
        actual_weight_g=actual_weight_g,
        tolerance_g=tolerance_g,
        allow_override=allow_weight_override,
    )
    shipment.remark = remark or shipment.remark
    completed, package_count = _maybe_complete_order(
        db,
        order=order,
        staff=staff,
    )
    return shipment, completed, package_count


def scan_shipment(
    db: Session,
    *,
    scan_code: str,
    staff: Staff,
    barcode: str | None = None,
    qty: Any = Decimal("1.000"),
    tracking_no: str | None = None,
    package_no: int | None = None,
    actual_weight_g: Any = None,
    weight_tolerance_g: Any = None,
    allow_weight_override: bool = False,
    remark: str | None = None,
    commit: bool = True,
) -> dict:
    scan_value = _required_text(scan_code, "扫描单号", 64)
    barcode_value = _optional_text(barcode, "商品条码", 64)
    tracking_value = _optional_text(tracking_no, "运单号", 64)
    package_value = _optional_int(package_no, "包裹序号")
    weight_value = _optional_weight(actual_weight_g, "实际重量")
    tolerance_value = _optional_weight(weight_tolerance_g, "重量误差区间")
    allow_override = _to_bool(allow_weight_override, "允许重量超差")
    remark_value = _optional_text(remark, "备注", 255)
    order, scan_source, resolved_tracking = _resolve_scan_order(db, scan_value)
    _validate_order_for_shipping(db, order)
    tracking_value = tracking_value or resolved_tracking
    if barcode_value:
        shipment, completed, package_count = _scan_item(
            db,
            order=order,
            scan_code=scan_value,
            scan_source=scan_source,
            barcode=barcode_value,
            qty=_to_qty(qty),
            tracking_no=tracking_value,
            package_no=package_value,
            actual_weight_g=weight_value,
            tolerance_g=tolerance_value,
            allow_weight_override=allow_override,
            remark=remark_value,
            staff=staff,
        )
    else:
        shipment, completed, package_count = _ship_remaining_items(
            db,
            order=order,
            tracking_no=tracking_value,
            package_no=package_value,
            shipping_type=SHIPPING_TYPE_EXPRESS,
            source=SOURCE_SCAN,
            actual_weight_g=weight_value,
            tolerance_g=tolerance_value,
            allow_weight_override=allow_override,
            remark=remark_value,
            staff=staff,
        )
    _commit(db, commit)
    return {
        "scan_code": scan_value,
        "scan_source": scan_source,
        "barcode": barcode_value,
        "completed": completed,
        "package_count": package_count,
        "remaining": {
            str(order_item_id): _qty_text(qty)
            for order_item_id, qty in _remaining_qty_map(db, order).items()
        },
        "shipment": shipment_dict(db, shipment),
    }


def ship_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    tracking_no: str | None = None,
    package_no: int | None = None,
    actual_weight_g: Any = None,
    weight_tolerance_g: Any = None,
    allow_weight_override: bool = False,
    remark: str | None = None,
    commit: bool = True,
) -> dict:
    order = db.get(Order, _to_int(order_id, "订单"))
    if order is None:
        raise BizException(ErrorCode.NOT_FOUND, "订单不存在")
    _validate_order_for_shipping(db, order)
    shipment, completed, package_count = _ship_remaining_items(
        db,
        order=order,
        tracking_no=_optional_text(tracking_no, "运单号", 64)
        or order.tracking_no,
        package_no=_optional_int(package_no, "包裹序号"),
        shipping_type=SHIPPING_TYPE_EXPRESS,
        source=SOURCE_ONE_CLICK,
        actual_weight_g=_optional_weight(actual_weight_g, "实际重量"),
        tolerance_g=_optional_weight(
            weight_tolerance_g, "重量误差区间"
        ),
        allow_weight_override=_to_bool(
            allow_weight_override, "允许重量超差"
        ),
        remark=_optional_text(remark, "备注", 255),
        staff=staff,
    )
    _commit(db, commit)
    return {
        "completed": completed,
        "package_count": package_count,
        "shipment": shipment_dict(db, shipment),
    }


def self_pickup_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    package_no: int | None = None,
    actual_weight_g: Any = None,
    weight_tolerance_g: Any = None,
    allow_weight_override: bool = False,
    remark: str | None = None,
    commit: bool = True,
) -> dict:
    order = db.get(Order, _to_int(order_id, "订单"))
    if order is None:
        raise BizException(ErrorCode.NOT_FOUND, "订单不存在")
    _validate_order_for_shipping(db, order)
    shipment, completed, package_count = _ship_remaining_items(
        db,
        order=order,
        tracking_no=None,
        package_no=_optional_int(package_no, "包裹序号"),
        shipping_type=SHIPPING_TYPE_SELF_PICKUP,
        source=SOURCE_SELF_PICKUP,
        actual_weight_g=_optional_weight(actual_weight_g, "实际重量"),
        tolerance_g=_optional_weight(
            weight_tolerance_g, "重量误差区间"
        ),
        allow_weight_override=_to_bool(
            allow_weight_override, "允许重量超差"
        ),
        remark=_optional_text(remark, "备注", 255),
        staff=staff,
    )
    _commit(db, commit)
    return {
        "completed": completed,
        "package_count": package_count,
        "shipment": shipment_dict(db, shipment),
    }


def record_platform_sync(
    db: Session,
    *,
    shipment_id: int,
    status: str,
    message: str | None,
    staff: Staff,
    commit: bool = True,
) -> OrderShipment:
    shipment = get_shipment_or_404(db, shipment_id)
    if shipment.status != STATUS_SHIPPED:
        raise BizException(ErrorCode.CONFLICT, "包裹发货后才能回传平台")
    status_value = str(status or "").strip().upper()
    if status_value not in SYNC_STATUSES:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "平台回传状态必须为 PENDING/SYNCED/FAILED/SKIPPED",
        )
    shipment.platform_sync_status = status_value
    shipment.platform_sync_message = _optional_text(message, "回传说明", 255)
    write_audit(
        db,
        action="PLATFORM_SYNC",
        target_type=DOC_TYPE,
        target_id=str(shipment.id),
        summary=f"更新平台回传状态：{status_value}",
        detail={"status": status_value, "message": shipment.platform_sync_message},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return shipment


def _qty_text(value: Decimal) -> str:
    if value is None:
        return "0.000"
    return format(Decimal(value).quantize(QTY_QUANT), "f")
