"""库存盘点服务（TASK-034）。

业务流程：
1. 创建盘点单时按仓库冻结库位现有库存，可选锁盘；
2. 盘点结果可逐条维护或通过 Excel 导入，导入和录入均包含库位；
3. 提交并完成审批后，复核盘点期间库存未发生变化；
4. 按明细差额调整库位库存，盘盈生成盘点入库单，盘亏生成盘点出库单，
   同时写入 ``CHECK_IN`` / ``CHECK_OUT`` 库存流水；
5. 盘点结束、驳回、作废或删除后解除盘点锁盘。

当前 ``stock_check_item`` 没有库存状态字段，因此按仓库类型映射
``NORMAL`` / ``DEFECTIVE`` 库存状态：实物仓盘点正常品，次品仓盘点次品。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.audit_log import AuditLog
from app.models.org import Staff
from app.models.product import Product, ProductSku
from app.models.stock import (
    StockCheck,
    StockCheckItem,
    StockFlow,
    StockIn,
    StockInItem,
    StockOut,
    StockOutItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, stock_in_service, warehouse_service
from app.services.excel import (
    Column,
    ParseResult,
    SheetSpec,
    export_data,
    parse_data,
    register_template,
)

DOC_TYPE = "stock_check"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_TERMINATED = "TERMINATED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
MAX_ITEMS = 5000

CHECK_FIELDS = {"warehouse_id", "lock_warehouse", "remark", "items"}
CHECK_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "location_id",
    "actual_qty",
    "barcode",
}

STOCK_CHECK_TEMPLATE = SheetSpec(
    name="stock_check",
    sheet_title="库存盘点",
    columns=[
        Column("product_code", "商品编码", required=True, width=18),
        Column("product_name", "商品名称", width=22),
        Column("spec_code", "规格编码", required=True, width=18),
        Column("spec_name", "规格名称", width=18),
        Column("location_code", "所在库位", required=True, width=22),
        Column("barcode", "条形码", width=18),
        Column("current_qty", "现有库存", type="decimal", width=14),
        Column("actual_qty", "盘点实存", required=True, type="decimal", width=14),
    ],
)
register_template("stock_check", STOCK_CHECK_TEMPLATE)


def _now() -> datetime:
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


def _to_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为布尔值")


def _to_qty(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value)).quantize(
            QTY_QUANT,
            rounding=ROUND_HALF_UP,
        )
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result < ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能小于 0")
    return result


def _optional_qty(value: Any, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    return _to_qty(value, label)


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


def _get_warehouse_or_404(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise BizException(ErrorCode.NOT_FOUND, "盘点仓库不存在")
    return warehouse


def _get_product_or_404(db: Session, product_id: int) -> Product:
    product = db.get(Product, product_id)
    if product is None:
        raise BizException(ErrorCode.BAD_REQUEST, "商品不存在")
    if product.is_disabled:
        raise BizException(ErrorCode.BAD_REQUEST, f"商品已禁用：{product.name}")
    return product


def _get_sku_or_404(
    db: Session,
    *,
    product_id: int,
    sku_id: int,
) -> ProductSku:
    sku = db.get(ProductSku, sku_id)
    if sku is None or sku.product_id != product_id:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格不属于该商品")
    if not sku.is_active:
        raise BizException(ErrorCode.BAD_REQUEST, "商品规格已停用")
    return sku


def _get_check_location_or_404(
    db: Session,
    *,
    warehouse_id: int,
    location_id: int,
) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise BizException(ErrorCode.NOT_FOUND, "盘点库位不存在")
    if location.warehouse_id != warehouse_id:
        raise BizException(ErrorCode.BAD_REQUEST, "盘点库位不属于当前仓库")
    if location.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟库位不能参与库存盘点")
    return location


def _stock_row(
    db: Session,
    *,
    location_id: int,
    sku_id: int,
    status: str,
    for_update: bool = False,
) -> StockLocation | None:
    stmt = select(StockLocation).where(
        StockLocation.location_id == location_id,
        StockLocation.product_sku_id == sku_id,
        StockLocation.status == status,
    )
    if for_update:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def _current_qty(
    db: Session,
    *,
    warehouse: Warehouse,
    location_id: int,
    sku_id: int,
    for_update: bool = False,
) -> Decimal:
    row = _stock_row(
        db,
        location_id=location_id,
        sku_id=sku_id,
        status=_stock_status(warehouse),
        for_update=for_update,
    )
    return Decimal(row.qty) if row is not None else ZERO_QTY


def _active_check(
    db: Session,
    *,
    warehouse_id: int,
    exclude_id: int | None = None,
) -> StockCheck | None:
    stmt = select(StockCheck).where(
        StockCheck.warehouse_id == warehouse_id,
        StockCheck.status == STATUS_PENDING,
    )
    if exclude_id is not None:
        stmt = stmt.where(StockCheck.id != exclude_id)
    return db.scalar(stmt.order_by(StockCheck.id.desc()))


def _set_warehouse_lock(
    db: Session,
    *,
    warehouse: Warehouse,
    enabled: bool,
    staff: Staff,
    reason: str,
) -> None:
    if enabled:
        if not warehouse.is_enabled:
            raise BizException(ErrorCode.CONFLICT, "仓库已停用，不能开始盘点")
        if warehouse.lock_type != "NONE":
            raise BizException(
                ErrorCode.CONFLICT,
                "仓库已被锁定，不能开始盘点",
            )
        warehouse.lock_type = "CHECK"
        warehouse.is_default = False
    elif warehouse.lock_type == "CHECK":
        warehouse.lock_type = "NONE"
    else:
        return
    write_audit(
        db,
        action="LOCK" if enabled else "UNLOCK",
        target_type=DOC_TYPE,
        target_id=str(warehouse.id),
        summary=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )


def _release_warehouse_lock(
    db: Session,
    *,
    record: StockCheck,
    staff: Staff,
) -> None:
    if not record.lock_warehouse:
        return
    warehouse = db.get(Warehouse, record.warehouse_id)
    if warehouse is not None and warehouse.lock_type == "CHECK":
        warehouse.lock_type = "NONE"
        write_audit(
            db,
            action="UNLOCK",
            target_type=DOC_TYPE,
            target_id=str(record.id),
            summary=f"盘点结束解锁仓库：{record.check_no}",
            actor_id=staff.id,
            actor_name=staff.name,
        )


def _snapshot_items(db: Session, warehouse: Warehouse) -> list[dict[str, Any]]:
    status = _stock_status(warehouse)
    rows = list(
        db.execute(
            select(
                StockLocation.product_sku_id,
                StockLocation.location_id,
                StockLocation.qty,
            )
            .join(Location, Location.id == StockLocation.location_id)
            .where(
                Location.warehouse_id == warehouse.id,
                StockLocation.status == status,
            )
            .order_by(
                StockLocation.location_id,
                StockLocation.product_sku_id,
            )
        )
    )
    result: list[dict[str, Any]] = []
    for row in rows:
        sku = db.get(ProductSku, row.product_sku_id)
        if sku is None:
            continue
        result.append(
            {
                "product_id": sku.product_id,
                "sku_id": sku.id,
                "location_id": row.location_id,
                "current_qty": Decimal(row.qty),
                "actual_qty": None,
                "barcode": sku.barcode,
            }
        )
    return result


def _normalize_items(
    db: Session,
    *,
    warehouse: Warehouse,
    raw_items: Any,
) -> list[dict[str, Any]]:
    if raw_items is None:
        result = _snapshot_items(db, warehouse)
        if not result:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "仓库没有可盘点的库存，请先选择盘点商品或完成入库上架",
            )
        return result
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少添加一条盘点明细")
    if len(raw_items) > MAX_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"单张盘点单最多 {MAX_ITEMS} 条明细",
        )

    result: list[dict[str, Any]] = []
    keys: set[tuple[int, int, int]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "盘点明细格式错误")
        _check_unknown_fields(raw, CHECK_ITEM_FIELDS)
        product = _get_product_or_404(
            db, _to_int_id(raw.get("product_id"), "商品")
        )
        sku = _get_sku_or_404(
            db,
            product_id=product.id,
            sku_id=_to_int_id(raw.get("sku_id"), "商品规格"),
        )
        location = _get_check_location_or_404(
            db,
            warehouse_id=warehouse.id,
            location_id=_to_int_id(raw.get("location_id"), "盘点库位"),
        )
        key = (product.id, sku.id, location.id)
        if key in keys:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "同一商品规格和库位不能重复盘点",
            )
        keys.add(key)
        result.append(
            {
                "product_id": product.id,
                "sku_id": sku.id,
                "location_id": location.id,
                "current_qty": _current_qty(
                    db,
                    warehouse=warehouse,
                    location_id=location.id,
                    sku_id=sku.id,
                ),
                "actual_qty": _optional_qty(raw.get("actual_qty"), "盘点实存"),
                "barcode": (
                    _optional_text(raw.get("barcode"), "条形码", 64)
                    or sku.barcode
                ),
            }
        )
    return result


def _check_items(db: Session, check_id: int) -> list[StockCheckItem]:
    return list(
        db.scalars(
            select(StockCheckItem)
            .where(StockCheckItem.check_id == check_id)
            .order_by(
                StockCheckItem.location_id,
                StockCheckItem.product_id,
                StockCheckItem.sku_id,
                StockCheckItem.id,
            )
        )
    )


def _check_item_dict(db: Session, item: StockCheckItem) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    location = db.get(Location, item.location_id) if item.location_id else None
    actual_qty = Decimal(item.actual_qty) if item.actual_qty is not None else None
    difference_qty = (
        actual_qty - Decimal(item.current_qty)
        if actual_qty is not None
        else None
    )
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_code": product.code if product else None,
        "product_name": product.name if product else None,
        "sku_id": item.sku_id,
        "spec_code": sku.spec_code if sku else None,
        "spec_name": sku.spec_name if sku else None,
        "barcode": item.barcode or (sku.barcode if sku else None),
        "location_id": item.location_id,
        "location_code": location.code if location else None,
        "current_qty": item.current_qty,
        "actual_qty": actual_qty,
        "difference_qty": difference_qty,
    }


def _approval_state(db: Session, check_id: int) -> dict:
    return approval_service.get_state(
        db,
        doc_type=DOC_TYPE,
        doc_id=check_id,
    )["state"]


def _has_imported_result(db: Session, check_id: int) -> bool:
    return (
        db.scalar(
            select(func.count(AuditLog.id)).where(
                AuditLog.target_type == DOC_TYPE,
                AuditLog.target_id == str(check_id),
                AuditLog.action == "IMPORT_RESULT",
            )
        )
        or 0
    ) > 0


def get_stock_check_or_404(db: Session, check_id: int, *, check_scope: bool = True) -> StockCheck:
    record = db.get(StockCheck, check_id)
    if record is None or (check_scope and not document_visible(db, record.created_by, DOC_TYPE, record.id)):
        raise BizException(ErrorCode.NOT_FOUND, "库存盘点单不存在")
    return record


def stock_check_dict(
    db: Session,
    record: StockCheck,
    *,
    with_items: bool = True,
    with_approval: bool = False,
) -> dict:
    warehouse = db.get(Warehouse, record.warehouse_id)
    creator = db.get(Staff, record.created_by) if record.created_by else None
    auditor = db.get(Staff, record.auditor_id) if record.auditor_id else None
    items = _check_items(db, record.id)
    current_total = sum(
        (Decimal(item.current_qty) for item in items),
        ZERO_QTY,
    )
    actual_total = sum(
        (
            Decimal(item.actual_qty)
            for item in items
            if item.actual_qty is not None
        ),
        ZERO_QTY,
    )
    gain_total = sum(
        (
            max(Decimal(item.actual_qty) - Decimal(item.current_qty), ZERO_QTY)
            for item in items
            if item.actual_qty is not None
        ),
        ZERO_QTY,
    )
    loss_total = sum(
        (
            max(Decimal(item.current_qty) - Decimal(item.actual_qty), ZERO_QTY)
            for item in items
            if item.actual_qty is not None
        ),
        ZERO_QTY,
    )
    result = {
        "id": record.id,
        "check_no": record.check_no,
        "warehouse_id": record.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "lock_warehouse": record.lock_warehouse,
        "warehouse_locked": (
            warehouse is not None and warehouse.lock_type == "CHECK"
        ),
        "status": record.status,
        "audit_status": record.audit_status,
        "remark": record.remark,
        "has_imported_result": _has_imported_result(db, record.id),
        "item_count": len(items),
        "result_entered_count": sum(
            1 for item in items if item.actual_qty is not None
        ),
        "current_total_qty": current_total,
        "actual_total_qty": actual_total,
        "gain_total_qty": gain_total,
        "loss_total_qty": loss_total,
        "created_by": record.created_by,
        "created_by_name": creator.name if creator else None,
        "check_time": record.created_at,
        "created_at": record.created_at,
        "auditor_id": record.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": record.audit_time,
        "updated_at": record.updated_at,
    }
    if with_items:
        result["items"] = [
            _check_item_dict(db, item) for item in items
        ]
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=DOC_TYPE,
            doc_id=record.id,
        )
    return result


def list_stock_checks(
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
    conditions = scope_conditions(db, StockCheck.created_by)
    if warehouse_id is not None:
        conditions.append(StockCheck.warehouse_id == warehouse_id)
    if status:
        conditions.append(StockCheck.status == status.upper())
    if audit_status:
        conditions.append(StockCheck.audit_status == audit_status.upper())
    if keyword:
        conditions.append(
            StockCheck.check_no.like(f"%{keyword.strip()}%")
        )
    total = db.scalar(
        select(func.count(StockCheck.id)).where(*conditions)
    ) or 0
    rows = list(
        db.scalars(
            select(StockCheck)
            .where(*conditions)
            .order_by(StockCheck.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            stock_check_dict(db, row, with_items=False)
            for row in rows
        ],
    }


def create_stock_check(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockCheck:
    _check_unknown_fields(data, CHECK_FIELDS)
    warehouse = _get_warehouse_or_404(
        db,
        _to_int_id(data.get("warehouse_id"), "盘点仓库"),
    )
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "仓库已停用，不能盘点")
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟仓不能进行库存盘点")
    if _active_check(db, warehouse_id=warehouse.id) is not None:
        raise BizException(
            ErrorCode.CONFLICT,
            "该仓库已有进行中的盘点单，不能重复创建",
        )

    lock_warehouse = _to_bool(
        data.get("lock_warehouse", True),
        "仓库锁盘",
    )
    if lock_warehouse:
        _set_warehouse_lock(
            db,
            warehouse=warehouse,
            enabled=True,
            staff=staff,
            reason=f"开始盘点锁盘：{warehouse.code}",
        )
    elif warehouse.lock_type != "NONE":
        raise BizException(ErrorCode.CONFLICT, "仓库已被锁定，不能开始盘点")

    try:
        items = _normalize_items(
            db,
            warehouse=warehouse,
            raw_items=data.get("items"),
        )
        record = StockCheck(
            check_no=generate_serial(SerialPrefix.STOCK_CHECK, dt=_now()),
            warehouse_id=warehouse.id,
            lock_warehouse=lock_warehouse,
            status=STATUS_PENDING,
            audit_status=AUDIT_PENDING,
            remark=_optional_text(data.get("remark"), "备注", 255),
            created_by=staff.id,
        )
        db.add(record)
        db.flush()
        for item in items:
            db.add(StockCheckItem(check_id=record.id, **item))
        db.flush()
        write_audit(
            db,
            action="CREATE",
            target_type=DOC_TYPE,
            target_id=str(record.id),
            summary=f"新增库存盘点单：{record.check_no}",
            detail={
                "warehouse_id": warehouse.id,
                "lock_warehouse": lock_warehouse,
                "item_count": len(items),
            },
            actor_id=staff.id,
            actor_name=staff.name,
        )
        _commit(db, commit)
        return record
    except Exception:
        db.rollback()
        raise


def _ensure_editable(db: Session, record: StockCheck) -> None:
    if record.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前盘点单状态不允许修改")
    state = _approval_state(db, record.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(
            ErrorCode.CONFLICT,
            "盘点单已进入审批流程，不能修改",
        )


def update_stock_check(
    db: Session,
    *,
    check_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockCheck:
    _check_unknown_fields(data, CHECK_FIELDS - {"warehouse_id"})
    record = get_stock_check_or_404(db, check_id)
    _ensure_editable(db, record)
    warehouse = _get_warehouse_or_404(db, record.warehouse_id)

    if "lock_warehouse" in data:
        lock_warehouse = _to_bool(data["lock_warehouse"], "仓库锁盘")
        if lock_warehouse != record.lock_warehouse:
            _set_warehouse_lock(
                db,
                warehouse=warehouse,
                enabled=lock_warehouse,
                staff=staff,
                reason=(
                    f"{'锁定' if lock_warehouse else '解锁'}盘点仓库："
                    f"{record.check_no}"
                ),
            )
            record.lock_warehouse = lock_warehouse

    if "remark" in data:
        record.remark = _optional_text(data["remark"], "备注", 255)

    if "items" in data:
        items = _normalize_items(
            db,
            warehouse=warehouse,
            raw_items=data["items"],
        )
        db.execute(
            delete(StockCheckItem).where(
                StockCheckItem.check_id == record.id
            )
        )
        db.flush()
        for item in items:
            db.add(StockCheckItem(check_id=record.id, **item))
        db.flush()

    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"修改库存盘点单：{record.check_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_stock_check(
    db: Session,
    *,
    check_id: int,
    staff: Staff,
    commit: bool = True,
) -> None:
    record = get_stock_check_or_404(db, check_id)
    _ensure_editable(db, record)
    _release_warehouse_lock(db, record=record, staff=staff)
    db.execute(
        delete(StockCheckItem).where(StockCheckItem.check_id == record.id)
    )
    check_no = record.check_no
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"删除库存盘点单：{check_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _ensure_result_complete(items: list[StockCheckItem]) -> None:
    if not items:
        raise BizException(ErrorCode.BAD_REQUEST, "盘点单没有商品明细")
    missing = [item for item in items if item.actual_qty is None]
    if missing:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"仍有 {len(missing)} 条明细未录入盘点实存",
        )


def _ensure_stock_snapshot(
    db: Session,
    *,
    record: StockCheck,
    items: list[StockCheckItem],
    for_update: bool = False,
) -> None:
    warehouse = _get_warehouse_or_404(db, record.warehouse_id)
    for item in items:
        if item.sku_id is None:
            raise BizException(
                ErrorCode.CONFLICT,
                "盘点明细必须指定商品规格",
            )
        current = _current_qty(
            db,
            warehouse=warehouse,
            location_id=item.location_id,
            sku_id=item.sku_id,
            for_update=for_update,
        )
        if current != Decimal(item.current_qty):
            raise BizException(
                ErrorCode.CONFLICT,
                f"盘点期间库存发生变化，请重新盘点：{item.id}",
            )


def _map_audit_status(record: StockCheck, state: dict) -> None:
    if state["status"] == "APPROVED":
        record.audit_status = AUDIT_APPROVED
    elif state["status"] == "REJECTED":
        record.audit_status = AUDIT_REJECTED
    else:
        record.audit_status = AUDIT_PENDING


def submit_stock_check(
    db: Session,
    *,
    check_id: int,
    staff: Staff,
    commit: bool = True,
) -> StockCheck:
    record = get_stock_check_or_404(db, check_id)
    _ensure_editable(db, record)
    items = _check_items(db, record.id)
    _ensure_result_complete(items)
    _ensure_stock_snapshot(db, record=record, items=items)
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=record.id,
        doc_no=record.check_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_PENDING
    record.auditor_id = None
    record.audit_time = None
    _commit(db, commit)
    return record


def _cost_price(product: Product, sku: ProductSku) -> Decimal | None:
    if sku.cost_price is not None:
        return round_money(sku.cost_price)
    if product.cost_price is not None:
        return round_money(product.cost_price)
    return None


def _prepare_application(
    db: Session,
    *,
    record: StockCheck,
) -> list[dict[str, Any]]:
    warehouse = _get_warehouse_or_404(db, record.warehouse_id)
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "盘点仓库已停用")
    if warehouse.is_virtual:
        raise BizException(ErrorCode.CONFLICT, "虚拟仓不能进行库存盘点")
    if record.lock_warehouse and warehouse.lock_type != "CHECK":
        raise BizException(
            ErrorCode.CONFLICT,
            "盘点锁盘状态已变更，请重新发起盘点",
        )
    items = _check_items(db, record.id)
    _ensure_result_complete(items)
    _ensure_stock_snapshot(
        db,
        record=record,
        items=items,
        for_update=True,
    )

    prepared: list[dict[str, Any]] = []
    delta_by_location: dict[int, Decimal] = {}
    for item in items:
        product = db.get(Product, item.product_id)
        sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
        location = db.get(Location, item.location_id)
        if product is None or sku is None or location is None:
            raise BizException(
                ErrorCode.CONFLICT,
                "盘点明细关联数据不存在",
            )
        if location.warehouse_id != record.warehouse_id:
            raise BizException(
                ErrorCode.CONFLICT,
                "盘点库位不属于当前仓库",
            )
        current_qty = Decimal(item.current_qty)
        actual_qty = Decimal(item.actual_qty)
        delta = actual_qty - current_qty
        if delta != ZERO_QTY:
            delta_by_location[location.id] = (
                delta_by_location.get(location.id, ZERO_QTY) + delta
            )
        prepared.append(
            {
                "item": item,
                "product": product,
                "sku": sku,
                "location": location,
                "current_qty": current_qty,
                "actual_qty": actual_qty,
                "delta": delta,
                "cost_price": _cost_price(product, sku),
            }
        )

    for location_id, delta in delta_by_location.items():
        if delta <= ZERO_QTY:
            continue
        location = db.get(Location, location_id)
        state = warehouse_service.location_capacity_state(db, location_id)
        if location.capacity and state["used_qty"] + delta > Decimal(
            location.capacity
        ):
            raise BizException(
                ErrorCode.CONFLICT,
                f"盘盈后库位容量不足：{location.code}",
            )
    return prepared


def _flow_cost(
    *,
    price: Decimal | None,
    qty: Decimal,
) -> Decimal | None:
    return round_money(price * qty) if price is not None else None


def _apply_stock_check(
    db: Session,
    *,
    record: StockCheck,
    staff: Staff,
) -> None:
    warehouse = _get_warehouse_or_404(db, record.warehouse_id)
    stock_status = _stock_status(warehouse)
    prepared = _prepare_application(db, record=record)
    gains = [row for row in prepared if row["delta"] > ZERO_QTY]
    losses = [row for row in prepared if row["delta"] < ZERO_QTY]
    stock_in_no = (
        generate_serial(SerialPrefix.STOCK_IN, dt=_now())
        if gains
        else None
    )
    stock_out_no = (
        generate_serial(SerialPrefix.STOCK_OUT, dt=_now())
        if losses
        else None
    )

    for row in prepared:
        item = row["item"]
        delta = row["delta"]
        if delta == ZERO_QTY:
            continue
        stock_row = _stock_row(
            db,
            location_id=item.location_id,
            sku_id=item.sku_id,
            status=stock_status,
            for_update=True,
        )
        if delta > ZERO_QTY and stock_row is None:
            stock_row = StockLocation(
                location_id=item.location_id,
                product_sku_id=item.sku_id,
                qty=ZERO_QTY,
                status=stock_status,
            )
            db.add(stock_row)
            db.flush()
        if stock_row is None:
            raise BizException(ErrorCode.CONFLICT, "盘点库存明细不存在")
        stock_row.qty = row["actual_qty"]
        db.add(
            StockFlow(
                warehouse_id=record.warehouse_id,
                product_id=item.product_id,
                sku_id=item.sku_id,
                location_id=item.location_id,
                unit_id=row["product"].main_unit_id,
                direction="IN" if delta > ZERO_QTY else "OUT",
                biz_type="CHECK_IN" if delta > ZERO_QTY else "CHECK_OUT",
                qty=abs(delta),
                cost_price=row["cost_price"],
                total_cost=_flow_cost(
                    price=row["cost_price"],
                    qty=abs(delta),
                ),
                source_no=record.check_no,
                order_no=stock_in_no if delta > ZERO_QTY else stock_out_no,
                flow_time=_now(),
                remark=f"库存盘点:{record.check_no}",
            )
        )
    db.flush()

    if gains:
        stock_in = StockIn(
            stock_in_no=stock_in_no,
            warehouse_id=record.warehouse_id,
            stock_in_type="CHECK",
            status="COMPLETED",
            audit_status=AUDIT_APPROVED,
            total_qty=sum((row["delta"] for row in gains), ZERO_QTY),
            remark=f"盘点盘盈:{record.check_no}",
            created_by=record.created_by,
            auditor_id=staff.id,
            audit_time=_now(),
        )
        db.add(stock_in)
        db.flush()
        for row in gains:
            db.add(
                StockInItem(
                    stock_in_id=stock_in.id,
                    product_id=row["item"].product_id,
                    sku_id=row["item"].sku_id,
                    qty=row["delta"],
                    cost_price=row["cost_price"],
                    amount=_flow_cost(
                        price=row["cost_price"],
                        qty=row["delta"],
                    ),
                    barcode=row["item"].barcode,
                )
            )
        db.flush()
        stock_in_service.generate_stock_in_voucher(
            db, stock_in=stock_in, staff=staff, commit=False
        )

    if losses:
        stock_out = StockOut(
            stock_out_no=stock_out_no,
            warehouse_id=record.warehouse_id,
            stock_out_type="CHECK",
            status="COMPLETED",
            audit_status=AUDIT_APPROVED,
            off_shelf_status="DONE",
            total_qty=sum((abs(row["delta"]) for row in losses), ZERO_QTY),
            remark=f"盘点盘亏:{record.check_no}",
            created_by=record.created_by,
            auditor_id=staff.id,
            audit_time=_now(),
        )
        db.add(stock_out)
        db.flush()
        for row in losses:
            db.add(
                StockOutItem(
                    stock_out_id=stock_out.id,
                    product_id=row["item"].product_id,
                    sku_id=row["item"].sku_id,
                    location_id=row["item"].location_id,
                    qty=abs(row["delta"]),
                    cost_price=row["cost_price"],
                    barcode=row["item"].barcode,
                )
            )
    db.flush()
    write_audit(
        db,
        action="APPLY",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"盘点结果更新库存：{record.check_no}",
        detail={
            "stock_in_no": stock_in_no,
            "stock_out_no": stock_out_no,
            "gain_qty": str(
                sum((row["delta"] for row in gains), ZERO_QTY)
            ),
            "loss_qty": str(
                sum((abs(row["delta"]) for row in losses), ZERO_QTY)
            ),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )


def approve_stock_check(
    db: Session,
    *,
    check_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> StockCheck:
    record = get_stock_check_or_404(db, check_id, check_scope=False)
    if record.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前盘点单状态不能审核")
    try:
        state = approval_service.approve(
            db,
            doc_type=DOC_TYPE,
            doc_id=record.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(record, state)
        if state["status"] == "APPROVED":
            _apply_stock_check(db, record=record, staff=staff)
            record.status = STATUS_APPROVED
            record.auditor_id = staff.id
            record.audit_time = _now()
            _release_warehouse_lock(db, record=record, staff=staff)
            write_audit(
                db,
                action="APPROVE",
                target_type=DOC_TYPE,
                target_id=str(record.id),
                summary=f"库存盘点审核完成：{record.check_no}",
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


def reject_stock_check(
    db: Session,
    *,
    check_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> StockCheck:
    record = get_stock_check_or_404(db, check_id, check_scope=False)
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前盘点单状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=record.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_REJECTED
    record.auditor_id = staff.id
    record.audit_time = _now()
    _release_warehouse_lock(db, record=record, staff=staff)
    _commit(db, commit)
    return record


def terminate_stock_check(
    db: Session,
    *,
    check_id: int,
    staff: Staff,
    reason: str,
    commit: bool = True,
) -> StockCheck:
    reason_text = _optional_text(reason, "作废原因", 255)
    if reason_text is None:
        raise BizException(ErrorCode.BAD_REQUEST, "作废原因不能为空")
    record = get_stock_check_or_404(db, check_id)
    _ensure_editable(db, record)
    record.status = STATUS_TERMINATED
    _release_warehouse_lock(db, record=record, staff=staff)
    write_audit(
        db,
        action="TERMINATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"作废库存盘点单：{record.check_no}",
        reason=reason_text,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def export_stock_check(db: Session, *, check_id: int) -> bytes:
    record = get_stock_check_or_404(db, check_id)
    rows = []
    for item in _check_items(db, record.id):
        data = _check_item_dict(db, item)
        rows.append(
            {
                "product_code": data["product_code"],
                "product_name": data["product_name"],
                "spec_code": data["spec_code"],
                "spec_name": data["spec_name"],
                "location_code": data["location_code"],
                "barcode": data["barcode"],
                "current_qty": data["current_qty"],
                "actual_qty": data["actual_qty"],
            }
        )
    return export_data(STOCK_CHECK_TEMPLATE, rows)


def _lookup_import_rows(
    db: Session,
    *,
    warehouse: Warehouse,
    parsed: ParseResult,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors = list(parsed.errors)
    product_codes = {
        str(row.get("product_code") or "").strip()
        for row in parsed.rows
    }
    spec_codes = {
        str(row.get("spec_code") or "").strip()
        for row in parsed.rows
    }
    location_codes = {
        str(row.get("location_code") or "").strip()
        for row in parsed.rows
    }
    products = {
        row.code: row
        for row in db.scalars(
            select(Product).where(Product.code.in_(product_codes))
        )
    }
    skus = {
        row.spec_code: row
        for row in db.scalars(
            select(ProductSku).where(ProductSku.spec_code.in_(spec_codes))
        )
    }
    locations = {
        row.code: row
        for row in db.scalars(
            select(Location).where(Location.code.in_(location_codes))
        )
    }

    result: list[dict[str, Any]] = []
    keys: set[tuple[int, int, int]] = set()
    for index, row in enumerate(parsed.rows, start=2):
        product_code = str(row.get("product_code") or "").strip()
        spec_code = str(row.get("spec_code") or "").strip()
        location_code = str(row.get("location_code") or "").strip()
        product = products.get(product_code)
        sku = skus.get(spec_code)
        location = locations.get(location_code)
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
                    "message": f"规格编码不存在或不属于商品：{spec_code}",
                }
            )
            continue
        if location is None or location.warehouse_id != warehouse.id:
            errors.append(
                {
                    "row": index,
                    "field": "location_code",
                    "message": f"库位不存在或不属于当前仓库：{location_code}",
                }
            )
            continue
        if location.is_virtual:
            errors.append(
                {
                    "row": index,
                    "field": "location_code",
                    "message": "虚拟库位不能参与库存盘点",
                }
            )
            continue
        key = (product.id, sku.id, location.id)
        if key in keys:
            errors.append(
                {
                    "row": index,
                    "field": "",
                    "message": "文件内商品、规格和库位重复",
                }
            )
            continue
        keys.add(key)
        try:
            actual_qty = _to_qty(row.get("actual_qty"), "盘点实存")
        except BizException as exc:
            errors.append(
                {
                    "row": index,
                    "field": "actual_qty",
                    "message": exc.message,
                }
            )
            continue
        result.append(
            {
                "product_id": product.id,
                "sku_id": sku.id,
                "location_id": location.id,
                "current_qty": _current_qty(
                    db,
                    warehouse=warehouse,
                    location_id=location.id,
                    sku_id=sku.id,
                ),
                "actual_qty": actual_qty,
                "barcode": (
                    _optional_text(row.get("barcode"), "条形码", 64)
                    or sku.barcode
                ),
            }
        )
    return result, errors


def import_stock_check_result(
    db: Session,
    *,
    check_id: int,
    file_bytes: bytes,
    staff: Staff,
    commit: bool = True,
) -> dict:
    record = get_stock_check_or_404(db, check_id)
    _ensure_editable(db, record)
    warehouse = _get_warehouse_or_404(db, record.warehouse_id)
    parsed = parse_data(file_bytes, STOCK_CHECK_TEMPLATE)
    prepared, errors = _lookup_import_rows(
        db,
        warehouse=warehouse,
        parsed=parsed,
    )
    if errors:
        return {
            "updated": 0,
            "created": 0,
            "errors": errors,
        }
    if not prepared:
        return {
            "updated": 0,
            "created": 0,
            "errors": [{"row": 0, "field": "", "message": "没有可导入的数据"}],
        }

    existing = {
        (item.product_id, item.sku_id, item.location_id): item
        for item in _check_items(db, record.id)
    }
    created = 0
    updated = 0
    for row in prepared:
        key = (row["product_id"], row["sku_id"], row["location_id"])
        item = existing.get(key)
        if item is None:
            db.add(StockCheckItem(check_id=record.id, **row))
            created += 1
        else:
            item.current_qty = row["current_qty"]
            item.actual_qty = row["actual_qty"]
            item.barcode = row["barcode"]
            updated += 1
    db.flush()
    write_audit(
        db,
        action="IMPORT_RESULT",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"导入库存盘点结果：{record.check_no}",
        detail={"created": created, "updated": updated},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "updated": updated,
        "created": created,
        "errors": [],
    }
