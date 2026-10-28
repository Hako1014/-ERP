"""采购计划服务（TASK-024）。

自动测算口径：
- 前 20 日销量：已付款、非取消、非赠品订单的商品/规格数量；
- 现有库存：非虚拟仓的 NORMAL 库位库存；
- 在途库存：已审核且未到货的采购订单余量；
- 预计采购数量 = max(前 20 日销量 - 现有库存 - 在途库存, 0)。

正在进行的采购计划数量用于重复生成提示，不重复从预计采购数量中扣减。
"""

from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
import hashlib
import json
from typing import Any
from uuid import UUID

from fastapi.encoders import jsonable_encoder
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import apply_owner_scope, document_visible
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money, to_decimal
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Brand, Unit
from app.models.order import Order, OrderItem
from app.models.org import Staff
from app.models.party import Supplier, SupplierPrice
from app.models.product import Product, ProductSku
from app.models.purchase import (
    PurchaseOrder,
    PurchaseOrderItem,
    PurchasePlan,
    PurchasePlanAttachment,
    PurchasePlanCreationRequest,
    PurchasePlanItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, party_service

DOC_TYPE = "purchase_plan"
PLAN_ACTIVE = "IN_PROGRESS"
PLAN_ABORTED = "ABORTED"
PLAN_FINISHED = "FINISHED"
AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

ZERO_QTY = Decimal("0")
QTY_QUANT = Decimal("0.001")
SALES_EXCLUDED_STATUSES = ("CANCELLED", "CANCELED", "DELETED")
IN_TRANSIT_STATUSES = ("APPROVED", "RECEIVING")

PLAN_HEADER_FIELDS = {"plan_date", "remark"}


def _now() -> datetime:
    """应用层统一存 UTC，模型列为无时区 DateTime。"""
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


def _optional_sku_id(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return _to_int_id(value, "规格")


def _to_qty(value: Any, label: str) -> Decimal:
    try:
        result = to_decimal(value).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result <= 0:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_price(value: Any, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result <= 0:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _to_datetime(value: Any, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}格式错误") from exc
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}")
    return text


def get_plan_or_404(
    db: Session, plan_id: int, *, check_scope: bool = True,
    company_id: int | None = None,
) -> PurchasePlan:
    stmt = select(PurchasePlan).where(PurchasePlan.id == plan_id)
    if company_id is not None:
        stmt = stmt.where(PurchasePlan.company_id == company_id)
    plan = db.scalar(stmt)
    if plan is None or (check_scope and not document_visible(db, plan.created_by, DOC_TYPE, plan.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购计划不存在")
    return plan


def _get_product_or_404(db: Session, product_id: int, *, company_id: int) -> Product:
    product = db.scalar(select(Product).where(
        Product.id == product_id,
        Product.company_id == company_id,
    ))
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    if product.is_disabled:
        raise BizException(ErrorCode.BAD_REQUEST, f"商品已禁用：{product.name}")
    return product


def _validate_sku(
    db: Session, product_id: int, sku_id: int | None, *, company_id: int
) -> ProductSku | None:
    if sku_id is None:
        return None
    sku = db.scalar(select(ProductSku).where(
        ProductSku.id == sku_id,
        ProductSku.company_id == company_id,
    ))
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")
    if not sku.is_active:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格已停用")
    return sku


def resolve_purchase_price(
    db: Session, *, supplier_id: int, product: Product, sku_id: int | None,
    company_id: int | None = None,
) -> Decimal | None:
    """取价顺序：供应商规格价、供应商商品价、规格成本价、商品成本价。"""
    supplier_price = party_service.get_supplier_price(
        db, supplier_id=supplier_id, product_id=product.id, sku_id=sku_id,
        company_id=company_id,
    )
    if supplier_price is not None:
        return round_money(supplier_price)
    if sku_id is not None:
        sku_stmt = select(ProductSku).where(ProductSku.id == sku_id)
        if company_id is not None:
            sku_stmt = sku_stmt.where(ProductSku.company_id == company_id)
        sku = db.scalar(sku_stmt)
        if sku is not None and sku.cost_price is not None:
            return round_money(sku.cost_price)
    if product.cost_price is not None:
        return round_money(product.cost_price)
    return None


def _sum_keyed(
    values: dict[tuple[int, int | None], Decimal],
    *,
    product_id: int,
    sku_id: int | None,
) -> Decimal:
    if sku_id is not None:
        return values.get((product_id, sku_id), ZERO_QTY)
    return sum(
        (qty for (pid, _), qty in values.items() if pid == product_id),
        ZERO_QTY,
    )


def _aggregate_sales(
    db: Session, product_ids: list[int], since: datetime,
    company_id: int | None = None,
) -> dict[tuple[int, int | None], Decimal]:
    if not product_ids:
        return {}
    order_date = func.coalesce(Order.pay_time, Order.order_time)
    conditions = [
        OrderItem.product_id.in_(product_ids),
        OrderItem.is_gift.is_(False),
        Order.pay_status == "PAID",
        Order.status.not_in(SALES_EXCLUDED_STATUSES),
        order_date >= since,
    ]
    if company_id is not None:
        conditions.extend((
            Order.company_id == company_id,
            OrderItem.company_id == company_id,
        ))
    rows = db.execute(
        select(
            OrderItem.product_id,
            OrderItem.sku_id,
            func.sum(OrderItem.qty),
        )
        .join(Order, Order.id == OrderItem.order_id)
        .where(*conditions)
        .group_by(OrderItem.product_id, OrderItem.sku_id)
    )
    return {(pid, sku_id): Decimal(qty or 0) for pid, sku_id, qty in rows}


def _aggregate_stock(
    db: Session, product_ids: list[int], company_id: int | None = None,
) -> dict[tuple[int, int | None], Decimal]:
    if not product_ids:
        return {}
    conditions = [
        ProductSku.product_id.in_(product_ids),
        StockLocation.status == "NORMAL",
        Warehouse.is_virtual.is_(False),
        Warehouse.is_enabled.is_(True),
    ]
    if company_id is not None:
        conditions.extend((
            ProductSku.company_id == company_id,
            StockLocation.company_id == company_id,
            Location.company_id == company_id,
            Warehouse.company_id == company_id,
        ))
    rows = db.execute(
        select(
            ProductSku.product_id,
            StockLocation.product_sku_id,
            func.sum(StockLocation.qty),
        )
        .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
        .join(Location, Location.id == StockLocation.location_id)
        .join(Warehouse, Warehouse.id == Location.warehouse_id)
        .where(*conditions)
        .group_by(ProductSku.product_id, StockLocation.product_sku_id)
    )
    return {(pid, sku_id): Decimal(qty or 0) for pid, sku_id, qty in rows}


def _aggregate_in_transit(
    db: Session, product_ids: list[int], company_id: int | None = None,
) -> dict[tuple[int, int | None], Decimal]:
    if not product_ids:
        return {}
    conditions = [
        PurchaseOrderItem.product_id.in_(product_ids),
        PurchaseOrderItem.qty > PurchaseOrderItem.arrived_qty,
        PurchaseOrder.audit_status == AUDIT_APPROVED,
        PurchaseOrder.status.in_(IN_TRANSIT_STATUSES),
    ]
    if company_id is not None:
        conditions.extend((
            PurchaseOrder.company_id == company_id,
            PurchaseOrderItem.company_id == company_id,
        ))
    rows = db.execute(
        select(
            PurchaseOrderItem.product_id,
            PurchaseOrderItem.sku_id,
            func.sum(PurchaseOrderItem.qty - PurchaseOrderItem.arrived_qty),
        )
        .join(PurchaseOrder, PurchaseOrder.id == PurchaseOrderItem.order_id)
        .where(*conditions)
        .group_by(PurchaseOrderItem.product_id, PurchaseOrderItem.sku_id)
    )
    return {(pid, sku_id): Decimal(qty or 0) for pid, sku_id, qty in rows}


def _aggregate_in_progress(
    db: Session, product_ids: list[int], company_id: int | None = None,
) -> dict[tuple[int, int | None], Decimal]:
    if not product_ids:
        return {}
    conditions = [
        PurchasePlanItem.product_id.in_(product_ids),
        PurchasePlanItem.planned_qty > PurchasePlanItem.purchased_qty,
        PurchasePlan.status == PLAN_ACTIVE,
    ]
    if company_id is not None:
        conditions.extend((
            PurchasePlan.company_id == company_id,
            PurchasePlanItem.company_id == company_id,
        ))
    rows = db.execute(
        select(
            PurchasePlanItem.product_id,
            PurchasePlanItem.sku_id,
            func.sum(PurchasePlanItem.planned_qty - PurchasePlanItem.purchased_qty),
        )
        .join(PurchasePlan, PurchasePlan.id == PurchasePlanItem.plan_id)
        .where(*conditions)
        .group_by(PurchasePlanItem.product_id, PurchasePlanItem.sku_id)
    )
    return {(pid, sku_id): Decimal(qty or 0) for pid, sku_id, qty in rows}


def _candidate_products(
    db: Session,
    *,
    supplier_id: int,
    keyword: str | None,
    page: int,
    page_size: int,
    company_id: int | None = None,
) -> tuple[list[Product], int]:
    party_service.get_supplier_or_404(db, supplier_id, company_id=company_id)
    conditions = [Product.is_disabled.is_(False)]
    if company_id is not None:
        conditions.append(Product.company_id == company_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Product.name.like(like),
                Product.code.like(like),
                Product.art_no.like(like),
            )
        )
    price_exists = (
        select(SupplierPrice.id)
        .where(
            SupplierPrice.supplier_id == supplier_id,
            SupplierPrice.product_id == Product.id,
            SupplierPrice.status == "ACTIVE",
            *([SupplierPrice.company_id == company_id] if company_id is not None else []),
        )
        .exists()
    )
    conditions.append(
        or_(Product.default_supplier_id == supplier_id, price_exists)
    )
    total = db.scalar(select(func.count(Product.id)).where(*conditions)) or 0
    products = list(
        db.scalars(
            select(Product)
            .where(*conditions)
            .order_by(Product.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return products, total


def preview_purchase_needs(
    db: Session,
    *,
    supplier_id: int,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 50,
    as_of: datetime | None = None,
    company_id: int | None = None,
) -> dict:
    """按前 20 日销量、现有库存和在途库存测算建议采购数量。"""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    products, total = _candidate_products(
        db,
        supplier_id=supplier_id,
        keyword=keyword,
        page=page,
        page_size=page_size,
        company_id=company_id,
    )
    product_ids = [product.id for product in products]
    since = (as_of or _now()) - timedelta(days=20)
    sales_map = _aggregate_sales(db, product_ids, since, company_id)
    stock_map = _aggregate_stock(db, product_ids, company_id)
    transit_map = _aggregate_in_transit(db, product_ids, company_id)
    in_progress_map = _aggregate_in_progress(db, product_ids, company_id)

    items: list[dict] = []
    for product in products:
        skus = list(
            db.scalars(
                select(ProductSku)
                .where(
                    ProductSku.product_id == product.id,
                    ProductSku.is_active.is_(True),
                    *([ProductSku.company_id == company_id] if company_id is not None else []),
                )
                .order_by(ProductSku.id)
            )
        )
        keys: list[int | None] = [sku.id for sku in skus] or [None]
        for sku_id in keys:
            sku = db.get(ProductSku, sku_id) if sku_id is not None else None
            sales_qty = _sum_keyed(
                sales_map, product_id=product.id, sku_id=sku_id
            )
            stock_qty = _sum_keyed(
                stock_map, product_id=product.id, sku_id=sku_id
            )
            transit_qty = _sum_keyed(
                transit_map, product_id=product.id, sku_id=sku_id
            )
            in_progress_qty = _sum_keyed(
                in_progress_map, product_id=product.id, sku_id=sku_id
            )
            suggested_qty = max(sales_qty - stock_qty - transit_qty, ZERO_QTY)
            price = resolve_purchase_price(
                db, supplier_id=supplier_id, product=product, sku_id=sku_id,
                company_id=company_id,
            )
            brand_stmt = select(Brand).where(Brand.id == product.brand_id) if product.brand_id else None
            if brand_stmt is not None and company_id is not None:
                brand_stmt = brand_stmt.where(Brand.company_id == company_id)
            brand = db.scalar(brand_stmt) if brand_stmt is not None else None
            items.append(
                {
                    "product_id": product.id,
                    "product_name": product.name,
                    "product_code": product.code,
                    "art_no": product.art_no,
                    "sku_id": sku_id,
                    "spec_name": sku.spec_name if sku else None,
                    "spec_code": sku.spec_code if sku else None,
                    "brand_id": product.brand_id,
                    "brand_name": brand.name if brand else None,
                    "supplier_id": supplier_id,
                    "purchase_price": price,
                    "sales_20d": sales_qty,
                    "current_stock": stock_qty,
                    "in_transit_stock": transit_qty,
                    "in_progress_qty": in_progress_qty,
                    "suggested_qty": suggested_qty,
                    "estimated_amount": (
                        round_money(price * suggested_qty)
                        if price is not None
                        else None
                    ),
                }
            )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items_count": len(items),
        "items": items,
        "sales_window_days": 20,
    }


def preview_purchase_needs_rows(
    db: Session,
    *,
    supplier_id: int,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
    as_of: datetime | None = None,
    company_id: int | None = None,
) -> dict:
    """按商品规格行分页测算采购需求，避免一个商品展开全部SKU导致无界结果。"""
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    party_service.get_supplier_or_404(db, supplier_id, company_id=company_id)
    conditions = [Product.is_disabled.is_(False)]
    if company_id is not None:
        conditions.append(Product.company_id == company_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(or_(
            Product.name.like(like), Product.code.like(like), Product.art_no.like(like),
        ))
    active_price_exists = (
        select(SupplierPrice.id)
        .where(
            SupplierPrice.supplier_id == supplier_id,
            SupplierPrice.product_id == Product.id,
            SupplierPrice.status == "ACTIVE",
            *([SupplierPrice.company_id == company_id] if company_id is not None else []),
        )
        .exists()
    )
    conditions.append(or_(Product.default_supplier_id == supplier_id, active_price_exists))

    sku_join = and_(
        ProductSku.product_id == Product.id,
        ProductSku.is_active.is_(True),
        *([ProductSku.company_id == company_id] if company_id is not None else []),
    )
    candidate_rows = (
        select(Product.id.label("product_id"), ProductSku.id.label("sku_id"))
        .select_from(Product)
        .outerjoin(ProductSku, sku_join)
        .where(*conditions)
        .subquery()
    )
    total = db.scalar(select(func.count()).select_from(candidate_rows)) or 0
    candidate_products_total = db.scalar(
        select(func.count(Product.id)).where(*conditions)
    ) or 0
    rows = db.execute(
        select(Product, ProductSku)
        .select_from(Product)
        .outerjoin(ProductSku, sku_join)
        .where(*conditions)
        .order_by(Product.id, ProductSku.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    product_ids = list(dict.fromkeys(product.id for product, _sku in rows))
    since = (as_of or _now()) - timedelta(days=20)
    sales_map = _aggregate_sales(db, product_ids, since, company_id)
    stock_map = _aggregate_stock(db, product_ids, company_id)
    transit_map = _aggregate_in_transit(db, product_ids, company_id)
    in_progress_map = _aggregate_in_progress(db, product_ids, company_id)

    items: list[dict] = []
    for product, sku in rows:
        sku_id = sku.id if sku else None
        sales_qty = _sum_keyed(sales_map, product_id=product.id, sku_id=sku_id)
        stock_qty = _sum_keyed(stock_map, product_id=product.id, sku_id=sku_id)
        transit_qty = _sum_keyed(transit_map, product_id=product.id, sku_id=sku_id)
        in_progress_qty = _sum_keyed(
            in_progress_map, product_id=product.id, sku_id=sku_id,
        )
        suggested_qty = max(sales_qty - stock_qty - transit_qty, ZERO_QTY)
        price = resolve_purchase_price(
            db, supplier_id=supplier_id, product=product, sku_id=sku_id,
            company_id=company_id,
        )
        brand_stmt = select(Brand).where(Brand.id == product.brand_id) if product.brand_id else None
        if brand_stmt is not None and company_id is not None:
            brand_stmt = brand_stmt.where(Brand.company_id == company_id)
        brand = db.scalar(brand_stmt) if brand_stmt is not None else None
        items.append({
            "product_id": product.id,
            "product_name": product.name,
            "product_code": product.code,
            "art_no": product.art_no,
            "sku_id": sku_id,
            "spec_name": sku.spec_name if sku else None,
            "spec_code": sku.spec_code if sku else None,
            "brand_id": product.brand_id,
            "brand_name": brand.name if brand else None,
            "supplier_id": supplier_id,
            "purchase_price": price,
            "sales_20d": sales_qty,
            "current_stock": stock_qty,
            "in_transit_stock": transit_qty,
            "in_progress_qty": in_progress_qty,
            "suggested_qty": suggested_qty,
            "estimated_amount": (
                round_money(price * suggested_qty) if price is not None else None
            ),
        })
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": items,
        "candidate_products_total": int(candidate_products_total),
        "sales_window_days": 20,
    }


def _validate_plan_item(
    db: Session, *, supplier_id: int, company_id: int, data: dict
) -> dict:
    _check_unknown_fields(
        data, {"product_id", "sku_id", "planned_qty", "purchase_price"}
    )
    product_id = _to_int_id(data.get("product_id"), "商品")
    sku_id = _optional_sku_id(data.get("sku_id"))
    product = _get_product_or_404(db, product_id, company_id=company_id)
    _validate_sku(db, product.id, sku_id, company_id=company_id)
    planned_qty = _to_qty(data.get("planned_qty"), "计划采购数量")
    purchase_price = (
        _optional_price(data.get("purchase_price"), "采购价")
        if "purchase_price" in data
        else resolve_purchase_price(
            db, supplier_id=supplier_id, product=product, sku_id=sku_id,
            company_id=company_id,
        )
    )
    amount = (
        round_money(planned_qty * purchase_price)
        if purchase_price is not None
        else None
    )
    return {
        "product_id": product.id,
        "sku_id": sku_id,
        "planned_qty": planned_qty,
        "purchase_price": purchase_price,
        "amount": amount,
    }


def _recalculate_plan(db: Session, plan: PurchasePlan) -> None:
    items = list(
        db.scalars(
            select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan.id)
        )
    )
    plan.total_qty = sum((item.planned_qty for item in items), ZERO_QTY)
    plan.total_amount = sum(
        (item.amount for item in items if item.amount is not None), ZERO_QTY
    )


def create_plan(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
    request_id: str | None = None,
    assistant_draft_id: str | None = None,
) -> PurchasePlan:
    _check_unknown_fields(
        data, {"supplier_id", "plan_date", "remark", "items"}
    )
    supplier_id = _to_int_id(data.get("supplier_id"), "供应商")
    supplier = party_service.get_supplier_or_404(
        db, supplier_id, company_id=staff.company_id
    )
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用，不能生成采购计划")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少选择一个计划采购商品")
    if len(raw_items) > 500:
        raise BizException(ErrorCode.BAD_REQUEST, "单次最多生成 500 个计划明细")

    normalized_items: list[dict] = []
    keys: set[tuple[int, int | None]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "计划明细格式错误")
        item = _validate_plan_item(
            db, supplier_id=supplier_id, company_id=staff.company_id, data=raw
        )
        key = (item["product_id"], item["sku_id"])
        if key in keys:
            raise BizException(ErrorCode.BAD_REQUEST, "同一商品规格不能重复")
        keys.add(key)
        normalized_items.append(item)

    plan = PurchasePlan(
        company_id=staff.company_id,
        plan_no=generate_serial(SerialPrefix.PURCHASE_PLAN, dt=_now()),
        supplier_id=supplier_id,
        plan_date=_to_datetime(data.get("plan_date"), "计划采购时间") or _now(),
        status=PLAN_ACTIVE,
        audit_status=AUDIT_PENDING,
        remark=_optional_text(data.get("remark"), "备注", 255),
        created_by=staff.id,
    )
    db.add(plan)
    db.flush()
    for item in normalized_items:
        db.add(PurchasePlanItem(
            company_id=plan.company_id, plan_id=plan.id, **item,
        ))
    db.flush()
    _recalculate_plan(db, plan)
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(plan.id),
        summary=f"生成采购计划：{plan.plan_no}",
        detail={
            "supplier_id": supplier_id,
            "item_count": len(normalized_items),
            **(
                {
                    "source": "assistant_draft",
                    "assistant_draft_id": assistant_draft_id,
                    "request_id": request_id,
                }
                if assistant_draft_id
                else {}
            ),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return plan


def _creation_request_stmt(*, staff: Staff, request_id: str):
    return select(PurchasePlanCreationRequest).where(
        PurchasePlanCreationRequest.company_id == staff.company_id,
        PurchasePlanCreationRequest.staff_id == staff.id,
        PurchasePlanCreationRequest.request_id == request_id,
    )


def create_plan_idempotent(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    request_id: str,
    assistant_draft_id: str | None = None,
    assistant_attachment: dict | None = None,
) -> dict:
    """幂等创建采购计划；请求预留可恢复，业务单据与成功结果同事务提交。"""
    try:
        request_id = str(UUID(request_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "幂等请求标识必须是 UUID") from exc

    canonical_payload = json.dumps(
        data if assistant_attachment is None else {
            "data": data, "assistant_attachment": assistant_attachment,
        },
        ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    payload_hash = hashlib.sha256(canonical_payload.encode("utf-8")).hexdigest()

    request = db.scalar(_creation_request_stmt(staff=staff, request_id=request_id))
    if request is None:
        request = PurchasePlanCreationRequest(
            company_id=staff.company_id,
            staff_id=staff.id,
            request_id=request_id,
            payload_hash=payload_hash,
            status="PENDING",
        )
        db.add(request)
        try:
            # 单独提交请求占位，避免进程在结果未知时失去恢复锚点。
            db.commit()
        except IntegrityError:
            db.rollback()

    request = db.scalar(
        _creation_request_stmt(staff=staff, request_id=request_id).with_for_update()
    )
    if request is None:
        raise BizException(ErrorCode.INTERNAL_ERROR, "创建请求记录暂不可用，请重试")
    if request.payload_hash != payload_hash:
        raise BizException(ErrorCode.CONFLICT, "同一幂等请求标识不能用于不同的表单内容")
    if request.status == "SUCCEEDED" and request.result_json:
        return json.loads(request.result_json)
    if request.status == "FAILED":
        raise BizException(
            ErrorCode.CONFLICT,
            request.failure_message or "此前创建失败，请修改内容后重新发起保存",
        )

    try:
        # 保存点让确定性校验失败只回滚本次业务尝试，外层仍可记录 FAILED。
        with db.begin_nested():
            plan = create_plan(
                db,
                data=data,
                staff=staff,
                commit=False,
                request_id=request_id,
                assistant_draft_id=assistant_draft_id,
            )
            if assistant_attachment is not None:
                required = {
                    "id", "storage_key", "source_attachment_id", "display_name",
                    "media_type", "size", "sha256",
                }
                if set(assistant_attachment) != required:
                    raise BizException(ErrorCode.BAD_REQUEST, "采购计划附件信息无效")
                db.add(PurchasePlanAttachment(
                    id=assistant_attachment["id"],
                    company_id=staff.company_id,
                    plan_id=plan.id,
                    created_by=staff.id,
                    source_assistant_attachment_id=assistant_attachment["source_attachment_id"],
                    display_name=assistant_attachment["display_name"],
                    media_type=assistant_attachment["media_type"],
                    size=assistant_attachment["size"],
                    sha256=assistant_attachment["sha256"],
                    storage_key=assistant_attachment["storage_key"],
                ))
                db.flush()
            result = plan_dict(db, plan, with_items=True, with_approval=True)
            request.status = "SUCCEEDED"
            request.plan_id = plan.id
            result = jsonable_encoder(result)
            request.result_json = json.dumps(
                result, ensure_ascii=False, separators=(",", ":")
            )
            request.failure_message = None
        db.commit()
        return result
    except BizException as exc:
        request.status = "FAILED"
        request.failure_message = exc.message[:255]
        db.commit()
        raise
    except Exception:
        # PENDING 是可恢复状态；重试同一标识与同一内容会继续执行。
        db.rollback()
        raise


def get_creation_request_result(
    db: Session, *, staff: Staff, request_id: str
) -> dict:
    try:
        request_id = str(UUID(request_id))
    except (TypeError, ValueError, AttributeError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "请求标识必须是 UUID") from exc
    request = db.scalar(_creation_request_stmt(staff=staff, request_id=request_id))
    if request is None:
        return {"status": "NOT_FOUND", "result": None, "message": None}
    return {
        "status": request.status,
        "result": json.loads(request.result_json) if request.result_json else None,
        "message": request.failure_message,
    }


def plan_item_dict(db: Session, item: PurchasePlanItem) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    brand = db.get(Brand, product.brand_id) if product and product.brand_id else None
    unit = (
        db.get(Unit, product.main_unit_id)
        if product and product.main_unit_id
        else None
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
        "brand_id": product.brand_id if product else None,
        "brand_name": brand.name if brand else None,
        "unit_id": product.main_unit_id if product else None,
        "unit_name": unit.name if unit else None,
        "planned_qty": item.planned_qty,
        "purchased_qty": item.purchased_qty,
        "remaining_qty": max(item.planned_qty - item.purchased_qty, ZERO_QTY),
        "purchase_price": item.purchase_price,
        "amount": item.amount,
    }


def plan_dict(
    db: Session,
    plan: PurchasePlan,
    *,
    with_items: bool = True,
    with_approval: bool = False,
    company_id: int | None = None,
) -> dict:
    supplier_stmt = select(Supplier).where(Supplier.id == plan.supplier_id)
    if company_id is not None:
        supplier_stmt = supplier_stmt.where(Supplier.company_id == company_id)
    supplier = db.scalar(supplier_stmt)
    data = {
        "id": plan.id,
        "plan_no": plan.plan_no,
        "supplier_id": plan.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "plan_date": plan.plan_date,
        "status": plan.status,
        "audit_status": plan.audit_status,
        "total_qty": plan.total_qty,
        "total_amount": plan.total_amount,
        "remark": plan.remark,
        "created_by": plan.created_by,
        "auditor_id": plan.auditor_id,
        "audit_time": plan.audit_time,
        "created_at": plan.created_at,
        "updated_at": plan.updated_at,
    }
    if with_items:
        items = list(
            db.scalars(
                select(PurchasePlanItem)
                .where(
                    PurchasePlanItem.plan_id == plan.id,
                    *([PurchasePlanItem.company_id == company_id] if company_id is not None else []),
                )
                .order_by(PurchasePlanItem.id)
            )
        )
        data["items"] = [plan_item_dict(db, item) for item in items]
    attachments = db.scalars(select(PurchasePlanAttachment).where(
        PurchasePlanAttachment.plan_id == plan.id,
        PurchasePlanAttachment.company_id == (company_id or plan.company_id),
    ).order_by(PurchasePlanAttachment.created_at, PurchasePlanAttachment.id)).all()
    data["attachments"] = [
        {"id": item.id, "display_name": item.display_name, "media_type": item.media_type,
         "size": item.size, "created_at": item.created_at}
        for item in attachments
    ]
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=DOC_TYPE, doc_id=plan.id
        )
    return data


def list_plans(
    db: Session,
    *,
    supplier_id: int | None = None,
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
        conditions.append(PurchasePlan.company_id == company_id)
    if supplier_id is not None:
        conditions.append(PurchasePlan.supplier_id == supplier_id)
    if status:
        conditions.append(PurchasePlan.status == status.upper())
    if audit_status:
        conditions.append(PurchasePlan.audit_status == audit_status.upper())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                PurchasePlan.plan_no.like(like),
                PurchasePlan.supplier_id.in_(
                    select(Supplier.id).where(
                        Supplier.name.like(like),
                        *([Supplier.company_id == company_id] if company_id is not None else []),
                    )
                ),
            )
        )
    total = db.scalar(
        apply_owner_scope(
            select(func.count(PurchasePlan.id)).where(*conditions),
            PurchasePlan.created_by,
            db,
        )
    ) or 0
    rows = list(
        db.scalars(
            apply_owner_scope(
                select(PurchasePlan)
                .where(*conditions)
                .order_by(PurchasePlan.id.desc())
                .offset((page - 1) * page_size)
                .limit(page_size),
                PurchasePlan.created_by,
                db,
            )
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            plan_dict(
                db, row, with_items=False, with_approval=False,
                company_id=company_id,
            )
            for row in rows
        ],
    }


def _approval_state(db: Session, plan_id: int) -> dict:
    return approval_service.get_state(db, doc_type=DOC_TYPE, doc_id=plan_id)["state"]


def _ensure_editable(db: Session, plan: PurchasePlan) -> None:
    if plan.status != PLAN_ACTIVE:
        raise BizException(ErrorCode.CONFLICT, "当前采购计划状态不允许修改")
    state = _approval_state(db, plan.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "采购计划已进入审批流程，不能修改")


def update_plan(
    db: Session,
    *,
    plan_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchasePlan:
    _check_unknown_fields(data, PLAN_HEADER_FIELDS)
    plan = get_plan_or_404(db, plan_id)
    _ensure_editable(db, plan)
    if "plan_date" in data:
        parsed = _to_datetime(data["plan_date"], "计划采购时间")
        if parsed is None:
            raise BizException(ErrorCode.BAD_REQUEST, "计划采购时间不能为空")
        plan.plan_date = parsed
    if "remark" in data:
        plan.remark = _optional_text(data["remark"], "备注", 255)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(plan.id),
        summary=f"修改采购计划：{plan.plan_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return plan


def batch_update_plan_items(
    db: Session,
    *,
    plan_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchasePlan:
    _check_unknown_fields(data, {"item_ids", "planned_qty", "purchase_price"})
    if "planned_qty" not in data and "purchase_price" not in data:
        raise BizException(
            ErrorCode.BAD_REQUEST, "批量修改至少提供采购数量或采购价"
        )
    plan = get_plan_or_404(db, plan_id)
    _ensure_editable(db, plan)

    item_ids = data.get("item_ids")
    if not isinstance(item_ids, list) or not item_ids:
        raise BizException(ErrorCode.BAD_REQUEST, "至少选择一条计划明细")
    normalized_ids = {_to_int_id(item_id, "计划明细") for item_id in item_ids}
    items = list(
        db.scalars(
            select(PurchasePlanItem).where(
                PurchasePlanItem.plan_id == plan.id,
                PurchasePlanItem.id.in_(normalized_ids),
            )
        )
    )
    if len(items) != len(normalized_ids):
        raise BizException(ErrorCode.BAD_REQUEST, "存在不属于当前计划的明细")

    planned_qty = (
        _to_qty(data["planned_qty"], "计划采购数量")
        if "planned_qty" in data
        else None
    )
    purchase_price = (
        _optional_price(data["purchase_price"], "采购价")
        if "purchase_price" in data
        else None
    )
    for item in items:
        if planned_qty is not None:
            item.planned_qty = planned_qty
        if "purchase_price" in data:
            item.purchase_price = purchase_price
        item.amount = (
            round_money(item.planned_qty * item.purchase_price)
            if item.purchase_price is not None
            else None
        )
    db.flush()
    _recalculate_plan(db, plan)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(plan.id),
        summary=f"批量修改采购计划明细：{plan.plan_no}",
        detail={"item_ids": sorted(normalized_ids), "fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return plan


def delete_plan(
    db: Session, *, plan_id: int, staff: Staff, commit: bool = True
) -> None:
    plan = get_plan_or_404(db, plan_id)
    _ensure_editable(db, plan)
    referenced = db.scalar(
        select(func.count(PurchaseOrder.id)).where(PurchaseOrder.plan_id == plan.id)
    )
    if referenced:
        raise BizException(ErrorCode.CONFLICT, "采购计划已生成采购订单，不能删除")
    for item in db.scalars(
        select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan.id)
    ):
        db.delete(item)
    db.delete(plan)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(plan.id),
        summary=f"删除采购计划：{plan.plan_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(plan: PurchasePlan, state: dict) -> None:
    status = state["status"]
    if status == "APPROVED":
        plan.audit_status = AUDIT_APPROVED
    elif status == "REJECTED":
        plan.audit_status = AUDIT_REJECTED
    else:
        plan.audit_status = AUDIT_PENDING


def submit_plan(
    db: Session, *, plan_id: int, staff: Staff, commit: bool = True
) -> PurchasePlan:
    plan = get_plan_or_404(db, plan_id)
    if plan.status != PLAN_ACTIVE:
        raise BizException(ErrorCode.CONFLICT, "当前采购计划状态不能提交审批")
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=plan.id,
        doc_no=plan.plan_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(plan, state)
    plan.auditor_id = None
    plan.audit_time = None
    _commit(db, commit)
    return plan


def approve_plan(
    db: Session,
    *,
    plan_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchasePlan:
    # 审批人可审核他人单据；审批引擎会验证当前环节的审批人身份。
    plan = get_plan_or_404(db, plan_id, check_scope=False)
    if plan.status != PLAN_ACTIVE:
        raise BizException(ErrorCode.CONFLICT, "当前采购计划状态不能审核")
    state = approval_service.approve(
        db,
        doc_type=DOC_TYPE,
        doc_id=plan.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(plan, state)
    if state["status"] == "APPROVED":
        plan.auditor_id = staff.id
        plan.audit_time = _now()
    _commit(db, commit)
    return plan


def reject_plan(
    db: Session,
    *,
    plan_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchasePlan:
    plan = get_plan_or_404(db, plan_id, check_scope=False)
    if plan.status != PLAN_ACTIVE:
        raise BizException(ErrorCode.CONFLICT, "当前采购计划状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=plan.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(plan, state)
    plan.auditor_id = staff.id
    plan.audit_time = _now()
    _commit(db, commit)
    return plan


def unaudit_plan(
    db: Session,
    *,
    plan_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> PurchasePlan:
    plan = get_plan_or_404(db, plan_id, check_scope=False)
    if plan.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购计划尚未审核通过，不能弃审")
    state = approval_service.unaudit(
        db,
        doc_type=DOC_TYPE,
        doc_id=plan.id,
        staff=staff,
        reason=reason,
        step=step,
        commit=False,
    )
    _map_audit_status(plan, state)
    plan.auditor_id = None
    plan.audit_time = None
    _commit(db, commit)
    return plan


def abort_plan(
    db: Session, *, plan_id: int, staff: Staff, commit: bool = True
) -> PurchasePlan:
    plan = get_plan_or_404(db, plan_id)
    if plan.status != PLAN_ACTIVE:
        raise BizException(ErrorCode.CONFLICT, "仅进行中的采购计划可以中止")
    plan.status = PLAN_ABORTED
    write_audit(
        db,
        action="ABORT",
        target_type=DOC_TYPE,
        target_id=str(plan.id),
        summary=f"中止采购计划：{plan.plan_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return plan


def finish_plan(
    db: Session, *, plan_id: int, staff: Staff, commit: bool = True
) -> PurchasePlan:
    plan = get_plan_or_404(db, plan_id)
    if plan.status != PLAN_ACTIVE:
        raise BizException(ErrorCode.CONFLICT, "仅进行中的采购计划可以完成")
    if plan.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购计划审核通过后才能标记采购完成")
    items = list(
        db.scalars(
            select(PurchasePlanItem).where(PurchasePlanItem.plan_id == plan.id)
        )
    )
    if not items or any(item.purchased_qty < item.planned_qty for item in items):
        raise BizException(ErrorCode.CONFLICT, "计划明细尚未全部采购完成")
    plan.status = PLAN_FINISHED
    write_audit(
        db,
        action="FINISH",
        target_type=DOC_TYPE,
        target_id=str(plan.id),
        summary=f"采购计划已完成：{plan.plan_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return plan
