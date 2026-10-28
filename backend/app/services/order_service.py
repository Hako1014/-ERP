"""订单抓取与审核服务（TASK-036）。

口径说明：
- 订单状态：NEW（新订单）→ PENDING（待处理，审核通过后进入）→ SHIPPED（已发货）→
  DONE（已完成）；CANCELLED（已取消）、DELETED（已删除）为终态。
- 审核状态：PENDING（待审核）/ APPROVED（审核通过）/ REJECTED（审核不通过），由通用
  审批引擎驱动；审核人与审核时间记录在审批留痕中，不在订单表冗余存储。
- 审核校验顺序：库存是否满足 → 发货仓库与收货区域是否匹配 → 按最优惠运费匹配物流。
- 审核不扣减库存：PRD 4.6.4 规定「发货时自动扣减库存」，扣减由发货环节（TASK-039）负责。
- 订单抓取：真实平台抓单在 TASK-065，本任务提供 payload 批量抓取（按订单号幂等更新）
  与 Excel 导入两条通道，手工新增同样复用该逻辑。

待业务确认：
- 订单审核默认单环节（财务审核，见 docs/权限矩阵.md）；如需多环节直接在「审批管理」配置。
- 库存不足时审核直接拒绝，不做缺货放行；如需允许缺货审核需业务确认。
- 物流最优惠匹配在没有任何运费模板命中时只返回提示，不阻断审核。
"""

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.audit_log import AuditLog
from app.models.approval import ApprovalRecord
from app.models.order import Order, OrderItem
from app.models.org import Staff
from app.models.platform import Logistics, Platform, Shop
from app.models.product import Product, ProductSku
from app.models.warehouse import Warehouse
from app.services import approval_service, logistics_service, stock_query_service
from app.services.excel import (
    Column,
    ParseResult,
    SheetSpec,
    export_data,
    parse_data,
    register_template,
)
from app.repositories.order_repo import list_order_items

DOC_TYPE = "order"

STATUS_NEW = "NEW"
STATUS_PENDING = "PENDING"
STATUS_SHIPPED = "SHIPPED"
STATUS_DONE = "DONE"
STATUS_CANCELLED = "CANCELLED"
STATUS_DELETED = "DELETED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

FLAG_DELETED = "DELETED"
FLAG_CANCELLING = "CANCELLING"

QTY_QUANT = Decimal("0.001")
WEIGHT_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
ZERO_MONEY = Decimal("0.00")

STATUS_LABELS = {
    STATUS_NEW: "新订单",
    STATUS_PENDING: "待处理",
    STATUS_SHIPPED: "已发货",
    STATUS_DONE: "已完成",
    STATUS_CANCELLED: "已取消",
    STATUS_DELETED: "已删除",
}

# 订单标识（PRD 4.6.1）
ORDER_FLAGS = {
    "DISTRIBUTION": "分销订单",
    "MERGED": "合并订单",
    "WEISHANG": "微商",
    "REFUND": "退款订单",
    "RETURN": "退货订单",
    "DELETED": "删除订单",
    "CANCELLING": "取消中订单",
    "RESEND_BEFORE": "补发前订单",
    "RESEND_AFTER": "补发后订单",
    "SPLIT_MAIN": "拆分主单",
    "SPLIT_CHILD": "拆分子单",
}

FLAG_MODES = {"SET", "ADD", "REMOVE"}

CREATE_FIELDS = {
    "order_no",
    "shop_id",
    "warehouse_id",
    "logistics_id",
    "buyer_nick",
    "buyer_message",
    "seller_remark",
    "receiver_name",
    "receiver_phone",
    "receiver_province",
    "receiver_city",
    "receiver_address",
    "order_time",
    "pay_time",
    "pay_status",
    "freight",
    "platform_discount",
    "seller_discount",
    "paid_amount",
    "weight",
    "flags",
    "remark",
    "items",
}

UPDATE_FIELDS = CREATE_FIELDS

ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "sku_name",
    "qty",
    "price",
    "is_gift",
}

ORDER_IMPORT_TEMPLATE = SheetSpec(
    name="order",
    sheet_title="订单",
    columns=[
        Column("order_no", "订单号", required=True, width=24),
        Column("platform_code", "平台编码", required=True, width=14),
        Column("shop_name", "店铺名称", required=True, width=20),
        Column("order_time", "下单时间", width=20),
        Column("pay_time", "付款时间", width=20),
        Column("pay_status", "付款状态", width=12),
        Column("buyer_nick", "买家昵称", width=18),
        Column("receiver_name", "收货人", width=14),
        Column("receiver_phone", "收货人手机号", width=18),
        Column("receiver_province", "收货省", width=14),
        Column("receiver_city", "收货市", width=14),
        Column("receiver_address", "收货地址", width=40),
        Column("warehouse_code", "发货仓库编码", width=18),
        Column("logistics_name", "物流公司", width=18),
        Column("freight", "运费", type="decimal", width=12),
        Column("platform_discount", "平台优惠", type="decimal", width=12),
        Column("seller_discount", "卖家优惠", type="decimal", width=12),
        Column("buyer_message", "买家留言", width=24),
        Column("seller_remark", "卖家备注", width=24),
        Column("product_code", "商品编码", required=True, width=18),
        Column("sku_code", "规格编码", width=18),
        Column("qty", "数量", required=True, type="decimal", width=12),
        Column("price", "单价", type="decimal", width=12),
        Column("is_gift", "是否赠品", width=12),
    ],
)
register_template("order", ORDER_IMPORT_TEMPLATE)

ORDER_EXPORT_TEMPLATE = SheetSpec(
    name="order_export",
    sheet_title="订单",
    columns=[
        Column("order_no", "订单号", width=24),
        Column("shop_name", "店铺名称", width=20),
        Column("status_label", "订单状态", width=14),
        Column("audit_status", "审核状态", width=14),
        Column("flags_label", "订单标识", width=24),
        Column("buyer_nick", "买家昵称", width=18),
        Column("receiver_name", "收货人", width=14),
        Column("receiver_phone", "收货人手机号", width=18),
        Column("receiver_region", "收货区域", width=24),
        Column("warehouse_name", "发货仓库", width=18),
        Column("logistics_company", "物流公司", width=18),
        Column("tracking_no", "运单号", width=22),
        Column("total_qty", "商品数量", type="decimal", width=14),
        Column("order_amount", "订单金额", type="decimal", width=14),
        Column("paid_amount", "实付金额", type="decimal", width=14),
        Column("pay_status", "付款状态", width=12),
        Column("order_time", "下单时间", width=20),
        Column("pay_time", "付款时间", width=20),
    ],
)
register_template("order_export", ORDER_EXPORT_TEMPLATE)


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


def _optional_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int_id(value, label)


def _to_qty(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result < ZERO_QTY or (not allow_zero and result == ZERO_QTY):
        operator = "不能小于 0" if allow_zero else "必须大于 0"
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}{operator}")
    return result


def _to_money(value: Any, label: str, *, default: Decimal = ZERO_MONEY) -> Decimal:
    if value in (None, ""):
        return default
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < ZERO_MONEY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为负数")
    return result


def _optional_weight(value: Any, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = Decimal(str(value)).quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法重量") from exc
    if result < ZERO_QTY:
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


def _optional_datetime(value: Any, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None, microsecond=0)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text = str(value).strip()
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法时间")


def _flags_of(order: Order) -> list[str]:
    if not order.flags:
        return []
    return [item.strip() for item in order.flags.split(",") if item.strip()]


def _set_flags(order: Order, flags: list[str]) -> None:
    deduped = list(dict.fromkeys(flags))
    order.flags = ",".join(deduped) if deduped else None


def _flags_label(order: Order) -> str | None:
    names = [ORDER_FLAGS.get(code, code) for code in _flags_of(order)]
    return "、".join(names) if names else None


def _order_items(db: Session, order_id: int) -> list[OrderItem]:
    return list_order_items(db, order_id)


def order_item_dict(
    db: Session, item: OrderItem, *, company_id: int | None = None,
) -> dict:
    product_stmt = select(Product).where(Product.id == item.product_id)
    if company_id is not None:
        product_stmt = product_stmt.where(Product.company_id == company_id)
    product = db.scalar(product_stmt)
    sku_stmt = select(ProductSku).where(ProductSku.id == item.sku_id) if item.sku_id else None
    if sku_stmt is not None and company_id is not None:
        sku_stmt = sku_stmt.where(ProductSku.company_id == company_id)
    sku = db.scalar(sku_stmt) if sku_stmt is not None else None
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_code": product.code if product is not None else None,
        "product_name": product.name if product is not None else None,
        "product_image": product.image if product is not None else None,
        "sku_id": item.sku_id,
        "sku_name": item.sku_name or (sku.spec_name if sku is not None else None),
        "spec_code": sku.spec_code if sku is not None else None,
        "qty": Decimal(item.qty or 0),
        "price": round_money(item.price) if item.price is not None else None,
        "amount": round_money(item.amount) if item.amount is not None else None,
        "is_gift": item.is_gift,
    }


def order_dict(
    db: Session,
    order: Order,
    *,
    with_items: bool = True,
    with_approval: bool = False,
    company_id: int | None = None,
) -> dict:
    def company_row(model, row_id):
        if row_id is None:
            return None
        stmt = select(model).where(model.id == row_id)
        if company_id is not None and hasattr(model, "company_id"):
            stmt = stmt.where(model.company_id == company_id)
        return db.scalar(stmt)

    shop = company_row(Shop, order.shop_id)
    platform = company_row(Platform, shop.platform_id) if shop is not None else None
    warehouse = company_row(Warehouse, order.warehouse_id)
    logistics = company_row(Logistics, order.logistics_id)
    items = _order_items(db, order.id) if with_items else []
    if company_id is not None and with_items:
        items = [item for item in items if item.company_id == company_id]
    total_qty = sum((Decimal(item.qty or 0) for item in items), ZERO_QTY)
    region = " ".join(
        part for part in [order.receiver_province, order.receiver_city] if part
    )
    result: dict[str, Any] = {
        "id": order.id,
        "order_no": order.order_no,
        "shop_id": order.shop_id,
        "shop_name": shop.name if shop is not None else None,
        "platform_id": platform.id if platform is not None else None,
        "platform_name": platform.name if platform is not None else None,
        "warehouse_id": order.warehouse_id,
        "warehouse_name": warehouse.name if warehouse is not None else None,
        "logistics_id": order.logistics_id,
        "logistics_name": logistics.name if logistics is not None else None,
        "logistics_company": order.logistics_company,
        "tracking_no": order.tracking_no,
        "buyer_nick": order.buyer_nick,
        "buyer_message": order.buyer_message,
        "seller_remark": order.seller_remark,
        "receiver_name": order.receiver_name,
        "receiver_phone": order.receiver_phone,
        "receiver_province": order.receiver_province,
        "receiver_city": order.receiver_city,
        "receiver_region": region or None,
        "receiver_address": order.receiver_address,
        "status": order.status,
        "status_label": STATUS_LABELS.get(order.status, order.status),
        "audit_status": order.audit_status,
        "pay_status": order.pay_status,
        "flags": _flags_of(order),
        "flags_label": _flags_label(order),
        "order_amount": round_money(order.order_amount or 0),
        "paid_amount": round_money(order.paid_amount or 0),
        "freight": round_money(order.freight or 0),
        "platform_discount": round_money(order.platform_discount or 0),
        "seller_discount": round_money(order.seller_discount or 0),
        "weight": order.weight,
        "total_qty": total_qty,
        "merge_order_no": order.merge_order_no,
        "parent_order_no": order.parent_order_no,
        "order_time": order.order_time,
        "pay_time": order.pay_time,
        "ship_time": order.ship_time,
        "remark": order.remark,
        "created_at": order.created_at,
        "updated_at": order.updated_at,
    }
    if with_items:
        result["items"] = [
            order_item_dict(db, item, company_id=company_id) for item in items
        ]
    if with_approval:
        result["approval"] = _approval_state(db, order.id)
    return result


def get_order_or_404(
    db: Session, order_id: int, *, company_id: int | None = None,
) -> Order:
    stmt = select(Order).where(Order.id == order_id)
    if company_id is not None:
        stmt = stmt.where(Order.company_id == company_id)
    order = db.scalar(stmt)
    if order is None:
        raise BizException(ErrorCode.NOT_FOUND, "订单不存在")
    return order


def list_orders(
    db: Session,
    *,
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    pay_status: str | None = None,
    flag: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
    company_id: int | None = None,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if company_id is not None:
        conditions.append(Order.company_id == company_id)
    if shop_id is not None:
        conditions.append(Order.shop_id == shop_id)
    if warehouse_id is not None:
        conditions.append(Order.warehouse_id == warehouse_id)
    if status:
        conditions.append(Order.status == status.upper())
    if audit_status:
        conditions.append(Order.audit_status == audit_status.upper())
    if pay_status:
        conditions.append(Order.pay_status == pay_status.strip().upper())
    if flag:
        conditions.append(Order.flags.like(f"%{flag.strip().upper()}%"))
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Order.order_no.like(like),
                Order.buyer_nick.like(like),
                Order.receiver_name.like(like),
                Order.receiver_phone.like(like),
                Order.tracking_no.like(like),
            )
        )
    if start_time is not None:
        conditions.append(Order.order_time >= start_time)
    if end_time is not None:
        conditions.append(Order.order_time <= end_time)

    total = db.scalar(select(func.count(Order.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Order)
            .where(*conditions)
            .order_by(Order.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [order_dict(db, row) for row in rows],
    }


def list_order_flags() -> list[dict]:
    return [{"code": code, "label": label} for code, label in ORDER_FLAGS.items()]


# --------------------------------------------------------------------------- #
# 订单写入（手工新增 / 抓取 / 导入共用）
# --------------------------------------------------------------------------- #


def _normalize_item(
    db: Session,
    *,
    raw: dict,
    index: int,
) -> dict[str, Any]:
    _check_unknown_fields(raw, ITEM_FIELDS)
    product_id = _to_int_id(raw.get("product_id"), f"第 {index} 行商品")
    product = db.get(Product, product_id)
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"第 {index} 行商品不存在")
    sku_id = _optional_id(raw.get("sku_id"), f"第 {index} 行商品规格")
    sku: ProductSku | None = None
    if sku_id is not None:
        sku = db.get(ProductSku, sku_id)
        if sku is None or sku.product_id != product_id:
            raise BizException(
                ErrorCode.BAD_REQUEST, f"第 {index} 行商品规格不属于该商品"
            )
    qty = _to_qty(raw.get("qty"), f"第 {index} 行数量")
    price = _to_money(
        raw.get("price") if raw.get("price") is not None else product.online_price,
        f"第 {index} 行单价",
    )
    is_gift = _to_bool(raw.get("is_gift") or False, f"第 {index} 行是否赠品")
    return {
        "product_id": product_id,
        "sku_id": sku_id,
        "sku_name": _optional_text(
            raw.get("sku_name")
            if raw.get("sku_name") is not None
            else (sku.spec_name if sku is not None else None),
            f"第 {index} 行规格名称",
            128,
        ),
        "qty": qty,
        "price": price,
        "amount": round_money(qty * price),
        "is_gift": is_gift,
    }


def _normalize_items(db: Session, raw_items: Any) -> list[dict[str, Any]]:
    if raw_items in (None, ""):
        return []
    if not isinstance(raw_items, list):
        raise BizException(ErrorCode.BAD_REQUEST, "订单明细必须为数组")
    if len(raw_items) > 500:
        raise BizException(ErrorCode.BAD_REQUEST, "订单明细不能超过 500 行")
    return [
        _normalize_item(db, raw=raw, index=index)
        for index, raw in enumerate(raw_items, start=1)
    ]


def _normalize_flags(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    raw = value if isinstance(value, list) else str(value).split(",")
    flags: list[str] = []
    for item in raw:
        code = str(item).strip().upper()
        if not code:
            continue
        if code not in ORDER_FLAGS:
            raise BizException(ErrorCode.BAD_REQUEST, f"不支持的订单标识：{code}")
        flags.append(code)
    return list(dict.fromkeys(flags))


def _normalize_header(db: Session, data: dict, *, default_order_no: str | None) -> dict:
    shop_id = _to_int_id(data.get("shop_id"), "店铺")
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise BizException(ErrorCode.BAD_REQUEST, "店铺不存在")
    warehouse_id = _optional_id(data.get("warehouse_id"), "发货仓库")
    if warehouse_id is not None:
        warehouse = db.get(Warehouse, warehouse_id)
        if warehouse is None:
            raise BizException(ErrorCode.BAD_REQUEST, "发货仓库不存在")
        if not warehouse.is_enabled:
            raise BizException(ErrorCode.CONFLICT, "发货仓库已停用")
    logistics_id = _optional_id(data.get("logistics_id"), "物流公司")
    logistics_company: str | None = None
    if logistics_id is not None:
        logistics = db.get(Logistics, logistics_id)
        if logistics is None:
            raise BizException(ErrorCode.BAD_REQUEST, "物流公司不存在")
        logistics_company = logistics.name
    order_no = _optional_text(data.get("order_no"), "订单号", 64) or default_order_no
    if not order_no:
        raise BizException(ErrorCode.BAD_REQUEST, "订单号不能为空")
    return {
        "order_no": order_no,
        "shop_id": shop_id,
        "warehouse_id": warehouse_id,
        "logistics_id": logistics_id,
        "logistics_company": logistics_company,
        "buyer_nick": _optional_text(data.get("buyer_nick"), "买家昵称", 64),
        "buyer_message": _optional_text(data.get("buyer_message"), "买家留言", 255),
        "seller_remark": _optional_text(data.get("seller_remark"), "卖家备注", 255),
        "receiver_name": _optional_text(data.get("receiver_name"), "收货人", 64),
        "receiver_phone": _optional_text(data.get("receiver_phone"), "收货人手机号", 32),
        "receiver_province": _optional_text(
            data.get("receiver_province"), "收货省", 32
        ),
        "receiver_city": _optional_text(data.get("receiver_city"), "收货市", 32),
        "receiver_address": _optional_text(data.get("receiver_address"), "收货地址", 255),
        "order_time": _optional_datetime(data.get("order_time"), "下单时间"),
        "pay_time": _optional_datetime(data.get("pay_time"), "付款时间"),
        "pay_status": (
            str(data.get("pay_status")).strip().upper()
            if data.get("pay_status") not in (None, "")
            else "UNPAID"
        ),
        "freight": _to_money(data.get("freight"), "运费"),
        "platform_discount": _to_money(data.get("platform_discount"), "平台优惠"),
        "seller_discount": _to_money(data.get("seller_discount"), "卖家优惠"),
        "weight": _optional_weight(data.get("weight"), "重量"),
        "remark": _optional_text(data.get("remark"), "备注", 255),
    }


def _recalculate_order(
    db: Session,
    order: Order,
    *,
    paid_amount: Any = None,
) -> None:
    items = _order_items(db, order.id)
    amount = sum(
        (
            item.amount
            if item.amount is not None
            else round_money(Decimal(item.qty or 0) * Decimal(item.price or 0))
        )
        for item in items
    ) if items else ZERO_MONEY
    order.order_amount = round_money(
        Decimal(amount) + round_money(order.freight or 0)
    )
    if paid_amount not in (None, ""):
        order.paid_amount = _to_money(paid_amount, "实付金额")
    else:
        discounts = round_money(
            Decimal(order.platform_discount or 0) + Decimal(order.seller_discount or 0)
        )
        order.paid_amount = max(order.order_amount - discounts, ZERO_MONEY)
    if order.weight is None:
        computed = ZERO_QTY
        for item in items:
            product = db.get(Product, item.product_id)
            if product is None or product.weight is None:
                continue
            computed += Decimal(item.qty or 0) * Decimal(product.weight)
        if computed > ZERO_QTY:
            order.weight = computed.quantize(WEIGHT_QUANT, rounding=ROUND_HALF_UP)


def _upsert_order(
    db: Session,
    *,
    header: dict[str, Any],
    items: list[dict[str, Any]],
    staff: Staff,
    paid_amount: Any = None,
    source: str = "MANUAL",
) -> tuple[Order, bool]:
    order = db.scalar(select(Order).where(Order.order_no == header["order_no"]))
    created = order is None
    if created:
        order = Order(
            **header,
            status=STATUS_NEW,
            audit_status=AUDIT_PENDING,
        )
        db.add(order)
        db.flush()
    else:
        if order.status in {STATUS_SHIPPED, STATUS_DONE}:
            raise BizException(
                ErrorCode.CONFLICT,
                f"订单「{order.order_no}」已发货或已完成，不能覆盖",
            )
        for key, value in header.items():
            setattr(order, key, value)
        for item in _order_items(db, order.id):
            db.delete(item)
        db.flush()
    for item in items:
        db.add(OrderItem(order_id=order.id, **item))
    db.flush()
    _recalculate_order(db, order, paid_amount=paid_amount)
    action = "CREATE" if created else "UPDATE"
    write_audit(
        db,
        action=action,
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=(
            f"{'抓取/新增' if created else '更新'}订单：{order.order_no}"
            f"（来源 {source}）"
        ),
        detail={"order_no": order.order_no, "item_count": len(items), "source": source},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    return order, created


def create_order(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Order:
    _check_unknown_fields(data, CREATE_FIELDS)
    items = _normalize_items(db, data.get("items"))
    default_no = generate_serial(SerialPrefix.ORDER, dt=_now())
    header = _normalize_header(db, data, default_order_no=default_no)
    order, _ = _upsert_order(
        db,
        header=header,
        items=items,
        staff=staff,
        paid_amount=data.get("paid_amount"),
        source="MANUAL",
    )
    _set_flags(order, _normalize_flags(data.get("flags")))
    _commit(db, commit)
    return order


def fetch_orders(
    db: Session,
    *,
    orders: list[dict],
    staff: Staff,
    commit: bool = True,
) -> dict:
    """批量抓取订单（平台推送/第三方抓单入口，按订单号幂等）。

    每个元素包含订单头字段与 ``items`` 明细；已存在且未发货的订单按最新数据覆盖，
    已发货或已完成的订单跳过并返回原因，供 TASK-065 平台对接复用。
    """
    if not orders:
        raise BizException(ErrorCode.BAD_REQUEST, "抓取订单列表不能为空")
    if len(orders) > 500:
        raise BizException(ErrorCode.BAD_REQUEST, "单次抓取订单不能超过 500 单")

    created = 0
    updated = 0
    skipped: list[dict] = []
    for index, payload in enumerate(orders, start=1):
        _check_unknown_fields(payload, CREATE_FIELDS)
        items = _normalize_items(db, payload.get("items"))
        header = _normalize_header(db, payload, default_order_no=None)
        try:
            order, is_created = _upsert_order(
                db,
                header=header,
                items=items,
                staff=staff,
                paid_amount=payload.get("paid_amount"),
                source="FETCH",
            )
        except BizException as exc:
            if "已发货或已完成" not in exc.message:
                raise
            skipped.append(
                {
                    "index": index,
                    "order_no": header["order_no"],
                    "reason": exc.message,
                }
            )
            continue
        if payload.get("flags") is not None:
            _set_flags(order, _normalize_flags(payload.get("flags")))
        if is_created:
            created += 1
        else:
            updated += 1
    _commit(db, commit)
    return {"created": created, "updated": updated, "skipped": skipped}


def update_order(
    db: Session,
    *,
    order_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Order:
    _check_unknown_fields(data, UPDATE_FIELDS)
    order = get_order_or_404(db, order_id)
    _ensure_editable(db, order)

    merged = {
        "order_no": order.order_no,
        "shop_id": order.shop_id,
        "warehouse_id": order.warehouse_id,
        "logistics_id": order.logistics_id,
        "buyer_nick": order.buyer_nick,
        "buyer_message": order.buyer_message,
        "seller_remark": order.seller_remark,
        "receiver_name": order.receiver_name,
        "receiver_phone": order.receiver_phone,
        "receiver_province": order.receiver_province,
        "receiver_city": order.receiver_city,
        "receiver_address": order.receiver_address,
        "order_time": order.order_time,
        "pay_time": order.pay_time,
        "pay_status": order.pay_status,
        "freight": order.freight,
        "platform_discount": order.platform_discount,
        "seller_discount": order.seller_discount,
        "weight": order.weight,
        "remark": order.remark,
    }
    merged.update({key: value for key, value in data.items() if key != "items"})
    header = _normalize_header(db, merged, default_order_no=order.order_no)
    for key, value in header.items():
        setattr(order, key, value)

    if "items" in data:
        items = _normalize_items(db, data.get("items"))
        for item in _order_items(db, order.id):
            db.delete(item)
        db.flush()
        for item in items:
            db.add(OrderItem(order_id=order.id, **item))
        db.flush()
    _recalculate_order(db, order, paid_amount=data.get("paid_amount"))
    if "flags" in data:
        _set_flags(order, _normalize_flags(data.get("flags")))
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"修改订单：{order.order_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def delete_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> Order:
    """删除订单：置为已删除终态并打「删除订单」标识，保留记录便于追溯。"""
    order = get_order_or_404(db, order_id)
    if order.status == STATUS_DELETED:
        raise BizException(ErrorCode.CONFLICT, "订单已删除")
    if order.status in {STATUS_SHIPPED, STATUS_DONE}:
        raise BizException(ErrorCode.CONFLICT, "订单已发货或已完成，不能删除")
    order.status = STATUS_DELETED
    _set_flags(order, _flags_of(order) + [FLAG_DELETED])
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"删除订单：{order.order_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


# --------------------------------------------------------------------------- #
# 仓库 / 物流设置
# --------------------------------------------------------------------------- #


def _ensure_fulfillment_editable(order: Order) -> None:
    if FLAG_DELETED in _flags_of(order) or order.status == STATUS_DELETED:
        raise BizException(ErrorCode.CONFLICT, "订单已删除，不能修改")
    if order.status not in {STATUS_NEW, STATUS_PENDING}:
        raise BizException(ErrorCode.CONFLICT, "当前订单状态不允许修改仓库或物流")


def set_order_warehouse(
    db: Session,
    *,
    order_id: int,
    warehouse_id: int,
    staff: Staff,
    commit: bool = True,
) -> Order:
    order = get_order_or_404(db, order_id)
    _ensure_fulfillment_editable(order)
    warehouse = db.get(Warehouse, _to_int_id(warehouse_id, "发货仓库"))
    if warehouse is None:
        raise BizException(ErrorCode.BAD_REQUEST, "发货仓库不存在")
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "发货仓库已停用")
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟仓不能作为发货仓库")
    order.warehouse_id = warehouse.id
    write_audit(
        db,
        action="SET_WAREHOUSE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"设置订单发货仓库：{warehouse.name}",
        detail={"warehouse_id": warehouse.id},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def set_order_logistics(
    db: Session,
    *,
    order_id: int,
    logistics_id: int | None = None,
    logistics_company: str | None = None,
    tracking_no: str | None = None,
    staff: Staff,
    commit: bool = True,
) -> Order:
    order = get_order_or_404(db, order_id)
    _ensure_fulfillment_editable(order)
    detail: dict[str, Any] = {}
    if logistics_id is not None:
        logistics = db.get(Logistics, _to_int_id(logistics_id, "物流公司"))
        if logistics is None:
            raise BizException(ErrorCode.BAD_REQUEST, "物流公司不存在")
        if not logistics.is_enabled:
            raise BizException(ErrorCode.CONFLICT, "物流公司已停用")
        order.logistics_id = logistics.id
        order.logistics_company = logistics.name
        detail["logistics_id"] = logistics.id
    if logistics_company is not None:
        order.logistics_company = _optional_text(logistics_company, "物流公司", 64)
        detail["logistics_company"] = order.logistics_company
    if tracking_no is not None:
        order.tracking_no = _optional_text(tracking_no, "运单号", 64)
        detail["tracking_no"] = order.tracking_no
    if not detail:
        raise BizException(ErrorCode.BAD_REQUEST, "请至少设置物流公司或运单号")
    write_audit(
        db,
        action="SET_LOGISTICS",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"设置订单物流：{order.logistics_company or '-'}",
        detail=detail,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


def set_order_flags(
    db: Session,
    *,
    order_id: int,
    flags: list[str],
    mode: str = "SET",
    staff: Staff,
    commit: bool = True,
) -> Order:
    """维护订单标识：SET 全量覆盖，ADD 追加，REMOVE 移除。"""
    mode_value = (mode or "SET").upper()
    if mode_value not in FLAG_MODES:
        raise BizException(ErrorCode.BAD_REQUEST, "标识操作必须为 SET/ADD/REMOVE")
    order = get_order_or_404(db, order_id)
    targets = _normalize_flags(flags)
    if not targets:
        raise BizException(ErrorCode.BAD_REQUEST, "请至少指定一个订单标识")
    current = _flags_of(order)
    if mode_value == "SET":
        _set_flags(order, targets)
    elif mode_value == "ADD":
        _set_flags(order, current + targets)
    else:
        _set_flags(order, [code for code in current if code not in set(targets)])
    write_audit(
        db,
        action="SET_FLAGS",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"维护订单标识：{mode_value} {','.join(targets)}",
        detail={"mode": mode_value, "flags": targets, "before": current},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order


# --------------------------------------------------------------------------- #
# 审核判断：仓库区域匹配、库存、最优惠物流
# --------------------------------------------------------------------------- #


def _region_tokens(warehouse: Warehouse) -> list[str]:
    raw = warehouse.ship_regions or ""
    return [token.strip() for token in raw.split(",") if token.strip()]


def region_matched(
    warehouse: Warehouse,
    *,
    province: str | None,
    city: str | None,
) -> bool:
    """仓库发货区域匹配：未配置发货区域表示全区域可发，否则按省或市命中。"""
    tokens = _region_tokens(warehouse)
    if not tokens:
        return True
    if province and province in tokens:
        return True
    return bool(city and city in tokens)


def match_warehouse(
    db: Session,
    *,
    province: str | None,
    city: str | None,
) -> Warehouse | None:
    """按收货区域匹配发货仓库：区域命中优先，其次默认仓库，最后按 id 升序。"""
    best: Warehouse | None = None
    best_score = -1
    for warehouse in db.scalars(
        select(Warehouse)
        .where(Warehouse.is_enabled.is_(True), Warehouse.is_virtual.is_(False))
        .order_by(Warehouse.id)
    ):
        if not region_matched(warehouse, province=province, city=city):
            continue
        tokens = _region_tokens(warehouse)
        score = 1
        if province and province in tokens:
            score += 2
        elif city and city in tokens:
            score += 2
        if warehouse.is_default:
            score += 1
        if score > best_score:
            best = warehouse
            best_score = score
    return best


def _available_qty(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    sku_id: int | None,
) -> Decimal:
    if sku_id is not None:
        return stock_query_service.available_qty(
            db,
            warehouse_id=warehouse_id,
            product_id=product_id,
            sku_id=sku_id,
        )
    total = ZERO_QTY
    for sku in db.scalars(
        select(ProductSku).where(ProductSku.product_id == product_id)
    ):
        total += stock_query_service.available_qty(
            db,
            warehouse_id=warehouse_id,
            product_id=product_id,
            sku_id=sku.id,
        )
    return total


def stock_shortage(
    db: Session,
    *,
    order: Order,
    warehouse_id: int,
    items: list[OrderItem] | None = None,
) -> list[dict]:
    rows = items if items is not None else _order_items(db, order.id)
    shortage: list[dict] = []
    for item in rows:
        available = _available_qty(
            db,
            warehouse_id=warehouse_id,
            product_id=item.product_id,
            sku_id=item.sku_id,
        )
        required = Decimal(item.qty or 0)
        if available < required:
            product = db.get(Product, item.product_id)
            sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
            shortage.append(
                {
                    "product_id": item.product_id,
                    "product_code": product.code if product is not None else None,
                    "product_name": product.name if product is not None else None,
                    "sku_id": item.sku_id,
                    "spec_name": sku.spec_name if sku is not None else None,
                    "required_qty": required,
                    "available_qty": available,
                }
            )
    return shortage


def best_logistics(
    db: Session,
    *,
    province: str | None,
    city: str | None,
    weight: Decimal | None,
    count: int,
) -> list[dict]:
    """按运费从低到高返回命中区域规则的物流候选（无模板命中时返回空列表）。"""
    candidates: list[dict] = []
    for logistics in db.scalars(
        select(Logistics).where(Logistics.is_enabled.is_(True)).order_by(Logistics.id)
    ):
        matched = logistics_service.match_area_rule(
            db,
            logistics_id=logistics.id,
            province=province,
            city=city,
            weight=weight,
        )
        if not matched["matched"]:
            continue
        template = matched["freight_template"]
        if template is None:
            continue
        try:
            fee = logistics_service.calculate_freight(
                db,
                template_id=template["id"],
                count=count,
                weight=weight,
            )["total_fee"]
        except BizException:
            continue
        candidates.append(
            {
                "logistics_id": logistics.id,
                "logistics_name": logistics.name,
                "template_id": template["id"],
                "template_name": template["name"],
                "total_fee": fee,
            }
        )
    candidates.sort(key=lambda item: (item["total_fee"], item["logistics_id"]))
    return candidates


def _order_count(items: list[OrderItem]) -> int:
    total = sum((Decimal(item.qty or 0) for item in items), ZERO_QTY)
    if total <= ZERO_QTY:
        return 0
    return int(total.to_integral_value(rounding=ROUND_HALF_UP))


# --------------------------------------------------------------------------- #
# 审核流程
# --------------------------------------------------------------------------- #


def _approval_state(db: Session, order_id: int) -> dict:
    return approval_service.get_state(db, doc_type=DOC_TYPE, doc_id=order_id)


def _ensure_editable(db: Session, order: Order) -> None:
    if FLAG_DELETED in _flags_of(order):
        raise BizException(ErrorCode.CONFLICT, "订单已删除，不能修改")
    if order.status not in {STATUS_NEW, STATUS_CANCELLED}:
        raise BizException(ErrorCode.CONFLICT, "当前订单状态不允许修改")
    state = _approval_state(db, order.id)["state"]
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "订单已进入审批流程，不能修改")


def submit_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Order:
    order = get_order_or_404(db, order_id)
    _ensure_editable(db, order)
    if not _order_items(db, order.id):
        raise BizException(ErrorCode.BAD_REQUEST, "订单没有商品明细，不能提交审核")
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        doc_no=order.order_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    order.audit_status = (
        AUDIT_APPROVED
        if state["status"] == "APPROVED"
        else (AUDIT_REJECTED if state["status"] == "REJECTED" else AUDIT_PENDING)
    )
    _commit(db, commit)
    return order


def approve_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> tuple[Order, dict]:
    """订单审核：库存判断 → 仓库区域匹配 → 最优惠物流匹配 → 审批引擎流转。

    新订单未提交时自动提交审批流，便于财务对抓取订单直接审核。
    """
    order = get_order_or_404(db, order_id)
    if FLAG_DELETED in _flags_of(order) or order.status == STATUS_DELETED:
        raise BizException(ErrorCode.CONFLICT, "订单已删除，不能审核")
    if order.status not in {STATUS_NEW, STATUS_PENDING}:
        raise BizException(ErrorCode.CONFLICT, "当前订单状态不能审核")
    items = _order_items(db, order.id)
    if not items:
        raise BizException(ErrorCode.BAD_REQUEST, "订单没有商品明细，不能审核")

    state = _approval_state(db, order.id)["state"]
    if state["status"] == "APPROVED":
        raise BizException(ErrorCode.CONFLICT, "订单已审核通过，无需重复审核")

    checks: dict[str, Any] = {"auto_submitted": False, "auto_warehouse": False}

    if order.warehouse_id is None:
        matched = match_warehouse(
            db,
            province=order.receiver_province,
            city=order.receiver_city,
        )
        if matched is None:
            raise BizException(
                ErrorCode.CONFLICT,
                "订单未设置发货仓库，且无法按收货区域匹配仓库，请先设置仓库",
            )
        order.warehouse_id = matched.id
        checks["auto_warehouse"] = True
    warehouse = db.get(Warehouse, order.warehouse_id)
    if warehouse is None or not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "发货仓库不存在或已停用")
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟仓不能作为发货仓库")
    if not region_matched(
        warehouse,
        province=order.receiver_province,
        city=order.receiver_city,
    ):
        raise BizException(
            ErrorCode.CONFLICT,
            f"发货仓库「{warehouse.name}」不在该订单收货区域内，请先调整发货仓库",
        )
    checks["warehouse_id"] = warehouse.id
    checks["warehouse_name"] = warehouse.name

    shortage = stock_shortage(db, order=order, warehouse_id=warehouse.id, items=items)
    if shortage:
        detail = "；".join(
            f"{row['product_name']}"
            f"{'/' + row['spec_name'] if row['spec_name'] else ''} "
            f"需要 {row['required_qty']} 可用 {row['available_qty']}"
            for row in shortage
        )
        raise BizException(ErrorCode.CONFLICT, f"库存不足，订单不能审核：{detail}")
    checks["stock_checked"] = True

    auto_logistics = False
    if order.logistics_id is None:
        candidates = best_logistics(
            db,
            province=order.receiver_province,
            city=order.receiver_city,
            weight=order.weight,
            count=_order_count(items),
        )
        checks["logistics_candidates"] = candidates
        if candidates:
            order.logistics_id = candidates[0]["logistics_id"]
            order.logistics_company = candidates[0]["logistics_name"]
            auto_logistics = True
    else:
        logistics = db.get(Logistics, order.logistics_id)
        if logistics is None or not logistics.is_enabled:
            raise BizException(ErrorCode.CONFLICT, "订单物流公司不存在或已停用")
        order.logistics_company = logistics.name
    checks["auto_logistics"] = auto_logistics

    if state["status"] == "NOT_SUBMITTED":
        state = approval_service.submit(
            db,
            doc_type=DOC_TYPE,
            doc_id=order.id,
            doc_no=order.order_no,
            staff=staff,
            commit=False,
        )
        checks["auto_submitted"] = True
    state = approval_service.approve(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    if state["status"] == "APPROVED":
        order.audit_status = AUDIT_APPROVED
        order.status = STATUS_PENDING
    else:
        order.audit_status = AUDIT_PENDING
    checks["approval_status"] = state["status"]
    checks["current_step"] = state["current_step"]
    write_audit(
        db,
        action="APPROVE",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"订单审核通过：{order.order_no}",
        detail=checks,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return order, checks


def reject_order(
    db: Session,
    *,
    order_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Order:
    order = get_order_or_404(db, order_id)
    if order.status != STATUS_NEW:
        raise BizException(ErrorCode.CONFLICT, "当前订单状态不能审核")
    state = _approval_state(db, order.id)["state"]
    if state["status"] == "NOT_SUBMITTED":
        approval_service.submit(
            db,
            doc_type=DOC_TYPE,
            doc_id=order.id,
            doc_no=order.order_no,
            staff=staff,
            commit=False,
        )
    approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    order.audit_status = AUDIT_REJECTED
    order.status = STATUS_NEW
    write_audit(
        db,
        action="REJECT",
        target_type=DOC_TYPE,
        target_id=str(order.id),
        summary=f"订单审核不通过：{order.order_no}",
        reason=comment,
        actor_id=staff.id,
        actor_name=staff.name,
    )
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
) -> Order:
    order = get_order_or_404(db, order_id)
    if order.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "订单尚未审核通过，无法弃审")
    if order.status not in {STATUS_NEW, STATUS_PENDING}:
        raise BizException(ErrorCode.CONFLICT, "订单已进入后续环节，不能弃审")
    approval_service.unaudit(
        db,
        doc_type=DOC_TYPE,
        doc_id=order.id,
        staff=staff,
        reason=reason,
        step=step,
        commit=False,
    )
    order.audit_status = AUDIT_PENDING
    order.status = STATUS_NEW
    _commit(db, commit)
    return order


def list_order_logs(db: Session, *, order_id: int) -> list[dict]:
    """操作日志：业务操作留痕 + 审批留痕，按时间倒序合并。"""
    order = get_order_or_404(db, order_id)
    rows: list[dict] = []
    for entry in db.scalars(
        select(AuditLog)
        .where(
            AuditLog.target_type == DOC_TYPE,
            AuditLog.target_id == str(order.id),
        )
        .order_by(AuditLog.id.desc())
    ):
        rows.append(
            {
                "source": "AUDIT",
                "action": entry.action,
                "operator_id": entry.actor_id,
                "operator_name": entry.actor_name,
                "summary": entry.summary,
                "reason": entry.reason,
                "detail": entry.detail,
                "created_at": entry.created_at,
            }
        )
    for record in db.scalars(
        select(ApprovalRecord)
        .where(
            ApprovalRecord.doc_type == DOC_TYPE,
            ApprovalRecord.doc_id == order.id,
        )
        .order_by(ApprovalRecord.id.desc())
    ):
        operator = db.get(Staff, record.approver_id) if record.approver_id else None
        rows.append(
            {
                "source": "APPROVAL",
                "action": record.action,
                "operator_id": record.approver_id,
                "operator_name": operator.name if operator is not None else None,
                "summary": f"审批第 {record.step} 环节：{record.status}",
                "reason": record.reason or record.comment,
                "detail": None,
                "created_at": record.audit_time or record.created_at,
            }
        )
    rows.sort(key=lambda item: item["created_at"] or datetime.min, reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Excel 导入导出
# --------------------------------------------------------------------------- #


def export_orders(
    db: Session,
    *,
    shop_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> bytes:
    listed = list_orders(
        db,
        shop_id=shop_id,
        status=status,
        audit_status=audit_status,
        keyword=keyword,
        start_time=start_time,
        end_time=end_time,
        page=1,
        page_size=200,
    )
    return export_data(
        ORDER_EXPORT_TEMPLATE,
        [
            {
                "order_no": item["order_no"],
                "shop_name": item["shop_name"],
                "status_label": item["status_label"],
                "audit_status": item["audit_status"],
                "flags_label": item["flags_label"],
                "buyer_nick": item["buyer_nick"],
                "receiver_name": item["receiver_name"],
                "receiver_phone": item["receiver_phone"],
                "receiver_region": item["receiver_region"],
                "warehouse_name": item["warehouse_name"],
                "logistics_company": item["logistics_company"],
                "tracking_no": item["tracking_no"],
                "total_qty": item["total_qty"],
                "order_amount": item["order_amount"],
                "paid_amount": item["paid_amount"],
                "pay_status": item["pay_status"],
                "order_time": item["order_time"],
                "pay_time": item["pay_time"],
            }
            for item in listed["items"]
        ],
    )


def _lookup_import_rows(
    db: Session,
    parsed: ParseResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors = list(parsed.errors)
    platform_codes = {
        str(row.get("platform_code") or "").strip() for row in parsed.rows
    }
    shop_names = {str(row.get("shop_name") or "").strip() for row in parsed.rows}
    product_codes = {
        str(row.get("product_code") or "").strip() for row in parsed.rows
    }
    spec_codes = {str(row.get("sku_code") or "").strip() for row in parsed.rows}
    warehouse_codes = {
        str(row.get("warehouse_code") or "").strip() for row in parsed.rows
    }
    logistics_names = {
        str(row.get("logistics_name") or "").strip() for row in parsed.rows
    }

    platforms = {
        row.code: row
        for row in db.scalars(select(Platform).where(Platform.code.in_(platform_codes)))
    }
    shops = {
        (platform.code, row.name): row
        for platform in platforms.values()
        for row in db.scalars(select(Shop).where(Shop.platform_id == platform.id))
    }
    products = {
        row.code: row
        for row in db.scalars(select(Product).where(Product.code.in_(product_codes)))
    }
    skus = {
        row.spec_code: row
        for row in db.scalars(
            select(ProductSku).where(ProductSku.spec_code.in_(spec_codes))
        )
    }
    warehouses = {
        row.code: row
        for row in db.scalars(
            select(Warehouse).where(Warehouse.code.in_(warehouse_codes))
        )
    }
    logistics_rows = {
        row.name: row
        for row in db.scalars(
            select(Logistics).where(Logistics.name.in_(logistics_names))
        )
    }

    grouped: dict[str, dict[str, Any]] = {}
    for index, row in enumerate(parsed.rows, start=2):
        order_no = str(row.get("order_no") or "").strip()
        platform_code = str(row.get("platform_code") or "").strip()
        shop_name = str(row.get("shop_name") or "").strip()
        product_code = str(row.get("product_code") or "").strip()
        spec_code = str(row.get("sku_code") or "").strip()
        warehouse_code = str(row.get("warehouse_code") or "").strip()
        logistics_name = str(row.get("logistics_name") or "").strip()

        platform = platforms.get(platform_code)
        if platform is None:
            errors.append(
                {
                    "row": index,
                    "field": "platform_code",
                    "message": f"平台编码不存在：{platform_code}",
                }
            )
            continue
        shop = shops.get((platform_code, shop_name))
        if shop is None:
            errors.append(
                {
                    "row": index,
                    "field": "shop_name",
                    "message": f"平台 {platform_code} 下不存在店铺：{shop_name}",
                }
            )
            continue
        product = products.get(product_code)
        if product is None:
            errors.append(
                {
                    "row": index,
                    "field": "product_code",
                    "message": f"商品编码不存在：{product_code}",
                }
            )
            continue
        sku: ProductSku | None = None
        if spec_code:
            sku = skus.get(spec_code)
            if sku is None or sku.product_id != product.id:
                errors.append(
                    {
                        "row": index,
                        "field": "sku_code",
                        "message": f"规格编码不存在或不属于该商品：{spec_code}",
                    }
                )
                continue
        warehouse = warehouses.get(warehouse_code) if warehouse_code else None
        if warehouse_code and warehouse is None:
            errors.append(
                {
                    "row": index,
                    "field": "warehouse_code",
                    "message": f"仓库编码不存在：{warehouse_code}",
                }
            )
            continue
        logistics = logistics_rows.get(logistics_name) if logistics_name else None
        if logistics_name and logistics is None:
            errors.append(
                {
                    "row": index,
                    "field": "logistics_name",
                    "message": f"物流公司不存在：{logistics_name}",
                }
            )
            continue
        try:
            qty = _to_qty(row.get("qty"), "数量")
            price = _to_money(row.get("price"), "单价")
            is_gift = _to_bool(row.get("is_gift") or False, "是否赠品")
        except BizException as exc:
            errors.append({"row": index, "field": "qty", "message": exc.message})
            continue

        group = grouped.get(order_no)
        if group is None:
            try:
                header = {
                    "shop_id": shop.id,
                    "warehouse_id": warehouse.id if warehouse is not None else None,
                    "logistics_id": logistics.id if logistics is not None else None,
                    "buyer_nick": row.get("buyer_nick"),
                    "buyer_message": row.get("buyer_message"),
                    "seller_remark": row.get("seller_remark"),
                    "receiver_name": row.get("receiver_name"),
                    "receiver_phone": row.get("receiver_phone"),
                    "receiver_province": row.get("receiver_province"),
                    "receiver_city": row.get("receiver_city"),
                    "receiver_address": row.get("receiver_address"),
                    "order_time": row.get("order_time"),
                    "pay_time": row.get("pay_time"),
                    "pay_status": row.get("pay_status"),
                    "freight": row.get("freight"),
                    "platform_discount": row.get("platform_discount"),
                    "seller_discount": row.get("seller_discount"),
                }
                header = _normalize_header(
                    db, header, default_order_no=order_no
                )
            except BizException as exc:
                errors.append({"row": index, "field": "", "message": exc.message})
                continue
            group = {"order_no": order_no, "header": header, "items": []}
            grouped[order_no] = group
        group["items"].append(
            {
                "product_id": product.id,
                "sku_id": sku.id if sku is not None else None,
                "sku_name": sku.spec_name if sku is not None else None,
                "qty": qty,
                "price": price,
                "amount": round_money(qty * price),
                "is_gift": is_gift,
            }
        )
    return list(grouped.values()), errors


def import_orders(
    db: Session,
    *,
    file_bytes: bytes,
    staff: Staff,
    commit: bool = True,
) -> dict:
    parsed = parse_data(file_bytes, ORDER_IMPORT_TEMPLATE)
    groups, errors = _lookup_import_rows(db, parsed)
    if errors:
        return {"created": 0, "updated": 0, "errors": errors}
    if not groups:
        return {
            "created": 0,
            "updated": 0,
            "errors": [{"row": 0, "field": "", "message": "没有可导入的数据"}],
        }
    created = 0
    updated = 0
    for group in groups:
        _, is_created = _upsert_order(
            db,
            header=group["header"],
            items=group["items"],
            staff=staff,
            source="IMPORT",
        )
        if is_created:
            created += 1
        else:
            updated += 1
    _commit(db, commit)
    return {"created": created, "updated": updated, "errors": []}
