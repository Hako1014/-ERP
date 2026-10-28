"""订单打印服务（TASK-038）。

口径说明：
- 一次“开始打印”生成一个打印批次号，快递单、拣货单、配货单、发货单共用该批次号。
- 同一打印批次只生成一张汇总拣货单；配货单按订单分别生成。
- 真实物流取号由 TASK-066 对接；当前支持手工录入运单号，未录入时使用本地运单号并明确标记来源。
- 打印机由前端/客户端选择，后端只保存名称；打印模板复用物流工具中的 PrintTemplate。
"""

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Brand, Category, Unit
from app.models.order import (
    DeliveryList,
    Order,
    OrderItem,
    PickList,
    PrintTemplate,
    Wave,
    WaveOrder,
    Waybill,
)
from app.models.org import Staff
from app.models.platform import Logistics, Shop
from app.models.product import Product, ProductSku
from app.models.warehouse import Location, StockLocation, Warehouse

DOC_TYPE_BATCH = "print_batch"
DOC_TYPE_WAYBILL = "waybill"

PRINT_TYPE_THERMAL = "THERMAL"
PRINT_TYPE_NORMAL = "NORMAL"
PRINT_TYPES = {PRINT_TYPE_THERMAL, PRINT_TYPE_NORMAL}

SORT_LOCATION = "LOCATION"
SORT_BRAND = "BRAND"
SORT_CATEGORY = "CATEGORY"
SORT_TYPES = {SORT_LOCATION, SORT_BRAND, SORT_CATEGORY}

DOCUMENT_WAYBILL = "WAYBILL"
DOCUMENT_PICK_LIST = "PICK_LIST"
DOCUMENT_DELIVERY_LIST = "DELIVERY_LIST"
DOCUMENT_SHIPPING_SLIP = "SHIPPING_SLIP"
DOCUMENT_ALL = "ALL"
DOCUMENT_TYPES = {
    DOCUMENT_WAYBILL,
    DOCUMENT_PICK_LIST,
    DOCUMENT_DELIVERY_LIST,
    DOCUMENT_SHIPPING_SLIP,
    DOCUMENT_ALL,
}

PRINTABLE_STATUSES = {"PENDING", "SHIPPED", "DONE"}
MAX_BATCH_ORDERS = 500
QTY_QUANT = Decimal("0.001")
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


def _normalize_order_ids(order_ids: list[int]) -> list[int]:
    if not order_ids:
        raise BizException(ErrorCode.BAD_REQUEST, "请至少选择一个订单")
    if len(order_ids) > MAX_BATCH_ORDERS:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"单次最多打印 {MAX_BATCH_ORDERS} 个订单",
        )
    result: list[int] = []
    for value in order_ids:
        order_id = _to_int(value, "订单")
        if order_id not in result:
            result.append(order_id)
    return result


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


def _normalize_print_type(value: Any) -> str:
    print_type = str(value or PRINT_TYPE_THERMAL).strip().upper()
    if print_type not in PRINT_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "打印类型必须为 THERMAL 或 NORMAL",
        )
    return print_type


def _normalize_sort_type(value: Any) -> str:
    sort_type = str(value or SORT_LOCATION).strip().upper()
    if sort_type not in SORT_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "排序方式必须为 LOCATION、BRAND 或 CATEGORY",
        )
    return sort_type


def _to_qty(value: Any, label: str = "数量") -> Decimal:
    try:
        result = Decimal(str(value or 0)).quantize(
            QTY_QUANT,
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    return result


def _qty_text(value: Decimal) -> str:
    return format(_to_qty(value), "f")


def _json_dump(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )


def _json_load(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get_orders(db: Session, order_ids: list[int]) -> list[Order]:
    normalized = _normalize_order_ids(order_ids)
    rows = list(
        db.scalars(select(Order).where(Order.id.in_(normalized)))
    )
    by_id = {order.id: order for order in rows}
    missing = [order_id for order_id in normalized if order_id not in by_id]
    if missing:
        raise BizException(
            ErrorCode.NOT_FOUND,
            "订单不存在：" + ", ".join(str(item) for item in missing),
        )
    orders = [by_id[order_id] for order_id in normalized]
    invalid = [
        order.order_no
        for order in orders
        if order.audit_status != "APPROVED"
        or order.status not in PRINTABLE_STATUSES
    ]
    if invalid:
        raise BizException(
            ErrorCode.CONFLICT,
            "以下订单当前不可打印（需审核通过且未取消/删除）："
            + ", ".join(invalid),
        )
    return orders


def _latest_waybill(db: Session, order_id: int) -> Waybill | None:
    return db.scalar(
        select(Waybill)
        .where(Waybill.order_id == order_id)
        .order_by(Waybill.id.desc())
        .limit(1)
    )


def _normalize_waybill_numbers(
    order_ids: list[int],
    raw: Any,
) -> dict[int, str]:
    if raw in (None, ""):
        return {}
    if isinstance(raw, dict):
        result: dict[int, str] = {}
        for key, value in raw.items():
            order_id = _to_int(key, "订单")
            if order_id not in order_ids:
                raise BizException(
                    ErrorCode.BAD_REQUEST,
                    f"运单号对应订单不在打印批次中：{order_id}",
                )
            number = _optional_text(value, "运单号", 64)
            if number:
                result[order_id] = number
        return result
    if isinstance(raw, list):
        if len(raw) != len(order_ids):
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "运单号数量必须与订单数量一致",
            )
        result = {}
        for order_id, value in zip(order_ids, raw, strict=True):
            number = _optional_text(value, "运单号", 64)
            if number:
                result[order_id] = number
        return result
    raise BizException(ErrorCode.BAD_REQUEST, "运单号必须为对象或数组")


def _assert_waybill_no_available(
    db: Session,
    *,
    waybill_no: str,
    order_id: int,
    seen: dict[str, int],
) -> None:
    previous_order_id = seen.get(waybill_no)
    if previous_order_id is not None and previous_order_id != order_id:
        raise BizException(
            ErrorCode.CONFLICT,
            f"运单号重复：{waybill_no}",
        )
    existing = db.scalar(
        select(Waybill)
        .where(
            Waybill.waybill_no == waybill_no,
            Waybill.order_id != order_id,
        )
        .order_by(Waybill.id.desc())
        .limit(1)
    )
    if existing is not None:
        raise BizException(
            ErrorCode.CONFLICT,
            f"运单号已被订单占用：{waybill_no}",
        )
    seen[waybill_no] = order_id


def _local_waybill_no() -> str:
    return generate_serial(SerialPrefix.WAYBILL, dt=_now())


def _ensure_waybill_rows(
    db: Session,
    *,
    orders: list[Order],
    print_type: str,
    waybill_numbers: dict[int, str],
) -> list[Waybill]:
    rows: list[Waybill] = []
    seen: dict[str, int] = {}
    for order in orders:
        latest = _latest_waybill(db, order.id)
        requested_no = waybill_numbers.get(order.id)
        waybill_no = requested_no

        if latest is None or latest.is_printed:
            if waybill_no is None:
                waybill_no = (
                    latest.waybill_no if latest and latest.waybill_no else None
                )
            if waybill_no is None:
                waybill_no = _local_waybill_no()
            _assert_waybill_no_available(
                db,
                waybill_no=waybill_no,
                order_id=order.id,
                seen=seen,
            )
            waybill = Waybill(
                order_id=order.id,
                waybill_no=waybill_no,
                print_type=print_type,
                is_printed=False,
            )
            db.add(waybill)
            db.flush()
        else:
            waybill = latest
            if waybill_no is None and waybill.waybill_no:
                waybill_no = waybill.waybill_no
            if waybill_no is None:
                waybill_no = _local_waybill_no()
            _assert_waybill_no_available(
                db,
                waybill_no=waybill_no,
                order_id=order.id,
                seen=seen,
            )
            waybill.waybill_no = waybill_no
            waybill.print_type = print_type

        order.tracking_no = waybill_no
        rows.append(waybill)
    return rows


def acquire_waybills(
    db: Session,
    *,
    order_ids: list[int],
    staff: Staff,
    print_type: str = PRINT_TYPE_THERMAL,
    waybill_nos: Any = None,
    provider: bool = False,
    commit: bool = True,
) -> dict:
    """获取运单号：手工、本地或物流 mock 适配器。"""
    normalized_ids = _normalize_order_ids(order_ids)
    orders = _get_orders(db, normalized_ids)
    print_type_value = _normalize_print_type(print_type)
    numbers = _normalize_waybill_numbers(normalized_ids, waybill_nos)
    if provider:
        if numbers:
            raise BizException(ErrorCode.BAD_REQUEST, "物流取号不能同时指定手工运单号")
        from app.integrations.logistics_provider import get_logistics_provider

        for order in orders:
            latest = _latest_waybill(db, order.id)
            if latest and latest.waybill_no:
                if not latest.waybill_no.startswith("MOCK-"):
                    raise BizException(ErrorCode.CONFLICT, "订单已有非 mock 运单号，请先回收")
                numbers[order.id] = latest.waybill_no
                continue
            logistics = db.get(Logistics, order.logistics_id) if order.logistics_id else None
            if logistics is None or not logistics.is_enabled:
                raise BizException(ErrorCode.CONFLICT, "订单未配置可用物流公司")
            try:
                numbers[order.id] = get_logistics_provider(logistics).acquire(order, logistics)
            except Exception as exc:
                raise BizException(ErrorCode.CONFLICT, "物流取号失败") from exc
    rows = _ensure_waybill_rows(
        db,
        orders=orders,
        print_type=print_type_value,
        waybill_numbers=numbers,
    )
    write_audit(
        db,
        action="ACQUIRE",
        target_type=DOC_TYPE_WAYBILL,
        target_id=str(rows[0].id) if len(rows) == 1 else None,
        summary=f"获取运单号 {len(rows)} 个",
        detail={
            "order_ids": normalized_ids,
            "print_type": print_type_value,
            "source": "PROVIDER_MOCK" if provider else "MANUAL" if numbers else "LOCAL",
            "waybill_nos": [row.waybill_no for row in rows],
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "print_type": print_type_value,
        "source": "PROVIDER_MOCK" if provider else "MANUAL" if numbers else "LOCAL",
        "count": len(rows),
        "items": [
            {
                "waybill_id": row.id,
                "order_id": row.order_id,
                "order_no": orders[index].order_no,
                "waybill_no": row.waybill_no,
                "print_type": row.print_type,
                "is_printed": row.is_printed,
            }
            for index, row in enumerate(rows)
        ],
    }


def recycle_waybills(
    db: Session,
    *,
    order_ids: list[int],
    staff: Staff,
    commit: bool = True,
) -> dict:
    """回收尚未打印的运单号，保留原值到审计日志。"""
    normalized_ids = _normalize_order_ids(order_ids)
    orders = _get_orders(db, normalized_ids)
    recycled: list[dict] = []
    skipped: list[dict] = []
    for order in orders:
        waybill = _latest_waybill(db, order.id)
        if waybill is None or not waybill.waybill_no:
            skipped.append({"order_id": order.id, "reason": "无待回收运单号"})
            continue
        if waybill.is_printed:
            raise BizException(
                ErrorCode.CONFLICT,
                f"订单 {order.order_no} 的运单号已打印，不能回收",
            )
        old_no = waybill.waybill_no
        if old_no.startswith("MOCK-"):
            from app.integrations.logistics_provider import get_logistics_provider

            logistics = db.get(Logistics, order.logistics_id) if order.logistics_id else None
            if logistics is None:
                raise BizException(ErrorCode.CONFLICT, "订单物流公司不存在，无法回收运单号")
            try:
                get_logistics_provider(logistics).recycle(order, logistics, old_no)
            except Exception as exc:
                raise BizException(ErrorCode.CONFLICT, "物流运单号回收失败") from exc
        waybill.waybill_no = None
        if order.tracking_no == old_no:
            order.tracking_no = None
        recycled.append(
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "waybill_id": waybill.id,
                "waybill_no": old_no,
            }
        )
    write_audit(
        db,
        action="RECYCLE",
        target_type=DOC_TYPE_WAYBILL,
        target_id=(
            str(recycled[0]["order_id"]) if len(recycled) == 1 else None
        ),
        summary=f"回收运单号 {len(recycled)} 个",
        detail={"recycled": recycled, "skipped": skipped},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "count": len(recycled),
        "items": recycled,
        "skipped": skipped,
    }


def _wave_info_map(
    db: Session,
    order_ids: list[int],
) -> dict[int, dict]:
    if not order_ids:
        return {}
    rows = db.execute(
        select(
            WaveOrder.order_id,
            WaveOrder.load_no,
            WaveOrder.sequence,
            WaveOrder.sort_key,
            Wave.id,
            Wave.wave_no,
        )
        .join(Wave, Wave.id == WaveOrder.wave_id)
        .where(WaveOrder.order_id.in_(order_ids))
    )
    return {
        int(order_id): {
            "wave_id": int(wave_id),
            "wave_no": wave_no,
            "load_no": load_no,
            "sequence": sequence,
            "sort_key": sort_key,
        }
        for order_id, load_no, sequence, sort_key, wave_id, wave_no in rows
    }


def _template_dict(template: PrintTemplate | None) -> dict | None:
    if template is None:
        return None
    return {
        "id": template.id,
        "name": template.name,
        "template_type": template.template_type,
        "logistics_id": template.logistics_id,
        "paper_width": template.paper_width,
        "paper_height": template.paper_height,
        "is_default": template.is_default,
    }


def _load_template(db: Session, template_id: int | None) -> PrintTemplate | None:
    if template_id is None:
        return None
    template = db.get(PrintTemplate, template_id)
    if template is None:
        raise BizException(ErrorCode.NOT_FOUND, "打印模板不存在")
    return template


def _batch_item_context(db: Session, orders: list[Order]) -> dict:
    """批量预取打印明细，避免每个订单及每个 SKU 分别访问数据库。"""
    order_ids = [order.id for order in orders]
    items = list(db.scalars(
        select(OrderItem).where(OrderItem.order_id.in_(order_ids)).order_by(OrderItem.id)
    ))
    product_ids = {item.product_id for item in items}
    sku_ids = {item.sku_id for item in items if item.sku_id is not None}
    products = {row.id: row for row in db.scalars(
        select(Product).where(Product.id.in_(product_ids))
    )} if product_ids else {}
    skus = {row.id: row for row in db.scalars(
        select(ProductSku).where(ProductSku.id.in_(sku_ids))
    )} if sku_ids else {}

    def by_id(model, ids: set[int]) -> dict:
        return {row.id: row for row in db.scalars(select(model).where(model.id.in_(ids)))} if ids else {}

    brands = by_id(Brand, {row.brand_id for row in products.values() if row.brand_id})
    categories = by_id(Category, {row.category_id for row in products.values() if row.category_id})
    units = by_id(Unit, {row.main_unit_id for row in products.values() if row.main_unit_id})
    warehouse_ids = {order.warehouse_id for order in orders if order.warehouse_id}
    locations: dict[tuple[int, int], list[str]] = {}
    if sku_ids and warehouse_ids:
        rows = db.execute(
            select(Location.warehouse_id, StockLocation.product_sku_id, Location.code)
            .join(StockLocation, StockLocation.location_id == Location.id)
            .where(Location.warehouse_id.in_(warehouse_ids), StockLocation.product_sku_id.in_(sku_ids))
            .order_by(Location.code)
        )
        for warehouse_id, sku_id, code in rows:
            locations.setdefault((warehouse_id, sku_id), []).append(code)
    items_by_order: dict[int, list[OrderItem]] = {}
    for item in items:
        items_by_order.setdefault(item.order_id, []).append(item)
    return {
        "items": items_by_order, "products": products, "skus": skus,
        "brands": brands, "categories": categories, "units": units,
        "locations": locations,
    }


def _item_records(db: Session, order: Order, *, context: dict | None = None) -> list[dict]:
    items = (context["items"].get(order.id, []) if context is not None else list(
        db.scalars(
            select(OrderItem)
            .where(OrderItem.order_id == order.id)
            .order_by(OrderItem.id)
        )
    ))
    records: list[dict] = []
    for item in items:
        product = (context["products"].get(item.product_id) if context is not None
                   else db.get(Product, item.product_id))
        sku = ((context["skus"].get(item.sku_id) if context is not None else db.get(ProductSku, item.sku_id))
               if item.sku_id else None)
        brand = (
            (context["brands"].get(product.brand_id) if context is not None else db.get(Brand, product.brand_id))
            if product is not None and product.brand_id
            else None
        )
        category = (
            (context["categories"].get(product.category_id) if context is not None else db.get(Category, product.category_id))
            if product is not None and product.category_id
            else None
        )
        unit = (
            (context["units"].get(product.main_unit_id) if context is not None else db.get(Unit, product.main_unit_id))
            if product is not None and product.main_unit_id
            else None
        )
        location_codes: list[str] = []
        if item.sku_id and order.warehouse_id:
            location_codes = context["locations"].get((order.warehouse_id, item.sku_id), []) if context is not None else list(
                db.scalars(
                    select(Location.code)
                    .join(
                        StockLocation,
                        StockLocation.location_id == Location.id,
                    )
                    .where(
                        Location.warehouse_id == order.warehouse_id,
                        StockLocation.product_sku_id == item.sku_id,
                    )
                    .order_by(Location.code)
                )
            )
        qty = _to_qty(item.qty)
        records.append(
            {
                "order_id": order.id,
                "order_no": order.order_no,
                "product_id": item.product_id,
                "product_code": product.code if product else None,
                "product_name": product.name if product else None,
                "sku_id": item.sku_id,
                "sku_code": sku.spec_code if sku else None,
                "sku_name": item.sku_name or (sku.spec_name if sku else None),
                "barcode": (
                    sku.barcode
                    if sku is not None and sku.barcode
                    else (product.barcode if product else None)
                ),
                "unit_name": unit.name if unit else None,
                "qty": qty,
                "price": round_money(item.price or 0),
                "amount": round_money(item.amount or 0),
                "is_gift": item.is_gift,
                "brand_id": product.brand_id if product else None,
                "brand_name": brand.name if brand else None,
                "category_id": product.category_id if product else None,
                "category_name": category.name if category else None,
                "location_codes": location_codes,
                "location_code": location_codes[0] if location_codes else None,
            }
        )
    return records


def _item_json(row: dict) -> dict:
    return {
        "order_id": row["order_id"],
        "order_no": row["order_no"],
        "product_id": row["product_id"],
        "product_code": row["product_code"],
        "product_name": row["product_name"],
        "sku_id": row["sku_id"],
        "sku_code": row["sku_code"],
        "sku_name": row["sku_name"],
        "barcode": row["barcode"],
        "unit_name": row["unit_name"],
        "qty": _qty_text(row["qty"]),
        "price": str(row["price"]),
        "amount": str(row["amount"]),
        "is_gift": row["is_gift"],
        "brand_id": row["brand_id"],
        "brand_name": row["brand_name"],
        "category_id": row["category_id"],
        "category_name": row["category_name"],
        "location_codes": row["location_codes"],
        "location_code": row["location_code"],
        "wave_id": row.get("wave_id"),
        "wave_no": row.get("wave_no"),
        "load_no": row.get("load_no"),
    }


def _sort_key(row: dict, sort_type: str) -> tuple:
    if sort_type == SORT_BRAND:
        primary = row["brand_name"] or "\uffff"
    elif sort_type == SORT_CATEGORY:
        primary = row["category_name"] or "\uffff"
    else:
        primary = row["location_code"] or "\uffff"
    return (
        primary,
        row["order_no"],
        row["product_code"] or "",
        row["sku_code"] or "",
        row["product_id"],
        row["sku_id"] or 0,
    )


def _batch_scope(
    wave_info: dict[int, dict],
    order_ids: list[int],
) -> tuple[int | None, str | None]:
    wave_ids = {
        wave_info[order_id]["wave_id"]
        for order_id in order_ids
        if order_id in wave_info
    }
    if len(wave_ids) != 1:
        return None, None
    load_nos = {
        wave_info[order_id].get("load_no")
        for order_id in order_ids
        if order_id in wave_info
    }
    return (
        next(iter(wave_ids)),
        next(iter(load_nos)) if len(load_nos) == 1 else None,
    )


def _base_metadata(
    *,
    batch_no: str,
    print_type: str,
    printer_name: str | None,
    template: PrintTemplate | None,
    include_shipping_slip: bool,
    sort_type: str,
) -> dict:
    return {
        "version": 1,
        "batch_no": batch_no,
        "print_type": print_type,
        "printer_name": printer_name,
        "template": _template_dict(template),
        "include_shipping_slip": include_shipping_slip,
        "sort_type": sort_type,
    }


def _build_pick_content(
    db: Session,
    *,
    orders: list[Order],
    batch_no: str,
    print_type: str,
    printer_name: str | None,
    template: PrintTemplate | None,
    include_shipping_slip: bool,
    sort_type: str,
    wave_info: dict[int, dict],
    item_context: dict | None = None,
) -> dict:
    rows: list[dict] = []
    for order in orders:
        info = wave_info.get(order.id, {})
        for item in _item_records(db, order, context=item_context):
            item["wave_id"] = info.get("wave_id")
            item["wave_no"] = info.get("wave_no")
            item["load_no"] = info.get("load_no")
            rows.append(item)
    rows.sort(key=lambda row: _sort_key(row, sort_type))
    total_qty = sum((row["qty"] for row in rows), ZERO_QTY)
    content = _base_metadata(
        batch_no=batch_no,
        print_type=print_type,
        printer_name=printer_name,
        template=template,
        include_shipping_slip=include_shipping_slip,
        sort_type=sort_type,
    )
    content.update(
        {
            "order_count": len(orders),
            "row_count": len(rows),
            "total_qty": _qty_text(total_qty),
            "rows": [
                {"sequence": index, **_item_json(row)}
                for index, row in enumerate(rows, start=1)
            ],
        }
    )
    return content


def _build_delivery_content(
    db: Session,
    *,
    order: Order,
    batch_no: str,
    print_type: str,
    printer_name: str | None,
    template: PrintTemplate | None,
    include_shipping_slip: bool,
    sort_type: str,
    wave_info: dict[int, dict],
    item_context: dict | None = None,
) -> dict:
    info = wave_info.get(order.id, {})
    items = _item_records(db, order, context=item_context)
    total_qty = sum((item["qty"] for item in items), ZERO_QTY)
    content = _base_metadata(
        batch_no=batch_no,
        print_type=print_type,
        printer_name=printer_name,
        template=template,
        include_shipping_slip=include_shipping_slip,
        sort_type=sort_type,
    )
    content.update(
        {
            "order_id": order.id,
            "order_no": order.order_no,
            "wave_id": info.get("wave_id"),
            "wave_no": info.get("wave_no"),
            "load_no": info.get("load_no"),
            "receiver_name": order.receiver_name,
            "receiver_phone": order.receiver_phone,
            "receiver_address": " ".join(
                part
                for part in [
                    order.receiver_province,
                    order.receiver_city,
                    order.receiver_address,
                ]
                if part
            ),
            "total_qty": _qty_text(total_qty),
            "items": [_item_json(item) for item in items],
        }
    )
    return content


def _shipping_slip_dict(
    db: Session,
    *,
    order: Order,
    batch_no: str,
    wave_info: dict[int, dict],
    item_context: dict | None = None,
) -> dict:
    shop = db.get(Shop, order.shop_id)
    logistics = (
        db.get(Logistics, order.logistics_id)
        if order.logistics_id
        else None
    )
    warehouse = (
        db.get(Warehouse, order.warehouse_id)
        if order.warehouse_id
        else None
    )
    info = wave_info.get(order.id, {})
    items = _item_records(db, order, context=item_context)
    total_qty = sum((item["qty"] for item in items), ZERO_QTY)
    return {
        "batch_no": batch_no,
        "order_id": order.id,
        "order_no": order.order_no,
        "shop_name": shop.name if shop else None,
        "warehouse_name": warehouse.name if warehouse else None,
        "logistics_name": logistics.name if logistics else order.logistics_company,
        "waybill_no": order.tracking_no,
        "wave_id": info.get("wave_id"),
        "wave_no": info.get("wave_no"),
        "load_no": info.get("load_no"),
        "receiver_name": order.receiver_name,
        "receiver_phone": order.receiver_phone,
        "receiver_address": " ".join(
            part
            for part in [
                order.receiver_province,
                order.receiver_city,
                order.receiver_address,
            ]
            if part
        ),
        "buyer_message": order.buyer_message,
        "seller_remark": order.seller_remark,
        "paid_amount": round_money(order.paid_amount or 0),
        "total_qty": total_qty,
        "items": items,
    }


def create_print_batch(
    db: Session,
    *,
    order_ids: list[int],
    staff: Staff,
    print_type: str = PRINT_TYPE_THERMAL,
    printer_name: str | None = None,
    template_id: int | None = None,
    include_shipping_slip: bool = False,
    sort_type: str = SORT_LOCATION,
    waybill_nos: Any = None,
    commit: bool = True,
) -> dict:
    """开始打印：统一批次号，生成运单、汇总拣货单、配货单和发货单数据。"""
    normalized_ids = _normalize_order_ids(order_ids)
    orders = _get_orders(db, normalized_ids)
    print_type_value = _normalize_print_type(print_type)
    sort_type_value = _normalize_sort_type(sort_type)
    printer_value = _optional_text(printer_name, "打印机名称", 128)
    include_shipping = _to_bool(include_shipping_slip, "同时打印发货单")
    template = _load_template(db, template_id)
    numbers = _normalize_waybill_numbers(normalized_ids, waybill_nos)

    batch_no = generate_serial(SerialPrefix.PRINT_BATCH, dt=_now())
    waybills = _ensure_waybill_rows(
        db,
        orders=orders,
        print_type=print_type_value,
        waybill_numbers=numbers,
    )
    for waybill in waybills:
        waybill.batch_no = batch_no
        waybill.is_printed = True

    wave_info = _wave_info_map(db, normalized_ids)
    item_context = _batch_item_context(db, orders)
    wave_id, load_no = _batch_scope(wave_info, normalized_ids)
    pick_content = _build_pick_content(
        db,
        orders=orders,
        batch_no=batch_no,
        print_type=print_type_value,
        printer_name=printer_value,
        template=template,
        include_shipping_slip=include_shipping,
        sort_type=sort_type_value,
        wave_info=wave_info,
        item_context=item_context,
    )
    pick_content["wave_id"] = wave_id
    scope_wave = db.get(Wave, wave_id) if wave_id is not None else None
    pick_content["wave_no"] = scope_wave.wave_no if scope_wave else None
    pick_content["load_no"] = load_no
    db.add(
        PickList(
            batch_no=batch_no,
            wave_id=wave_id,
            load_no=load_no,
            content=_json_dump(pick_content),
        )
    )

    for order in orders:
        info = wave_info.get(order.id, {})
        content = _build_delivery_content(
            db,
            order=order,
            batch_no=batch_no,
            print_type=print_type_value,
            printer_name=printer_value,
            template=template,
            include_shipping_slip=include_shipping,
            sort_type=sort_type_value,
            wave_info=wave_info,
            item_context=item_context,
        )
        db.add(
            DeliveryList(
                batch_no=batch_no,
                wave_id=info.get("wave_id"),
                load_no=info.get("load_no"),
                content=_json_dump(content),
            )
        )

    write_audit(
        db,
        action="PRINT",
        target_type=DOC_TYPE_BATCH,
        target_id=batch_no,
        summary=f"打印批次 {batch_no}，共 {len(orders)} 个订单",
        detail={
            "order_ids": normalized_ids,
            "print_type": print_type_value,
            "printer_name": printer_value,
            "template_id": template_id,
            "include_shipping_slip": include_shipping,
            "sort_type": sort_type_value,
            "waybill_source": (
                "MANUAL" if numbers else
                "PROVIDER_MOCK" if waybills and all(row.waybill_no and row.waybill_no.startswith("MOCK-") for row in waybills)
                else "LOCAL"
            ),
            "waybill_nos": [row.waybill_no for row in waybills],
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return get_print_batch(db, batch_no=batch_no)


def _batch_artifacts(
    db: Session,
    batch_no: str,
) -> tuple[list[Waybill], PickList | None, list[DeliveryList]]:
    normalized_batch_no = _optional_text(batch_no, "打印批次号", 64)
    if not normalized_batch_no:
        raise BizException(ErrorCode.BAD_REQUEST, "打印批次号不能为空")
    waybills = list(
        db.scalars(
            select(Waybill)
            .where(Waybill.batch_no == normalized_batch_no)
            .order_by(Waybill.id)
        )
    )
    pick_list = db.scalar(
        select(PickList)
        .where(PickList.batch_no == normalized_batch_no)
        .order_by(PickList.id)
        .limit(1)
    )
    delivery_lists = list(
        db.scalars(
            select(DeliveryList)
            .where(DeliveryList.batch_no == normalized_batch_no)
            .order_by(DeliveryList.id)
        )
    )
    if not waybills and pick_list is None and not delivery_lists:
        raise BizException(ErrorCode.NOT_FOUND, "打印批次不存在")
    return waybills, pick_list, delivery_lists


def get_print_batch(db: Session, *, batch_no: str) -> dict:
    waybills, pick_list, delivery_lists = _batch_artifacts(db, batch_no)
    pick_content = _json_load(pick_list.content) if pick_list else {}
    delivery_contents = [
        _json_load(row.content) for row in delivery_lists
    ]
    metadata = pick_content or (
        delivery_contents[0] if delivery_contents else {}
    )
    order_ids: list[int] = []
    for waybill in waybills:
        if waybill.order_id not in order_ids:
            order_ids.append(waybill.order_id)
    for content in delivery_contents:
        order_id = content.get("order_id")
        if order_id is not None and int(order_id) not in order_ids:
            order_ids.append(int(order_id))
    rows = list(db.scalars(select(Order).where(Order.id.in_(order_ids)))) if order_ids else []
    rows_by_id = {order.id: order for order in rows}
    orders = [rows_by_id[order_id] for order_id in order_ids if order_id in rows_by_id]
    order_by_id = {order.id: order for order in orders}
    wave_info = _wave_info_map(db, [order.id for order in orders])
    item_context = _batch_item_context(db, orders) if orders and bool(metadata.get("include_shipping_slip", False)) else None

    created_values = [
        row.created_at
        for row in [*waybills, *delivery_lists, pick_list]
        if row is not None and row.created_at is not None
    ]
    print_type = metadata.get("print_type") or (
        waybills[0].print_type if waybills else PRINT_TYPE_THERMAL
    )
    include_shipping = bool(metadata.get("include_shipping_slip", False))
    template = metadata.get("template")
    return {
        "batch_no": metadata.get("batch_no") or (
            waybills[0].batch_no if waybills else None
        ),
        "print_type": print_type,
        "printer_name": metadata.get("printer_name"),
        "template": template,
        "include_shipping_slip": include_shipping,
        "sort_type": metadata.get("sort_type") or SORT_LOCATION,
        "order_count": len(orders),
        "created_at": min(created_values) if created_values else None,
        "orders": [
            {
                "id": order.id,
                "order_no": order.order_no,
                "receiver_name": order.receiver_name,
                "receiver_phone": order.receiver_phone,
                "tracking_no": order.tracking_no,
                "wave_id": wave_info.get(order.id, {}).get("wave_id"),
                "wave_no": wave_info.get(order.id, {}).get("wave_no"),
                "load_no": wave_info.get(order.id, {}).get("load_no"),
            }
            for order in orders
        ],
        "waybills": [
            {
                "id": row.id,
                "order_id": row.order_id,
                "order_no": (
                    order_by_id[row.order_id].order_no
                    if row.order_id in order_by_id
                    else None
                ),
                "waybill_no": row.waybill_no,
                "print_type": row.print_type,
                "is_printed": row.is_printed,
                "created_at": row.created_at,
            }
            for row in waybills
        ],
        "pick_list": (
            {"id": pick_list.id, "batch_no": pick_list.batch_no, **pick_content}
            if pick_list is not None
            else None
        ),
        "delivery_lists": [
            {"id": row.id, "batch_no": row.batch_no, **content}
            for row, content in zip(
                delivery_lists,
                delivery_contents,
                strict=True,
            )
        ],
        "shipping_slips": (
            [
                _shipping_slip_dict(
                    db,
                    order=order,
                    batch_no=batch_no,
                    wave_info=wave_info,
                    item_context=item_context,
                )
                for order in orders
            ]
            if include_shipping
            else []
        ),
    }


def _template_page_size(template: dict | None) -> tuple[float, float] | None:
    if not template:
        return None
    width = template.get("paper_width")
    height = template.get("paper_height")
    if width in (None, "") or height in (None, ""):
        return None
    return float(width), float(height)


def _waybill_pdf_doc(
    db: Session,
    *,
    batch: dict,
    waybill: dict,
    order_by_id: dict[int, Order] | None = None,
    item_context: dict | None = None,
) -> tuple[Any, tuple[float, float] | None]:
    from app.services.pdf import PdfDocument, PdfTable

    order = (order_by_id.get(waybill["order_id"]) if order_by_id is not None
             else db.get(Order, waybill["order_id"]))
    if order is None:
        raise BizException(ErrorCode.NOT_FOUND, "运单关联订单不存在")
    logistics = (
        db.get(Logistics, order.logistics_id)
        if order.logistics_id
        else None
    )
    info = next(
        (
            item
            for item in batch["orders"]
            if item["id"] == order.id
        ),
        {},
    )
    items = _item_records(db, order, context=item_context)
    page_size = _template_page_size(batch.get("template"))
    return (
        PdfDocument(
            title="快递单",
            fields=[
                ("打印批次", batch["batch_no"]),
                ("订单号", order.order_no),
                ("运单号", waybill["waybill_no"] or ""),
                ("物流公司", logistics.name if logistics else ""),
                ("波次号", info.get("wave_no") or ""),
                ("装载号", info.get("load_no") or ""),
                ("收件人", order.receiver_name or ""),
                ("联系电话", order.receiver_phone or ""),
                (
                    "收件地址",
                    " ".join(
                        part
                        for part in [
                            order.receiver_province,
                            order.receiver_city,
                            order.receiver_address,
                        ]
                        if part
                    ),
                ),
                ("重量", order.weight or ""),
                ("卖家备注", order.seller_remark or ""),
            ],
            tables=[
                PdfTable(
                    headers=["商品编码", "商品名称", "规格", "数量"],
                    rows=[
                        [
                            item["product_code"] or "",
                            item["product_name"] or "",
                            item["sku_name"] or "",
                            _qty_text(item["qty"]),
                        ]
                        for item in items
                    ],
                )
            ],
            footers=[("打印类型", batch["print_type"])],
            page_size_mm=page_size,
        ),
        page_size,
    )


def _pick_list_pdf_doc(
    batch: dict,
) -> tuple[Any, tuple[float, float] | None]:
    from app.services.pdf import PdfDocument, PdfTable

    content = batch.get("pick_list") or {}
    rows = content.get("rows") or []
    page_size = _template_page_size(batch.get("template"))
    return (
        PdfDocument(
            title="汇总拣货单",
            fields=[
                ("打印批次", batch["batch_no"]),
                ("打印类型", batch["print_type"]),
                ("打印机", batch.get("printer_name") or ""),
                ("排序方式", batch.get("sort_type") or ""),
                ("订单数", batch.get("order_count") or 0),
                ("打印时间", batch.get("created_at") or ""),
            ],
            tables=[
                PdfTable(
                    headers=[
                        "序号",
                        "波次号",
                        "装载号",
                        "订单号",
                        "库位",
                        "商品编码",
                        "商品名称",
                        "规格",
                        "数量",
                    ],
                    rows=[
                        [
                            row.get("sequence"),
                            row.get("wave_no") or "",
                            row.get("load_no") or "",
                            row.get("order_no") or "",
                            row.get("location_code") or "",
                            row.get("product_code") or "",
                            row.get("product_name") or "",
                            row.get("sku_name") or "",
                            row.get("qty") or "0.000",
                        ]
                        for row in rows
                    ],
                )
            ],
            footers=[
                ("汇总数量", content.get("total_qty") or "0.000"),
                ("填写说明", "按库位/品牌/分类排序，按行拣货数量核对"),
            ],
            page_size_mm=page_size,
        ),
        page_size,
    )


def _delivery_pdf_doc(
    item: dict,
) -> tuple[Any, tuple[float, float] | None]:
    from app.services.pdf import PdfDocument, PdfTable

    return (
        PdfDocument(
            title="配货单",
            fields=[
                ("打印批次", item.get("batch_no") or ""),
                ("订单号", item.get("order_no") or ""),
                ("波次号", item.get("wave_no") or ""),
                ("装载号", item.get("load_no") or ""),
                ("收件人", item.get("receiver_name") or ""),
                ("联系电话", item.get("receiver_phone") or ""),
                ("收货地址", item.get("receiver_address") or ""),
            ],
            tables=[
                PdfTable(
                    headers=[
                        "商品编码",
                        "商品名称",
                        "规格",
                        "条码",
                        "配货数量",
                    ],
                    rows=[
                        [
                            row.get("product_code") or "",
                            row.get("product_name") or "",
                            row.get("sku_name") or "",
                            row.get("barcode") or "",
                            row.get("qty") or "0.000",
                        ]
                        for row in item.get("items") or []
                    ],
                )
            ],
            footers=[("配货总数量", item.get("total_qty") or "0.000")],
        ),
        None,
    )


def _shipping_slip_pdf_doc(
    item: dict,
) -> tuple[Any, tuple[float, float] | None]:
    from app.services.pdf import PdfDocument, PdfTable

    return (
        PdfDocument(
            title="发货单",
            fields=[
                ("打印批次", item.get("batch_no") or ""),
                ("订单号", item.get("order_no") or ""),
                ("店铺", item.get("shop_name") or ""),
                ("仓库", item.get("warehouse_name") or ""),
                ("物流公司", item.get("logistics_name") or ""),
                ("运单号", item.get("waybill_no") or ""),
                ("波次号", item.get("wave_no") or ""),
                ("装载号", item.get("load_no") or ""),
                ("收件人", item.get("receiver_name") or ""),
                ("联系电话", item.get("receiver_phone") or ""),
                ("收货地址", item.get("receiver_address") or ""),
            ],
            tables=[
                PdfTable(
                    headers=[
                        "商品编码",
                        "商品名称",
                        "规格",
                        "数量",
                        "单价",
                        "金额",
                    ],
                    rows=[
                        [
                            row.get("product_code") or "",
                            row.get("product_name") or "",
                            row.get("sku_name") or "",
                            _qty_text(row["qty"])
                            if isinstance(row.get("qty"), Decimal)
                            else (row.get("qty") or "0.000"),
                            row.get("price") or "0.00",
                            row.get("amount") or "0.00",
                        ]
                        for row in item.get("items") or []
                    ],
                )
            ],
            footers=[
                ("商品总数量", _qty_text(item.get("total_qty") or ZERO_QTY)),
                ("实付金额", item.get("paid_amount") or "0.00"),
                ("买家留言", item.get("buyer_message") or ""),
                ("卖家备注", item.get("seller_remark") or ""),
            ],
        ),
        None,
    )


def build_print_batch_pdf(
    db: Session,
    *,
    batch_no: str,
    document_type: str = DOCUMENT_ALL,
) -> bytes:
    from app.services.pdf import render_pdf_many

    document_type_value = str(document_type or DOCUMENT_ALL).strip().upper()
    if document_type_value not in DOCUMENT_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "单据类型必须为 WAYBILL/PICK_LIST/DELIVERY_LIST/SHIPPING_SLIP/ALL",
        )
    batch = get_print_batch(db, batch_no=batch_no)
    docs: list[Any] = []
    page_size: tuple[float, float] | None = None
    if document_type_value in {DOCUMENT_WAYBILL, DOCUMENT_ALL}:
        order_ids = {row["order_id"] for row in batch["waybills"]}
        pdf_orders = list(db.scalars(select(Order).where(Order.id.in_(order_ids)))) if order_ids else []
        order_by_id = {order.id: order for order in pdf_orders}
        item_context = _batch_item_context(db, pdf_orders) if pdf_orders else None
        for waybill in batch["waybills"]:
            doc, doc_page_size = _waybill_pdf_doc(
                db,
                batch=batch,
                waybill=waybill,
                order_by_id=order_by_id,
                item_context=item_context,
            )
            docs.append(doc)
            page_size = page_size or doc_page_size
    if document_type_value in {DOCUMENT_PICK_LIST, DOCUMENT_ALL}:
        doc, doc_page_size = _pick_list_pdf_doc(batch)
        docs.append(doc)
        page_size = page_size or doc_page_size
    if document_type_value in {DOCUMENT_DELIVERY_LIST, DOCUMENT_ALL}:
        docs.extend(_delivery_pdf_doc(item)[0] for item in batch["delivery_lists"])
    if document_type_value in {DOCUMENT_SHIPPING_SLIP, DOCUMENT_ALL}:
        docs.extend(
            _shipping_slip_pdf_doc(item)[0]
            for item in batch.get("shipping_slips") or []
        )
    if not docs:
        raise BizException(ErrorCode.NOT_FOUND, "该打印批次没有可打印单据")
    if document_type_value == DOCUMENT_ALL:
        page_size = (210.0, 297.0)
    return render_pdf_many(docs, page_size_mm=page_size)
