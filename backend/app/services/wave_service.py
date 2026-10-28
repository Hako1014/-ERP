"""波次管理服务（TASK-037）。

口径说明：
- 可运行订单：已审核通过且状态为 ``PENDING``，尚未加入任何波次。
- 策略筛选：策略配置物流、仓库、店铺时按对应字段过滤；为空表示不过滤。
- 余单：一次运行按策略最大波次订单数切分；不足一档的余单继续生成独立波次。
- 排序：支持默认、库位编码、商品默认供应商编码三种方式。
- 装载号：默认按每个波次内的物流方式分组生成；同一波次内不同快递使用不同装载号。

待业务确认：
- PRD 未细化“参与商品、支付时间、是否开票、是否货到付款”等策略字段的运算规则，
  本任务暂不自行推断，仅实现现有数据模型可明确承载的策略条件。
- 波次只负责订单分组、排序和装载号，不改变订单状态；打印与发货由 TASK-038/039 处理。
"""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.order import (
    DeliveryList,
    LoadNo,
    Order,
    OrderItem,
    PickList,
    Wave,
    WaveOrder,
    WaveStrategy,
)
from app.models.org import Staff
from app.models.party import Supplier
from app.models.platform import Logistics, Shop
from app.models.product import Product
from app.models.warehouse import Location, StockLocation, Warehouse, Zone

DOC_TYPE_STRATEGY = "wave_strategy"
DOC_TYPE_WAVE = "wave"

LOAD_NO_BY_EXPRESS = "BY_EXPRESS"
LOAD_NO_SINGLE = "SINGLE"
LOAD_NO_TYPES = {LOAD_NO_BY_EXPRESS, LOAD_NO_SINGLE}

SORT_DEFAULT = "DEFAULT"
SORT_LOCATION = "LOCATION"
SORT_SUPPLIER = "SUPPLIER"
SORT_TYPES = {SORT_DEFAULT, SORT_LOCATION, SORT_SUPPLIER}

DEFAULT_WAVE_ORDER_COUNT = 50
MAX_WAVE_ORDER_COUNT = 5000

STRATEGY_FIELDS = {
    "name",
    "logistics_id",
    "warehouse_id",
    "shop_ids",
    "wave_order_count",
    "load_no_type",
    "sort_type",
    "is_enabled",
    "description",
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
            ErrorCode.BAD_REQUEST,
            f"不支持的字段：{', '.join(sorted(unknown))}",
        )


def _required_text(value: Any, label: str, max_length: int) -> str:
    if value in (None, ""):
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    if len(text) > max_length:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}"
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
            ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}"
        )
    return text


def _to_int(value: Any, label: str) -> int:
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
    return _to_int(value, label)


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


def _normalize_shop_ids(db: Session, value: Any) -> list[int]:
    if value in (None, ""):
        return []
    raw = value if isinstance(value, list) else str(value).split(",")
    ids: list[int] = []
    for item in raw:
        shop_id = _to_int(item, "参与店铺")
        if shop_id not in ids:
            ids.append(shop_id)
    existing = set(
        db.scalars(select(Shop.id).where(Shop.id.in_(ids)))
    )
    missing = [shop_id for shop_id in ids if shop_id not in existing]
    if missing:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"参与店铺不存在：{', '.join(str(item) for item in missing)}",
        )
    return ids


def _shop_id_list(strategy: WaveStrategy) -> list[int]:
    if not strategy.shop_ids:
        return []
    return [
        int(item.strip())
        for item in strategy.shop_ids.split(",")
        if item.strip()
    ]


def get_wave_strategy_or_404(
    db: Session, strategy_id: int
) -> WaveStrategy:
    strategy = db.get(WaveStrategy, strategy_id)
    if strategy is None:
        raise BizException(ErrorCode.NOT_FOUND, "波次策略不存在")
    return strategy


def wave_strategy_dict(db: Session, strategy: WaveStrategy) -> dict:
    warehouse = (
        db.get(Warehouse, strategy.warehouse_id)
        if strategy.warehouse_id
        else None
    )
    logistics = (
        db.get(Logistics, strategy.logistics_id)
        if strategy.logistics_id
        else None
    )
    return {
        "id": strategy.id,
        "name": strategy.name,
        "logistics_id": strategy.logistics_id,
        "logistics_name": logistics.name if logistics else None,
        "warehouse_id": strategy.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "shop_ids": _shop_id_list(strategy),
        "wave_order_count": strategy.wave_order_count,
        "load_no_type": strategy.load_no_type,
        "sort_type": strategy.sort_type,
        "is_enabled": strategy.is_enabled,
        "description": strategy.description,
        "created_at": strategy.created_at,
        "updated_at": strategy.updated_at,
    }


def _strategy_values(
    db: Session,
    data: dict,
    *,
    existing: WaveStrategy | None = None,
) -> dict[str, Any]:
    _check_unknown_fields(data, STRATEGY_FIELDS)
    values: dict[str, Any] = {}

    if existing is None or "name" in data:
        name = _required_text(data.get("name"), "波次名称", 64)
        duplicate = db.scalar(
            select(WaveStrategy).where(
                WaveStrategy.name == name,
                WaveStrategy.id != (existing.id if existing else 0),
            )
        )
        if duplicate is not None:
            raise BizException(ErrorCode.CONFLICT, f"波次名称已存在：{name}")
        values["name"] = name

    if existing is None or "logistics_id" in data:
        logistics_id = _optional_id(data.get("logistics_id"), "物流方式")
        if logistics_id is not None:
            logistics = db.get(Logistics, logistics_id)
            if logistics is None:
                raise BizException(ErrorCode.BAD_REQUEST, "物流方式不存在")
            if not logistics.is_enabled:
                raise BizException(ErrorCode.CONFLICT, "物流方式已停用")
        values["logistics_id"] = logistics_id

    if existing is None or "warehouse_id" in data:
        warehouse_id = _optional_id(data.get("warehouse_id"), "仓库")
        if warehouse_id is not None:
            warehouse = db.get(Warehouse, warehouse_id)
            if warehouse is None:
                raise BizException(ErrorCode.BAD_REQUEST, "仓库不存在")
            if not warehouse.is_enabled:
                raise BizException(ErrorCode.CONFLICT, "仓库已停用")
            if warehouse.is_virtual:
                raise BizException(ErrorCode.CONFLICT, "虚拟仓不能参与波次")
        values["warehouse_id"] = warehouse_id

    if existing is None or "shop_ids" in data:
        shop_ids = _normalize_shop_ids(db, data.get("shop_ids"))
        values["shop_ids"] = ",".join(str(item) for item in shop_ids) or None

    if existing is None or "wave_order_count" in data:
        raw_count = data.get("wave_order_count", DEFAULT_WAVE_ORDER_COUNT)
        count = _to_int(raw_count, "波次订单数")
        if count > MAX_WAVE_ORDER_COUNT:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                f"波次订单数不能超过 {MAX_WAVE_ORDER_COUNT}",
            )
        values["wave_order_count"] = count

    if existing is None or "load_no_type" in data:
        load_no_type = str(
            data.get("load_no_type") or LOAD_NO_BY_EXPRESS
        ).strip().upper()
        if load_no_type not in LOAD_NO_TYPES:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "装载号方式必须为 BY_EXPRESS 或 SINGLE",
            )
        values["load_no_type"] = load_no_type

    if existing is None or "sort_type" in data:
        sort_type = str(
            data.get("sort_type") or SORT_DEFAULT
        ).strip().upper()
        if sort_type not in SORT_TYPES:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "排序条件必须为 DEFAULT/LOCATION/SUPPLIER",
            )
        values["sort_type"] = sort_type

    if existing is None or "is_enabled" in data:
        values["is_enabled"] = _to_bool(
            data.get("is_enabled", True), "启用状态"
        )

    if existing is None or "description" in data:
        values["description"] = _optional_text(
            data.get("description"), "波次描述", 255
        )

    return values


def create_wave_strategy(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> WaveStrategy:
    values = _strategy_values(db, data)
    strategy = WaveStrategy(**values)
    db.add(strategy)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE_STRATEGY,
        target_id=str(strategy.id),
        summary=f"新增波次策略：{strategy.name}",
        detail={"fields": sorted(values)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return strategy


def update_wave_strategy(
    db: Session,
    *,
    strategy_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> WaveStrategy:
    strategy = get_wave_strategy_or_404(db, strategy_id)
    values = _strategy_values(db, data, existing=strategy)
    for field, value in values.items():
        setattr(strategy, field, value)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE_STRATEGY,
        target_id=str(strategy.id),
        summary=f"修改波次策略：{strategy.name}",
        detail={"fields": sorted(values)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return strategy


def delete_wave_strategy(
    db: Session,
    *,
    strategy_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    strategy = get_wave_strategy_or_404(db, strategy_id)
    count = db.scalar(
        select(func.count(Wave.id)).where(Wave.strategy_id == strategy.id)
    )
    if count:
        raise BizException(
            ErrorCode.CONFLICT,
            "该策略已生成波次，不能删除，请改为停用",
        )
    db.delete(strategy)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE_STRATEGY,
        target_id=str(strategy.id),
        summary=f"删除波次策略：{strategy.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def list_wave_strategies(
    db: Session,
    *,
    keyword: str | None = None,
    is_enabled: bool | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                WaveStrategy.name.like(like),
                WaveStrategy.description.like(like),
            )
        )
    if is_enabled is not None:
        conditions.append(WaveStrategy.is_enabled.is_(is_enabled))
    total = db.scalar(
        select(func.count(WaveStrategy.id)).where(*conditions)
    ) or 0
    rows = list(
        db.scalars(
            select(WaveStrategy)
            .where(*conditions)
            .order_by(WaveStrategy.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [wave_strategy_dict(db, row) for row in rows],
    }


def _eligible_orders(
    db: Session,
    *,
    strategy: WaveStrategy,
    order_ids: list[int] | None = None,
) -> list[Order]:
    conditions: list[Any] = [
        Order.status == "PENDING",
        Order.audit_status == "APPROVED",
        Order.id.not_in(select(WaveOrder.order_id)),
    ]
    if strategy.warehouse_id is not None:
        conditions.append(Order.warehouse_id == strategy.warehouse_id)
    if strategy.logistics_id is not None:
        conditions.append(Order.logistics_id == strategy.logistics_id)
    shop_ids = _shop_id_list(strategy)
    if shop_ids:
        conditions.append(Order.shop_id.in_(shop_ids))
    if order_ids is not None:
        conditions.append(Order.id.in_(order_ids))
    return list(
        db.scalars(
            select(Order)
            .where(*conditions)
            .order_by(Order.order_time, Order.id)
        )
    )


def pending_order_count(
    db: Session,
    *,
    strategy_id: int,
    order_ids: list[int] | None = None,
) -> int:
    strategy = get_wave_strategy_or_404(db, strategy_id)
    return len(_eligible_orders(db, strategy=strategy, order_ids=order_ids))


def _location_sort_map(
    db: Session, order_ids: list[int]
) -> dict[int, tuple[tuple, str]]:
    if not order_ids:
        return {}
    rows = db.execute(
        select(
            OrderItem.order_id, Location.code, Location.priority,
            Zone.sort_order, Location.row_no, Location.col_no,
            Location.layer_no, Location.sort_order,
        )
        .join(Order, Order.id == OrderItem.order_id)
        .join(
            StockLocation,
            StockLocation.product_sku_id == OrderItem.sku_id,
        )
        .join(Location, Location.id == StockLocation.location_id)
        .outerjoin(Zone, Zone.id == Location.zone_id)
        .where(
            OrderItem.order_id.in_(order_ids),
            Location.warehouse_id == Order.warehouse_id,
            Location.is_enabled.is_(True),
        )
    )
    result: dict[int, tuple[tuple, str]] = {}
    for order_id, code, priority, zone_order, row_no, col_no, layer_no, sort_order in rows:
        route = (
            -(priority or 0), zone_order or 0, row_no or 0,
            col_no or 0, layer_no or 0, sort_order or 0, code,
        )
        current = result.get(int(order_id))
        if current is None or route < current[0]:
            result[int(order_id)] = (route, code)
    return result


def _supplier_sort_map(
    db: Session, order_ids: list[int]
) -> dict[int, str]:
    if not order_ids:
        return {}
    rows = db.execute(
        select(OrderItem.order_id, func.min(Supplier.code))
        .join(Product, Product.id == OrderItem.product_id)
        .join(Supplier, Supplier.id == Product.default_supplier_id)
        .where(OrderItem.order_id.in_(order_ids))
        .group_by(OrderItem.order_id)
    )
    return {
        int(order_id): str(code)
        for order_id, code in rows
        if code
    }


def _sort_orders(
    db: Session,
    *,
    orders: list[Order],
    sort_type: str,
) -> list[tuple[Order, str]]:
    order_ids = [order.id for order in orders]
    if sort_type == SORT_LOCATION:
        key_map = _location_sort_map(db, order_ids)
        prefix = "LOCATION"
    elif sort_type == SORT_SUPPLIER:
        key_map = _supplier_sort_map(db, order_ids)
        prefix = "SUPPLIER"
    else:
        key_map = {}
        prefix = "DEFAULT"

    def sort_key(order: Order) -> tuple:
        mapped = key_map.get(order.id)
        time_key = order.order_time or datetime.min
        if sort_type == SORT_DEFAULT:
            return (time_key, order.id)
        if sort_type == SORT_LOCATION:
            return (mapped[0] if mapped else (float("inf"),), time_key, order.id)
        return (mapped or "\uffff", time_key, order.id)

    result: list[tuple[Order, str]] = []
    for order in sorted(orders, key=sort_key):
        mapped = key_map.get(order.id)
        if sort_type == SORT_DEFAULT:
            text = (
                (order.order_time or datetime.min).isoformat()
                + f"|{order.id}"
            )
        else:
            code = mapped[1] if sort_type == SORT_LOCATION and mapped else mapped
            text = f"{prefix}:{code or '-'}|{order.id}"
        result.append((order, text))
    return result


def _express_code(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "NA"
    mapping = {
        "顺丰": "SF",
        "圆通": "YTO",
        "中通": "ZTO",
        "申通": "STO",
        "韵达": "YD",
        "邮政": "EMS",
        "EMS": "EMS",
        "天天": "TT",
        "京东": "JD",
        "极兔": "JT",
    }
    upper = raw.upper()
    for keyword, code in mapping.items():
        if keyword.upper() in upper:
            return code
    ascii_code = "".join(
        char for char in upper if char.isascii() and char.isalnum()
    )
    return ascii_code[:8] or "OTHER"


def _express_group(
    db: Session, order: Order
) -> tuple[str, str]:
    logistics = (
        db.get(Logistics, order.logistics_id)
        if order.logistics_id
        else None
    )
    raw = (
        (logistics.company or logistics.name)
        if logistics is not None
        else order.logistics_company
    )
    label = (
        logistics.name
        if logistics is not None
        else (order.logistics_company or "未设置")
    )
    return _express_code(raw), label


def _chunks(
    rows: list[tuple[Order, str]], size: int
) -> list[list[tuple[Order, str]]]:
    return [rows[index : index + size] for index in range(0, len(rows), size)]


def _create_wave(
    db: Session,
    *,
    strategy: WaveStrategy,
    rows: list[tuple[Order, str]],
    wave_index: int,
    wave_total: int,
    staff: Staff,
) -> Wave:
    wave_no = generate_serial(SerialPrefix.WAVE, dt=_now())
    groups: dict[str, dict[str, Any]] = {}
    for order, _ in rows:
        code, label = _express_group(db, order)
        groups.setdefault(code, {"label": label, "orders": []})
        groups[code]["orders"].append(order)

    labels = {group["label"] for group in groups.values()}
    express_type = (
        next(iter(labels)) if len(labels) == 1 else "混合快递"
    )
    wave_name = (
        strategy.name
        if wave_total == 1
        else f"{strategy.name}-{wave_index}"
    )
    wave = Wave(
        wave_no=wave_no,
        strategy_id=strategy.id,
        name=wave_name[:64],
        express_type=express_type,
        total_order_count=len(rows),
        created_by=staff.id,
    )
    db.add(wave)
    db.flush()

    counters: dict[str, int] = defaultdict(int)
    load_numbers: dict[str, str] = {}
    for sequence, (order, sort_key) in enumerate(rows, start=1):
        code, label = _express_group(db, order)
        counters[code] += 1
        if strategy.load_no_type == LOAD_NO_SINGLE:
            load_no = f"{wave_no}-L01"
        else:
            load_no = f"{wave_no}-{code}-{counters[code]:02d}"
        load_numbers.setdefault(code, load_no)
        db.add(
            WaveOrder(
                wave_id=wave.id,
                order_id=order.id,
                load_no=load_no,
                sequence=sequence,
                sort_key=sort_key[:255],
            )
        )

    for code, group in groups.items():
        db.add(
            LoadNo(
                wave_id=wave.id,
                load_no=load_numbers[code],
                express_type=group["label"],
            )
        )
    write_audit(
        db,
        action="RUN",
        target_type=DOC_TYPE_WAVE,
        target_id=str(wave.id),
        summary=f"运行波次策略「{strategy.name}」生成波次：{wave.wave_no}",
        detail={
            "strategy_id": strategy.id,
            "order_count": len(rows),
            "express_type": express_type,
            "wave_index": wave_index,
            "wave_total": wave_total,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()
    return wave


def _run_strategy(
    db: Session,
    *,
    strategy: WaveStrategy,
    staff: Staff,
    order_ids: list[int] | None,
) -> dict:
    if not strategy.is_enabled:
        raise BizException(
            ErrorCode.CONFLICT,
            f"波次策略「{strategy.name}」已停用",
        )
    orders = _eligible_orders(
        db, strategy=strategy, order_ids=order_ids
    )
    if order_ids is not None:
        requested = set(order_ids)
        found = {order.id for order in orders}
        missing = sorted(requested - found)
        if missing:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "以下订单不满足波次条件或已加入其它波次："
                + ", ".join(str(item) for item in missing),
            )
    if not orders:
        return {
            "strategy_id": strategy.id,
            "strategy_name": strategy.name,
            "wave_count": 0,
            "order_count": 0,
            "waves": [],
        }

    sorted_orders = _sort_orders(
        db,
        orders=orders,
        sort_type=strategy.sort_type or SORT_DEFAULT,
    )
    chunks = _chunks(sorted_orders, strategy.wave_order_count)
    waves: list[Wave] = []
    for index, chunk in enumerate(chunks, start=1):
        waves.append(
            _create_wave(
                db,
                strategy=strategy,
                rows=chunk,
                wave_index=index,
                wave_total=len(chunks),
                staff=staff,
            )
        )
    return {
        "strategy_id": strategy.id,
        "strategy_name": strategy.name,
        "wave_count": len(waves),
        "order_count": len(sorted_orders),
        "waves": [
            wave_dict(db, wave, with_orders=True)
            for wave in waves
        ],
    }


def run_wave_strategy(
    db: Session,
    *,
    strategy_id: int,
    staff: Staff,
    order_ids: list[int] | None = None,
    commit: bool = True,
) -> dict:
    strategy = get_wave_strategy_or_404(db, strategy_id)
    normalized_order_ids = (
        [_to_int(item, "订单") for item in order_ids]
        if order_ids
        else None
    )
    result = _run_strategy(
        db,
        strategy=strategy,
        staff=staff,
        order_ids=normalized_order_ids,
    )
    _commit(db, commit)
    return result


def run_wave_strategies(
    db: Session,
    *,
    strategy_ids: list[int],
    staff: Staff,
    order_ids: list[int] | None = None,
    commit: bool = True,
) -> dict:
    if not strategy_ids:
        raise BizException(ErrorCode.BAD_REQUEST, "请至少选择一个波次策略")
    if len(strategy_ids) > 20:
        raise BizException(ErrorCode.BAD_REQUEST, "单次最多运行 20 个波次策略")
    normalized_ids: list[int] = []
    for strategy_id in strategy_ids:
        value = _to_int(strategy_id, "波次策略")
        if value not in normalized_ids:
            normalized_ids.append(value)
    normalized_order_ids = (
        [_to_int(item, "订单") for item in order_ids]
        if order_ids
        else None
    )

    results: list[dict] = []
    for strategy_id in normalized_ids:
        strategy = get_wave_strategy_or_404(db, strategy_id)
        results.append(
            _run_strategy(
                db,
                strategy=strategy,
                staff=staff,
                order_ids=normalized_order_ids,
            )
        )
    _commit(db, commit)
    return {
        "strategy_count": len(results),
        "wave_count": sum(item["wave_count"] for item in results),
        "order_count": sum(item["order_count"] for item in results),
        "results": results,
    }


def get_wave_or_404(db: Session, wave_id: int) -> Wave:
    wave = db.get(Wave, wave_id)
    if wave is None:
        raise BizException(ErrorCode.NOT_FOUND, "波次不存在")
    return wave


def load_no_dict(row: LoadNo) -> dict:
    return {
        "id": row.id,
        "wave_id": row.wave_id,
        "load_no": row.load_no,
        "express_type": row.express_type,
    }


def _wave_orders(db: Session, wave_id: int) -> list[WaveOrder]:
    return list(
        db.scalars(
            select(WaveOrder)
            .where(WaveOrder.wave_id == wave_id)
            .order_by(WaveOrder.sequence, WaveOrder.id)
        )
    )


def wave_dict(
    db: Session,
    wave: Wave,
    *,
    with_orders: bool = True,
) -> dict:
    creator = db.get(Staff, wave.created_by) if wave.created_by else None
    result: dict[str, Any] = {
        "id": wave.id,
        "wave_no": wave.wave_no,
        "strategy_id": wave.strategy_id,
        "name": wave.name,
        "express_type": wave.express_type,
        "total_order_count": wave.total_order_count,
        "created_by": wave.created_by,
        "created_by_name": creator.name if creator else None,
        "created_at": wave.created_at,
        "updated_at": wave.updated_at,
    }
    load_numbers = list(
        db.scalars(
            select(LoadNo)
            .where(LoadNo.wave_id == wave.id)
            .order_by(LoadNo.id)
        )
    )
    result["load_numbers"] = [
        load_no_dict(row) for row in load_numbers
    ]
    if with_orders:
        orders: list[dict] = []
        for row in _wave_orders(db, wave.id):
            order = db.get(Order, row.order_id)
            if order is None:
                continue
            warehouse = (
                db.get(Warehouse, order.warehouse_id)
                if order.warehouse_id
                else None
            )
            logistics = (
                db.get(Logistics, order.logistics_id)
                if order.logistics_id
                else None
            )
            orders.append(
                {
                    "id": order.id,
                    "order_no": order.order_no,
                    "shop_id": order.shop_id,
                    "warehouse_id": order.warehouse_id,
                    "warehouse_name": (
                        warehouse.name if warehouse else None
                    ),
                    "logistics_id": order.logistics_id,
                    "logistics_name": (
                        logistics.name if logistics else None
                    ),
                    "tracking_no": order.tracking_no,
                    "receiver_name": order.receiver_name,
                    "receiver_phone": order.receiver_phone,
                    "paid_amount": order.paid_amount,
                    "status": order.status,
                    "order_time": order.order_time,
                    "load_no": row.load_no,
                    "sequence": row.sequence,
                    "sort_key": row.sort_key,
                }
            )
        result["orders"] = orders
    return result


def list_waves(
    db: Session,
    *,
    strategy_id: int | None = None,
    express_type: str | None = None,
    keyword: str | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    if start_time is not None and end_time is not None:
        from zoneinfo import ZoneInfo

        start_for_ordering = (
            start_time.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            if start_time.tzinfo is not None else start_time
        )
        end_for_ordering = (
            end_time.astimezone(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)
            if end_time.tzinfo is not None else end_time
        )
        if start_for_ordering > end_for_ordering:
            raise BizException(ErrorCode.BAD_REQUEST, "波次开始时间不能晚于结束时间")

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions: list[Any] = []
    if strategy_id is not None:
        conditions.append(Wave.strategy_id == strategy_id)
    if express_type:
        conditions.append(Wave.express_type == express_type.strip())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(Wave.wave_no.like(like), Wave.name.like(like))
        )
    if start_time is not None:
        conditions.append(Wave.created_at >= start_time)
    if end_time is not None:
        conditions.append(Wave.created_at <= end_time)
    total = db.scalar(select(func.count(Wave.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Wave)
            .where(*conditions)
            .order_by(Wave.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": int(total),
        "page": page,
        "page_size": page_size,
        "items": [wave_dict(db, row, with_orders=False) for row in rows],
    }


def list_wave_load_numbers(db: Session, *, wave_id: int) -> list[dict]:
    get_wave_or_404(db, wave_id)
    return [
        load_no_dict(row)
        for row in db.scalars(
            select(LoadNo)
            .where(LoadNo.wave_id == wave_id)
            .order_by(LoadNo.id)
        )
    ]


def delete_wave(
    db: Session,
    *,
    wave_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    """删除未进入打印/拣货环节的空波次或误建波次。"""
    wave = get_wave_or_404(db, wave_id)
    pick_count = db.scalar(
        select(func.count(PickList.id)).where(PickList.wave_id == wave.id)
    )
    delivery_count = db.scalar(
        select(func.count(DeliveryList.id)).where(
            DeliveryList.wave_id == wave.id
        )
    )
    if pick_count or delivery_count:
        raise BizException(
            ErrorCode.CONFLICT,
            "波次已生成拣货单或配货单，不能删除",
        )
    for row in _wave_orders(db, wave.id):
        db.delete(row)
    for row in db.scalars(
        select(LoadNo).where(LoadNo.wave_id == wave.id)
    ):
        db.delete(row)
    wave_no = wave.wave_no
    db.delete(wave)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE_WAVE,
        target_id=str(wave_id),
        summary=f"删除波次：{wave_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
