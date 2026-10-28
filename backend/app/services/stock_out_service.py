"""采购出库服务（TASK-031）。

采购退货审核通过后生成待下架出库单。出库单审核通过后，仓库按商品规格
选择实际库位和数量，服务扣减库位库存、写入库存流水，并通知采购退货模块
完成已退数量与退款收入单登记。
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.basic import Unit
from app.models.org import Staff
from app.models.product import Product, ProductSku
from app.models.purchase import (
    PurchaseOrder,
    PurchaseReturn,
    PurchaseReturnItem,
)
from app.models.stock import StockFlow, StockOut, StockOutItem
from app.models.warehouse import Location, StockLocation, Warehouse
from app.services import approval_service, purchase_return_service

DOC_TYPE = "stock_out"
STOCK_OUT_TYPE = "PURCHASE_RETURN"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_TERMINATED = "TERMINATED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

OFF_SHELF_NONE = "NONE"
OFF_SHELF_DONE = "DONE"

QTY_QUANT = Decimal("0.001")
ZERO_QTY = Decimal("0.000")

CREATE_FIELDS = {"purchase_return_id", "remark"}
UPDATE_FIELDS = {"remark"}
OFF_SHELF_FIELDS = {"stock_out_item_id", "location_id", "qty"}


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


def get_stock_out_or_404(db: Session, stock_out_id: int, *, check_scope: bool = True) -> StockOut:
    stock_out = db.get(StockOut, stock_out_id)
    if stock_out is None or (check_scope and not document_visible(db, stock_out.created_by, DOC_TYPE, stock_out.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购出库单不存在")
    return stock_out


def _stock_out_items(db: Session, stock_out_id: int) -> list[StockOutItem]:
    return list(
        db.scalars(
            select(StockOutItem)
            .where(StockOutItem.stock_out_id == stock_out_id)
            .order_by(StockOutItem.id)
        )
    )


def _stock_out_item_dict(db: Session, item: StockOutItem) -> dict:
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
        "cost_price": item.cost_price,
        "barcode": item.barcode,
    }


def stock_out_dict(
    db: Session,
    stock_out: StockOut,
    *,
    with_items: bool = False,
    with_approval: bool = False,
) -> dict:
    warehouse = db.get(Warehouse, stock_out.warehouse_id)
    purchase_return = (
        db.get(PurchaseReturn, stock_out.purchase_return_id)
        if stock_out.purchase_return_id
        else None
    )
    creator = db.get(Staff, stock_out.created_by) if stock_out.created_by else None
    auditor = db.get(Staff, stock_out.auditor_id) if stock_out.auditor_id else None
    result = {
        "id": stock_out.id,
        "stock_out_no": stock_out.stock_out_no,
        "warehouse_id": stock_out.warehouse_id,
        "warehouse_name": warehouse.name if warehouse else None,
        "stock_out_type": stock_out.stock_out_type,
        "purchase_return_id": stock_out.purchase_return_id,
        "purchase_return_no": (
            purchase_return.return_no if purchase_return else None
        ),
        "status": stock_out.status,
        "audit_status": stock_out.audit_status,
        "off_shelf_status": stock_out.off_shelf_status,
        "total_qty": stock_out.total_qty,
        "remark": stock_out.remark,
        "created_by": stock_out.created_by,
        "created_by_name": creator.name if creator else None,
        "auditor_id": stock_out.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": stock_out.audit_time,
        "created_at": stock_out.created_at,
        "updated_at": stock_out.updated_at,
    }
    if with_items:
        result["items"] = [
            _stock_out_item_dict(db, item)
            for item in _stock_out_items(db, stock_out.id)
        ]
    if with_approval:
        result["approval"] = approval_service.get_state(
            db,
            doc_type=DOC_TYPE,
            doc_id=stock_out.id,
        )
    return result


def _active_stock_out(
    db: Session, purchase_return_id: int
) -> StockOut | None:
    return db.scalar(
        select(StockOut)
        .where(
            StockOut.purchase_return_id == purchase_return_id,
            StockOut.status != STATUS_TERMINATED,
        )
        .order_by(StockOut.id.desc())
    )


def _validate_warehouse(db: Session, warehouse_id: int) -> Warehouse:
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise BizException(ErrorCode.NOT_FOUND, "出库仓库不存在")
    if not warehouse.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "出库仓库已停用")
    if warehouse.lock_type != "NONE":
        raise BizException(ErrorCode.CONFLICT, "出库仓库已锁定")
    return warehouse


def create_stock_out(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> StockOut:
    _check_unknown_fields(data, CREATE_FIELDS)
    purchase_return_id = _to_int_id(
        data.get("purchase_return_id"),
        "采购退货单",
    )
    record = purchase_return_service.get_return_or_404(
        db, purchase_return_id
    )
    if record.status != "APPROVED" or record.audit_status != "APPROVED":
        raise BizException(ErrorCode.CONFLICT, "采购退货单审核通过后才能出库")
    if record.warehouse_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "采购退货单没有出库仓库")
    if _active_stock_out(db, record.id) is not None:
        raise BizException(ErrorCode.CONFLICT, "采购退货单已生成出库单")
    _validate_warehouse(db, record.warehouse_id)

    return_items = list(
        db.scalars(
            select(PurchaseReturnItem)
            .where(PurchaseReturnItem.return_id == record.id)
            .order_by(PurchaseReturnItem.id)
        )
    )
    if not return_items:
        raise BizException(ErrorCode.CONFLICT, "采购退货单没有可出库商品")
    for item in return_items:
        if item.sku_id is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "采购出库要求退货商品必须指定商品规格",
            )

    stock_out = StockOut(
        stock_out_no=generate_serial(SerialPrefix.STOCK_OUT, dt=_now()),
        warehouse_id=record.warehouse_id,
        stock_out_type=STOCK_OUT_TYPE,
        purchase_return_id=record.id,
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        off_shelf_status=OFF_SHELF_NONE,
        total_qty=record.total_qty,
        remark=_optional_text(data.get("remark"), "备注", 255)
        or f"采购退货出库:{record.return_no}",
        created_by=staff.id,
    )
    db.add(stock_out)
    db.flush()
    for item in return_items:
        db.add(
            StockOutItem(
                stock_out_id=stock_out.id,
                product_id=item.product_id,
                sku_id=item.sku_id,
                location_id=None,
                qty=item.return_qty,
                cost_price=item.unit_price,
                barcode=None,
            )
        )
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(stock_out.id),
        summary=f"新增采购出库单：{stock_out.stock_out_no}",
        detail={"purchase_return_id": record.id},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return stock_out


def list_stock_outs(
    db: Session,
    *,
    warehouse_id: int | None = None,
    purchase_return_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    off_shelf_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, StockOut.created_by)
    if warehouse_id is not None:
        conditions.append(StockOut.warehouse_id == warehouse_id)
    if purchase_return_id is not None:
        conditions.append(StockOut.purchase_return_id == purchase_return_id)
    if status:
        conditions.append(StockOut.status == status.upper())
    if audit_status:
        conditions.append(StockOut.audit_status == audit_status.upper())
    if off_shelf_status:
        conditions.append(StockOut.off_shelf_status == off_shelf_status.upper())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                StockOut.stock_out_no.like(like),
                PurchaseReturn.return_no.like(like),
            )
        )
    total = (
        db.scalar(
            select(func.count(StockOut.id))
            .outerjoin(PurchaseReturn, PurchaseReturn.id == StockOut.purchase_return_id)
            .where(*conditions)
        )
        or 0
    )
    rows = list(
        db.scalars(
            select(StockOut)
            .outerjoin(PurchaseReturn, PurchaseReturn.id == StockOut.purchase_return_id)
            .where(*conditions)
            .order_by(StockOut.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [stock_out_dict(db, row) for row in rows],
    }


def _approval_state(db: Session, stock_out_id: int) -> dict:
    return approval_service.get_state(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_out_id,
    )["state"]


def _ensure_editable(db: Session, stock_out: StockOut) -> None:
    if stock_out.off_shelf_status == OFF_SHELF_DONE:
        raise BizException(ErrorCode.CONFLICT, "出库单已完成下架，不能修改")
    if stock_out.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前出库单状态不允许修改")
    state = _approval_state(db, stock_out.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "出库单已进入审批流程，不能修改")


def update_stock_out(
    db: Session,
    *,
    stock_out_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockOut:
    _check_unknown_fields(data, UPDATE_FIELDS)
    stock_out = get_stock_out_or_404(db, stock_out_id)
    _ensure_editable(db, stock_out)
    if "remark" in data:
        stock_out.remark = _optional_text(data["remark"], "备注", 255)
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(stock_out.id),
        summary=f"修改采购出库单：{stock_out.stock_out_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return stock_out


def delete_stock_out(
    db: Session, *, stock_out_id: int, staff: Staff, commit: bool = True
) -> None:
    stock_out = get_stock_out_or_404(db, stock_out_id)
    _ensure_editable(db, stock_out)
    for item in _stock_out_items(db, stock_out.id):
        db.delete(item)
    stock_out_no = stock_out.stock_out_no
    db.delete(stock_out)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(stock_out.id),
        summary=f"删除采购出库单：{stock_out_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(stock_out: StockOut, state: dict) -> None:
    status = state["status"]
    if status == "APPROVED":
        stock_out.audit_status = AUDIT_APPROVED
    elif status == "REJECTED":
        stock_out.audit_status = AUDIT_REJECTED
    else:
        stock_out.audit_status = AUDIT_PENDING


def submit_stock_out(
    db: Session, *, stock_out_id: int, staff: Staff, commit: bool = True
) -> StockOut:
    stock_out = get_stock_out_or_404(db, stock_out_id)
    _ensure_editable(db, stock_out)
    if not _stock_out_items(db, stock_out.id):
        raise BizException(ErrorCode.BAD_REQUEST, "出库单没有商品明细，不能提交")
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_out.id,
        doc_no=stock_out.stock_out_no,
        staff=staff,
        commit=False,
    )
    _map_audit_status(stock_out, state)
    stock_out.status = STATUS_PENDING
    stock_out.auditor_id = None
    stock_out.audit_time = None
    _commit(db, commit)
    return stock_out


def approve_stock_out(
    db: Session,
    *,
    stock_out_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> StockOut:
    stock_out = get_stock_out_or_404(db, stock_out_id, check_scope=False)
    if stock_out.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前出库单状态不能审核")
    state = approval_service.approve(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_out.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(stock_out, state)
    if state["status"] == "APPROVED":
        stock_out.status = STATUS_APPROVED
        stock_out.auditor_id = staff.id
        stock_out.audit_time = _now()
    else:
        stock_out.status = STATUS_PENDING
    _commit(db, commit)
    return stock_out


def reject_stock_out(
    db: Session,
    *,
    stock_out_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> StockOut:
    stock_out = get_stock_out_or_404(db, stock_out_id, check_scope=False)
    if stock_out.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前出库单状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=stock_out.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(stock_out, state)
    stock_out.status = STATUS_REJECTED
    stock_out.auditor_id = staff.id
    stock_out.audit_time = _now()
    _commit(db, commit)
    return stock_out


def _validate_off_shelf_location(
    db: Session, *, warehouse_id: int, location_id: int
) -> Location:
    location = db.get(Location, location_id)
    if location is None:
        raise BizException(ErrorCode.NOT_FOUND, "下架库位不存在")
    if location.warehouse_id != warehouse_id:
        raise BizException(ErrorCode.BAD_REQUEST, "下架库位不属于出库仓库")
    if not location.is_enabled:
        raise BizException(ErrorCode.CONFLICT, "下架库位已停用")
    return location


def off_shelf_stock_out(
    db: Session,
    *,
    stock_out_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> StockOut:
    _check_unknown_fields(data, {"items"})
    stock_out = get_stock_out_or_404(db, stock_out_id)
    if stock_out.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "出库单审核通过后才能下架")
    if stock_out.off_shelf_status == OFF_SHELF_DONE:
        raise BizException(ErrorCode.CONFLICT, "出库单已完成下架")
    if stock_out.purchase_return_id is None:
        raise BizException(ErrorCode.CONFLICT, "采购出库单未关联采购退货单")

    record = purchase_return_service.get_return_or_404(
        db, stock_out.purchase_return_id
    )
    if record.status != "APPROVED" or record.audit_status != "APPROVED":
        raise BizException(ErrorCode.CONFLICT, "采购退货单审核通过后才能下架")

    raw_items = data.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "下架明细不能为空")

    item_map = {item.id: item for item in _stock_out_items(db, stock_out.id)}
    if not item_map:
        raise BizException(ErrorCode.CONFLICT, "出库单没有可下架商品")
    for item in item_map.values():
        if item.sku_id is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                "采购出库下架必须指定商品规格",
            )

    warehouse = _validate_warehouse(db, stock_out.warehouse_id)
    stock_status = (
        "DEFECTIVE"
        if warehouse.warehouse_type == "DEFECTIVE"
        else "NORMAL"
    )
    required = {item.id: item.qty for item in item_map.values()}
    allocations: list[dict[str, Any]] = []
    available: dict[int, Decimal] = {}

    for raw in raw_items:
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "下架明细格式错误")
        _check_unknown_fields(raw, OFF_SHELF_FIELDS)
        item_id = _to_int_id(raw.get("stock_out_item_id"), "出库明细")
        location_id = _to_int_id(raw.get("location_id"), "下架库位")
        qty = _to_qty(raw.get("qty"), "下架数量")
        item = item_map.get(item_id)
        if item is None:
            raise BizException(ErrorCode.BAD_REQUEST, "出库明细不属于当前出库单")
        if qty > required[item.id]:
            raise BizException(ErrorCode.CONFLICT, "下架数量超过应出库数量")
        location = _validate_off_shelf_location(
            db,
            warehouse_id=stock_out.warehouse_id,
            location_id=location_id,
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
                "cost_price": item.cost_price,
                "barcode": item.barcode,
                "location": location,
                "qty": qty,
                "stock_row": stock_row,
            }
        )

    missing = [item_id for item_id, qty in required.items() if qty > ZERO_QTY]
    if missing:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            "下架数量合计必须等于出库数量",
        )

    purchase_order = (
        db.get(PurchaseOrder, record.order_id) if record.order_id else None
    )
    return_items = {
        (item.product_id, item.sku_id): item
        for item in db.scalars(
            select(PurchaseReturnItem).where(
                PurchaseReturnItem.return_id == record.id
            )
        )
    }
    for item in item_map.values():
        db.delete(item)
    db.flush()

    created_items: list[tuple[StockOutItem, dict[str, Any]]] = []
    for allocation in allocations:
        stock_row = allocation["stock_row"]
        stock_row.qty -= allocation["qty"]
        item = StockOutItem(
            stock_out_id=stock_out.id,
            product_id=allocation["product_id"],
            sku_id=allocation["sku_id"],
            location_id=allocation["location"].id,
            qty=allocation["qty"],
            cost_price=allocation["cost_price"],
            barcode=allocation["barcode"],
        )
        db.add(item)
        created_items.append((item, allocation))
    db.flush()

    for item, allocation in created_items:
        qty = allocation["qty"]
        location = allocation["location"]
        return_item = return_items.get(
            (allocation["product_id"], allocation["sku_id"])
        )
        product = db.get(Product, allocation["product_id"])
        unit = db.get(Unit, return_item.unit_id) if return_item else None
        if unit is None and product is not None and product.main_unit_id:
            unit = db.get(Unit, product.main_unit_id)
        total_cost = (
            round_money(qty * allocation["cost_price"])
            if allocation["cost_price"] is not None
            else None
        )
        db.add(
            StockFlow(
                warehouse_id=stock_out.warehouse_id,
                product_id=allocation["product_id"],
                sku_id=allocation["sku_id"],
                location_id=location.id,
                unit_id=unit.id if unit else None,
                direction="OUT",
                biz_type=STOCK_OUT_TYPE,
                qty=qty,
                cost_price=allocation["cost_price"],
                total_cost=total_cost,
                source_no=stock_out.stock_out_no,
                order_no=purchase_order.order_no if purchase_order else None,
                flow_time=_now(),
                remark=f"采购退货:{record.return_no}",
            )
        )
    db.flush()

    stock_out.status = STATUS_APPROVED
    stock_out.audit_status = AUDIT_APPROVED
    stock_out.off_shelf_status = OFF_SHELF_DONE
    stock_out.auditor_id = staff.id
    stock_out.audit_time = _now()
    purchase_return_service.finalize_manual_return_outbound(
        db,
        return_id=record.id,
        stock_out_id=stock_out.id,
        staff=staff,
        commit=False,
    )
    write_audit(
        db,
        action="OFF_SHELF",
        target_type=DOC_TYPE,
        target_id=str(stock_out.id),
        summary=f"采购出库下架完成：{stock_out.stock_out_no}",
        detail={
            "purchase_return_id": record.id,
            "warehouse_id": stock_out.warehouse_id,
            "items": [
                {
                    "stock_out_item_id": item.id,
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
    return stock_out
