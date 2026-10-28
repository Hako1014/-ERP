"""库存查询、库存调整与库位调整服务（TASK-033）。

库存口径：
- 总库存：所有实际库位 ``stock_location.qty`` 之和。
- 占用库存：待审核/已审核但未完成下架的采购出库、调拨出库数量之和。
- 可用库存：总库存 - 占用库存；缺口通过 ``shortage_qty`` 单独返回。

库位调整仅允许同一仓库内不同库位之间移动，移动前后仓库实物总量不变。
当前模型未定义拆箱单位和良品/不良品转换字段，因此本任务不扩展这两类规则。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import require_scope_policy
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Unit
from app.models.org import Staff
from app.models.product import Product, ProductSku
from app.models.stock import (
    LocationAdjust,
    LocationAdjustItem,
    StockCost,
    StockFlow,
    StockOut,
    StockOutItem,
    StockWarning,
    TransferOut,
    TransferOutItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, warehouse_service

ADJUST_DOC_TYPE = "location_adjust"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_TERMINATED = "TERMINATED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

OFF_SHELF_DONE = "DONE"
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")


def assistant_inventory_totals(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
) -> dict[str, Any]:
    """返回助手允许使用的库存数量汇总，不含商品、仓库或成本字段。

    通过库存地点的公司归属汇总当前公司的数量；不返回商品、仓库或成本字段。
    """
    require_scope_policy(db, staff_id=staff_id, company_id=company_id)

    row = db.execute(
        select(
            func.coalesce(
                func.sum(
                    case(
                        (StockLocation.status == "NORMAL", StockLocation.qty),
                        else_=0,
                    )
                ),
                0,
            ).label("normal_quantity"),
            func.coalesce(
                func.sum(
                    case(
                        (StockLocation.status == "DEFECTIVE", StockLocation.qty),
                        else_=0,
                    )
                ),
                0,
            ).label("defective_quantity"),
        )
        .join(Location, Location.id == StockLocation.location_id)
        .where(
            StockLocation.company_id == company_id,
            Location.company_id == company_id,
        )
    ).one()
    return {
        "normal_quantity": row.normal_quantity,
        "defective_quantity": row.defective_quantity,
    }

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
ZERO_MONEY = Decimal("0.00")

ADJUST_FIELDS = {"warehouse_id", "remark", "items"}
ADJUST_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "from_location_id",
    "to_location_id",
    "qty",
}


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


def _to_qty(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result < ZERO_QTY or (not allow_zero and result == ZERO_QTY):
        operator = "不能小于 0" if allow_zero else "必须大于 0"
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}{operator}")
    return result


def _to_money(value: Any, label: str) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < ZERO_MONEY:
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


def _stock_status(warehouse: Warehouse) -> str:
    return "DEFECTIVE" if warehouse.warehouse_type == "DEFECTIVE" else "NORMAL"


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


def _get_location_or_404(
    db: Session,
    *,
    warehouse_id: int,
    location_id: int,
    label: str,
) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise BizException(ErrorCode.NOT_FOUND, f"{label}库位不存在")
    if location.warehouse_id != warehouse_id:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}库位不属于当前仓库")
    if not location.is_enabled:
        raise BizException(ErrorCode.CONFLICT, f"{label}库位已停用")
    return location


def _resolve_cost(
    *,
    product: Product,
    sku: ProductSku,
    explicit: Any = None,
) -> Decimal | None:
    if explicit not in (None, ""):
        return _to_money(explicit, "库存成本")
    if sku.cost_price is not None:
        return round_money(sku.cost_price)
    if product.cost_price is not None:
        return round_money(product.cost_price)
    return None


def _upsert_stock_location(
    db: Session,
    *,
    location_id: int,
    sku_id: int,
    qty: Decimal,
    status: str,
) -> StockLocation:
    row = db.scalar(
        select(StockLocation).where(
            StockLocation.location_id == location_id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == status,
        )
    )
    if row is None:
        row = StockLocation(
            location_id=location_id,
            product_sku_id=sku_id,
            qty=qty,
            status=status,
        )
        db.add(row)
    else:
        row.qty += qty
    db.flush()
    return row


def stock_flow_dict(db: Session, flow: StockFlow) -> dict:
    product = db.get(Product, flow.product_id)
    sku = db.get(ProductSku, flow.sku_id) if flow.sku_id else None
    warehouse = db.get(Warehouse, flow.warehouse_id)
    location = db.get(Location, flow.location_id) if flow.location_id else None
    unit = db.get(Unit, flow.unit_id) if flow.unit_id else None
    return {
        "id": flow.id,
        "warehouse_id": flow.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "product_id": flow.product_id,
        "product_code": product.code if product else None,
        "product_name": product.name if product else None,
        "product_image": product.image if product else None,
        "sku_id": flow.sku_id,
        "spec_code": sku.spec_code if sku else None,
        "spec_name": sku.spec_name if sku else None,
        "location_id": flow.location_id,
        "location_code": location.code if location else None,
        "unit_id": flow.unit_id,
        "unit_name": unit.name if unit else None,
        "direction": flow.direction,
        "biz_type": flow.biz_type,
        "qty": flow.qty,
        "cost_price": flow.cost_price,
        "total_cost": flow.total_cost,
        "source_no": flow.source_no,
        "order_no": flow.order_no,
        "flow_time": flow.flow_time,
        "remark": flow.remark,
        "created_at": flow.created_at,
    }


def list_stock_flows(
    db: Session,
    *,
    warehouse_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    location_id: int | None = None,
    direction: str | None = None,
    biz_type: str | None = None,
    source_no: str | None = None,
    order_no: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    if start_time is not None and start_time.tzinfo is not None:
        start_time = start_time.astimezone(_BUSINESS_TIMEZONE).replace(tzinfo=None)
    if end_time is not None and end_time.tzinfo is not None:
        end_time = end_time.astimezone(_BUSINESS_TIMEZONE).replace(tzinfo=None)
    if start_time is not None and end_time is not None and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if warehouse_id is not None:
        conditions.append(StockFlow.warehouse_id == warehouse_id)
    if product_id is not None:
        conditions.append(StockFlow.product_id == product_id)
    if sku_id is not None:
        conditions.append(StockFlow.sku_id == sku_id)
    if location_id is not None:
        conditions.append(StockFlow.location_id == location_id)
    if direction:
        conditions.append(StockFlow.direction == direction.upper())
    if biz_type:
        conditions.append(StockFlow.biz_type == biz_type.upper())
    if source_no:
        conditions.append(StockFlow.source_no.like(f"%{source_no.strip()}%"))
    if order_no:
        conditions.append(StockFlow.order_no.like(f"%{order_no.strip()}%"))
    if start_time is not None:
        conditions.append(StockFlow.flow_time >= start_time)
    if end_time is not None:
        conditions.append(StockFlow.flow_time <= end_time)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Product.code.like(like),
                Product.name.like(like),
                ProductSku.spec_code.like(like),
                ProductSku.spec_name.like(like),
            )
        )
    total_stmt = (
        select(func.count(StockFlow.id))
        .join(Product, Product.id == StockFlow.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockFlow.sku_id)
        .where(*conditions)
    )
    total = db.scalar(total_stmt) or 0
    rows = list(
        db.scalars(
            select(StockFlow)
            .join(Product, Product.id == StockFlow.product_id)
            .outerjoin(ProductSku, ProductSku.id == StockFlow.sku_id)
            .where(*conditions)
            .order_by(StockFlow.flow_time.desc(), StockFlow.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [stock_flow_dict(db, row) for row in rows],
    }


def _occupied_qty(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    sku_id: int,
) -> Decimal:
    outbound = db.scalar(
        select(func.coalesce(func.sum(StockOutItem.qty), 0))
        .join(StockOut, StockOut.id == StockOutItem.stock_out_id)
        .where(
            StockOut.warehouse_id == warehouse_id,
            StockOutItem.product_id == product_id,
            StockOutItem.sku_id == sku_id,
            StockOut.status.in_([STATUS_PENDING, STATUS_APPROVED]),
            StockOut.off_shelf_status != OFF_SHELF_DONE,
        )
    )
    transfer = db.scalar(
        select(func.coalesce(func.sum(TransferOutItem.qty), 0))
        .join(TransferOut, TransferOut.id == TransferOutItem.transfer_out_id)
        .where(
            TransferOut.warehouse_id == warehouse_id,
            TransferOutItem.product_id == product_id,
            TransferOutItem.sku_id == sku_id,
            TransferOut.status.in_([STATUS_PENDING, STATUS_APPROVED]),
        )
    )
    return Decimal(outbound or 0) + Decimal(transfer or 0)


def occupied_qty(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    sku_id: int,
) -> Decimal:
    """公开占用库存口径（待审核/已审核未完成下架的出库、调拨出库）。"""
    return _occupied_qty(
        db,
        warehouse_id=warehouse_id,
        product_id=product_id,
        sku_id=sku_id,
    )


def available_qty(
    db: Session,
    *,
    warehouse_id: int,
    product_id: int,
    sku_id: int,
    status: str = "NORMAL",
) -> Decimal:
    """仓库可用库存 = 指定库存状态总量 - 占用库存。"""
    total = db.scalar(
        select(func.coalesce(func.sum(StockLocation.qty), 0))
        .join(Location, Location.id == StockLocation.location_id)
        .where(
            Location.warehouse_id == warehouse_id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == status,
        )
    )
    occupied = occupied_qty(
        db,
        warehouse_id=warehouse_id,
        product_id=product_id,
        sku_id=sku_id,
    )
    return Decimal(total or 0) - occupied


def list_stock_summary(
    db: Session,
    *,
    warehouse_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    keyword: str | None = None,
    below_warning: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if warehouse_id is not None:
        conditions.append(StockLocation.location_id.in_(
            select(Location.id).where(Location.warehouse_id == warehouse_id)
        ))
    if product_id is not None:
        conditions.append(ProductSku.product_id == product_id)
    if sku_id is not None:
        conditions.append(StockLocation.product_sku_id == sku_id)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Product.code.like(like),
                Product.name.like(like),
                ProductSku.spec_code.like(like),
                ProductSku.spec_name.like(like),
            )
        )

    base = (
        select(
            Location.warehouse_id.label("warehouse_id"),
            Product.id.label("product_id"),
            ProductSku.id.label("sku_id"),
            func.sum(StockLocation.qty).label("total_qty"),
            func.sum(
                case(
                    (StockLocation.status == "NORMAL", StockLocation.qty),
                    else_=0,
                )
            ).label("normal_qty"),
            func.sum(
                case(
                    (StockLocation.status == "DEFECTIVE", StockLocation.qty),
                    else_=0,
                )
            ).label("defective_qty"),
        )
        .join(Location, Location.id == StockLocation.location_id)
        .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
        .join(Product, Product.id == ProductSku.product_id)
        .where(*conditions)
        .group_by(Location.warehouse_id, Product.id, ProductSku.id)
    )
    rows = list(
        db.execute(
            base.order_by(
                Location.warehouse_id,
                Product.id,
                ProductSku.id,
            )
        )
    )

    result: list[dict] = []
    warning_rows = {
        (row.product_id, row.sku_id): row
        for row in db.scalars(select(StockWarning))
    }
    for row in rows:
        warehouse = db.get(Warehouse, row.warehouse_id)
        product = db.get(Product, row.product_id)
        sku = db.get(ProductSku, row.sku_id)
        total_qty = Decimal(row.total_qty or 0)
        occupied_qty = _occupied_qty(
            db,
            warehouse_id=row.warehouse_id,
            product_id=row.product_id,
            sku_id=row.sku_id,
        )
        available_qty = total_qty - occupied_qty
        shortage_qty = max(occupied_qty - total_qty, ZERO_QTY)
        warning = warning_rows.get((row.product_id, row.sku_id))
        warn_qty = (
            Decimal(warning.warn_qty)
            if warning is not None
            else Decimal(product.warn_stock or 0)
        )
        is_below = total_qty < warn_qty
        cost_price = None
        if sku is not None and sku.cost_price is not None:
            cost_price = round_money(sku.cost_price)
        elif product is not None and product.cost_price is not None:
            cost_price = round_money(product.cost_price)
        result.append(
            {
                "warehouse_id": row.warehouse_id,
                "warehouse_name": warehouse.name if warehouse else None,
                "product_id": row.product_id,
                "product_code": product.code if product else None,
                "product_name": product.name if product else None,
                "product_image": product.image if product else None,
                "sku_id": row.sku_id,
                "spec_code": sku.spec_code if sku else None,
                "spec_name": sku.spec_name if sku else None,
                "normal_qty": Decimal(row.normal_qty or 0),
                "defective_qty": Decimal(row.defective_qty or 0),
                "total_qty": total_qty,
                "occupied_qty": occupied_qty,
                "available_qty": available_qty,
                "shortage_qty": shortage_qty,
                "warn_qty": warn_qty,
                "is_below_warning": is_below,
                "cost_price": cost_price,
                "stock_cost": (
                    round_money(total_qty * cost_price)
                    if cost_price is not None
                    else None
                ),
            }
        )

    if below_warning is not None:
        result = [
            item
            for item in result
            if item["is_below_warning"] == below_warning
        ]
    total = len(result)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": result[start : start + page_size],
    }


def list_stock_locations(
    db: Session,
    *,
    warehouse_id: int | None = None,
    location_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
) -> list[dict]:
    conditions = []
    if warehouse_id is not None:
        conditions.append(Location.warehouse_id == warehouse_id)
    if location_id is not None:
        conditions.append(Location.id == location_id)
    if product_id is not None:
        conditions.append(ProductSku.product_id == product_id)
    if sku_id is not None:
        conditions.append(StockLocation.product_sku_id == sku_id)
    rows = list(
        db.execute(
            select(StockLocation, Location, Product, ProductSku)
            .join(Location, Location.id == StockLocation.location_id)
            .join(ProductSku, ProductSku.id == StockLocation.product_sku_id)
            .join(Product, Product.id == ProductSku.product_id)
            .where(*conditions)
            .order_by(
                Location.warehouse_id,
                Product.id,
                ProductSku.id,
                Location.priority.desc(),
                Location.sort_order,
                Location.code,
            )
        )
    )
    return [
        {
            "stock_location_id": stock_row.id,
            "warehouse_id": location.warehouse_id,
            "warehouse_name": db.get(Warehouse, location.warehouse_id).name,
            "location_id": location.id,
            "location_code": location.code,
            "location_capacity": location.capacity,
            "product_id": product.id,
            "product_code": product.code,
            "product_name": product.name,
            "sku_id": sku.id,
            "spec_code": sku.spec_code,
            "spec_name": sku.spec_name,
            "status": stock_row.status,
            "qty": stock_row.qty,
        }
        for stock_row, location, product, sku in rows
    ]


def manual_in(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockFlow:
    _check_unknown_fields(
        data,
        {
            "warehouse_id",
            "location_id",
            "product_id",
            "sku_id",
            "qty",
            "cost_price",
            "remark",
        },
    )
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, _to_int_id(data.get("warehouse_id"), "仓库")
    )
    location = _get_location_or_404(
        db,
        warehouse_id=warehouse.id,
        location_id=_to_int_id(data.get("location_id"), "库位"),
        label="上架",
    )
    product = _get_product_or_404(
        db, _to_int_id(data.get("product_id"), "商品")
    )
    sku = _get_sku_or_404(
        db,
        product_id=product.id,
        sku_id=_to_int_id(data.get("sku_id"), "商品规格"),
    )
    qty = _to_qty(data.get("qty"), "入库数量")
    cost_price = _resolve_cost(
        product=product,
        sku=sku,
        explicit=data.get("cost_price"),
    )
    warehouse_service.ensure_location_capacity(
        db,
        location.id,
        added_qty=qty,
    )
    _upsert_stock_location(
        db,
        location_id=location.id,
        sku_id=sku.id,
        qty=qty,
        status=_stock_status(warehouse),
    )
    flow = StockFlow(
        warehouse_id=warehouse.id,
        product_id=product.id,
        sku_id=sku.id,
        location_id=location.id,
        unit_id=product.main_unit_id,
        direction="IN",
        biz_type="MANUAL_IN",
        qty=qty,
        cost_price=cost_price,
        total_cost=(
            round_money(qty * cost_price)
            if cost_price is not None
            else None
        ),
        source_no=None,
        flow_time=_now(),
        remark=_optional_text(data.get("remark"), "备注", 255) or "手动入库",
    )
    db.add(flow)
    db.flush()
    write_audit(
        db,
        action="MANUAL_IN",
        target_type="stock",
        target_id=str(location.id),
        summary=f"手动入库：{sku.spec_code} +{qty}",
        detail={
            "warehouse_id": warehouse.id,
            "location_id": location.id,
            "product_id": product.id,
            "sku_id": sku.id,
            "qty": str(qty),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return flow


def manual_out(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockFlow:
    _check_unknown_fields(
        data,
        {
            "warehouse_id",
            "location_id",
            "product_id",
            "sku_id",
            "qty",
            "remark",
        },
    )
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, _to_int_id(data.get("warehouse_id"), "仓库")
    )
    location = _get_location_or_404(
        db,
        warehouse_id=warehouse.id,
        location_id=_to_int_id(data.get("location_id"), "库位"),
        label="下架",
    )
    product = _get_product_or_404(
        db, _to_int_id(data.get("product_id"), "商品")
    )
    sku = _get_sku_or_404(
        db,
        product_id=product.id,
        sku_id=_to_int_id(data.get("sku_id"), "商品规格"),
    )
    qty = _to_qty(data.get("qty"), "出库数量")
    stock_status = _stock_status(warehouse)
    stock_row = db.scalar(
        select(StockLocation)
        .where(
            StockLocation.location_id == location.id,
            StockLocation.product_sku_id == sku.id,
            StockLocation.status == stock_status,
        )
        .with_for_update()
    )
    if stock_row is None or stock_row.qty < qty:
        raise BizException(ErrorCode.CONFLICT, "库位库存不足，不能手动出库")
    stock_row.qty -= qty
    cost_price = _resolve_cost(product=product, sku=sku)
    flow = StockFlow(
        warehouse_id=warehouse.id,
        product_id=product.id,
        sku_id=sku.id,
        location_id=location.id,
        unit_id=product.main_unit_id,
        direction="OUT",
        biz_type="MANUAL_OUT",
        qty=qty,
        cost_price=cost_price,
        total_cost=(
            round_money(qty * cost_price)
            if cost_price is not None
            else None
        ),
        flow_time=_now(),
        remark=_optional_text(data.get("remark"), "备注", 255) or "手动出库",
    )
    db.add(flow)
    db.flush()
    write_audit(
        db,
        action="MANUAL_OUT",
        target_type="stock",
        target_id=str(location.id),
        summary=f"手动出库：{sku.spec_code} -{qty}",
        detail={
            "warehouse_id": warehouse.id,
            "location_id": location.id,
            "product_id": product.id,
            "sku_id": sku.id,
            "qty": str(qty),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return flow


def set_location_stock(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockFlow | None:
    _check_unknown_fields(
        data,
        {
            "warehouse_id",
            "location_id",
            "product_id",
            "sku_id",
            "target_qty",
            "cost_price",
            "remark",
        },
    )
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, _to_int_id(data.get("warehouse_id"), "仓库")
    )
    location = _get_location_or_404(
        db,
        warehouse_id=warehouse.id,
        location_id=_to_int_id(data.get("location_id"), "库位"),
        label="调整",
    )
    product = _get_product_or_404(
        db, _to_int_id(data.get("product_id"), "商品")
    )
    sku = _get_sku_or_404(
        db,
        product_id=product.id,
        sku_id=_to_int_id(data.get("sku_id"), "商品规格"),
    )
    target_qty = _to_qty(
        data.get("target_qty"),
        "目标库存",
        allow_zero=True,
    )
    stock_status = _stock_status(warehouse)
    stock_row = db.scalar(
        select(StockLocation)
        .where(
            StockLocation.location_id == location.id,
            StockLocation.product_sku_id == sku.id,
            StockLocation.status == stock_status,
        )
        .with_for_update()
    )
    current_qty = Decimal(stock_row.qty) if stock_row is not None else ZERO_QTY
    delta = target_qty - current_qty
    if delta > ZERO_QTY:
        warehouse_service.ensure_location_capacity(
            db,
            location.id,
            added_qty=delta,
        )
    if delta == ZERO_QTY:
        write_audit(
            db,
            action="SET_STOCK",
            target_type="stock",
            target_id=str(location.id),
            summary=f"库存无变化：{sku.spec_code} {target_qty}",
            detail={"qty": str(target_qty)},
            actor_id=staff.id,
            actor_name=staff.name,
        )
        _commit(db, commit)
        return None
    if stock_row is None:
        stock_row = StockLocation(
            location_id=location.id,
            product_sku_id=sku.id,
            qty=ZERO_QTY,
            status=stock_status,
        )
        db.add(stock_row)
    stock_row.qty = target_qty
    cost_price = _resolve_cost(
        product=product,
        sku=sku,
        explicit=data.get("cost_price"),
    )
    flow = StockFlow(
        warehouse_id=warehouse.id,
        product_id=product.id,
        sku_id=sku.id,
        location_id=location.id,
        unit_id=product.main_unit_id,
        direction="IN" if delta > ZERO_QTY else "OUT",
        biz_type="ADJUST_IN" if delta > ZERO_QTY else "ADJUST_OUT",
        qty=abs(delta),
        cost_price=cost_price,
        total_cost=(
            round_money(abs(delta) * cost_price)
            if cost_price is not None
            else None
        ),
        flow_time=_now(),
        remark=(
            _optional_text(data.get("remark"), "备注", 255)
            or f"库存微调至 {target_qty}"
        ),
    )
    db.add(flow)
    db.flush()
    write_audit(
        db,
        action="SET_STOCK",
        target_type="stock",
        target_id=str(location.id),
        summary=f"库存微调：{sku.spec_code} {current_qty}->{target_qty}",
        detail={
            "warehouse_id": warehouse.id,
            "location_id": location.id,
            "product_id": product.id,
            "sku_id": sku.id,
            "before_qty": str(current_qty),
            "after_qty": str(target_qty),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return flow


def _warning_rows(db: Session) -> list[dict]:
    conditions = []
    rows = list(
        db.execute(
            select(
                Product.id.label("product_id"),
                Product.code,
                Product.name,
                Product.warn_stock,
                ProductSku.id.label("sku_id"),
                ProductSku.spec_code,
                ProductSku.spec_name,
                func.coalesce(func.sum(StockLocation.qty), 0).label("current_qty"),
            )
            .join(ProductSku, ProductSku.product_id == Product.id)
            .outerjoin(
                StockLocation,
                StockLocation.product_sku_id == ProductSku.id,
            )
            .where(*conditions)
            .group_by(
                Product.id,
                Product.code,
                Product.name,
                Product.warn_stock,
                ProductSku.id,
                ProductSku.spec_code,
                ProductSku.spec_name,
            )
            .order_by(Product.id, ProductSku.id)
        )
    )
    result = []
    for row in rows:
        current_qty = Decimal(row.current_qty or 0)
        warn_qty = Decimal(row.warn_stock or 0)
        result.append(
            {
                "product_id": row.product_id,
                "product_code": row.code,
                "product_name": row.name,
                "sku_id": row.sku_id,
                "spec_code": row.spec_code,
                "spec_name": row.spec_name,
                "current_qty": current_qty,
                "warn_qty": warn_qty,
                "is_below_warning": current_qty < warn_qty,
            }
        )
    return result


def list_stock_warnings(
    db: Session,
    *,
    only_below: bool = False,
) -> list[dict]:
    rows = _warning_rows(db)
    if only_below:
        rows = [row for row in rows if row["is_below_warning"]]
    return rows


def refresh_stock_warnings(
    db: Session,
    *,
    staff: Staff,
    commit: bool = True,
) -> list[dict]:
    rows = _warning_rows(db)
    existing = {
        (row.product_id, row.sku_id): row
        for row in db.scalars(select(StockWarning))
    }
    for item in rows:
        row = existing.get((item["product_id"], item["sku_id"]))
        if row is None:
            row = StockWarning(
                product_id=item["product_id"],
                sku_id=item["sku_id"],
                current_qty=item["current_qty"],
                warn_qty=item["warn_qty"],
            )
            db.add(row)
        else:
            row.current_qty = item["current_qty"]
            row.warn_qty = item["warn_qty"]
    db.flush()
    write_audit(
        db,
        action="REFRESH",
        target_type="stock_warning",
        target_id=None,
        summary=f"刷新库存预警：{len(rows)} 条",
        detail={
            "below_count": sum(
                1 for item in rows if item["is_below_warning"]
            )
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return rows


def _cost_rows(db: Session) -> list[dict]:
    rows = list(
        db.execute(
            select(
                Product.id.label("product_id"),
                Product.code,
                Product.name,
                Product.cost_price.label("product_cost_price"),
                ProductSku.id.label("sku_id"),
                ProductSku.spec_code,
                ProductSku.spec_name,
                ProductSku.cost_price.label("sku_cost_price"),
                func.coalesce(func.sum(StockLocation.qty), 0).label("qty"),
            )
            .join(ProductSku, ProductSku.product_id == Product.id)
            .outerjoin(
                StockLocation,
                StockLocation.product_sku_id == ProductSku.id,
            )
            .group_by(
                Product.id,
                Product.code,
                Product.name,
                Product.cost_price,
                ProductSku.id,
                ProductSku.spec_code,
                ProductSku.spec_name,
                ProductSku.cost_price,
            )
            .order_by(Product.id, ProductSku.id)
        )
    )
    result = []
    for row in rows:
        qty = Decimal(row.qty or 0)
        raw_cost = (
            row.sku_cost_price
            if row.sku_cost_price is not None
            else row.product_cost_price
        )
        cost_price = round_money(raw_cost) if raw_cost is not None else None
        result.append(
            {
                "product_id": row.product_id,
                "product_code": row.code,
                "product_name": row.name,
                "sku_id": row.sku_id,
                "spec_code": row.spec_code,
                "spec_name": row.spec_name,
                "cost_price": cost_price,
                "qty": qty,
                "total_cost": (
                    round_money(qty * cost_price)
                    if cost_price is not None
                    else None
                ),
            }
        )
    return result


def list_stock_costs(db: Session) -> list[dict]:
    return _cost_rows(db)


def refresh_stock_costs(
    db: Session,
    *,
    staff: Staff,
    commit: bool = True,
) -> list[dict]:
    rows = _cost_rows(db)
    existing = {
        (row.product_id, row.sku_id): row
        for row in db.scalars(select(StockCost))
    }
    for item in rows:
        row = existing.get((item["product_id"], item["sku_id"]))
        if row is None:
            row = StockCost(
                product_id=item["product_id"],
                sku_id=item["sku_id"],
                cost_price=item["cost_price"],
                qty=item["qty"],
                total_cost=item["total_cost"],
            )
            db.add(row)
        else:
            row.cost_price = item["cost_price"]
            row.qty = item["qty"]
            row.total_cost = item["total_cost"]
    db.flush()
    write_audit(
        db,
        action="REFRESH",
        target_type="stock_cost",
        target_id=None,
        summary=f"刷新库存成本：{len(rows)} 条",
        detail={},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return rows


def _location_adjust_items(
    db: Session, adjust_id: int
) -> list[LocationAdjustItem]:
    return list(
        db.scalars(
            select(LocationAdjustItem)
            .where(LocationAdjustItem.adjust_id == adjust_id)
            .order_by(LocationAdjustItem.id)
        )
    )


def _location_adjust_item_dict(
    db: Session, item: LocationAdjustItem
) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    from_location = db.get(Location, item.from_location_id)
    to_location = db.get(Location, item.to_location_id)
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_code": product.code if product else None,
        "product_name": product.name if product else None,
        "sku_id": item.sku_id,
        "spec_code": sku.spec_code if sku else None,
        "spec_name": sku.spec_name if sku else None,
        "from_location_id": item.from_location_id,
        "from_location_code": (
            from_location.code if from_location else None
        ),
        "to_location_id": item.to_location_id,
        "to_location_code": to_location.code if to_location else None,
        "qty": item.qty,
    }


def get_location_adjust_or_404(
    db: Session, adjust_id: int
) -> LocationAdjust:
    row = db.get(LocationAdjust, adjust_id)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "库位调整单不存在")
    return row


def location_adjust_dict(
    db: Session,
    record: LocationAdjust,
    *,
    with_items: bool = True,
    with_approval: bool = False,
) -> dict:
    warehouse = db.get(Warehouse, record.warehouse_id)
    creator = db.get(Staff, record.created_by) if record.created_by else None
    auditor = db.get(Staff, record.auditor_id) if record.auditor_id else None
    result = {
        "id": record.id,
        "adjust_no": record.adjust_no,
        "warehouse_id": record.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "status": record.status,
        "audit_status": record.audit_status,
        "remark": record.remark,
        "created_by": record.created_by,
        "created_by_name": creator.name if creator else None,
        "auditor_id": record.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": record.audit_time,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }
    if with_items:
        result["items"] = [
            _location_adjust_item_dict(db, item)
            for item in _location_adjust_items(db, record.id)
        ]
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=ADJUST_DOC_TYPE,
            doc_id=record.id,
        )
    return result


def list_location_adjusts(
    db: Session,
    *,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if warehouse_id is not None:
        conditions.append(LocationAdjust.warehouse_id == warehouse_id)
    if status:
        conditions.append(LocationAdjust.status == status.upper())
    if audit_status:
        conditions.append(LocationAdjust.audit_status == audit_status.upper())
    if keyword:
        conditions.append(
            LocationAdjust.adjust_no.like(f"%{keyword.strip()}%")
        )
    total = db.scalar(
        select(func.count(LocationAdjust.id)).where(*conditions)
    ) or 0
    rows = list(
        db.scalars(
            select(LocationAdjust)
            .where(*conditions)
            .order_by(LocationAdjust.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [location_adjust_dict(db, row) for row in rows],
    }


def _validate_adjust_stock(
    db: Session,
    *,
    warehouse: Warehouse,
    items: list[dict[str, Any]],
    lock_sources: bool = False,
) -> dict[tuple[int, int], StockLocation]:
    """按库位与 SKU 聚合校验调出库存和库位净容量变化。

    库位容量按库位内全部 SKU 的总数量计算，因此容量校验必须使用同一
    调入库位下的汇总数量，不能逐条明细单独判断。
    """

    stock_status = _stock_status(warehouse)
    source_required: dict[tuple[int, int], Decimal] = {}
    source_removed_by_location: dict[int, Decimal] = {}
    target_added_by_location: dict[int, Decimal] = {}
    location_ids: set[int] = set()

    for item in items:
        from_location_id = int(item["from_location_id"])
        to_location_id = int(item["to_location_id"])
        sku_id = int(item["sku_id"])
        qty = Decimal(item["qty"])
        location_ids.update({from_location_id, to_location_id})
        source_key = (from_location_id, sku_id)
        source_required[source_key] = (
            source_required.get(source_key, ZERO_QTY) + qty
        )
        source_removed_by_location[from_location_id] = (
            source_removed_by_location.get(from_location_id, ZERO_QTY) + qty
        )
        target_added_by_location[to_location_id] = (
            target_added_by_location.get(to_location_id, ZERO_QTY) + qty
        )

    for location_id in location_ids:
        _get_location_or_404(
            db,
            warehouse_id=warehouse.id,
            location_id=location_id,
            label="调整",
        )

    source_rows: dict[tuple[int, int], StockLocation] = {}
    for (location_id, sku_id), qty in sorted(source_required.items()):
        stmt = select(StockLocation).where(
            StockLocation.location_id == location_id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == stock_status,
        )
        if lock_sources:
            stmt = stmt.with_for_update()
        stock_row = db.scalar(stmt)
        if stock_row is None or stock_row.qty < qty:
            location = db.get(Location, location_id)
            raise BizException(
                ErrorCode.CONFLICT,
                f"调出库位库存不足：{location.code if location else location_id}",
            )
        source_rows[(location_id, sku_id)] = stock_row

    for location_id in location_ids:
        net_delta = target_added_by_location.get(
            location_id, ZERO_QTY
        ) - source_removed_by_location.get(location_id, ZERO_QTY)
        if net_delta <= ZERO_QTY:
            continue
        location = db.get(Location, location_id)
        state = warehouse_service.location_capacity_state(db, location_id)
        if location.capacity and state["used_qty"] + net_delta > Decimal(
            location.capacity
        ):
            raise BizException(
                ErrorCode.CONFLICT,
                f"调入库位容量不足：{location.code}",
            )

    return source_rows


def _normalize_adjust_items(
    db: Session,
    *,
    warehouse: Warehouse,
    raw_items: Any,
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少添加一条库位调整明细")
    result: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "库位调整明细格式错误")
        _check_unknown_fields(raw, ADJUST_ITEM_FIELDS)
        product = _get_product_or_404(
            db, _to_int_id(raw.get("product_id"), "商品")
        )
        sku = _get_sku_or_404(
            db,
            product_id=product.id,
            sku_id=_to_int_id(raw.get("sku_id"), "商品规格"),
        )
        from_location = _get_location_or_404(
            db,
            warehouse_id=warehouse.id,
            location_id=_to_int_id(raw.get("from_location_id"), "调出"),
            label="调出",
        )
        to_location = _get_location_or_404(
            db,
            warehouse_id=warehouse.id,
            location_id=_to_int_id(raw.get("to_location_id"), "调入"),
            label="调入",
        )
        if from_location.id == to_location.id:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "调出库位和调入库位不能相同",
            )
        qty = _to_qty(raw.get("qty"), "调整数量")
        result.append(
            {
                "product_id": product.id,
                "sku_id": sku.id,
                "from_location_id": from_location.id,
                "to_location_id": to_location.id,
                "qty": qty,
            }
        )

    _validate_adjust_stock(db, warehouse=warehouse, items=result)
    return result


def _approval_state(db: Session, adjust_id: int) -> dict:
    return approval_service.get_state(
        db,
        doc_type=ADJUST_DOC_TYPE,
        doc_id=adjust_id,
    )["state"]


def _ensure_editable(db: Session, record: LocationAdjust) -> None:
    if record.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前库位调整单状态不允许修改")
    if (
        _approval_state(db, record.id)["status"]
        not in {"NOT_SUBMITTED", "REJECTED"}
    ):
        raise BizException(
            ErrorCode.CONFLICT,
            "库位调整单已进入审批流程，不能修改",
        )


def create_location_adjust(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> LocationAdjust:
    _check_unknown_fields(data, ADJUST_FIELDS)
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db,
        _to_int_id(data.get("warehouse_id"), "调整仓库"),
    )
    items = _normalize_adjust_items(
        db,
        warehouse=warehouse,
        raw_items=data.get("items"),
    )
    record = LocationAdjust(
        adjust_no=generate_serial(SerialPrefix.LOCATION_ADJUST, dt=_now()),
        warehouse_id=warehouse.id,
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        remark=_optional_text(data.get("remark"), "备注", 255),
        created_by=staff.id,
    )
    db.add(record)
    db.flush()
    for item in items:
        db.add(LocationAdjustItem(adjust_id=record.id, **item))
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=ADJUST_DOC_TYPE,
        target_id=str(record.id),
        summary=f"新增库位调整单：{record.adjust_no}",
        detail={"warehouse_id": warehouse.id, "item_count": len(items)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def update_location_adjust(
    db: Session,
    *,
    adjust_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> LocationAdjust:
    _check_unknown_fields(data, ADJUST_FIELDS)
    record = get_location_adjust_or_404(db, adjust_id)
    _ensure_editable(db, record)
    warehouse_id = (
        _to_int_id(data.get("warehouse_id"), "调整仓库")
        if "warehouse_id" in data
        else record.warehouse_id
    )
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, warehouse_id
    )
    record.warehouse_id = warehouse.id
    if "remark" in data:
        record.remark = _optional_text(data["remark"], "备注", 255)
    if "items" in data:
        items = _normalize_adjust_items(
            db,
            warehouse=warehouse,
            raw_items=data["items"],
        )
        db.execute(
            delete(LocationAdjustItem).where(
                LocationAdjustItem.adjust_id == record.id
            )
        )
        db.flush()
        for item in items:
            db.add(LocationAdjustItem(adjust_id=record.id, **item))
        db.flush()
    else:
        existing = [
            {
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "from_location_id": item.from_location_id,
                "to_location_id": item.to_location_id,
                "qty": item.qty,
            }
            for item in _location_adjust_items(db, record.id)
        ]
        _normalize_adjust_items(
            db,
            warehouse=warehouse,
            raw_items=existing,
        )
    write_audit(
        db,
        action="UPDATE",
        target_type=ADJUST_DOC_TYPE,
        target_id=str(record.id),
        summary=f"修改库位调整单：{record.adjust_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_location_adjust(
    db: Session,
    *,
    adjust_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    record = get_location_adjust_or_404(db, adjust_id)
    _ensure_editable(db, record)
    for item in _location_adjust_items(db, record.id):
        db.delete(item)
    adjust_no = record.adjust_no
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=ADJUST_DOC_TYPE,
        target_id=str(record.id),
        summary=f"删除库位调整单：{adjust_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(record: LocationAdjust, state: dict) -> None:
    if state["status"] == "APPROVED":
        record.audit_status = AUDIT_APPROVED
    elif state["status"] == "REJECTED":
        record.audit_status = AUDIT_REJECTED
    else:
        record.audit_status = AUDIT_PENDING


def submit_location_adjust(
    db: Session,
    *,
    adjust_id: int,
    staff: Staff,
    commit: bool = True,
) -> LocationAdjust:
    record = get_location_adjust_or_404(db, adjust_id)
    _ensure_editable(db, record)
    if not _location_adjust_items(db, record.id):
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "库位调整单没有商品明细，不能提交",
        )
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, record.warehouse_id
    )
    existing = [
        {
            "product_id": item.product_id,
            "sku_id": item.sku_id,
            "from_location_id": item.from_location_id,
            "to_location_id": item.to_location_id,
            "qty": item.qty,
        }
        for item in _location_adjust_items(db, record.id)
    ]
    _normalize_adjust_items(
        db,
        warehouse=warehouse,
        raw_items=existing,
    )
    state = approval_service.submit(
        db,
        doc_type=ADJUST_DOC_TYPE,
        doc_id=record.id,
        doc_no=record.adjust_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_PENDING
    record.auditor_id = None
    record.audit_time = None
    _commit(db, commit)
    return record


def _execute_location_adjust(
    db: Session,
    *,
    record: LocationAdjust,
    staff: Staff,
) -> None:
    warehouse = warehouse_service.ensure_warehouse_selectable(
        db, record.warehouse_id
    )
    items = _location_adjust_items(db, record.id)
    if not items:
        raise BizException(ErrorCode.CONFLICT, "库位调整单没有商品明细")
    stock_status = _stock_status(warehouse)
    normalized = [
        {
            "from_location_id": item.from_location_id,
            "to_location_id": item.to_location_id,
            "sku_id": item.sku_id,
            "qty": item.qty,
        }
        for item in items
    ]
    source_rows = _validate_adjust_stock(
        db,
        warehouse=warehouse,
        items=normalized,
        lock_sources=True,
    )
    for item in items:
        source = source_rows[(item.from_location_id, item.sku_id)]
        source.qty -= item.qty
        _upsert_stock_location(
            db,
            location_id=item.to_location_id,
            sku_id=item.sku_id,
            qty=item.qty,
            status=stock_status,
        )
        product = db.get(Product, item.product_id)
        sku = db.get(ProductSku, item.sku_id)
        cost_price = (
            sku.cost_price
            if sku is not None and sku.cost_price is not None
            else (product.cost_price if product is not None else None)
        )
        total_cost = (
            round_money(item.qty * cost_price)
            if cost_price is not None
            else None
        )
        for location_id, direction, biz_type in (
            (item.from_location_id, "OUT", "ADJUST_OUT"),
            (item.to_location_id, "IN", "ADJUST_IN"),
        ):
            db.add(
                StockFlow(
                    warehouse_id=warehouse.id,
                    product_id=item.product_id,
                    sku_id=item.sku_id,
                    location_id=location_id,
                    unit_id=product.main_unit_id if product else None,
                    direction=direction,
                    biz_type=biz_type,
                    qty=item.qty,
                    cost_price=cost_price,
                    total_cost=total_cost,
                    source_no=record.adjust_no,
                    flow_time=_now(),
                    remark=f"库位调整:{record.adjust_no}",
                )
            )
    db.flush()


def approve_location_adjust(
    db: Session,
    *,
    adjust_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> LocationAdjust:
    record = get_location_adjust_or_404(db, adjust_id)
    if record.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(
            ErrorCode.CONFLICT,
            "当前库位调整单状态不能审核",
        )
    try:
        state = approval_service.approve(
            db,
            doc_type=ADJUST_DOC_TYPE,
            doc_id=record.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(record, state)
        if state["status"] == "APPROVED":
            _execute_location_adjust(db, record=record, staff=staff)
            record.status = STATUS_APPROVED
            record.auditor_id = staff.id
            record.audit_time = _now()
            write_audit(
                db,
                action="APPROVE",
                target_type=ADJUST_DOC_TYPE,
                target_id=str(record.id),
                summary=f"库位调整审核完成：{record.adjust_no}",
                detail={"item_count": len(_location_adjust_items(db, record.id))},
                actor_id=staff.id,
                actor_name=staff.name,
            )
        else:
            record.status = STATUS_PENDING
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return record


def reject_location_adjust(
    db: Session,
    *,
    adjust_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> LocationAdjust:
    record = get_location_adjust_or_404(db, adjust_id)
    if record.status != STATUS_PENDING:
        raise BizException(
            ErrorCode.CONFLICT,
            "当前库位调整单状态不能审核",
        )
    state = approval_service.reject(
        db,
        doc_type=ADJUST_DOC_TYPE,
        doc_id=record.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_REJECTED
    record.auditor_id = staff.id
    record.audit_time = _now()
    _commit(db, commit)
    return record
