"""仓库调拨服务（TASK-032）。

完整流程：
1. 创建调拨单并提交审批；
2. 调拨单审批通过后生成调拨出库单和调拨入库单；
3. 调拨出库单审批通过后选择实际库位下架，扣减调出仓库存并写入出库流水；
4. 调拨入库单审批通过后签收入库，选择实际库位上架、增加调入仓库存、
   写入入库流水和上架记录。

``sync_stock`` 在当前阶段记录调拨确认时的线上库存同步选择；入库完成后写入
待同步审计留痕，真实平台库存同步由 TASK-035 店铺库存任务处理。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Unit
from app.models.org import Staff
from app.models.product import Product, ProductSku
from app.models.stock import (
    Putaway,
    StockFlow,
    Transfer,
    TransferIn,
    TransferInItem,
    TransferItem,
    TransferOut,
    TransferOutItem,
)
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, warehouse_service

DOC_TYPE = "transfer"
OUT_DOC_TYPE = "transfer_out"
IN_DOC_TYPE = "transfer_in"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_OUTBOUND = "OUTBOUND"
STATUS_COMPLETED = "COMPLETED"
STATUS_TERMINATED = "TERMINATED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")
MAX_ITEMS = 500

TRANSFER_FIELDS = {
    "from_warehouse_id",
    "to_warehouse_id",
    "sync_stock",
    "remark",
    "items",
}

TRANSFER_ITEM_FIELDS = {
    "product_id",
    "sku_id",
    "qty",
    "barcode",
}

OFF_SHELF_FIELDS = {"transfer_out_item_id", "location_id", "qty"}
RECEIVE_FIELDS = {"transfer_in_item_id", "location_id", "qty"}


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


def _optional_int_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int_id(value, label)


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
        result = Decimal(str(value)).quantize(QTY_QUANT, rounding=ROUND_HALF_UP)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法数量") from exc
    if result <= ZERO_QTY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
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


def get_transfer_or_404(db: Session, transfer_id: int) -> Transfer:
    record = db.get(Transfer, transfer_id)
    if record is None or not document_visible(db, record.created_by, DOC_TYPE, record.id):
        raise BizException(ErrorCode.NOT_FOUND, "调拨单不存在")
    return record


def get_transfer_out_or_404(db: Session, transfer_out_id: int) -> TransferOut:
    record = db.get(TransferOut, transfer_out_id)
    if record is None or not document_visible(db, record.created_by, OUT_DOC_TYPE, record.id):
        raise BizException(ErrorCode.NOT_FOUND, "调拨出库单不存在")
    return record


def get_transfer_in_or_404(db: Session, transfer_in_id: int) -> TransferIn:
    record = db.get(TransferIn, transfer_in_id)
    if record is None or not document_visible(db, record.created_by, IN_DOC_TYPE, record.id):
        raise BizException(ErrorCode.NOT_FOUND, "调拨入库单不存在")
    return record


def _validate_warehouses(
    db: Session, *, from_warehouse_id: int, to_warehouse_id: int
) -> tuple[Warehouse, Warehouse]:
    if from_warehouse_id == to_warehouse_id:
        raise BizException(ErrorCode.BAD_REQUEST, "调出仓库和调入仓库不能相同")
    from_warehouse = warehouse_service.ensure_warehouse_selectable(
        db, from_warehouse_id
    )
    to_warehouse = warehouse_service.ensure_warehouse_selectable(
        db, to_warehouse_id
    )
    return from_warehouse, to_warehouse


def _available_qty(
    db: Session,
    *,
    warehouse: Warehouse,
    sku_id: int,
) -> Decimal:
    value = db.scalar(
        select(func.coalesce(func.sum(StockLocation.qty), 0))
        .join(Location, Location.id == StockLocation.location_id)
        .where(
            Location.warehouse_id == warehouse.id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == _stock_status(warehouse),
        )
    )
    return Decimal(value or 0)


def _normalize_item(
    db: Session,
    *,
    from_warehouse: Warehouse,
    data: dict,
) -> dict[str, Any]:
    _check_unknown_fields(data, TRANSFER_ITEM_FIELDS)
    product_id = _to_int_id(data.get("product_id"), "商品")
    sku_id = _to_int_id(data.get("sku_id"), "商品规格")
    product = _get_product_or_404(db, product_id)
    sku = _get_sku_or_404(db, product_id=product.id, sku_id=sku_id)
    qty = _to_qty(data.get("qty"), "调拨数量")
    available = _available_qty(
        db,
        warehouse=from_warehouse,
        sku_id=sku.id,
    )
    if qty > available:
        raise BizException(
            ErrorCode.CONFLICT,
            f"{product.name}调拨数量超过调出仓可用库存 {available}",
        )
    barcode = _optional_text(data.get("barcode"), "条形码", 64) or sku.barcode
    return {
        "product_id": product.id,
        "sku_id": sku.id,
        "qty": qty,
        "barcode": barcode,
    }


def _normalize_items(
    db: Session,
    *,
    from_warehouse: Warehouse,
    raw_items: Any,
) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "至少添加一条调拨商品明细")
    if len(raw_items) > MAX_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"单张调拨单最多 {MAX_ITEMS} 条明细"
        )
    result: list[dict[str, Any]] = []
    keys: set[tuple[int, int]] = set()
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "调拨商品明细格式错误")
        item = _normalize_item(
            db,
            from_warehouse=from_warehouse,
            data=raw,
        )
        key = (item["product_id"], item["sku_id"])
        if key in keys:
            raise BizException(ErrorCode.BAD_REQUEST, "同一商品规格不能重复")
        keys.add(key)
        result.append(item)
    return result


def _transfer_items(db: Session, transfer_id: int) -> list[TransferItem]:
    return list(
        db.scalars(
            select(TransferItem)
            .where(TransferItem.transfer_id == transfer_id)
            .order_by(TransferItem.id)
        )
    )


def _transfer_out_items(
    db: Session, transfer_out_id: int
) -> list[TransferOutItem]:
    return list(
        db.scalars(
            select(TransferOutItem)
            .where(TransferOutItem.transfer_out_id == transfer_out_id)
            .order_by(TransferOutItem.id)
        )
    )


def _transfer_in_items(
    db: Session, transfer_in_id: int
) -> list[TransferInItem]:
    return list(
        db.scalars(
            select(TransferInItem)
            .where(TransferInItem.transfer_in_id == transfer_in_id)
            .order_by(TransferInItem.id)
        )
    )


def _get_transfer_out(db: Session, transfer_id: int) -> TransferOut | None:
    return db.scalar(
        select(TransferOut)
        .where(
            TransferOut.transfer_id == transfer_id,
            TransferOut.status != STATUS_TERMINATED,
        )
        .order_by(TransferOut.id.desc())
    )


def _get_transfer_in(db: Session, transfer_id: int) -> TransferIn | None:
    return db.scalar(
        select(TransferIn)
        .where(
            TransferIn.transfer_id == transfer_id,
            TransferIn.status != STATUS_TERMINATED,
        )
        .order_by(TransferIn.id.desc())
    )


def _transfer_item_dict(db: Session, item: TransferItem) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    transfer = db.get(Transfer, item.transfer_id)
    from_warehouse = (
        db.get(Warehouse, transfer.from_warehouse_id) if transfer else None
    )
    available = (
        _available_qty(
            db,
            warehouse=from_warehouse,
            sku_id=item.sku_id,
        )
        if from_warehouse is not None and item.sku_id is not None
        else ZERO_QTY
    )
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "sku_id": item.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "qty": item.qty,
        "available_qty": available,
        "barcode": item.barcode,
    }


def _transfer_out_item_dict(db: Session, item: TransferOutItem) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    location = db.get(Location, item.location_id) if item.location_id else None
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "sku_id": item.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "location_id": item.location_id,
        "location_code": location.code if location else None,
        "qty": item.qty,
    }


def _transfer_in_item_dict(db: Session, item: TransferInItem) -> dict:
    product = db.get(Product, item.product_id)
    sku = db.get(ProductSku, item.sku_id) if item.sku_id else None
    location = db.get(Location, item.location_id) if item.location_id else None
    return {
        "id": item.id,
        "product_id": item.product_id,
        "product_name": product.name if product else None,
        "product_code": product.code if product else None,
        "sku_id": item.sku_id,
        "spec_name": sku.spec_name if sku else None,
        "spec_code": sku.spec_code if sku else None,
        "location_id": item.location_id,
        "location_code": location.code if location else None,
        "qty": item.qty,
    }


def transfer_out_dict(
    db: Session,
    record: TransferOut,
    *,
    with_items: bool = False,
    with_approval: bool = False,
) -> dict:
    warehouse = db.get(Warehouse, record.warehouse_id)
    creator = db.get(Staff, record.created_by) if record.created_by else None
    auditor = db.get(Staff, record.auditor_id) if record.auditor_id else None
    items = _transfer_out_items(db, record.id)
    result = {
        "id": record.id,
        "transfer_out_no": record.transfer_out_no,
        "transfer_id": record.transfer_id,
        "warehouse_id": record.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "status": record.status,
        "audit_status": record.audit_status,
        "total_qty": sum((item.qty for item in items), ZERO_QTY),
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
            _transfer_out_item_dict(db, item) for item in items
        ]
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=OUT_DOC_TYPE,
            doc_id=record.id,
        )
    return result


def transfer_in_dict(
    db: Session,
    record: TransferIn,
    *,
    with_items: bool = False,
    with_approval: bool = False,
) -> dict:
    warehouse = db.get(Warehouse, record.warehouse_id)
    creator = db.get(Staff, record.created_by) if record.created_by else None
    auditor = db.get(Staff, record.auditor_id) if record.auditor_id else None
    items = _transfer_in_items(db, record.id)
    result = {
        "id": record.id,
        "transfer_in_no": record.transfer_in_no,
        "transfer_id": record.transfer_id,
        "warehouse_id": record.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "status": record.status,
        "audit_status": record.audit_status,
        "total_qty": sum((item.qty for item in items), ZERO_QTY),
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
        result["items"] = [_transfer_in_item_dict(db, item) for item in items]
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=IN_DOC_TYPE,
            doc_id=record.id,
        )
    return result


def transfer_dict(
    db: Session,
    record: Transfer,
    *,
    with_items: bool = True,
    with_docs: bool = True,
    with_approval: bool = False,
) -> dict:
    from_warehouse = db.get(Warehouse, record.from_warehouse_id)
    to_warehouse = db.get(Warehouse, record.to_warehouse_id)
    creator = db.get(Staff, record.created_by) if record.created_by else None
    auditor = db.get(Staff, record.auditor_id) if record.auditor_id else None
    result = {
        "id": record.id,
        "transfer_no": record.transfer_no,
        "from_warehouse_id": record.from_warehouse_id,
        "from_warehouse_name": from_warehouse.name if from_warehouse else None,
        "to_warehouse_id": record.to_warehouse_id,
        "to_warehouse_name": to_warehouse.name if to_warehouse else None,
        "sync_stock": record.sync_stock,
        "status": record.status,
        "audit_status": record.audit_status,
        "total_qty": record.total_qty,
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
            _transfer_item_dict(db, item)
            for item in _transfer_items(db, record.id)
        ]
    if with_docs:
        transfer_out = _get_transfer_out(db, record.id)
        transfer_in = _get_transfer_in(db, record.id)
        result["transfer_out"] = (
            transfer_out_dict(db, transfer_out, with_items=True)
            if transfer_out
            else None
        )
        result["transfer_in"] = (
            transfer_in_dict(db, transfer_in, with_items=True)
            if transfer_in
            else None
        )
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=DOC_TYPE,
            doc_id=record.id,
        )
    return result


def list_transfers(
    db: Session,
    *,
    from_warehouse_id: int | None = None,
    to_warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, Transfer.created_by)
    if from_warehouse_id is not None:
        conditions.append(Transfer.from_warehouse_id == from_warehouse_id)
    if to_warehouse_id is not None:
        conditions.append(Transfer.to_warehouse_id == to_warehouse_id)
    if status:
        conditions.append(Transfer.status == status.upper())
    if audit_status:
        conditions.append(Transfer.audit_status == audit_status.upper())
    if keyword:
        conditions.append(Transfer.transfer_no.like(f"%{keyword.strip()}%"))
    total = db.scalar(select(func.count(Transfer.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Transfer)
            .where(*conditions)
            .order_by(Transfer.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            transfer_dict(
                db,
                row,
                with_items=False,
                with_docs=True,
            )
            for row in rows
        ],
    }


def list_transfer_outs(
    db: Session,
    *,
    transfer_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, TransferOut.created_by)
    if transfer_id is not None:
        conditions.append(TransferOut.transfer_id == transfer_id)
    if warehouse_id is not None:
        conditions.append(TransferOut.warehouse_id == warehouse_id)
    if status:
        conditions.append(TransferOut.status == status.upper())
    if audit_status:
        conditions.append(TransferOut.audit_status == audit_status.upper())
    total = db.scalar(select(func.count(TransferOut.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(TransferOut)
            .where(*conditions)
            .order_by(TransferOut.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [transfer_out_dict(db, row) for row in rows],
    }


def list_transfer_ins(
    db: Session,
    *,
    transfer_id: int | None = None,
    warehouse_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, TransferIn.created_by)
    if transfer_id is not None:
        conditions.append(TransferIn.transfer_id == transfer_id)
    if warehouse_id is not None:
        conditions.append(TransferIn.warehouse_id == warehouse_id)
    if status:
        conditions.append(TransferIn.status == status.upper())
    if audit_status:
        conditions.append(TransferIn.audit_status == audit_status.upper())
    total = db.scalar(select(func.count(TransferIn.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(TransferIn)
            .where(*conditions)
            .order_by(TransferIn.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [transfer_in_dict(db, row) for row in rows],
    }


def _resolve_values(
    db: Session,
    *,
    data: dict,
    existing: Transfer | None = None,
) -> tuple[dict[str, Any], Warehouse]:
    _check_unknown_fields(data, TRANSFER_FIELDS)
    from_warehouse_id = (
        _to_int_id(data.get("from_warehouse_id"), "调出仓库")
        if existing is None or "from_warehouse_id" in data
        else existing.from_warehouse_id
    )
    to_warehouse_id = (
        _to_int_id(data.get("to_warehouse_id"), "调入仓库")
        if existing is None or "to_warehouse_id" in data
        else existing.to_warehouse_id
    )
    from_warehouse, _ = _validate_warehouses(
        db,
        from_warehouse_id=from_warehouse_id,
        to_warehouse_id=to_warehouse_id,
    )
    values: dict[str, Any] = {
        "from_warehouse_id": from_warehouse_id,
        "to_warehouse_id": to_warehouse_id,
    }
    if existing is None or "sync_stock" in data:
        values["sync_stock"] = _to_bool(
            data.get(
                "sync_stock",
                existing.sync_stock if existing is not None else False,
            ),
            "是否同步线上库存",
        )
    if "remark" in data:
        values["remark"] = _optional_text(data["remark"], "备注", 255)
    return values, from_warehouse


def create_transfer(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Transfer:
    values, from_warehouse = _resolve_values(db, data=data)
    items = _normalize_items(
        db,
        from_warehouse=from_warehouse,
        raw_items=data.get("items"),
    )
    record = Transfer(
        transfer_no=generate_serial(SerialPrefix.TRANSFER, dt=_now()),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        total_qty=sum((item["qty"] for item in items), ZERO_QTY),
        created_by=staff.id,
        **values,
    )
    db.add(record)
    db.flush()
    for item in items:
        db.add(TransferItem(transfer_id=record.id, **item))
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"新增调拨单：{record.transfer_no}",
        detail={
            "from_warehouse_id": record.from_warehouse_id,
            "to_warehouse_id": record.to_warehouse_id,
            "sync_stock": record.sync_stock,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _approval_state(db: Session, doc_type: str, doc_id: int) -> dict:
    return approval_service.get_state(
        db,
        doc_type=doc_type,
        doc_id=doc_id,
    )["state"]


def _ensure_editable(db: Session, record: Transfer) -> None:
    if record.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前调拨单状态不允许修改")
    state = _approval_state(db, DOC_TYPE, record.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "调拨单已进入审批流程，不能修改")


def update_transfer(
    db: Session,
    *,
    transfer_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Transfer:
    record = get_transfer_or_404(db, transfer_id)
    _ensure_editable(db, record)
    values, from_warehouse = _resolve_values(
        db,
        data=data,
        existing=record,
    )
    for field, value in values.items():
        setattr(record, field, value)

    if "items" in data:
        items = _normalize_items(
            db,
            from_warehouse=from_warehouse,
            raw_items=data["items"],
        )
        for item in _transfer_items(db, record.id):
            db.delete(item)
        db.flush()
        for item in items:
            db.add(TransferItem(transfer_id=record.id, **item))
        db.flush()
    elif "from_warehouse_id" in data:
        current = [
            {
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "qty": item.qty,
                "barcode": item.barcode,
            }
            for item in _transfer_items(db, record.id)
        ]
        _normalize_items(
            db,
            from_warehouse=from_warehouse,
            raw_items=current,
        )

    record.total_qty = sum(
        (item.qty for item in _transfer_items(db, record.id)),
        ZERO_QTY,
    )
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"修改调拨单：{record.transfer_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def delete_transfer(
    db: Session, *, transfer_id: int, staff: Staff, commit: bool = True
) -> None:
    record = get_transfer_or_404(db, transfer_id)
    _ensure_editable(db, record)
    if _get_transfer_out(db, record.id) is not None:
        raise BizException(ErrorCode.CONFLICT, "调拨单已生成调拨出库单，不能删除")
    if _get_transfer_in(db, record.id) is not None:
        raise BizException(ErrorCode.CONFLICT, "调拨单已生成调拨入库单，不能删除")
    for item in _transfer_items(db, record.id):
        db.delete(item)
    transfer_no = record.transfer_no
    db.delete(record)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(record.id),
        summary=f"删除调拨单：{transfer_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(record: Any, state: dict) -> None:
    if state["status"] == "APPROVED":
        record.audit_status = AUDIT_APPROVED
    elif state["status"] == "REJECTED":
        record.audit_status = AUDIT_REJECTED
    else:
        record.audit_status = AUDIT_PENDING


def _ensure_transfer_docs(
    db: Session,
    *,
    record: Transfer,
    staff: Staff,
) -> tuple[TransferOut, TransferIn]:
    transfer_out = _get_transfer_out(db, record.id)
    if transfer_out is None:
        transfer_out = TransferOut(
            transfer_out_no=generate_serial(
                SerialPrefix.TRANSFER_OUT, dt=_now()
            ),
            transfer_id=record.id,
            warehouse_id=record.from_warehouse_id,
            status=STATUS_PENDING,
            audit_status=AUDIT_PENDING,
            remark=f"调拨出库:{record.transfer_no}",
            created_by=record.created_by or staff.id,
        )
        db.add(transfer_out)
        db.flush()

    transfer_in = _get_transfer_in(db, record.id)
    if transfer_in is None:
        transfer_in = TransferIn(
            transfer_in_no=generate_serial(
                SerialPrefix.TRANSFER_IN, dt=_now()
            ),
            transfer_id=record.id,
            warehouse_id=record.to_warehouse_id,
            status=STATUS_PENDING,
            audit_status=AUDIT_PENDING,
            remark=f"调拨入库:{record.transfer_no}",
            created_by=record.created_by or staff.id,
        )
        db.add(transfer_in)
        db.flush()

    existing_out_items = _transfer_out_items(db, transfer_out.id)
    if not existing_out_items:
        for item in _transfer_items(db, record.id):
            db.add(
                TransferOutItem(
                    transfer_out_id=transfer_out.id,
                    product_id=item.product_id,
                    sku_id=item.sku_id,
                    location_id=None,
                    qty=item.qty,
                )
            )

    existing_in_items = _transfer_in_items(db, transfer_in.id)
    if not existing_in_items:
        for item in _transfer_items(db, record.id):
            db.add(
                TransferInItem(
                    transfer_in_id=transfer_in.id,
                    product_id=item.product_id,
                    sku_id=item.sku_id,
                    location_id=None,
                    qty=item.qty,
                )
            )
    db.flush()
    return transfer_out, transfer_in


def submit_transfer(
    db: Session, *, transfer_id: int, staff: Staff, commit: bool = True
) -> Transfer:
    record = get_transfer_or_404(db, transfer_id)
    _ensure_editable(db, record)
    if not _transfer_items(db, record.id):
        raise BizException(ErrorCode.BAD_REQUEST, "调拨单没有商品明细，不能提交")
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=record.id,
        doc_no=record.transfer_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_PENDING
    record.auditor_id = None
    record.audit_time = None
    _commit(db, commit)
    return record


def approve_transfer(
    db: Session,
    *,
    transfer_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Transfer:
    record = get_transfer_or_404(db, transfer_id)
    if record.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前调拨单状态不能审核")
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
            record.status = STATUS_APPROVED
            record.auditor_id = staff.id
            record.audit_time = _now()
            _ensure_transfer_docs(db, record=record, staff=staff)
        else:
            record.status = STATUS_PENDING
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return record


def reject_transfer(
    db: Session,
    *,
    transfer_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Transfer:
    record = get_transfer_or_404(db, transfer_id)
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前调拨单状态不能审核")
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
    _commit(db, commit)
    return record


def _ensure_doc_editable(
    db: Session,
    record: TransferOut | TransferIn,
    *,
    doc_type: str,
) -> None:
    if record.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前单据状态不允许修改")
    state = _approval_state(db, doc_type, record.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "单据已进入审批流程，不能修改")


def submit_transfer_out(
    db: Session, *, transfer_out_id: int, staff: Staff, commit: bool = True
) -> TransferOut:
    record = get_transfer_out_or_404(db, transfer_out_id)
    transfer = get_transfer_or_404(db, record.transfer_id)
    if transfer.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "调拨单审核通过后才能提交出库")
    _ensure_doc_editable(db, record, doc_type=OUT_DOC_TYPE)
    if not _transfer_out_items(db, record.id):
        raise BizException(ErrorCode.BAD_REQUEST, "调拨出库单没有商品明细")
    state = approval_service.submit(
        db,
        doc_type=OUT_DOC_TYPE,
        doc_id=record.id,
        doc_no=record.transfer_out_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_PENDING
    record.auditor_id = None
    record.audit_time = None
    _commit(db, commit)
    return record


def approve_transfer_out(
    db: Session,
    *,
    transfer_out_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> TransferOut:
    record = get_transfer_out_or_404(db, transfer_out_id)
    transfer = get_transfer_or_404(db, record.transfer_id)
    if transfer.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "调拨单审核通过后才能审核出库")
    if record.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前调拨出库单状态不能审核")
    state = approval_service.approve(
        db,
        doc_type=OUT_DOC_TYPE,
        doc_id=record.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(record, state)
    if state["status"] == "APPROVED":
        record.status = STATUS_APPROVED
        record.auditor_id = staff.id
        record.audit_time = _now()
    else:
        record.status = STATUS_PENDING
    _commit(db, commit)
    return record


def reject_transfer_out(
    db: Session,
    *,
    transfer_out_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> TransferOut:
    record = get_transfer_out_or_404(db, transfer_out_id)
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前调拨出库单状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=OUT_DOC_TYPE,
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


def submit_transfer_in(
    db: Session, *, transfer_in_id: int, staff: Staff, commit: bool = True
) -> TransferIn:
    record = get_transfer_in_or_404(db, transfer_in_id)
    transfer = get_transfer_or_404(db, record.transfer_id)
    transfer_out = _get_transfer_out(db, transfer.id)
    if transfer_out is None or transfer_out.status != STATUS_COMPLETED:
        raise BizException(ErrorCode.CONFLICT, "调拨出库完成后才能提交签收入库")
    _ensure_doc_editable(db, record, doc_type=IN_DOC_TYPE)
    if not _transfer_in_items(db, record.id):
        raise BizException(ErrorCode.BAD_REQUEST, "调拨入库单没有商品明细")
    state = approval_service.submit(
        db,
        doc_type=IN_DOC_TYPE,
        doc_id=record.id,
        doc_no=record.transfer_in_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(record, state)
    record.status = STATUS_PENDING
    record.auditor_id = None
    record.audit_time = None
    _commit(db, commit)
    return record


def approve_transfer_in(
    db: Session,
    *,
    transfer_in_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> TransferIn:
    record = get_transfer_in_or_404(db, transfer_in_id)
    transfer = get_transfer_or_404(db, record.transfer_id)
    transfer_out = _get_transfer_out(db, transfer.id)
    if transfer_out is None or transfer_out.status != STATUS_COMPLETED:
        raise BizException(ErrorCode.CONFLICT, "调拨出库完成后才能审核签收入库")
    if record.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前调拨入库单状态不能审核")
    state = approval_service.approve(
        db,
        doc_type=IN_DOC_TYPE,
        doc_id=record.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(record, state)
    if state["status"] == "APPROVED":
        record.status = STATUS_APPROVED
        record.auditor_id = staff.id
        record.audit_time = _now()
    else:
        record.status = STATUS_PENDING
    _commit(db, commit)
    return record


def reject_transfer_in(
    db: Session,
    *,
    transfer_in_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> TransferIn:
    record = get_transfer_in_or_404(db, transfer_in_id)
    if record.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前调拨入库单状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=IN_DOC_TYPE,
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


def _validate_location(
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


def off_shelf_transfer_out(
    db: Session,
    *,
    transfer_out_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> TransferOut:
    _check_unknown_fields(data, {"items"})
    record = get_transfer_out_or_404(db, transfer_out_id)
    transfer = get_transfer_or_404(db, record.transfer_id)
    if record.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "调拨出库单审核通过后才能下架")
    if transfer.status not in {STATUS_APPROVED, STATUS_OUTBOUND}:
        raise BizException(ErrorCode.CONFLICT, "当前调拨单状态不能执行出库")
    from_warehouse = warehouse_service.ensure_warehouse_selectable(
        db, transfer.from_warehouse_id
    )
    stock_status = _stock_status(from_warehouse)
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "调拨出库下架明细不能为空")

    item_map = {item.id: item for item in _transfer_out_items(db, record.id)}
    if not item_map:
        raise BizException(ErrorCode.CONFLICT, "调拨出库单没有可下架商品")
    for item in item_map.values():
        if item.sku_id is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "调拨出库下架必须指定商品规格",
            )

    required = {item.id: item.qty for item in item_map.values()}
    available: dict[int, Decimal] = {}
    allocations: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "调拨出库明细格式错误")
        _check_unknown_fields(raw, OFF_SHELF_FIELDS)
        item_id = _to_int_id(raw.get("transfer_out_item_id"), "调拨出库明细")
        location_id = _to_int_id(raw.get("location_id"), "下架库位")
        qty = _to_qty(raw.get("qty"), "下架数量")
        item = item_map.get(item_id)
        if item is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "调拨出库明细不属于当前单据",
            )
        if qty > required[item.id]:
            raise BizException(ErrorCode.CONFLICT, "下架数量超过应出库数量")
        location = _validate_location(
            db,
            warehouse_id=transfer.from_warehouse_id,
            location_id=location_id,
            label="下架",
        )
        stock_row = db.scalar(
            select(StockLocation)
            .where(
                StockLocation.location_id == location.id,
                StockLocation.product_sku_id == item.sku_id,
                StockLocation.status == stock_status,
            )
            .with_for_update()
        )
        if stock_row is None:
            raise BizException(ErrorCode.CONFLICT, "所选库位没有可下架库存")
        row_available = available.setdefault(stock_row.id, stock_row.qty)
        if qty > row_available:
            raise BizException(
                ErrorCode.CONFLICT,
                f"下架数量超过库位库存：{location.code}",
            )
        available[stock_row.id] = row_available - qty
        required[item.id] -= qty
        allocations.append(
            {
                "source_item_id": item.id,
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "location": location,
                "qty": qty,
                "stock_row": stock_row,
            }
        )

    if any(qty > ZERO_QTY for qty in required.values()):
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "下架数量合计必须等于调拨出库数量",
        )

    for item in item_map.values():
        db.delete(item)
    db.flush()
    created_items: list[tuple[TransferOutItem, dict[str, Any]]] = []
    for allocation in allocations:
        stock_row = allocation["stock_row"]
        stock_row.qty -= allocation["qty"]
        item = TransferOutItem(
            transfer_out_id=record.id,
            product_id=allocation["product_id"],
            sku_id=allocation["sku_id"],
            location_id=allocation["location"].id,
            qty=allocation["qty"],
        )
        db.add(item)
        created_items.append((item, allocation))
    db.flush()

    for item, allocation in created_items:
        product = db.get(Product, allocation["product_id"])
        sku = db.get(ProductSku, allocation["sku_id"])
        cost_price = (
            sku.cost_price
            if sku is not None and sku.cost_price is not None
            else (product.cost_price if product is not None else None)
        )
        db.add(
            StockFlow(
                warehouse_id=transfer.from_warehouse_id,
                product_id=allocation["product_id"],
                sku_id=allocation["sku_id"],
                location_id=allocation["location"].id,
                unit_id=product.main_unit_id if product else None,
                direction="OUT",
                biz_type="TRANSFER_OUT",
                qty=allocation["qty"],
                cost_price=cost_price,
                total_cost=(
                    (allocation["qty"] * cost_price).quantize(Decimal("0.01"))
                    if cost_price is not None
                    else None
                ),
                source_no=record.transfer_out_no,
                order_no=transfer.transfer_no,
                flow_time=_now(),
                remark=f"调拨出库:{transfer.transfer_no}",
            )
        )
    db.flush()

    record.status = STATUS_COMPLETED
    record.audit_status = AUDIT_APPROVED
    record.auditor_id = staff.id
    record.audit_time = _now()
    transfer.status = STATUS_OUTBOUND
    write_audit(
        db,
        action="OFF_SHELF",
        target_type=OUT_DOC_TYPE,
        target_id=str(record.id),
        summary=f"调拨出库下架完成：{record.transfer_out_no}",
        detail={
            "transfer_id": transfer.id,
            "items": [
                {
                    "transfer_out_item_id": item.id,
                    "source_item_id": allocation["source_item_id"],
                    "location_id": allocation["location"].id,
                    "qty": str(allocation["qty"]),
                }
                for item, allocation in created_items
            ],
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record


def _upsert_stock_location(
    db: Session,
    *,
    location: Location,
    sku_id: int,
    qty: Decimal,
    status: str,
) -> StockLocation:
    stock_row = db.scalar(
        select(StockLocation).where(
            StockLocation.location_id == location.id,
            StockLocation.product_sku_id == sku_id,
            StockLocation.status == status,
        )
    )
    if stock_row is None:
        stock_row = StockLocation(
            location_id=location.id,
            product_sku_id=sku_id,
            qty=qty,
            status=status,
        )
        db.add(stock_row)
    else:
        stock_row.qty += qty
    db.flush()
    return stock_row


def receive_transfer_in(
    db: Session,
    *,
    transfer_in_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> TransferIn:
    _check_unknown_fields(data, {"items"})
    record = get_transfer_in_or_404(db, transfer_in_id)
    transfer = get_transfer_or_404(db, record.transfer_id)
    transfer_out = _get_transfer_out(db, transfer.id)
    if transfer_out is None or transfer_out.status != STATUS_COMPLETED:
        raise BizException(ErrorCode.CONFLICT, "调拨出库完成后才能签收入库")
    if record.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "调拨入库单审核通过后才能签收")
    to_warehouse = warehouse_service.ensure_warehouse_selectable(
        db, transfer.to_warehouse_id
    )
    stock_status = _stock_status(to_warehouse)
    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "调拨入库签收明细不能为空")

    item_map = {item.id: item for item in _transfer_in_items(db, record.id)}
    if not item_map:
        raise BizException(ErrorCode.CONFLICT, "调拨入库单没有可签收商品")
    for item in item_map.values():
        if item.sku_id is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "调拨入库签收必须指定商品规格",
            )

    required = {item.id: item.qty for item in item_map.values()}
    added_by_location: dict[int, Decimal] = {}
    allocations: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "调拨入库明细格式错误")
        _check_unknown_fields(raw, RECEIVE_FIELDS)
        item_id = _to_int_id(raw.get("transfer_in_item_id"), "调拨入库明细")
        location_id = _to_int_id(raw.get("location_id"), "上架库位")
        qty = _to_qty(raw.get("qty"), "签收数量")
        item = item_map.get(item_id)
        if item is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "调拨入库明细不属于当前单据",
            )
        if qty > required[item.id]:
            raise BizException(ErrorCode.CONFLICT, "签收数量超过应入库数量")
        location = _validate_location(
            db,
            warehouse_id=transfer.to_warehouse_id,
            location_id=location_id,
            label="上架",
        )
        added = added_by_location.get(location.id, ZERO_QTY) + qty
        state = warehouse_service.location_capacity_state(db, location.id)
        if location.capacity and state["used_qty"] + added > Decimal(
            location.capacity
        ):
            raise BizException(
                ErrorCode.CONFLICT,
                f"上架库位容量不足：{location.code}",
            )
        added_by_location[location.id] = added
        required[item.id] -= qty
        allocations.append(
            {
                "source_item_id": item.id,
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "location": location,
                "qty": qty,
            }
        )

    if any(qty > ZERO_QTY for qty in required.values()):
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "签收数量合计必须等于调拨入库数量",
        )

    for item in item_map.values():
        db.delete(item)
    db.flush()
    created_items: list[tuple[TransferInItem, dict[str, Any]]] = []
    for allocation in allocations:
        item = TransferInItem(
            transfer_in_id=record.id,
            product_id=allocation["product_id"],
            sku_id=allocation["sku_id"],
            location_id=allocation["location"].id,
            qty=allocation["qty"],
        )
        db.add(item)
        _upsert_stock_location(
            db,
            location=allocation["location"],
            sku_id=allocation["sku_id"],
            qty=allocation["qty"],
            status=stock_status,
        )
        created_items.append((item, allocation))
    db.flush()

    for item, allocation in created_items:
        product = db.get(Product, allocation["product_id"])
        sku = db.get(ProductSku, allocation["sku_id"])
        cost_price = (
            sku.cost_price
            if sku is not None and sku.cost_price is not None
            else (product.cost_price if product is not None else None)
        )
        db.add(
            Putaway(
                warehouse_id=transfer.to_warehouse_id,
                source_type="TRANSFER_IN",
                source_id=record.id,
                location_id=allocation["location"].id,
                product_id=allocation["product_id"],
                sku_id=allocation["sku_id"],
                qty=allocation["qty"],
                operator_id=staff.id,
            )
        )
        db.add(
            StockFlow(
                warehouse_id=transfer.to_warehouse_id,
                product_id=allocation["product_id"],
                sku_id=allocation["sku_id"],
                location_id=allocation["location"].id,
                unit_id=product.main_unit_id if product else None,
                direction="IN",
                biz_type="TRANSFER_IN",
                qty=allocation["qty"],
                cost_price=cost_price,
                total_cost=(
                    (allocation["qty"] * cost_price).quantize(Decimal("0.01"))
                    if cost_price is not None
                    else None
                ),
                source_no=record.transfer_in_no,
                order_no=transfer.transfer_no,
                flow_time=_now(),
                remark=(
                    f"调拨入库:{transfer.transfer_no};"
                    f"同步线上库存:{'是' if transfer.sync_stock else '否'}"
                ),
            )
        )
    db.flush()

    record.status = STATUS_COMPLETED
    record.audit_status = AUDIT_APPROVED
    record.auditor_id = staff.id
    record.audit_time = _now()
    transfer.status = STATUS_COMPLETED
    if transfer.sync_stock:
        write_audit(
            db,
            action="SYNC_STOCK_REQUESTED",
            target_type=DOC_TYPE,
            target_id=str(transfer.id),
            summary=f"调拨完成，提交线上库存同步：{transfer.transfer_no}",
            detail={
                "transfer_in_id": record.id,
                "to_warehouse_id": transfer.to_warehouse_id,
            },
            actor_id=staff.id,
            actor_name=staff.name,
        )
    write_audit(
        db,
        action="RECEIVE",
        target_type=IN_DOC_TYPE,
        target_id=str(record.id),
        summary=f"调拨入库签收完成：{record.transfer_in_no}",
        detail={
            "transfer_id": transfer.id,
            "items": [
                {
                    "transfer_in_item_id": item.id,
                    "source_item_id": allocation["source_item_id"],
                    "location_id": allocation["location"].id,
                    "qty": str(allocation["qty"]),
                }
                for item, allocation in created_items
            ],
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return record
