"""店铺库存与库存结存服务（TASK-035）。

口径说明：
- 仓库可用库存：仓库 ``NORMAL`` 库存总量减去占用库存（待审核/已审核未完成下架的
  出库、调拨出库），复用 ``stock_query_service`` 的公开口径。
- 店铺独享库存：店铺在指定仓库保留的库存额度。店铺可用库存取「独享额度」与「仓库
  可用库存」的较小值；未设置额度（0）的店铺不与仓库或其它店铺共享剩余库存，可用
  库存为 0。
- 库存结存：结存日期固定为月底，按 SKU 存档数量与成本；同一日期重复结存按覆盖处理。
- 浮动成本：启用后按「上次结存日（不含）到本次结存日/回写时点（含）」的入库流水加权
  平均计算；期间没有可用入库成本时回退规格成本价、商品成本价。
- 成本回写：结存后按结存成本回写窗口内出库明细、出库流水与盘盈盘亏流水成本，每条
  被调整的记录写入 ``stock_cost_rewrite_log`` 留痕（批次号 + 成本前后值 + 成本来源）。

业务确认（2026-09-23）：
- 结存数量按良品 + 次品合计统计，不单独只结存良品。
- 浮动成本按期间入库加权平均，与财务口径一致。
- 未设置独享额度的店铺不共享仓库剩余库存。
- 成本回写必须留痕，不允许只做覆盖。

待业务确认：
- 店铺库存「同步线上库存」在平台接口（TASK-065）落地前仅在本地记录同步时点与数量。
"""

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.org import Staff
from app.models.platform import Platform, Shop
from app.models.product import Product, ProductSku
from app.models.stock import (
    ShopStock,
    StockCostRewriteLog,
    StockFlow,
    StockOut,
    StockOutItem,
    StockSetting,
    StockSettlement,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import stock_query_service
from app.services.excel import (
    Column,
    ParseResult,
    SheetSpec,
    export_data,
    parse_data,
    register_template,
)

DOC_TYPE_SETTING = "stock_setting"
DOC_TYPE_SHOP_STOCK = "shop_stock"
DOC_TYPE_SETTLEMENT = "stock_settlement"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
ZERO_MONEY = Decimal("0.00")
_BUSINESS_TIMEZONE = ZoneInfo("Asia/Shanghai")

SETTING_FIELDS = {
    "sync_stock_default",
    "shop_stock_enabled",
    "floating_cost_enabled",
    "off_shelf_below_warning",
}

SHOP_STOCK_FIELDS = {
    "shop_id",
    "warehouse_id",
    "product_id",
    "sku_id",
    "exclusive_qty",
    "is_sync",
    "is_enabled",
    "effective_start",
    "effective_end",
    "remark",
}

SETTLEMENT_TEMPLATE = SheetSpec(
    name="stock_settlement",
    sheet_title="库存结存",
    columns=[
        Column("settlement_date", "结存日期", required=True, type="date", width=16),
        Column("product_code", "商品编码", required=True, width=18),
        Column("product_name", "商品名称", width=22),
        Column("spec_code", "规格编码", required=True, width=18),
        Column("spec_name", "规格名称", width=18),
        Column("qty", "结存数量", required=True, type="decimal", width=14),
        Column("cost_price", "结存成本", type="decimal", width=14),
    ],
)
register_template("stock_settlement", SETTLEMENT_TEMPLATE)


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


def _to_qty(value: Any, label: str, *, allow_zero: bool = True) -> Decimal:
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


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
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
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法时间")


def _month_end(year: int, month: int) -> date:
    if month == 12:
        return date(year, 12, 31)
    return date(year, month + 1, 1) - timedelta(days=1)


def _parse_month(value: Any) -> date:
    text = str(value).strip()
    try:
        year_text, month_text = text.split("-")
        year, month = int(year_text), int(month_text)
    except (ValueError, AttributeError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "月份格式必须为 YYYY-MM") from exc
    if not 1 <= month <= 12:
        raise BizException(ErrorCode.BAD_REQUEST, "月份必须在 1-12 之间")
    return _month_end(year, month)


def _resolve_settlement_date(settlement_date: Any = None, month: Any = None) -> date:
    if settlement_date not in (None, "") and month not in (None, ""):
        raise BizException(ErrorCode.BAD_REQUEST, "结存日期与月份只能二选一")
    if month not in (None, ""):
        return _parse_month(month)
    if settlement_date in (None, ""):
        today = _now().date()
        return _month_end(today.year, today.month)
    if isinstance(settlement_date, datetime):
        target = settlement_date.date()
    elif isinstance(settlement_date, date):
        target = settlement_date
    else:
        text = str(settlement_date).strip()
        try:
            target = datetime.strptime(text, "%Y-%m-%d").date()
        except ValueError as exc:
            raise BizException(
                ErrorCode.BAD_REQUEST, "结存日期格式必须为 YYYY-MM-DD"
            ) from exc
    if target != _month_end(target.year, target.month):
        raise BizException(ErrorCode.BAD_REQUEST, "结存日期必须为当月最后一天")
    return target


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    return product


def _get_sku_or_404(db: Session, *, product_id: int, sku_id: int) -> ProductSku:
    sku = db.get(ProductSku, sku_id)
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")
    return sku


# --------------------------------------------------------------------------- #
# 库存设置
# --------------------------------------------------------------------------- #


def read_stock_setting(db: Session, company_id: int) -> StockSetting:
    """纯读取：默认对象为 transient，不入会话、不 flush、不分配数据库 ID。"""
    row = db.scalar(select(StockSetting).where(StockSetting.company_id == company_id))
    if row is None:
        row = StockSetting(
            company_id=company_id,
            sync_stock_default=False,
            shop_stock_enabled=False,
            floating_cost_enabled=False,
            off_shelf_below_warning=False,
        )
    return row


def get_stock_setting(db: Session, company_id: int) -> StockSetting:
    """写流程专用：确保设置已持久化；GET 必须使用 read_stock_setting。"""
    row = read_stock_setting(db, company_id)
    if row.id is None:
        db.add(row)
        db.flush()
    return row


def stock_setting_dict(row: StockSetting) -> dict:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "sync_stock_default": row.sync_stock_default,
        "shop_stock_enabled": row.shop_stock_enabled,
        "floating_cost_enabled": row.floating_cost_enabled,
        "off_shelf_below_warning": row.off_shelf_below_warning,
        "updated_at": row.updated_at,
    }


def update_stock_setting(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockSetting:
    _check_unknown_fields(data, SETTING_FIELDS)
    row = get_stock_setting(db, staff.company_id)
    changed: dict[str, Any] = {}
    for field in sorted(SETTING_FIELDS):
        if field not in data:
            continue
        value = _to_bool(data[field], field)
        if getattr(row, field) != value:
            changed[field] = value
            setattr(row, field, value)
    if changed:
        write_audit(
            db,
            action="UPDATE",
            target_type=DOC_TYPE_SETTING,
            target_id=str(row.id),
            summary="更新库存设置",
            detail=changed,
            actor_id=staff.id,
            actor_name=staff.name,
        )
    _commit(db, commit)
    return row


# --------------------------------------------------------------------------- #
# 店铺独享库存
# --------------------------------------------------------------------------- #


def _is_effective(row: ShopStock, at: datetime | None = None) -> bool:
    if not row.is_enabled:
        return False
    moment = at or _now()
    if row.effective_start is not None and moment < row.effective_start:
        return False
    if row.effective_end is not None and moment > row.effective_end:
        return False
    return True


def _shop_available_qty(row: ShopStock, available: Decimal) -> Decimal:
    """店铺可用库存：只有配置了独享额度的店铺可用，未配置额度的店铺不共享仓库库存。"""
    exclusive = Decimal(row.exclusive_qty or 0)
    if exclusive <= ZERO_QTY or available <= ZERO_QTY:
        return ZERO_QTY
    return min(exclusive, available)


def shop_stock_dict(db: Session, row: ShopStock) -> dict:
    shop = db.get(Shop, row.shop_id)
    platform = db.get(Platform, shop.platform_id) if shop is not None else None
    warehouse = db.get(Warehouse, row.warehouse_id)
    product = db.get(Product, row.product_id)
    sku = db.get(ProductSku, row.sku_id)
    stock_qty = db.scalar(
        select(func.coalesce(func.sum(StockLocation.qty), 0))
        .join(Location, Location.id == StockLocation.location_id)
        .where(
            Location.warehouse_id == row.warehouse_id,
            StockLocation.product_sku_id == row.sku_id,
            StockLocation.status == "NORMAL",
        )
    )
    occupied = stock_query_service.occupied_qty(
        db,
        warehouse_id=row.warehouse_id,
        product_id=row.product_id,
        sku_id=row.sku_id,
    )
    available = Decimal(stock_qty or 0) - occupied
    shop_available = _shop_available_qty(row, available)
    return {
        "id": row.id,
        "shop_id": row.shop_id,
        "shop_name": shop.name if shop is not None else None,
        "platform_id": platform.id if platform is not None else None,
        "platform_name": platform.name if platform is not None else None,
        "shop_type": platform.name if platform is not None else None,
        "warehouse_id": row.warehouse_id,
        "warehouse_name": warehouse.name if warehouse is not None else None,
        "product_id": row.product_id,
        "product_code": product.code if product is not None else None,
        "product_name": product.name if product is not None else None,
        "sku_id": row.sku_id,
        "spec_code": sku.spec_code if sku is not None else None,
        "spec_name": sku.spec_name if sku is not None else None,
        "exclusive_qty": Decimal(row.exclusive_qty or 0),
        "stock_qty": Decimal(stock_qty or 0),
        "occupied_qty": occupied,
        "available_qty": available,
        "shop_available_qty": shop_available,
        "is_sync": row.is_sync,
        "is_enabled": row.is_enabled,
        "is_effective": _is_effective(row),
        "effective_start": row.effective_start,
        "effective_end": row.effective_end,
        "last_sync_qty": row.last_sync_qty,
        "last_sync_at": row.last_sync_at,
        "remark": row.remark,
        "created_by": row.created_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get_shop_stock_or_404(db: Session, shop_stock_id: int) -> ShopStock:
    row = db.get(ShopStock, shop_stock_id)
    if row is None:
        raise BizException(ErrorCode.NOT_FOUND, "店铺库存设置不存在")
    return row


def list_shop_stocks(
    db: Session,
    *,
    shop_id: int | None = None,
    warehouse_id: int | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    is_enabled: bool | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = []
    if shop_id is not None:
        conditions.append(ShopStock.shop_id == shop_id)
    if warehouse_id is not None:
        conditions.append(ShopStock.warehouse_id == warehouse_id)
    if product_id is not None:
        conditions.append(ShopStock.product_id == product_id)
    if sku_id is not None:
        conditions.append(ShopStock.sku_id == sku_id)
    if is_enabled is not None:
        conditions.append(ShopStock.is_enabled.is_(is_enabled))
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Shop.name.like(like),
                Product.code.like(like),
                Product.name.like(like),
                ProductSku.spec_code.like(like),
                ProductSku.spec_name.like(like),
            )
        )

    base = (
        select(ShopStock)
        .join(Shop, Shop.id == ShopStock.shop_id)
        .join(Product, Product.id == ShopStock.product_id)
        .join(ProductSku, ProductSku.id == ShopStock.sku_id)
        .where(*conditions)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = list(
        db.scalars(
            base.order_by(ShopStock.shop_id, ShopStock.warehouse_id, ShopStock.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "items": [shop_stock_dict(db, row) for row in rows],
    }


def _validate_shop_stock_relations(
    db: Session,
    *,
    shop_id: int,
    warehouse_id: int,
    product_id: int,
    sku_id: int,
) -> tuple[Shop, Warehouse, Product, ProductSku]:
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise BizException(ErrorCode.BAD_REQUEST, "店铺不存在")
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise BizException(ErrorCode.BAD_REQUEST, "仓库不存在")
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "仓库已停用")
    product = _get_product_or_404(db, product_id)
    sku = _get_sku_or_404(db, product_id=product_id, sku_id=sku_id)
    return shop, warehouse, product, sku


def _normalize_shop_stock(
    db: Session,
    *,
    data: dict,
    current: ShopStock | None = None,
) -> dict:
    def pick(field: str) -> Any:
        if field in data:
            return data[field]
        return getattr(current, field) if current is not None else None

    shop_id = _to_int_id(pick("shop_id"), "店铺")
    warehouse_id = _to_int_id(pick("warehouse_id"), "仓库")
    product_id = _to_int_id(pick("product_id"), "商品")
    sku_id = _to_int_id(pick("sku_id"), "商品规格")
    _validate_shop_stock_relations(
        db,
        shop_id=shop_id,
        warehouse_id=warehouse_id,
        product_id=product_id,
        sku_id=sku_id,
    )
    raw_exclusive = pick("exclusive_qty")
    exclusive_qty = _to_qty(
        ZERO_QTY if raw_exclusive in (None, "") else raw_exclusive,
        "店铺独享库存",
    )
    raw_sync = pick("is_sync")
    is_sync = _to_bool(False if raw_sync is None else raw_sync, "同步线上库存")
    raw_enabled = pick("is_enabled")
    is_enabled = _to_bool(True if raw_enabled is None else raw_enabled, "是否生效")
    effective_start = _optional_datetime(pick("effective_start"), "有效期开始时间")
    effective_end = _optional_datetime(pick("effective_end"), "有效期结束时间")
    if (
        effective_start is not None
        and effective_end is not None
        and effective_start > effective_end
    ):
        raise BizException(ErrorCode.BAD_REQUEST, "有效期开始时间不能晚于结束时间")
    return {
        "shop_id": shop_id,
        "warehouse_id": warehouse_id,
        "product_id": product_id,
        "sku_id": sku_id,
        "exclusive_qty": exclusive_qty,
        "is_sync": is_sync,
        "is_enabled": is_enabled,
        "effective_start": effective_start,
        "effective_end": effective_end,
        "remark": _optional_text(pick("remark"), "备注", 255),
    }


def _ensure_shop_stock_unique(
    db: Session,
    *,
    shop_id: int,
    warehouse_id: int,
    sku_id: int,
    exclude_id: int | None = None,
) -> None:
    stmt = select(ShopStock).where(
        ShopStock.shop_id == shop_id,
        ShopStock.warehouse_id == warehouse_id,
        ShopStock.sku_id == sku_id,
    )
    if exclude_id is not None:
        stmt = stmt.where(ShopStock.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(
            ErrorCode.CONFLICT, "该店铺在此仓库下已存在相同规格的库存设置"
        )


def create_shop_stock(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ShopStock:
    _check_unknown_fields(data, SHOP_STOCK_FIELDS)
    values = _normalize_shop_stock(db, data=data)
    _ensure_shop_stock_unique(
        db,
        shop_id=values["shop_id"],
        warehouse_id=values["warehouse_id"],
        sku_id=values["sku_id"],
    )
    row = ShopStock(**values, created_by=staff.id)
    db.add(row)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE_SHOP_STOCK,
        target_id=str(row.id),
        summary="新增店铺库存设置",
        detail={
            "shop_id": row.shop_id,
            "warehouse_id": row.warehouse_id,
            "sku_id": row.sku_id,
            "exclusive_qty": str(row.exclusive_qty),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return row


def update_shop_stock(
    db: Session,
    *,
    shop_stock_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> ShopStock:
    _check_unknown_fields(data, SHOP_STOCK_FIELDS)
    row = get_shop_stock_or_404(db, shop_stock_id)
    values = _normalize_shop_stock(db, data=data, current=row)
    _ensure_shop_stock_unique(
        db,
        shop_id=values["shop_id"],
        warehouse_id=values["warehouse_id"],
        sku_id=values["sku_id"],
        exclude_id=row.id,
    )
    for field, value in values.items():
        setattr(row, field, value)
    db.flush()
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE_SHOP_STOCK,
        target_id=str(row.id),
        summary="修改店铺库存设置",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return row


def delete_shop_stock(
    db: Session,
    *,
    shop_stock_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    row = get_shop_stock_or_404(db, shop_stock_id)
    detail = {
        "shop_id": row.shop_id,
        "warehouse_id": row.warehouse_id,
        "sku_id": row.sku_id,
    }
    db.delete(row)
    db.flush()
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE_SHOP_STOCK,
        target_id=str(shop_stock_id),
        summary="删除店铺库存设置",
        detail=detail,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def sync_shop_stock(
    db: Session,
    *,
    staff: Staff,
    shop_id: int | None = None,
    shop_stock_ids: list[int] | None = None,
    commit: bool = True,
) -> dict:
    """同步线上库存：按当前可用库存计算同步数量并记录同步时点。

    平台接口在 TASK-065 落地前，本方法只完成本地口径计算与同步留痕。
    """
    setting = get_stock_setting(db, staff.company_id)
    if not setting.shop_stock_enabled:
        raise BizException(
            ErrorCode.CONFLICT, "店铺库存设置未启用，请先在库存设置中开启"
        )
    stmt = select(ShopStock)
    if shop_stock_ids:
        stmt = stmt.where(ShopStock.id.in_(shop_stock_ids))
    if shop_id is not None:
        stmt = stmt.where(ShopStock.shop_id == shop_id)
    rows = list(db.scalars(stmt.order_by(ShopStock.id)))
    if not rows:
        raise BizException(ErrorCode.NOT_FOUND, "没有符合条件的店铺库存设置")

    moment = _now()
    synced: list[dict] = []
    skipped: list[dict] = []
    for row in rows:
        if not _is_effective(row, moment):
            skipped.append(
                {
                    "id": row.id,
                    "shop_id": row.shop_id,
                    "sku_id": row.sku_id,
                    "reason": "设置未生效或已过期",
                }
            )
            continue
        if not row.is_sync:
            skipped.append(
                {
                    "id": row.id,
                    "shop_id": row.shop_id,
                    "sku_id": row.sku_id,
                    "reason": "未开启同步线上库存",
                }
            )
            continue
        data = shop_stock_dict(db, row)
        row.last_sync_qty = data["shop_available_qty"]
        row.last_sync_at = moment
        synced.append(
            {
                "id": row.id,
                "shop_id": row.shop_id,
                "shop_name": data["shop_name"],
                "warehouse_id": row.warehouse_id,
                "sku_id": row.sku_id,
                "spec_code": data["spec_code"],
                "available_qty": data["available_qty"],
                "synced_qty": data["shop_available_qty"],
                "synced_at": moment,
            }
        )
    db.flush()
    write_audit(
        db,
        action="SYNC",
        target_type=DOC_TYPE_SHOP_STOCK,
        target_id=str(shop_id) if shop_id is not None else None,
        summary=f"同步线上库存：成功 {len(synced)} 条，跳过 {len(skipped)} 条",
        detail={"synced": len(synced), "skipped": len(skipped)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "synced_count": len(synced),
        "skipped_count": len(skipped),
        "synced": synced,
        "skipped": skipped,
    }


# --------------------------------------------------------------------------- #
# 库存结存与成本
# --------------------------------------------------------------------------- #


def _sku_stock_rows(db: Session) -> list[Any]:
    return list(
        db.execute(
            select(
                Product.id.label("product_id"),
                Product.code,
                Product.name,
                Product.cost_price.label("product_cost_price"),
                Product.use_float_cost,
                ProductSku.id.label("sku_id"),
                ProductSku.spec_code,
                ProductSku.spec_name,
                ProductSku.cost_price.label("sku_cost_price"),
                func.coalesce(func.sum(StockLocation.qty), 0).label("qty"),
            )
            .join(ProductSku, ProductSku.product_id == Product.id)
            .outerjoin(StockLocation, StockLocation.product_sku_id == ProductSku.id)
            .group_by(
                Product.id,
                Product.code,
                Product.name,
                Product.cost_price,
                Product.use_float_cost,
                ProductSku.id,
                ProductSku.spec_code,
                ProductSku.spec_name,
                ProductSku.cost_price,
            )
            .order_by(Product.id, ProductSku.id)
        )
    )


def _base_cost(row: Any) -> Decimal | None:
    raw = (
        row.sku_cost_price
        if row.sku_cost_price is not None
        else row.product_cost_price
    )
    return round_money(raw) if raw is not None else None


def _period_inbound_cost(
    db: Session,
    *,
    sku_id: int,
    start: datetime | None,
    end: datetime,
) -> Decimal | None:
    """期间入库加权平均成本；无可用入库记录时返回 None。"""
    conditions = [
        StockFlow.sku_id == sku_id,
        StockFlow.direction == "IN",
        StockFlow.qty > 0,
        StockFlow.cost_price.is_not(None),
        StockFlow.total_cost.is_not(None),
        StockFlow.flow_time.is_not(None),
        StockFlow.flow_time <= end,
    ]
    if start is not None:
        conditions.append(StockFlow.flow_time > start)
    qty = db.scalar(select(func.coalesce(func.sum(StockFlow.qty), 0)).where(*conditions))
    amount = db.scalar(
        select(func.coalesce(func.sum(StockFlow.total_cost), 0)).where(*conditions)
    )
    total_qty = Decimal(qty or 0)
    if total_qty <= ZERO_QTY:
        return None
    return round_money(Decimal(amount or 0) / total_qty)


def _last_settlement_date(
    db: Session, *, before: datetime | None = None
) -> datetime | None:
    stmt = select(func.max(StockSettlement.settlement_date))
    if before is not None:
        stmt = stmt.where(StockSettlement.settlement_date < before)
    return db.scalar(stmt)


def _settlement_cost_map(db: Session, settlement_date: datetime) -> dict[int, Decimal]:
    rows = db.scalars(
        select(StockSettlement).where(
            StockSettlement.settlement_date == settlement_date
        )
    )
    return {
        row.sku_id: round_money(row.cost_price)
        for row in rows
        if row.sku_id is not None and row.cost_price is not None
    }


def _resolve_cost(
    db: Session,
    *,
    row: Any,
    use_float: bool,
    start: datetime | None,
    end: datetime,
) -> Decimal | None:
    if use_float:
        cost = _period_inbound_cost(db, sku_id=row.sku_id, start=start, end=end)
        if cost is not None:
            return cost
    return _base_cost(row)


def create_settlement(
    db: Session,
    *,
    staff: Staff,
    settlement_date: Any = None,
    month: Any = None,
    commit: bool = True,
) -> dict:
    """月底结存：按 SKU 固定库存数量与成本，同一日期重复执行按覆盖处理。"""
    target_day = _resolve_settlement_date(settlement_date, month)
    target = datetime.combine(target_day, time.min)
    setting = get_stock_setting(db, staff.company_id)

    existing = list(
        db.scalars(
            select(StockSettlement).where(StockSettlement.settlement_date == target)
        )
    )
    for row in existing:
        db.delete(row)
    db.flush()

    start = _last_settlement_date(db, before=target)
    created = 0
    total_qty = ZERO_QTY
    total_cost = ZERO_MONEY
    for row in _sku_stock_rows(db):
        qty = Decimal(row.qty or 0)
        if qty == ZERO_QTY:
            continue
        cost = _resolve_cost(
            db,
            row=row,
            use_float=bool(setting.floating_cost_enabled or row.use_float_cost),
            start=start,
            end=target,
        )
        db.add(
            StockSettlement(
                settlement_date=target,
                product_id=row.product_id,
                sku_id=row.sku_id,
                qty=qty,
                cost_price=cost,
            )
        )
        created += 1
        total_qty += qty
        if cost is not None:
            total_cost += round_money(qty * cost)
    db.flush()
    write_audit(
        db,
        action="SETTLE",
        target_type=DOC_TYPE_SETTLEMENT,
        target_id=target_day.isoformat(),
        summary=f"执行库存结存：{target_day.isoformat()}，共 {created} 条",
        detail={
            "settlement_date": target_day.isoformat(),
            "item_count": created,
            "replaced": len(existing),
            "floating_cost": setting.floating_cost_enabled,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "settlement_date": target_day,
        "item_count": created,
        "total_qty": total_qty,
        "total_cost": total_cost,
        "replaced": len(existing),
    }


def _settlement_row_dict(db: Session, row: StockSettlement) -> dict:
    product = db.get(Product, row.product_id)
    sku = db.get(ProductSku, row.sku_id) if row.sku_id else None
    cost_price = round_money(row.cost_price) if row.cost_price is not None else None
    qty = Decimal(row.qty or 0)
    return {
        "id": row.id,
        "settlement_date": row.settlement_date,
        "product_id": row.product_id,
        "product_code": product.code if product is not None else None,
        "product_name": product.name if product is not None else None,
        "sku_id": row.sku_id,
        "spec_code": sku.spec_code if sku is not None else None,
        "spec_name": sku.spec_name if sku is not None else None,
        "qty": qty,
        "cost_price": cost_price,
        "total_cost": (
            round_money(qty * cost_price) if cost_price is not None else None
        ),
    }


def _settlement_conditions(
    *,
    settlement_date: date | None,
    product_id: int | None,
    sku_id: int | None,
    keyword: str | None,
) -> list[Any]:
    conditions: list[Any] = []
    if settlement_date is not None:
        conditions.append(
            StockSettlement.settlement_date
            == datetime.combine(settlement_date, time.min)
        )
    if product_id is not None:
        conditions.append(StockSettlement.product_id == product_id)
    if sku_id is not None:
        conditions.append(StockSettlement.sku_id == sku_id)
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
    return conditions


def list_settlements(
    db: Session,
    *,
    settlement_date: date | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = _settlement_conditions(
        settlement_date=settlement_date,
        product_id=product_id,
        sku_id=sku_id,
        keyword=keyword,
    )
    base = (
        select(StockSettlement)
        .join(Product, Product.id == StockSettlement.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockSettlement.sku_id)
        .where(*conditions)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = list(
        db.scalars(
            base.order_by(
                StockSettlement.settlement_date.desc(),
                StockSettlement.product_id,
                StockSettlement.sku_id,
            )
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "items": [_settlement_row_dict(db, row) for row in rows],
    }


def list_settlement_periods(db: Session) -> list[dict]:
    rows = db.execute(
        select(
            StockSettlement.settlement_date,
            func.count(StockSettlement.id).label("item_count"),
            func.coalesce(func.sum(StockSettlement.qty), 0).label("total_qty"),
            func.coalesce(
                func.sum(StockSettlement.qty * StockSettlement.cost_price), 0
            ).label("total_cost"),
        )
        .group_by(StockSettlement.settlement_date)
        .order_by(StockSettlement.settlement_date.desc())
    )
    return [
        {
            "settlement_date": (
                row.settlement_date.date()
                if isinstance(row.settlement_date, datetime)
                else row.settlement_date
            ),
            "item_count": int(row.item_count or 0),
            "total_qty": Decimal(row.total_qty or 0),
            "total_cost": round_money(row.total_cost or 0),
        }
        for row in rows
    ]


def export_settlements(
    db: Session,
    *,
    settlement_date: date | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    keyword: str | None = None,
) -> bytes:
    conditions = _settlement_conditions(
        settlement_date=settlement_date,
        product_id=product_id,
        sku_id=sku_id,
        keyword=keyword,
    )
    rows = list(
        db.scalars(
            select(StockSettlement)
            .join(Product, Product.id == StockSettlement.product_id)
            .outerjoin(ProductSku, ProductSku.id == StockSettlement.sku_id)
            .where(*conditions)
            .order_by(
                StockSettlement.settlement_date.desc(),
                StockSettlement.product_id,
                StockSettlement.sku_id,
            )
        )
    )
    export_rows = [
        {
            "settlement_date": item["settlement_date"].date()
            if isinstance(item["settlement_date"], datetime)
            else item["settlement_date"],
            "product_code": item["product_code"],
            "product_name": item["product_name"],
            "spec_code": item["spec_code"],
            "spec_name": item["spec_name"],
            "qty": item["qty"],
            "cost_price": item["cost_price"],
        }
        for item in (_settlement_row_dict(db, row) for row in rows)
    ]
    return export_data(SETTLEMENT_TEMPLATE, export_rows)


def _lookup_settlement_rows(
    db: Session,
    parsed: ParseResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors = list(parsed.errors)
    product_codes = {
        str(row.get("product_code") or "").strip() for row in parsed.rows
    }
    spec_codes = {str(row.get("spec_code") or "").strip() for row in parsed.rows}
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

    prepared: list[dict[str, Any]] = []
    for index, row in enumerate(parsed.rows, start=2):
        product_code = str(row.get("product_code") or "").strip()
        spec_code = str(row.get("spec_code") or "").strip()
        product = products.get(product_code)
        sku = skus.get(spec_code)
        if product is None:
            errors.append(
                {
                    "row": index,
                    "field": "product_code",
                    "message": f"商品编码不存在：{product_code}",
                }
            )
            continue
        if sku is None or sku.product_id != product.id:
            errors.append(
                {
                    "row": index,
                    "field": "spec_code",
                    "message": f"规格编码不存在或不属于该商品：{spec_code}",
                }
            )
            continue
        try:
            target_day = _resolve_settlement_date(row.get("settlement_date"))
        except BizException as exc:
            errors.append(
                {"row": index, "field": "settlement_date", "message": exc.message}
            )
            continue
        try:
            qty = _to_qty(row.get("qty"), "结存数量")
            raw_cost = row.get("cost_price")
            cost_price = (
                _to_money(raw_cost, "结存成本") if raw_cost is not None else None
            )
        except BizException as exc:
            errors.append({"row": index, "field": "qty", "message": exc.message})
            continue
        prepared.append(
            {
                "settlement_date": datetime.combine(target_day, time.min),
                "product_id": product.id,
                "sku_id": sku.id,
                "qty": qty,
                "cost_price": cost_price,
            }
        )
    return prepared, errors


def import_settlements(
    db: Session,
    *,
    file_bytes: bytes,
    staff: Staff,
    commit: bool = True,
) -> dict:
    parsed = parse_data(file_bytes, SETTLEMENT_TEMPLATE)
    prepared, errors = _lookup_settlement_rows(db, parsed)
    if errors:
        return {"created": 0, "updated": 0, "errors": errors}
    if not prepared:
        return {
            "created": 0,
            "updated": 0,
            "errors": [{"row": 0, "field": "", "message": "没有可导入的数据"}],
        }

    dates = {row["settlement_date"] for row in prepared}
    replaced = 0
    for target in dates:
        for row in list(
            db.scalars(
                select(StockSettlement).where(
                    StockSettlement.settlement_date == target
                )
            )
        ):
            db.delete(row)
            replaced += 1
    db.flush()

    created = 0
    for row in prepared:
        db.add(StockSettlement(**row))
        created += 1
    db.flush()
    write_audit(
        db,
        action="IMPORT",
        target_type=DOC_TYPE_SETTLEMENT,
        target_id=None,
        summary=f"导入库存结存：{created} 条",
        detail={
            "created": created,
            "replaced": replaced,
            "dates": sorted(target.date().isoformat() for target in dates),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {"created": created, "updated": replaced, "errors": []}


def rewrite_outbound_cost(
    db: Session,
    *,
    staff: Staff,
    settlement_date: Any = None,
    commit: bool = True,
) -> dict:
    """回写出库成本与盘盈盘亏成本。

    窗口为「上次结存日（不含）到本次结存日或当前时点（含）」。成本取值优先级：
    结存成本 → 浮动成本（期间入库加权平均）→ 规格成本价 → 商品成本价。
    每条被调整的记录同时写入 ``stock_cost_rewrite_log``，保留成本前后值与来源。
    """

    setting = get_stock_setting(db, staff.company_id)
    if settlement_date in (None, ""):
        end = _now()
        settlement_costs: dict[int, Decimal] = {}
    else:
        target_day = _resolve_settlement_date(settlement_date)
        end = datetime.combine(target_day, time.max)
        settlement_costs = _settlement_cost_map(
            db, datetime.combine(target_day, time.min)
        )
    start = _last_settlement_date(db, before=end)
    cost_cache: dict[int, tuple[Decimal | None, str]] = {}
    batch_no = generate_serial(SerialPrefix.COST_REWRITE, dt=_now())

    def resolve(product_id: int, sku_id: int | None) -> tuple[Decimal | None, str]:
        """按「结存成本 → 浮动成本 → 规格成本价 → 商品成本价」解析单个 SKU 成本。"""
        if sku_id is not None and sku_id in cost_cache:
            return cost_cache[sku_id]
        product = db.get(Product, product_id)
        if sku_id is None:
            if product is not None and product.cost_price is not None:
                return round_money(product.cost_price), "PRODUCT_COST"
            return None, "NONE"
        cost: Decimal | None = settlement_costs.get(sku_id)
        source = "SETTLEMENT"
        sku = db.get(ProductSku, sku_id)
        if cost is None:
            source = "NONE"
        if cost is None and sku is not None:
            use_float = bool(
                setting.floating_cost_enabled
                or (product is not None and product.use_float_cost)
            )
            if use_float:
                cost = _period_inbound_cost(db, sku_id=sku.id, start=start, end=end)
                if cost is not None:
                    source = "FLOATING"
            if cost is None:
                if sku.cost_price is not None:
                    cost = round_money(sku.cost_price)
                    source = "SPEC_COST"
                elif product is not None and product.cost_price is not None:
                    cost = round_money(product.cost_price)
                    source = "PRODUCT_COST"
        cost_cache[sku_id] = (cost, source)
        return cost, source

    item_conditions: list[Any] = [StockOut.created_at <= end]
    flow_conditions: list[Any] = [
        StockFlow.flow_time.is_not(None),
        StockFlow.flow_time <= end,
        or_(StockFlow.direction == "OUT", StockFlow.biz_type == "CHECK_IN"),
    ]
    if start is not None:
        item_conditions.append(StockOut.created_at > start)
        flow_conditions.append(StockFlow.flow_time > start)

    items = list(
        db.scalars(
            select(StockOutItem)
            .join(StockOut, StockOut.id == StockOutItem.stock_out_id)
            .where(*item_conditions)
        )
    )
    flows = list(db.scalars(select(StockFlow).where(*flow_conditions)))
    stock_outs = {
        row.id: row
        for row in db.scalars(
            select(StockOut).where(
                StockOut.id.in_({item.stock_out_id for item in items})
            )
        )
    }

    changed_items = 0
    logs: list[StockCostRewriteLog] = []
    for item in items:
        cost, source = resolve(item.product_id, item.sku_id)
        if cost is None:
            continue
        old_cost = round_money(item.cost_price) if item.cost_price is not None else None
        if old_cost == cost:
            continue
        item.cost_price = cost
        changed_items += 1
        stock_out = stock_outs.get(item.stock_out_id)
        logs.append(
            StockCostRewriteLog(
                batch_no=batch_no,
                period_start=start,
                period_end=end,
                target_type="STOCK_OUT_ITEM",
                target_id=item.id,
                warehouse_id=stock_out.warehouse_id if stock_out is not None else None,
                product_id=item.product_id,
                sku_id=item.sku_id,
                qty=Decimal(item.qty or 0),
                cost_source=source,
                old_cost_price=old_cost,
                new_cost_price=cost,
                old_total_cost=(
                    round_money(Decimal(item.qty or 0) * old_cost)
                    if old_cost is not None
                    else None
                ),
                new_total_cost=round_money(Decimal(item.qty or 0) * cost),
                created_by=staff.id,
            )
        )

    changed_flows = 0
    for flow in flows:
        cost, source = resolve(flow.product_id, flow.sku_id)
        if cost is None:
            continue
        qty = Decimal(flow.qty or 0)
        total_cost = round_money(qty * cost)
        current_cost = (
            round_money(flow.cost_price) if flow.cost_price is not None else None
        )
        current_total = (
            round_money(flow.total_cost) if flow.total_cost is not None else None
        )
        if current_cost == cost and current_total == total_cost:
            continue
        flow.cost_price = cost
        flow.total_cost = total_cost
        changed_flows += 1
        logs.append(
            StockCostRewriteLog(
                batch_no=batch_no,
                period_start=start,
                period_end=end,
                target_type="STOCK_FLOW",
                target_id=flow.id,
                warehouse_id=flow.warehouse_id,
                product_id=flow.product_id,
                sku_id=flow.sku_id,
                qty=qty,
                cost_source=source,
                old_cost_price=current_cost,
                new_cost_price=cost,
                old_total_cost=current_total,
                new_total_cost=total_cost,
                created_by=staff.id,
            )
        )

    db.add_all(logs)
    db.flush()
    write_audit(
        db,
        action="REWRITE_COST",
        target_type=DOC_TYPE_SETTLEMENT,
        target_id=batch_no,
        summary=(
            f"回写出库成本（批次 {batch_no}）：出库明细 {changed_items} 条、"
            f"库存流水 {changed_flows} 条"
        ),
        detail={
            "batch_no": batch_no,
            "period_start": start,
            "period_end": end,
            "stock_out_items": len(items),
            "stock_flows": len(flows),
            "changed_items": changed_items,
            "changed_flows": changed_flows,
            "log_count": len(logs),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "batch_no": batch_no,
        "period_start": start,
        "period_end": end,
        "stock_out_item_count": len(items),
        "stock_flow_count": len(flows),
        "changed_item_count": changed_items,
        "changed_flow_count": changed_flows,
        "log_count": len(logs),
    }


def list_cost_rewrite_logs(
    db: Session,
    *,
    batch_no: str | None = None,
    product_id: int | None = None,
    sku_id: int | None = None,
    target_type: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    """查询库存成本回写留痕，用于财务追溯被调整的成本前后值。"""
    if start_time is not None and start_time.tzinfo is not None:
        start_time = start_time.astimezone(_BUSINESS_TIMEZONE).replace(tzinfo=None)
    if end_time is not None and end_time.tzinfo is not None:
        end_time = end_time.astimezone(_BUSINESS_TIMEZONE).replace(tzinfo=None)
    if start_time is not None and end_time is not None and start_time > end_time:
        raise BizException(ErrorCode.BAD_REQUEST, "开始时间不能晚于结束时间")
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if batch_no:
        conditions.append(StockCostRewriteLog.batch_no == batch_no.strip())
    if product_id is not None:
        conditions.append(StockCostRewriteLog.product_id == product_id)
    if sku_id is not None:
        conditions.append(StockCostRewriteLog.sku_id == sku_id)
    if target_type:
        conditions.append(StockCostRewriteLog.target_type == target_type)
    if start_time is not None:
        conditions.append(StockCostRewriteLog.created_at >= start_time)
    if end_time is not None:
        conditions.append(StockCostRewriteLog.created_at <= end_time)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                StockCostRewriteLog.batch_no.like(like),
                Product.code.like(like),
                Product.name.like(like),
                ProductSku.spec_code.like(like),
                ProductSku.spec_name.like(like),
            )
        )

    base = (
        select(StockCostRewriteLog)
        .join(Product, Product.id == StockCostRewriteLog.product_id)
        .outerjoin(ProductSku, ProductSku.id == StockCostRewriteLog.sku_id)
        .where(*conditions)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery()))
    rows = list(
        db.scalars(
            base.order_by(StockCostRewriteLog.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    items = []
    for row in rows:
        product = db.get(Product, row.product_id)
        sku = db.get(ProductSku, row.sku_id) if row.sku_id else None
        warehouse = db.get(Warehouse, row.warehouse_id) if row.warehouse_id else None
        creator = db.get(Staff, row.created_by) if row.created_by else None
        items.append(
            {
                "id": row.id,
                "batch_no": row.batch_no,
                "period_start": row.period_start,
                "period_end": row.period_end,
                "target_type": row.target_type,
                "target_id": row.target_id,
                "warehouse_id": row.warehouse_id,
                "warehouse_name": warehouse.name if warehouse is not None else None,
                "product_id": row.product_id,
                "product_code": product.code if product is not None else None,
                "product_name": product.name if product is not None else None,
                "sku_id": row.sku_id,
                "spec_code": sku.spec_code if sku is not None else None,
                "spec_name": sku.spec_name if sku is not None else None,
                "qty": Decimal(row.qty or 0),
                "cost_source": row.cost_source,
                "old_cost_price": row.old_cost_price,
                "new_cost_price": row.new_cost_price,
                "old_total_cost": row.old_total_cost,
                "new_total_cost": row.new_total_cost,
                "created_by": row.created_by,
                "created_by_name": creator.name if creator is not None else None,
                "created_at": row.created_at,
            }
        )
    return {
        "total": int(total or 0),
        "page": page,
        "page_size": page_size,
        "items": items,
    }
