"""仓库与库位服务（TASK-029）。

覆盖仓库、库区、库位维护，以及库位编码、容量校验和统一可选仓库校验。
库存增减和上架流程由后续库存任务调用本模块，不在这里直接改库存。
"""

from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, literal, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import to_decimal
from app.db.base import Base
from app.models.org import Staff
from app.models.warehouse import Location, StockLocation, Warehouse, Zone

WAREHOUSE_FIELDS = {
    "code",
    "name",
    "region",
    "address",
    "warehouse_type",
    "is_default",
    "sync_stock",
    "enable_location",
    "zero_stock_out",
    "is_virtual",
    "ship_regions",
    "is_enabled",
    "lock_type",
    "remark",
}
ZONE_FIELDS = {"warehouse_id", "code", "name", "sort_order"}
LOCATION_FIELDS = {
    "warehouse_id",
    "zone_id",
    "code",
    "row_no",
    "col_no",
    "layer_no",
    "capacity",
    "priority",
    "sort_order",
    "is_virtual",
    "is_enabled",
}

WAREHOUSE_TYPES = {"FINISHED", "DEFECTIVE"}
LOCK_TYPES = {"NONE", "MANUAL", "CHECK"}


def _commit(db: Session, commit: bool) -> None:
    if commit:
        db.commit()
    else:
        db.flush()


def _require_text(value: Any, label: str, max_length: int) -> str:
    if value is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    text = str(value).strip()
    if not text:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为空")
    if len(text) > max_length:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}")
    return text


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return _require_text(value, label, max_length)


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "1", "yes", "y"}:
            return True
        if text in {"false", "0", "no", "n"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _to_int(value: Any, label: str, *, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc
    if minimum is not None and result < minimum:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能小于 {minimum}")
    return result


def _optional_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int(value, label, minimum=1)


def _check_unknown_fields(data: dict, allowed: set[str]) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"不支持的字段：{', '.join(sorted(unknown))}"
        )


def _validate_warehouse_type(value: Any) -> str:
    warehouse_type = _require_text(value, "仓库类型", 16).upper()
    if warehouse_type not in WAREHOUSE_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST, "仓库类型仅支持 FINISHED / DEFECTIVE"
        )
    return warehouse_type


def _validate_lock_type(value: Any) -> str:
    lock_type = _require_text(value, "锁盘类型", 16).upper()
    if lock_type not in LOCK_TYPES:
        raise BizException(
            ErrorCode.BAD_REQUEST, "锁盘类型仅支持 NONE / MANUAL / CHECK"
        )
    return lock_type


def _referenced_rows(
    db: Session,
    *,
    target_fullname: str,
    target_id: int,
) -> list[str]:
    used: list[str] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            if not any(
                fk.target_fullname == target_fullname
                for fk in column.foreign_keys
            ):
                continue
            stmt = select(literal(1)).select_from(table).where(column == target_id).limit(1)
            if db.execute(stmt).first() is not None:
                used.append(table.name)
                break
    return used


# --------------------------------------------------------------------------- #
# 仓库
# --------------------------------------------------------------------------- #


def warehouse_dict(warehouse: Warehouse) -> dict:
    return {
        "id": warehouse.id,
        "code": warehouse.code,
        "name": warehouse.name,
        "region": warehouse.region,
        "address": warehouse.address,
        "warehouse_type": warehouse.warehouse_type,
        "is_default": warehouse.is_default,
        "sync_stock": warehouse.sync_stock,
        "enable_location": warehouse.enable_location,
        "zero_stock_out": warehouse.zero_stock_out,
        "is_virtual": warehouse.is_virtual,
        "ship_regions": warehouse.ship_regions,
        "is_enabled": warehouse.is_enabled,
        "lock_type": warehouse.lock_type,
        "remark": warehouse.remark,
        "selectable": warehouse.is_enabled and warehouse.lock_type == "NONE",
    }


def get_warehouse_or_404(
    db: Session, warehouse_id: int, *, company_id: int | None = None,
) -> Warehouse:
    stmt = select(Warehouse).where(Warehouse.id == warehouse_id)
    if company_id is not None:
        stmt = stmt.where(Warehouse.company_id == company_id)
    warehouse = db.scalar(stmt)
    if warehouse is None:
        raise BizException(ErrorCode.NOT_FOUND, "仓库不存在")
    return warehouse


def list_warehouses(
    db: Session,
    *,
    keyword: str | None = None,
    warehouse_type: str | None = None,
    is_enabled: bool | None = None,
    selectable_only: bool = False,
) -> list[Warehouse]:
    conditions = []
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Warehouse.code.like(like),
                Warehouse.name.like(like),
                Warehouse.region.like(like),
                Warehouse.address.like(like),
            )
        )
    if warehouse_type:
        conditions.append(
            Warehouse.warehouse_type == _validate_warehouse_type(warehouse_type)
        )
    if is_enabled is not None:
        conditions.append(Warehouse.is_enabled.is_(is_enabled))
    if selectable_only:
        conditions.extend(
            [
                Warehouse.is_enabled.is_(True),
                Warehouse.lock_type == "NONE",
            ]
        )
    return list(
        db.scalars(
            select(Warehouse)
            .where(*conditions)
            .order_by(Warehouse.is_default.desc(), Warehouse.id)
        )
    )


def _validate_warehouse_unique(
    db: Session,
    *,
    code: str,
    exclude_id: int | None = None,
) -> None:
    stmt = select(Warehouse.id).where(Warehouse.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Warehouse.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"仓库编号已存在：{code}")


def _warehouse_values(
    db: Session,
    data: dict,
    *,
    existing: Warehouse | None = None,
) -> dict[str, Any]:
    _check_unknown_fields(data, WAREHOUSE_FIELDS)
    values: dict[str, Any] = {}

    if existing is None or "code" in data:
        code = _require_text(data.get("code"), "仓库编号", 32)
        _validate_warehouse_unique(
            db, code=code, exclude_id=existing.id if existing else None
        )
        values["code"] = code
    if existing is None or "name" in data:
        values["name"] = _require_text(data.get("name"), "仓库名称", 64)

    for field, label, max_length in (
        ("region", "地区", 64),
        ("address", "详细地址", 255),
        ("ship_regions", "可发货区域", 128),
        ("remark", "备注", 255),
    ):
        if field in data:
            values[field] = _optional_text(data[field], label, max_length)

    for field, label in (
        ("is_default", "默认仓库"),
        ("sync_stock", "参与同步库存"),
        ("enable_location", "开启库位"),
        ("zero_stock_out", "零库存出库"),
        ("is_virtual", "虚拟仓"),
        ("is_enabled", "启用状态"),
    ):
        if field in data:
            values[field] = _to_bool(data[field], label)

    if existing is None or "warehouse_type" in data:
        values["warehouse_type"] = _validate_warehouse_type(
            data.get(
                "warehouse_type",
                existing.warehouse_type if existing is not None else "FINISHED",
            )
        )
    if existing is None or "lock_type" in data:
        values["lock_type"] = _validate_lock_type(
            data.get(
                "lock_type",
                existing.lock_type if existing is not None else "NONE",
            )
        )

    merged = {
        "warehouse_type": values.get(
            "warehouse_type",
            existing.warehouse_type if existing is not None else "FINISHED",
        ),
        "is_virtual": values.get(
            "is_virtual", existing.is_virtual if existing is not None else False
        ),
        "is_default": values.get(
            "is_default", existing.is_default if existing is not None else False
        ),
        "is_enabled": values.get(
            "is_enabled", existing.is_enabled if existing is not None else True
        ),
    }
    if merged["warehouse_type"] == "DEFECTIVE" or merged["is_virtual"]:
        values["sync_stock"] = False
    if merged["is_default"] and not merged["is_enabled"]:
        if existing is not None and "is_default" not in data:
            values["is_default"] = False
        else:
            raise BizException(ErrorCode.BAD_REQUEST, "默认仓库必须处于启用状态")
    return values


def _reset_default_warehouse(db: Session, *, keep_id: int) -> None:
    for row in db.scalars(
        select(Warehouse).where(
            Warehouse.id != keep_id,
            Warehouse.is_default.is_(True),
        )
    ):
        row.is_default = False


def create_warehouse(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Warehouse:
    values = _warehouse_values(db, data)
    values.setdefault("warehouse_type", "FINISHED")
    values.setdefault("is_default", False)
    values.setdefault("sync_stock", False)
    values.setdefault("enable_location", False)
    values.setdefault("zero_stock_out", False)
    values.setdefault("is_virtual", False)
    values.setdefault("is_enabled", True)
    values.setdefault("lock_type", "NONE")
    warehouse = Warehouse(**values)
    db.add(warehouse)
    db.flush()
    if warehouse.is_default:
        _reset_default_warehouse(db, keep_id=warehouse.id)
    write_audit(
        db,
        action="CREATE",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"新增仓库：{warehouse.code} {warehouse.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return warehouse


def update_warehouse(
    db: Session,
    *,
    warehouse_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Warehouse:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    values = _warehouse_values(db, data, existing=warehouse)
    for field, value in values.items():
        setattr(warehouse, field, value)
    if not warehouse.is_enabled:
        warehouse.is_default = False
    if warehouse.is_default:
        _reset_default_warehouse(db, keep_id=warehouse.id)
    write_audit(
        db,
        action="UPDATE",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"修改仓库：{warehouse.code} {warehouse.name}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return warehouse


def set_warehouse_enabled(
    db: Session,
    *,
    warehouse_id: int,
    enabled: bool,
    staff: Staff,
    commit: bool = True,
) -> Warehouse:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    warehouse.is_enabled = enabled
    if not enabled:
        warehouse.is_default = False
    write_audit(
        db,
        action="ENABLE" if enabled else "DISABLE",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"{'启用' if enabled else '停用'}仓库：{warehouse.code} {warehouse.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return warehouse


def set_default_warehouse(
    db: Session, *, warehouse_id: int, staff: Staff, commit: bool = True
) -> Warehouse:
    warehouse = ensure_warehouse_selectable(db, warehouse_id)
    warehouse.is_default = True
    _reset_default_warehouse(db, keep_id=warehouse.id)
    write_audit(
        db,
        action="SET_DEFAULT",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"设置默认仓库：{warehouse.code} {warehouse.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return warehouse


def lock_warehouse(
    db: Session,
    *,
    warehouse_id: int,
    staff: Staff,
    commit: bool = True,
) -> Warehouse:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    warehouse.lock_type = "MANUAL"
    warehouse.is_default = False
    write_audit(
        db,
        action="LOCK",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"手动锁定仓库：{warehouse.code} {warehouse.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return warehouse


def unlock_warehouse(
    db: Session,
    *,
    warehouse_id: int,
    staff: Staff,
    force: bool = False,
    commit: bool = True,
) -> Warehouse:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    if warehouse.lock_type == "CHECK" and not force:
        raise BizException(
            ErrorCode.CONFLICT,
            "仓库处于盘点锁盘状态，应由盘点流程解锁",
        )
    warehouse.lock_type = "NONE"
    write_audit(
        db,
        action="UNLOCK",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"解锁仓库：{warehouse.code} {warehouse.name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return warehouse


def ensure_warehouse_selectable(db: Session, warehouse_id: int) -> Warehouse:
    """业务选择仓库前的统一校验入口。"""
    warehouse = get_warehouse_or_404(db, warehouse_id)
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "仓库已停用，不能在业务中选择")
    if warehouse.lock_type != "NONE":
        raise BizException(ErrorCode.CONFLICT, "仓库已锁定，不能在业务中选择")
    return warehouse


def delete_warehouse(
    db: Session, *, warehouse_id: int, staff: Staff, commit: bool = True
) -> None:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    child_count = db.scalar(
        select(func.count(Zone.id)).where(Zone.warehouse_id == warehouse.id)
    ) or 0
    location_count = db.scalar(
        select(func.count(Location.id)).where(
            Location.warehouse_id == warehouse.id
        )
    ) or 0
    if child_count or location_count:
        raise BizException(
            ErrorCode.CONFLICT,
            "仓库下已有库区或库位，不能删除，请先清理库位或改为停用",
        )
    referenced = _referenced_rows(
        db,
        target_fullname="warehouse.id",
        target_id=warehouse.id,
    )
    referenced = [name for name in referenced if name not in {"zone", "location"}]
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"仓库已被 {','.join(sorted(referenced))} 引用，不能删除，请改为停用",
        )
    code = warehouse.code
    name = warehouse.name
    db.delete(warehouse)
    write_audit(
        db,
        action="DELETE",
        target_type="warehouse",
        target_id=str(warehouse.id),
        summary=f"删除仓库：{code} {name}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 库区
# --------------------------------------------------------------------------- #


def zone_dict(db: Session, zone: Zone) -> dict:
    warehouse = db.get(Warehouse, zone.warehouse_id)
    return {
        "id": zone.id,
        "warehouse_id": zone.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "code": zone.code,
        "name": zone.name,
        "sort_order": zone.sort_order,
    }


def get_zone_or_404(
    db: Session, zone_id: int, *, company_id: int | None = None,
) -> Zone:
    stmt = select(Zone).where(Zone.id == zone_id)
    if company_id is not None:
        stmt = stmt.where(Zone.company_id == company_id)
    zone = db.scalar(stmt)
    if zone is None:
        raise BizException(ErrorCode.NOT_FOUND, "库区不存在")
    return zone


def _validate_zone_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    if not warehouse.enable_location:
        raise BizException(
            ErrorCode.CONFLICT,
            "仓库未开启库位，不能创建或维护库区",
        )
    return warehouse


def _validate_zone_unique(
    db: Session,
    *,
    warehouse_id: int,
    code: str,
    exclude_id: int | None = None,
) -> None:
    stmt = select(Zone.id).where(
        Zone.warehouse_id == warehouse_id,
        Zone.code == code,
    )
    if exclude_id is not None:
        stmt = stmt.where(Zone.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"库区编号已存在：{code}")


def list_zones(db: Session, *, warehouse_id: int) -> list[Zone]:
    get_warehouse_or_404(db, warehouse_id)
    return list(
        db.scalars(
            select(Zone)
            .where(Zone.warehouse_id == warehouse_id)
            .order_by(Zone.sort_order, Zone.id)
        )
    )


def create_zone(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Zone:
    _check_unknown_fields(data, ZONE_FIELDS)
    warehouse_id = _optional_id(data.get("warehouse_id"), "所属仓库")
    if warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "所属仓库不能为空")
    _validate_zone_warehouse(db, warehouse_id)
    code = _require_text(data.get("code"), "库区编号", 32)
    _validate_zone_unique(db, warehouse_id=warehouse_id, code=code)
    zone = Zone(
        warehouse_id=warehouse_id,
        code=code,
        name=_optional_text(data.get("name"), "库区名称", 64),
        sort_order=_to_int(data.get("sort_order", 0), "排序"),
    )
    db.add(zone)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="zone",
        target_id=str(zone.id),
        summary=f"新增库区：{zone.code}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return zone


def update_zone(
    db: Session,
    *,
    zone_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Zone:
    _check_unknown_fields(data, ZONE_FIELDS)
    zone = get_zone_or_404(db, zone_id)
    _validate_zone_warehouse(db, zone.warehouse_id)
    if "warehouse_id" in data:
        warehouse_id = _optional_id(data["warehouse_id"], "所属仓库")
        if warehouse_id != zone.warehouse_id:
            raise BizException(ErrorCode.BAD_REQUEST, "库区不能跨仓库调整")
    if "code" in data:
        code = _require_text(data["code"], "库区编号", 32)
        _validate_zone_unique(
            db,
            warehouse_id=zone.warehouse_id,
            code=code,
            exclude_id=zone.id,
        )
        zone.code = code
    if "name" in data:
        zone.name = _optional_text(data["name"], "库区名称", 64)
    if "sort_order" in data:
        zone.sort_order = _to_int(data["sort_order"], "排序")
    write_audit(
        db,
        action="UPDATE",
        target_type="zone",
        target_id=str(zone.id),
        summary=f"修改库区：{zone.code}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return zone


def delete_zone(
    db: Session, *, zone_id: int, staff: Staff, commit: bool = True
) -> None:
    zone = get_zone_or_404(db, zone_id)
    location_count = db.scalar(
        select(func.count(Location.id)).where(Location.zone_id == zone.id)
    ) or 0
    if location_count:
        raise BizException(
            ErrorCode.CONFLICT,
            "库区下已有库位，不能删除，请先删除或停用库位",
        )
    code = zone.code
    db.delete(zone)
    write_audit(
        db,
        action="DELETE",
        target_type="zone",
        target_id=str(zone.id),
        summary=f"删除库区：{code}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


# --------------------------------------------------------------------------- #
# 库位
# --------------------------------------------------------------------------- #


def generate_location_code(
    warehouse: Warehouse,
    zone: Zone | None,
    *,
    row_no: int,
    col_no: int,
    layer_no: int,
) -> str:
    """按「仓库-库区-行列层」生成库位编码。"""
    parts = [warehouse.code]
    if zone is not None:
        parts.append(f"{zone.code}区")
    parts.extend(
        [
            f"{row_no:02d}行",
            f"{col_no:02d}列",
            f"{layer_no:02d}层",
        ]
    )
    return "-".join(parts)


def _location_used_qty(
    db: Session, location_id: int, *, company_id: int | None = None,
) -> Decimal:
    conditions = [StockLocation.location_id == location_id]
    if company_id is not None:
        conditions.append(StockLocation.company_id == company_id)
    used = db.scalar(select(func.coalesce(func.sum(StockLocation.qty), 0)).where(*conditions))
    try:
        return to_decimal(used or 0)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise BizException(ErrorCode.INTERNAL_ERROR, "库位容量数据异常") from exc


def location_capacity_state(
    db: Session, location_id: int, *, company_id: int | None = None,
) -> dict:
    location = get_location_or_404(db, location_id, company_id=company_id)
    used = _location_used_qty(db, location.id, company_id=company_id)
    capacity = int(location.capacity or 0)
    remaining = None if capacity == 0 else to_decimal(capacity) - used
    return {
        "location_id": location.id,
        "capacity": capacity,
        "used_qty": used,
        "remaining_qty": remaining,
        "unlimited": capacity == 0,
    }


def ensure_location_capacity(
    db: Session,
    location_id: int,
    *,
    added_qty: Any,
) -> dict:
    """校验库位增加库存后的容量；容量 0 表示不限。"""
    location = get_location_or_404(db, location_id)
    try:
        delta = to_decimal(added_qty)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "变动数量必须为合法数字") from exc
    state = location_capacity_state(db, location.id)
    new_qty = state["used_qty"] + delta
    if location.capacity and new_qty > to_decimal(location.capacity):
        raise BizException(
            ErrorCode.CONFLICT,
            f"库位容量不足：容量 {location.capacity}，已用 {state['used_qty']}，"
            f"本次变动 {delta}",
        )
    state.update(
        {
            "added_qty": delta,
            "new_qty": new_qty,
            "remaining_after": (
                None
                if not location.capacity
                else to_decimal(location.capacity) - new_qty
            ),
        }
    )
    return state


def location_dict(db: Session, location: Location) -> dict:
    warehouse = db.get(Warehouse, location.warehouse_id)
    zone = db.get(Zone, location.zone_id) if location.zone_id else None
    used = _location_used_qty(db, location.id)
    capacity = int(location.capacity or 0)
    return {
        "id": location.id,
        "warehouse_id": location.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "zone_id": location.zone_id,
        "zone_code": zone.code if zone else None,
        "zone_name": zone.name if zone else None,
        "code": location.code,
        "row_no": location.row_no,
        "col_no": location.col_no,
        "layer_no": location.layer_no,
        "capacity": capacity,
        "used_qty": used,
        "remaining_qty": (
            None if capacity == 0 else max(to_decimal(capacity) - used, Decimal("0"))
        ),
        "priority": location.priority,
        "sort_order": location.sort_order,
        "is_virtual": location.is_virtual,
        "is_enabled": location.is_enabled,
    }


def get_location_or_404(
    db: Session, location_id: int, *, company_id: int | None = None,
) -> Location:
    stmt = select(Location).where(Location.id == location_id)
    if company_id is not None:
        stmt = stmt.where(Location.company_id == company_id)
    location = db.scalar(stmt)
    if location is None:
        raise BizException(ErrorCode.NOT_FOUND, "库位不存在")
    return location


def _validate_location_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = get_warehouse_or_404(db, warehouse_id)
    if not warehouse.enable_location:
        raise BizException(
            ErrorCode.CONFLICT,
            "仓库未开启库位，不能创建或维护库位",
        )
    return warehouse


def _validate_location_zone(
    db: Session, *, warehouse_id: int, zone_id: int | None
) -> Zone | None:
    if zone_id is None:
        return None
    zone = get_zone_or_404(db, zone_id)
    if zone.warehouse_id != warehouse_id:
        raise BizException(ErrorCode.BAD_REQUEST, "库区不属于所选仓库")
    return zone


def _validate_location_unique(
    db: Session,
    *,
    code: str,
    exclude_id: int | None = None,
) -> None:
    stmt = select(Location.id).where(Location.code == code)
    if exclude_id is not None:
        stmt = stmt.where(Location.id != exclude_id)
    if db.scalar(stmt) is not None:
        raise BizException(ErrorCode.CONFLICT, f"库位编码已存在：{code}")


def _validate_capacity_value(value: Any) -> int:
    return _to_int(value, "容量", minimum=0)


def list_locations(
    db: Session,
    *,
    warehouse_id: int | None = None,
    zone_id: int | None = None,
    is_enabled: bool | None = None,
    keyword: str | None = None,
) -> list[Location]:
    conditions = []
    if warehouse_id is not None:
        get_warehouse_or_404(db, warehouse_id)
        conditions.append(Location.warehouse_id == warehouse_id)
    if zone_id is not None:
        zone = get_zone_or_404(db, zone_id)
        if warehouse_id is not None and zone.warehouse_id != warehouse_id:
            return []
        conditions.append(Location.zone_id == zone_id)
    if is_enabled is not None:
        conditions.append(Location.is_enabled.is_(is_enabled))
    if keyword:
        conditions.append(Location.code.like(f"%{keyword.strip()}%"))
    return list(
        db.scalars(
            select(Location)
            .where(*conditions)
            .order_by(
                Location.priority.desc(),
                Location.sort_order,
                Location.code,
            )
        )
    )


def create_location(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Location:
    _check_unknown_fields(data, LOCATION_FIELDS)
    warehouse_id = _optional_id(data.get("warehouse_id"), "所属仓库")
    if warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "所属仓库不能为空")
    warehouse = _validate_location_warehouse(db, warehouse_id)
    zone_id = _optional_id(data.get("zone_id"), "库区")
    zone = _validate_location_zone(
        db, warehouse_id=warehouse_id, zone_id=zone_id
    )
    row_no = _to_int(data.get("row_no", 1), "行号", minimum=1)
    col_no = _to_int(data.get("col_no", 1), "列号", minimum=1)
    layer_no = _to_int(data.get("layer_no", 1), "层号", minimum=1)
    code = _optional_text(data.get("code"), "库位编码", 64)
    if code is None:
        code = generate_location_code(
            warehouse,
            zone,
            row_no=row_no,
            col_no=col_no,
            layer_no=layer_no,
        )
    _validate_location_unique(db, code=code)
    location = Location(
        warehouse_id=warehouse_id,
        zone_id=zone_id,
        code=code,
        row_no=row_no,
        col_no=col_no,
        layer_no=layer_no,
        capacity=_validate_capacity_value(data.get("capacity", 0)),
        priority=_to_int(data.get("priority", 0), "优先级"),
        sort_order=_to_int(data.get("sort_order", 0), "排序"),
        is_virtual=_to_bool(data.get("is_virtual", False), "虚拟库位"),
        is_enabled=_to_bool(data.get("is_enabled", True), "启用状态"),
    )
    db.add(location)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="location",
        target_id=str(location.id),
        summary=f"新增库位：{location.code}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return location


def generate_locations(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> list[Location]:
    """按行、列、层三重循环批量生成库位。"""
    allowed = {
        "warehouse_id",
        "zone_id",
        "row_count",
        "col_count",
        "layer_count",
        "capacity",
        "priority",
        "sort_order",
        "is_virtual",
        "is_enabled",
    }
    _check_unknown_fields(data, allowed)
    warehouse_id = _optional_id(data.get("warehouse_id"), "所属仓库")
    zone_id = _optional_id(data.get("zone_id"), "库区")
    if warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "所属仓库不能为空")
    if zone_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "库区不能为空")
    warehouse = _validate_location_warehouse(db, warehouse_id)
    zone = _validate_location_zone(
        db, warehouse_id=warehouse_id, zone_id=zone_id
    )
    row_count = _to_int(data.get("row_count"), "货架行数", minimum=1)
    col_count = _to_int(data.get("col_count"), "每货架列数", minimum=1)
    layer_count = _to_int(data.get("layer_count"), "每货架层数", minimum=1)
    capacity = _validate_capacity_value(data.get("capacity", 0))
    priority = _to_int(data.get("priority", 0), "优先级")
    sort_order = _to_int(data.get("sort_order", 0), "排序")
    is_virtual = _to_bool(data.get("is_virtual", False), "虚拟库位")
    is_enabled = _to_bool(data.get("is_enabled", True), "启用状态")

    generated: list[tuple[str, int, int, int]] = []
    for row_no in range(1, row_count + 1):
        for col_no in range(1, col_count + 1):
            for layer_no in range(1, layer_count + 1):
                code = generate_location_code(
                    warehouse,
                    zone,
                    row_no=row_no,
                    col_no=col_no,
                    layer_no=layer_no,
                )
                generated.append((code, row_no, col_no, layer_no))

    codes = [item[0] for item in generated]
    existing = db.scalar(
        select(Location.code).where(Location.code.in_(codes)).limit(1)
    )
    if existing is not None:
        raise BizException(ErrorCode.CONFLICT, f"库位编码已存在：{existing}")

    locations: list[Location] = []
    for code, row_no, col_no, layer_no in generated:
        location = Location(
            warehouse_id=warehouse_id,
            zone_id=zone_id,
            code=code,
            row_no=row_no,
            col_no=col_no,
            layer_no=layer_no,
            capacity=capacity,
            priority=priority,
            sort_order=sort_order,
            is_virtual=is_virtual,
            is_enabled=is_enabled,
        )
        db.add(location)
        locations.append(location)
    db.flush()
    write_audit(
        db,
        action="GENERATE",
        target_type="location",
        target_id=str(zone.id),
        summary=f"批量生成库位：{zone.code} 共 {len(locations)} 个",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return locations


def update_location(
    db: Session,
    *,
    location_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Location:
    _check_unknown_fields(data, LOCATION_FIELDS)
    location = get_location_or_404(db, location_id)
    warehouse = _validate_location_warehouse(db, location.warehouse_id)
    if "warehouse_id" in data:
        warehouse_id = _optional_id(data["warehouse_id"], "所属仓库")
        if warehouse_id != location.warehouse_id:
            raise BizException(ErrorCode.BAD_REQUEST, "库位不能跨仓库调整")

    zone_id = location.zone_id
    zone = db.get(Zone, zone_id) if zone_id else None
    if "zone_id" in data:
        zone_id = _optional_id(data["zone_id"], "库区")
        zone = _validate_location_zone(
            db, warehouse_id=location.warehouse_id, zone_id=zone_id
        )

    row_no = (
        _to_int(data["row_no"], "行号", minimum=1)
        if "row_no" in data
        else location.row_no
    )
    col_no = (
        _to_int(data["col_no"], "列号", minimum=1)
        if "col_no" in data
        else location.col_no
    )
    layer_no = (
        _to_int(data["layer_no"], "层号", minimum=1)
        if "layer_no" in data
        else location.layer_no
    )
    capacity = (
        _validate_capacity_value(data["capacity"])
        if "capacity" in data
        else location.capacity
    )
    if capacity and _location_used_qty(db, location.id) > to_decimal(capacity):
        raise BizException(
            ErrorCode.CONFLICT,
            "库位已用容量超过新容量，不能修改",
        )

    if "code" in data:
        code = _require_text(data["code"], "库位编码", 64)
    elif any(
        field in data for field in ("zone_id", "row_no", "col_no", "layer_no")
    ):
        code = generate_location_code(
            warehouse,
            zone,
            row_no=row_no,
            col_no=col_no,
            layer_no=layer_no,
        )
    else:
        code = location.code
    _validate_location_unique(db, code=code, exclude_id=location.id)

    location.zone_id = zone_id
    location.code = code
    location.row_no = row_no
    location.col_no = col_no
    location.layer_no = layer_no
    location.capacity = capacity
    if "priority" in data:
        location.priority = _to_int(data["priority"], "优先级")
    if "sort_order" in data:
        location.sort_order = _to_int(data["sort_order"], "排序")
    if "is_virtual" in data:
        location.is_virtual = _to_bool(data["is_virtual"], "虚拟库位")
    if "is_enabled" in data:
        location.is_enabled = _to_bool(data["is_enabled"], "启用状态")

    write_audit(
        db,
        action="UPDATE",
        target_type="location",
        target_id=str(location.id),
        summary=f"修改库位：{location.code}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return location


def set_location_enabled(
    db: Session,
    *,
    location_id: int,
    enabled: bool,
    staff: Staff,
    commit: bool = True,
) -> Location:
    location = get_location_or_404(db, location_id)
    location.is_enabled = enabled
    write_audit(
        db,
        action="ENABLE" if enabled else "DISABLE",
        target_type="location",
        target_id=str(location.id),
        summary=f"{'启用' if enabled else '停用'}库位：{location.code}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return location


def delete_location(
    db: Session, *, location_id: int, staff: Staff, commit: bool = True
) -> None:
    location = get_location_or_404(db, location_id)
    referenced = _referenced_rows(
        db,
        target_fullname="location.id",
        target_id=location.id,
    )
    if referenced:
        raise BizException(
            ErrorCode.CONFLICT,
            f"库位已被 {','.join(sorted(referenced))} 引用，不能删除，请改为停用",
        )
    code = location.code
    db.delete(location)
    write_audit(
        db,
        action="DELETE",
        target_type="location",
        target_id=str(location.id),
        summary=f"删除库位：{code}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
