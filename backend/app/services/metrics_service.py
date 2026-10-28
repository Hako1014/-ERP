"""报表与看板聚合服务（TASK-047）。

指标口径：
- 销售单：已付款、未取消/未删除的订单，订单日期取付款时间，缺失时取下单时间；
- 销售商品：在销售单范围内排除赠品明细；
- 业务日：按 ``settings.timezone`` 切分后换算为 UTC 无时区时间查询；
- 库存预警：复用库存查询服务的现有预警口径；
- 采购在途：已审核且状态为 APPROVED/RECEIVING 的采购订单未到货数量。

本模块是首页与采购报表的唯一口径入口，接口层不重复编写过滤条件。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.data_scope import scope_conditions
from app.core.query_access import authorize_query

from app.core.config import get_settings
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.models.basic import Brand, Category
from app.models.org import Staff
from app.models.order import Order, OrderItem
from app.models.party import Customer, Supplier
from app.models.platform import Shop
from app.models.product import Product, ProductSku
from app.models.purchase import PurchaseInvoice, PurchaseOrder, PurchaseOrderItem
from app.models.stock import StockIn, StockInItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import stock_query_service

TrendPeriod = Literal["day", "week", "month"]

PAID = "PAID"
SALES_EXCLUDED_STATUSES = ("CANCELLED", "CANCELED", "DELETED")
PURCHASE_IN_TRANSIT_STATUSES = ("APPROVED", "RECEIVING")
STOCK_IN_APPROVED = "APPROVED"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
ZERO_MONEY = Decimal("0.00")
PERCENT_QUANT = Decimal("0.01")

TREND_LIMITS: dict[TrendPeriod, int] = {
    "day": 30,
    "week": 12,
    "month": 12,
}

ORDER_STATUS_LABELS = {
    "NEW": "新订单",
    "PENDING": "待处理",
    "SHIPPED": "已发货",
    "DONE": "已完成",
    "CANCELLED": "已取消",
    "CANCELED": "已取消",
    "DELETED": "已删除",
}


def _timezone() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def _business_today(as_of: date | None = None) -> date:
    if as_of is not None:
        return as_of
    return datetime.now(_timezone()).date()


def _to_utc_naive(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _local_day_bounds(day: date) -> tuple[datetime, datetime]:
    tz = _timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = start + timedelta(days=1)
    return _to_utc_naive(start), _to_utc_naive(end)


def _local_date_range_bounds(
    start_date: date,
    end_date: date,
) -> tuple[datetime, datetime]:
    if end_date < start_date:
        raise BizException(ErrorCode.BAD_REQUEST, "结束日期不能早于开始日期")
    start, _ = _local_day_bounds(start_date)
    _, end = _local_day_bounds(end_date)
    return start, end


def _effective_order_time():
    return func.coalesce(Order.pay_time, Order.order_time)


def _effective_order_time_in_range(start: datetime, end: datetime):
    """Keep the date predicate sargable while preserving COALESCE semantics."""
    return or_(
        and_(Order.pay_time >= start, Order.pay_time < end),
        and_(
            Order.pay_time.is_(None),
            Order.order_time >= start,
            Order.order_time < end,
        ),
    )


def _sales_conditions() -> list[Any]:
    return [
        Order.pay_status == PAID,
        Order.status.not_in(SALES_EXCLUDED_STATUSES),
    ]


def _change_rate(current: Decimal, previous: Decimal) -> Decimal | None:
    if previous == ZERO_MONEY:
        return None
    return ((current - previous) / previous * Decimal("100")).quantize(
        PERCENT_QUANT,
        rounding=ROUND_HALF_UP,
    )


def _order_period_stats(
    db: Session,
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Decimal | int]:
    start, end = _local_date_range_bounds(start_date, end_date)
    row = db.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(Order.order_amount), 0),
        ).where(
            *_sales_conditions(),
            _effective_order_time_in_range(start, end),
        )
    ).one()
    return {
        "amount": round_money(row[1] or 0),
        "count": int(row[0] or 0),
    }


def _customer_period_count(
    db: Session,
    *,
    start_date: date,
    end_date: date,
) -> int:
    start, end = _local_date_range_bounds(start_date, end_date)
    return int(
        db.scalar(
            select(func.count(Customer.id)).where(
                Customer.created_at >= start,
                Customer.created_at < end,
            )
        )
        or 0
    )


def _metric(
    *,
    key: str,
    label: str,
    value: Decimal | int,
    previous_value: Decimal | int | None,
    unit: str,
) -> dict[str, Any]:
    change_rate = None
    if previous_value is not None:
        change_rate = _change_rate(
            Decimal(value),
            Decimal(previous_value),
        )
    return {
        "key": key,
        "label": label,
        "value": value,
        "previous_value": previous_value,
        "change_rate": change_rate,
        "unit": unit,
    }


def _period_start(day: date, period: TrendPeriod) -> date:
    if period == "week":
        return day - timedelta(days=day.weekday())
    if period == "month":
        return day.replace(day=1)
    return day


def _shift_month(day: date, months: int) -> date:
    month_index = day.year * 12 + (day.month - 1) + months
    year, month_zero = divmod(month_index, 12)
    return date(year, month_zero + 1, 1)


def _shift_period(day: date, period: TrendPeriod, amount: int) -> date:
    if period == "day":
        return day + timedelta(days=amount)
    if period == "week":
        return day + timedelta(weeks=amount)
    return _shift_month(day, amount)


def _period_key(day: date, period: TrendPeriod) -> str:
    if period == "week":
        iso_year, iso_week, _ = day.isocalendar()
        return f"{iso_year}-W{iso_week:02d}"
    if period == "month":
        return day.strftime("%Y-%m")
    return day.isoformat()


def _trend_ranges(
    period: TrendPeriod,
    as_of: date,
) -> list[tuple[str, date, date]]:
    count = TREND_LIMITS[period]
    current_start = _period_start(as_of, period)
    first_start = _shift_period(current_start, period, -(count - 1))
    ranges: list[tuple[str, date, date]] = []
    for index in range(count):
        start = _shift_period(first_start, period, index)
        end = _shift_period(start, period, 1) - timedelta(days=1)
        ranges.append((_period_key(start, period), start, end))
    return ranges


def sales_trend(
    db: Session,
    *,
    period: TrendPeriod = "day",
    as_of: date | None = None,
) -> dict[str, Any]:
    """返回指定粒度的近端销售趋势，空周期补零。"""
    if period not in TREND_LIMITS:
        raise BizException(ErrorCode.BAD_REQUEST, "销售趋势粒度仅支持日/周/月")
    current_day = _business_today(as_of)
    ranges = _trend_ranges(period, current_day)
    last_end = min(ranges[-1][2], current_day)
    start, end = _local_date_range_bounds(ranges[0][1], last_end)
    effective_time = _effective_order_time()
    aggregate_columns = []
    for index, (_, bucket_start, bucket_end) in enumerate(ranges):
        bounded_end = min(bucket_end, current_day)
        bucket_start_utc, bucket_end_utc = _local_date_range_bounds(
            bucket_start, bounded_end,
        )
        in_bucket = (
            (effective_time >= bucket_start_utc)
            & (effective_time < bucket_end_utc)
        )
        aggregate_columns.extend((
            func.coalesce(
                func.sum(case((in_bucket, Order.order_amount), else_=0)), 0,
            ).label(f"amount_{index}"),
            func.coalesce(
                func.sum(case((in_bucket, 1), else_=0)), 0,
            ).label(f"count_{index}"),
        ))
    aggregate = db.execute(
        select(*aggregate_columns).where(
            *_sales_conditions(),
            _effective_order_time_in_range(start, end),
        )
    ).one()
    bucket_values = {
        key: {
            "amount": round_money(Decimal(aggregate[index * 2] or 0)),
            "order_count": int(aggregate[index * 2 + 1] or 0),
        }
        for index, (key, _, _) in enumerate(ranges)
    }
    return {
        "period": period,
        "start_date": ranges[0][1],
        "end_date": ranges[-1][2],
        "items": [
            {
                "period": key,
                "start_date": start_date,
                "end_date": end_date,
                "amount": bucket_values[key]["amount"],
                "order_count": bucket_values[key]["order_count"],
            }
            for key, start_date, end_date in ranges
        ],
    }


def _sales_by_dimension(
    db: Session,
    *,
    dimension: Literal["category", "brand"],
    start_date: date,
    end_date: date,
) -> list[dict[str, Any]]:
    if dimension == "category":
        dimension_id = Product.category_id
        dimension_name = Category.name
        name_model = Category
        null_label = "未分类"
    else:
        dimension_id = Product.brand_id
        dimension_name = Brand.name
        name_model = Brand
        null_label = "未设置品牌"

    start, end = _local_date_range_bounds(start_date, end_date)
    effective_time = _effective_order_time()
    amount_expr = func.coalesce(
        OrderItem.amount,
        OrderItem.qty * OrderItem.price,
    )
    rows = list(
        db.execute(
            select(
                dimension_id,
                dimension_name,
                func.coalesce(func.sum(OrderItem.qty), 0),
                func.coalesce(func.sum(amount_expr), 0),
            )
            .select_from(OrderItem)
            .join(Order, Order.id == OrderItem.order_id)
            .join(Product, Product.id == OrderItem.product_id)
            .outerjoin(name_model, name_model.id == dimension_id)
            .where(
                *_sales_conditions(),
                OrderItem.is_gift.is_(False),
                effective_time >= start,
                effective_time < end,
            )
            .group_by(dimension_id, dimension_name)
        )
    )
    total_amount = sum(
        (round_money(row[3] or 0) for row in rows),
        ZERO_MONEY,
    )
    result: list[dict[str, Any]] = []
    for item_id, item_name, qty, amount in rows:
        money = round_money(amount or 0)
        ratio = (
            (money / total_amount * Decimal("100")).quantize(
                PERCENT_QUANT,
                rounding=ROUND_HALF_UP,
            )
            if total_amount
            else ZERO_MONEY
        )
        result.append(
            {
                "id": item_id,
                "name": item_name or null_label,
                "qty": Decimal(qty or 0).quantize(QTY_QUANT),
                "amount": money,
                "ratio": ratio,
            }
        )
    result.sort(key=lambda item: (item["amount"], item["qty"]), reverse=True)
    return result


def recent_orders(db: Session, *, limit: int = 8) -> list[dict[str, Any]]:
    limit = min(max(limit, 1), 50)
    rows = list(
        db.scalars(
            select(Order)
            .order_by(
                func.coalesce(Order.order_time, Order.created_at).desc(),
                Order.id.desc(),
            )
            .limit(limit)
        )
    )
    result: list[dict[str, Any]] = []
    for order in rows:
        shop = db.get(Shop, order.shop_id)
        result.append(
            {
                "id": order.id,
                "order_no": order.order_no,
                "customer": order.buyer_nick or order.receiver_name,
                "shop_id": order.shop_id,
                "shop_name": shop.name if shop is not None else None,
                "amount": round_money(order.order_amount or 0),
                "status": order.status,
                "status_label": ORDER_STATUS_LABELS.get(
                    order.status,
                    order.status,
                ),
                "order_time": order.order_time,
                "created_at": order.created_at,
            }
        )
    return result


def _hot_product_sales(
    db: Session,
    *,
    start_date: date,
    end_date: date,
) -> dict[int, dict[str, Decimal]]:
    start, end = _local_date_range_bounds(start_date, end_date)
    effective_time = _effective_order_time()
    amount_expr = func.coalesce(
        OrderItem.amount,
        OrderItem.qty * OrderItem.price,
    )
    rows = db.execute(
        select(
            OrderItem.product_id,
            func.coalesce(func.sum(OrderItem.qty), 0),
            func.coalesce(func.sum(amount_expr), 0),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(
            *_sales_conditions(),
            OrderItem.is_gift.is_(False),
            effective_time >= start,
            effective_time < end,
        )
        .group_by(OrderItem.product_id)
    )
    return {
        int(product_id): {
            "qty": Decimal(qty or 0).quantize(QTY_QUANT),
            "amount": round_money(amount or 0),
        }
        for product_id, qty, amount in rows
    }


def hot_products(
    db: Session,
    *,
    as_of: date | None = None,
    days: int = 30,
    limit: int = 10,
) -> list[dict[str, Any]]:
    if days < 1:
        raise BizException(ErrorCode.BAD_REQUEST, "统计天数必须大于 0")
    limit = min(max(limit, 1), 50)
    end_date = _business_today(as_of)
    start_date = end_date - timedelta(days=days - 1)
    previous_end = start_date - timedelta(days=1)
    previous_start = previous_end - timedelta(days=days - 1)
    current = _hot_product_sales(
        db,
        start_date=start_date,
        end_date=end_date,
    )
    previous = _hot_product_sales(
        db,
        start_date=previous_start,
        end_date=previous_end,
    )
    ranked = sorted(
        current,
        key=lambda product_id: (
            current[product_id]["qty"],
            current[product_id]["amount"],
        ),
        reverse=True,
    )[:limit]
    result: list[dict[str, Any]] = []
    for product_id in ranked:
        product = db.get(Product, product_id)
        if product is None:
            continue
        current_row = current[product_id]
        previous_qty = previous.get(product_id, {}).get("qty", ZERO_QTY)
        average_price = (
            round_money(current_row["amount"] / current_row["qty"])
            if current_row["qty"] > ZERO_QTY
            else ZERO_MONEY
        )
        price = (
            round_money(product.online_price)
            if product.online_price is not None
            else average_price
        )
        change_rate = _change_rate(current_row["qty"], previous_qty)
        result.append(
            {
                "product_id": product.id,
                "product_code": product.code,
                "product_name": product.name,
                "sales_qty": current_row["qty"],
                "price": price,
                "previous_sales_qty": previous_qty,
                "change_rate": change_rate,
            }
        )
    return result


def inventory_warning_count(db: Session) -> int:
    warning_skus = (
        select(ProductSku.id)
        .join(Product, Product.id == ProductSku.product_id)
        .outerjoin(StockLocation, StockLocation.product_sku_id == ProductSku.id)
        .group_by(ProductSku.id, Product.warn_stock)
        .having(
            func.coalesce(func.sum(StockLocation.qty), 0)
            < func.coalesce(Product.warn_stock, 0)
        )
        .subquery()
    )
    return int(db.scalar(select(func.count()).select_from(warning_skus)) or 0)


def dashboard_overview(
    db: Session,
    *,
    trend_period: TrendPeriod = "day",
    as_of: date | None = None,
) -> dict[str, Any]:
    """一次性返回首页指标卡、图表和列表所需聚合数据。"""
    current_day = _business_today(as_of)
    previous_day = current_day - timedelta(days=1)
    today = _order_period_stats(
        db,
        start_date=current_day,
        end_date=current_day,
    )
    yesterday = _order_period_stats(
        db,
        start_date=previous_day,
        end_date=previous_day,
    )
    customers_today = _customer_period_count(
        db,
        start_date=current_day,
        end_date=current_day,
    )
    customers_yesterday = _customer_period_count(
        db,
        start_date=previous_day,
        end_date=previous_day,
    )
    return {
        "as_of": current_day,
        "timezone": get_settings().timezone,
        "metrics": [
            _metric(
                key="today_order_amount",
                label="今日订单金额",
                value=today["amount"],
                previous_value=yesterday["amount"],
                unit="CNY",
            ),
            _metric(
                key="today_order_count",
                label="订单数量",
                value=today["count"],
                previous_value=yesterday["count"],
                unit="ORDER",
            ),
            _metric(
                key="stock_warning_count",
                label="库存预警",
                value=inventory_warning_count(db),
                previous_value=None,
                unit="SKU",
            ),
            _metric(
                key="customer_growth",
                label="客户增长",
                value=customers_today,
                previous_value=customers_yesterday,
                unit="CUSTOMER",
            ),
        ],
        "sales_trend": sales_trend(
            db,
            period=trend_period,
            as_of=current_day,
        ),
        "category_sales": _sales_by_dimension(
            db,
            dimension="category",
            start_date=current_day.replace(day=1),
            end_date=current_day,
        ),
        "brand_sales": _sales_by_dimension(
            db,
            dimension="brand",
            start_date=current_day.replace(day=1),
            end_date=current_day,
        ),
        "recent_orders": recent_orders(db),
        "hot_products": hot_products(db, as_of=current_day),
    }


def _normalize_report_time(value: datetime | date | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.min)
    return _to_utc_naive(value)


def _append_time_conditions(
    conditions: list[Any],
    column: Any,
    start_time: datetime | date | None,
    end_time: datetime | date | None,
) -> None:
    start = _normalize_report_time(start_time)
    end = _normalize_report_time(end_time)
    if start is not None:
        conditions.append(column >= start)
    if end is not None:
        conditions.append(column <= end)


def purchase_order_report(
    db: Session,
    *,
    start_time: datetime | date | None = None,
    end_time: datetime | date | None = None,
    supplier_id: int | None = None,
) -> dict[str, Any]:
    """采购订单按供应商汇总。"""
    authorize_query(db, "purchase_report")
    conditions: list[Any] = scope_conditions(db, PurchaseOrder.created_by)
    _append_time_conditions(
        conditions,
        PurchaseOrder.created_at,
        start_time,
        end_time,
    )
    if supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == supplier_id)
    rows = list(
        db.execute(
            select(
                PurchaseOrder.supplier_id,
                Supplier.name,
                func.count(PurchaseOrder.id),
                func.coalesce(func.sum(PurchaseOrder.total_qty), 0),
                func.coalesce(func.sum(PurchaseOrder.total_amount), 0),
                func.coalesce(func.sum(PurchaseOrder.arrived_qty), 0),
                func.coalesce(
                    func.sum(
                        PurchaseOrder.total_qty - PurchaseOrder.arrived_qty
                    ),
                    0,
                ),
            )
            .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .where(*conditions)
            .group_by(PurchaseOrder.supplier_id, Supplier.name)
            .order_by(PurchaseOrder.supplier_id)
        )
    )
    items = [
        {
            "supplier_id": row[0],
            "supplier_name": row[1],
            "order_count": int(row[2] or 0),
            "total_qty": Decimal(row[3] or 0).quantize(QTY_QUANT),
            "total_amount": round_money(row[4] or 0),
            "arrived_qty": Decimal(row[5] or 0).quantize(QTY_QUANT),
            "in_transit_qty": Decimal(row[6] or 0).quantize(QTY_QUANT),
        }
        for row in rows
    ]
    return {
        "items": items,
        "summary": {
            "supplier_count": len(items),
            "order_count": sum(item["order_count"] for item in items),
            "total_qty": sum(
                (item["total_qty"] for item in items),
                ZERO_QTY,
            ),
            "total_amount": sum(
                (item["total_amount"] for item in items),
                ZERO_MONEY,
            ),
            "arrived_qty": sum(
                (item["arrived_qty"] for item in items),
                ZERO_QTY,
            ),
            "in_transit_qty": sum(
                (item["in_transit_qty"] for item in items),
                ZERO_QTY,
            ),
        },
    }


def purchase_inbound_report(
    db: Session,
    *,
    start_time: datetime | date | None = None,
    end_time: datetime | date | None = None,
    supplier_id: int | None = None,
    purchaser_id: int | None = None,
    product_id: int | None = None,
) -> dict[str, Any]:
    """采购入库按供应商、采购员、商品规格汇总，仅统计审核通过单。"""
    authorize_query(db, "purchase_report")
    conditions: list[Any] = [
        *scope_conditions(db, StockIn.created_by),
        *scope_conditions(db, PurchaseOrder.created_by),
        StockIn.stock_in_type == "PURCHASE",
        StockIn.audit_status == STOCK_IN_APPROVED,
    ]
    _append_time_conditions(
        conditions,
        StockIn.created_at,
        start_time,
        end_time,
    )
    if supplier_id is not None:
        conditions.append(PurchaseOrder.supplier_id == supplier_id)
    if purchaser_id is not None:
        conditions.append(PurchaseOrder.purchaser_id == purchaser_id)
    if product_id is not None:
        conditions.append(StockInItem.product_id == product_id)
    amount_expr = func.coalesce(
        StockInItem.amount,
        StockInItem.qty * func.coalesce(StockInItem.cost_price, 0),
    )
    rows = list(
        db.execute(
            select(
                PurchaseOrder.supplier_id,
                Supplier.name,
                PurchaseOrder.purchaser_id,
                Staff.name,
                StockInItem.product_id,
                Product.code,
                Product.name,
                StockInItem.sku_id,
                ProductSku.spec_code,
                ProductSku.spec_name,
                func.count(func.distinct(StockIn.id)),
                func.coalesce(func.sum(StockInItem.qty), 0),
                func.coalesce(func.sum(amount_expr), 0),
            )
            .select_from(StockInItem)
            .join(StockIn, StockIn.id == StockInItem.stock_in_id)
            .join(PurchaseOrder, PurchaseOrder.id == StockIn.purchase_order_id)
            .join(Supplier, Supplier.id == PurchaseOrder.supplier_id)
            .outerjoin(Staff, Staff.id == PurchaseOrder.purchaser_id)
            .join(Product, Product.id == StockInItem.product_id)
            .outerjoin(ProductSku, ProductSku.id == StockInItem.sku_id)
            .where(*conditions)
            .group_by(
                PurchaseOrder.supplier_id,
                Supplier.name,
                PurchaseOrder.purchaser_id,
                Staff.name,
                StockInItem.product_id,
                Product.code,
                Product.name,
                StockInItem.sku_id,
                ProductSku.spec_code,
                ProductSku.spec_name,
            )
            .order_by(
                PurchaseOrder.supplier_id,
                PurchaseOrder.purchaser_id,
                StockInItem.product_id,
                StockInItem.sku_id,
            )
        )
    )
    items = [
        {
            "supplier_id": row[0],
            "supplier_name": row[1],
            "purchaser_id": row[2],
            "purchaser_name": row[3],
            "product_id": row[4],
            "product_code": row[5],
            "product_name": row[6],
            "sku_id": row[7],
            "spec_code": row[8],
            "spec_name": row[9],
            "stock_in_count": int(row[10] or 0),
            "qty": Decimal(row[11] or 0).quantize(QTY_QUANT),
            "amount": round_money(row[12] or 0),
        }
        for row in rows
    ]
    stock_in_count = db.scalar(
        select(func.count(func.distinct(StockIn.id)))
        .select_from(StockInItem)
        .join(StockIn, StockIn.id == StockInItem.stock_in_id)
        .join(PurchaseOrder, PurchaseOrder.id == StockIn.purchase_order_id)
        .outerjoin(Staff, Staff.id == PurchaseOrder.purchaser_id)
        .join(Product, Product.id == StockInItem.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockInItem.sku_id)
        .where(*conditions)
    )
    return {
        "items": items,
        "summary": {
            "stock_in_count": int(stock_in_count or 0),
            "qty": sum((item["qty"] for item in items), ZERO_QTY),
            "amount": sum(
                (item["amount"] for item in items),
                ZERO_MONEY,
            ),
        },
    }


def purchase_invoice_report(
    db: Session,
    *,
    start_time: datetime | date | None = None,
    end_time: datetime | date | None = None,
    supplier_id: int | None = None,
    write_off_status: str | None = None,
) -> dict[str, Any]:
    """采购发票按供应商和核销状态汇总。"""
    authorize_query(db, "purchase_invoice_report")
    conditions: list[Any] = scope_conditions(db, PurchaseInvoice.created_by)
    _append_time_conditions(
        conditions,
        PurchaseInvoice.created_at,
        start_time,
        end_time,
    )
    if supplier_id is not None:
        conditions.append(PurchaseInvoice.supplier_id == supplier_id)
    if write_off_status:
        conditions.append(
            PurchaseInvoice.write_off_status == write_off_status.upper()
        )
    net_expr = PurchaseInvoice.amount - func.coalesce(
        PurchaseInvoice.tax_amount,
        0,
    )
    rows = list(
        db.execute(
            select(
                PurchaseInvoice.supplier_id,
                Supplier.name,
                PurchaseInvoice.write_off_status,
                func.count(PurchaseInvoice.id),
                func.coalesce(func.sum(PurchaseInvoice.amount), 0),
                func.coalesce(func.sum(PurchaseInvoice.tax_amount), 0),
                func.coalesce(func.sum(net_expr), 0),
                func.coalesce(func.sum(PurchaseInvoice.write_off_amount), 0),
            )
            .join(Supplier, Supplier.id == PurchaseInvoice.supplier_id)
            .where(*conditions)
            .group_by(
                PurchaseInvoice.supplier_id,
                Supplier.name,
                PurchaseInvoice.write_off_status,
            )
            .order_by(
                PurchaseInvoice.supplier_id,
                PurchaseInvoice.write_off_status,
            )
        )
    )
    items = [
        {
            "supplier_id": row[0],
            "supplier_name": row[1],
            "write_off_status": row[2],
            "invoice_count": int(row[3] or 0),
            "amount": round_money(row[4] or 0),
            "tax_amount": round_money(row[5] or 0),
            "net_amount": round_money(row[6] or 0),
            "write_off_amount": round_money(row[7] or 0),
        }
        for row in rows
    ]
    return {
        "items": items,
        "summary": {
            "invoice_count": sum(
                item["invoice_count"] for item in items
            ),
            "amount": sum(
                (item["amount"] for item in items),
                ZERO_MONEY,
            ),
            "tax_amount": sum(
                (item["tax_amount"] for item in items),
                ZERO_MONEY,
            ),
            "net_amount": sum(
                (item["net_amount"] for item in items),
                ZERO_MONEY,
            ),
            "write_off_amount": sum(
                (item["write_off_amount"] for item in items),
                ZERO_MONEY,
            ),
        },
    }


def inventory_in_transit_report(
    db: Session,
    *,
    warehouse_id: int | None = None,
    supplier_id: int | None = None,
    product_id: int | None = None,
    keyword: str | None = None,
) -> dict[str, Any]:
    """库存查询在途口径：有效仓库良品库存 + 已审核采购未到货数量。"""
    authorize_query(db, "inventory_report")
    stock_conditions: list[Any] = [
        StockLocation.status == "NORMAL",
        Warehouse.is_virtual.is_(False),
        Warehouse.is_enabled.is_(True),
    ]
    if warehouse_id is not None:
        stock_conditions.append(Location.warehouse_id == warehouse_id)
    if product_id is not None:
        stock_conditions.append(ProductSku.product_id == product_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        stock_conditions.append(
            or_(
                Product.code.like(like),
                Product.name.like(like),
                ProductSku.spec_code.like(like),
                ProductSku.spec_name.like(like),
            )
        )
    stock_rows = list(
        db.execute(
            select(
                ProductSku.product_id,
                StockLocation.product_sku_id,
                func.coalesce(func.sum(StockLocation.qty), 0),
            )
            .select_from(StockLocation)
            .join(Location, Location.id == StockLocation.location_id)
            .join(Warehouse, Warehouse.id == Location.warehouse_id)
            .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
            .join(Product, Product.id == ProductSku.product_id)
            .where(*stock_conditions)
            .group_by(ProductSku.product_id, StockLocation.product_sku_id)
        )
    )

    transit_conditions: list[Any] = [
        *scope_conditions(db, PurchaseOrder.created_by),
        PurchaseOrder.audit_status == "APPROVED",
        PurchaseOrder.status.in_(PURCHASE_IN_TRANSIT_STATUSES),
        PurchaseOrderItem.qty > PurchaseOrderItem.arrived_qty,
    ]
    if warehouse_id is not None:
        transit_conditions.append(PurchaseOrder.warehouse_id == warehouse_id)
    if supplier_id is not None:
        transit_conditions.append(PurchaseOrder.supplier_id == supplier_id)
    if product_id is not None:
        transit_conditions.append(PurchaseOrderItem.product_id == product_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        transit_conditions.append(
            or_(
                Product.code.like(like),
                Product.name.like(like),
                ProductSku.spec_code.like(like),
                ProductSku.spec_name.like(like),
            )
        )
    transit_rows = list(
        db.execute(
            select(
                PurchaseOrderItem.product_id,
                PurchaseOrderItem.sku_id,
                func.coalesce(
                    func.sum(
                        PurchaseOrderItem.qty - PurchaseOrderItem.arrived_qty
                    ),
                    0,
                ),
            )
            .select_from(PurchaseOrderItem)
            .join(
                PurchaseOrder,
                PurchaseOrder.id == PurchaseOrderItem.order_id,
            )
            .join(Product, Product.id == PurchaseOrderItem.product_id)
            .outerjoin(ProductSku, ProductSku.id == PurchaseOrderItem.sku_id)
            .where(*transit_conditions)
            .group_by(
                PurchaseOrderItem.product_id,
                PurchaseOrderItem.sku_id,
            )
        )
    )

    stock_map: dict[tuple[int, int | None], Decimal] = {
        (int(pid), sku_id): Decimal(qty or 0).quantize(QTY_QUANT)
        for pid, sku_id, qty in stock_rows
    }
    transit_map: dict[tuple[int, int | None], Decimal] = {
        (int(pid), sku_id): Decimal(qty or 0).quantize(QTY_QUANT)
        for pid, sku_id, qty in transit_rows
    }
    keys = sorted(
        set(stock_map) | set(transit_map),
        key=lambda key: (key[0], -1 if key[1] is None else key[1]),
    )
    items: list[dict[str, Any]] = []
    for pid, sku_id in keys:
        product = db.get(Product, pid)
        if product is None:
            continue
        sku = db.get(ProductSku, sku_id) if sku_id is not None else None
        current_qty = stock_map.get((pid, sku_id), ZERO_QTY)
        in_transit_qty = transit_map.get((pid, sku_id), ZERO_QTY)
        items.append(
            {
                "product_id": pid,
                "product_code": product.code,
                "product_name": product.name,
                "sku_id": sku_id,
                "spec_code": sku.spec_code if sku is not None else None,
                "spec_name": sku.spec_name if sku is not None else None,
                "current_qty": current_qty,
                "in_transit_qty": in_transit_qty,
                "total_qty": current_qty + in_transit_qty,
            }
        )
    return {
        "items": items,
        "summary": {
            "sku_count": len(items),
            "current_qty": sum(
                (item["current_qty"] for item in items),
                ZERO_QTY,
            ),
            "in_transit_qty": sum(
                (item["in_transit_qty"] for item in items),
                ZERO_QTY,
            ),
            "total_qty": sum(
                (item["total_qty"] for item in items),
                ZERO_QTY,
            ),
        },
    }
