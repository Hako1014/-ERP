"""采购发票服务（TASK-028）。

覆盖发票类型与现行默认税率、发票 CRUD、审批、与采购入库单成本核算以及
核销状态维护。

金额口径：
- `amount` 为发票价税合计，红字发票使用负数。
- `tax_amount = amount * tax_rate / (100 + tax_rate)`，即按含税金额价税分离。
- `net_amount = amount - tax_amount`，作为采购成本分摊金额。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.org import Staff
from app.models.party import Supplier
from app.models.product import Product
from app.models.purchase import PurchaseInvoice, PurchaseOrder
from app.models.stock import StockIn, StockInItem
from app.repositories.rbac_repo import list_staff_role_codes
from app.services import approval_service, party_service

DOC_TYPE = "purchase_invoice"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

WRITE_OFF_NONE = "NONE"
WRITE_OFF_PARTIAL = "PARTIAL"
WRITE_OFF_FULL = "FULL"

MAX_STOCK_INS = 200
MAX_COST_ALLOCATION_ROWS = 10000

INVOICE_TYPES: dict[str, dict[str, Any]] = {
    "NORMAL": {"name": "普通发票", "tax_rate": Decimal("0.00"), "red": False},
    "SPECIAL": {"name": "专用发票", "tax_rate": Decimal("13.00"), "red": False},
    "AGRICULTURAL": {
        "name": "农副产品发票",
        "tax_rate": Decimal("9.00"),
        "red": False,
    },
    "RED_SPECIAL": {
        "name": "红字专用发票",
        "tax_rate": Decimal("13.00"),
        "red": True,
    },
    "RED_NORMAL": {
        "name": "红字普通发票",
        "tax_rate": Decimal("0.00"),
        "red": True,
    },
    "RED_AGRICULTURAL": {
        "name": "红字农副产品发票",
        "tax_rate": Decimal("9.00"),
        "red": True,
    },
}

TYPE_ALIASES = {
    config["name"]: code for code, config in INVOICE_TYPES.items()
}

INVOICE_FIELDS = {
    "supplier_id",
    "invoice_type",
    "tax_rate",
    "amount",
    "business_type",
    "voucher_no",
    "description",
    "stock_in_ids",
}


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


def _optional_text(value: Any, label: str, max_length: int) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}")
    return text


def _normalize_invoice_type(value: Any) -> str:
    if value in (None, ""):
        raise BizException(ErrorCode.BAD_REQUEST, "发票类型不能为空")
    raw = str(value).strip()
    code = raw.upper()
    if code in INVOICE_TYPES:
        return code
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    raise BizException(ErrorCode.BAD_REQUEST, f"不支持的发票类型：{raw}")


def _to_invoice_amount(value: Any) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "发票金额必须为合法金额") from exc
    if result == Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "发票金额不能为 0")
    return result


def _to_tax_rate(value: Any, default: Decimal) -> Decimal:
    if value in (None, ""):
        return default
    try:
        rate = Decimal(str(value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "税率必须为合法数字") from exc
    if rate < Decimal("0.00") or rate > Decimal("100.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "税率必须在 0 到 100 之间")
    return rate


def _tax_amount(amount: Decimal, tax_rate: Decimal) -> Decimal:
    if tax_rate == Decimal("0.00"):
        return Decimal("0.00")
    return round_money(amount * tax_rate / (Decimal("100.00") + tax_rate))


def _normalize_stock_in_ids(value: Any) -> list[int]:
    if value in (None, ""):
        return []
    if not isinstance(value, (list, tuple)):
        raise BizException(ErrorCode.BAD_REQUEST, "入库单必须为 ID 数组")
    if len(value) > MAX_STOCK_INS:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"单张发票最多关联 {MAX_STOCK_INS} 张入库单"
        )
    result: list[int] = []
    for raw in value:
        stock_in_id = _to_int_id(raw, "入库单")
        if stock_in_id not in result:
            result.append(stock_in_id)
    return result


def _stock_in_ids_json(values: list[int]) -> str | None:
    return json.dumps(values, ensure_ascii=False) if values else None


def _parse_stock_in_ids(invoice: PurchaseInvoice) -> list[int]:
    if not invoice.stock_in_ids:
        return []
    try:
        loaded = json.loads(invoice.stock_in_ids)
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    result: list[int] = []
    for raw in loaded:
        try:
            value = int(raw)
        except (TypeError, ValueError):
            continue
        if value > 0 and value not in result:
            result.append(value)
    return result


def get_invoice_or_404(db: Session, invoice_id: int, *, check_scope: bool = True) -> PurchaseInvoice:
    invoice = db.get(PurchaseInvoice, invoice_id)
    if invoice is None or (check_scope and not document_visible(db, invoice.created_by, DOC_TYPE, invoice.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购发票不存在")
    return invoice


def _stock_in_items(db: Session, stock_in_id: int) -> list[StockInItem]:
    return list(
        db.scalars(
            select(StockInItem)
            .where(StockInItem.stock_in_id == stock_in_id)
            .order_by(StockInItem.id)
        )
    )


def _validate_stock_ins(
    db: Session,
    *,
    supplier_id: int,
    stock_in_ids: list[int],
    exclude_invoice_id: int | None = None,
    allow_reused: bool = False,
) -> list[StockIn]:
    if not stock_in_ids:
        raise BizException(ErrorCode.BAD_REQUEST, "采购发票必须关联至少一张入库单")

    stock_ins: list[StockIn] = []
    for stock_in_id in stock_in_ids:
        stock_in = db.get(StockIn, stock_in_id)
        if stock_in is None:
            raise BizException(
                ErrorCode.BAD_REQUEST, f"入库单不存在：{stock_in_id}"
            )
        if stock_in.audit_status != AUDIT_APPROVED:
            raise BizException(
                ErrorCode.CONFLICT,
                f"入库单未审核通过，不能核算成本：{stock_in.stock_in_no}",
            )
        if stock_in.purchase_order_id is None:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                f"入库单未关联采购订单，无法核对供应商：{stock_in.stock_in_no}",
            )
        order = db.get(PurchaseOrder, stock_in.purchase_order_id)
        if order is None or order.supplier_id != supplier_id:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                f"入库单供应商与发票不一致：{stock_in.stock_in_no}",
            )
        if not _stock_in_items(db, stock_in.id):
            raise BizException(
                ErrorCode.BAD_REQUEST, f"入库单没有商品明细：{stock_in.stock_in_no}"
            )
        stock_ins.append(stock_in)

    if not allow_reused:
        used_ids: set[int] = set()
        stmt = select(PurchaseInvoice).where(
            PurchaseInvoice.status == STATUS_APPROVED
        )
        if exclude_invoice_id is not None:
            stmt = stmt.where(PurchaseInvoice.id != exclude_invoice_id)
        for other in db.scalars(stmt):
            used_ids.update(_parse_stock_in_ids(other))
        duplicated = sorted(set(stock_in_ids) & used_ids)
        if duplicated:
            raise BizException(
                ErrorCode.CONFLICT,
                f"入库单已用于其他已审核发票：{', '.join(str(v) for v in duplicated)}",
            )
    return stock_ins


def _allocation_weight(item: StockInItem) -> Decimal:
    if item.amount is not None and item.amount != Decimal("0.00"):
        return abs(item.amount)
    if item.cost_price is not None:
        fallback = abs(item.qty * item.cost_price)
        if fallback > Decimal("0.00"):
            return fallback
    return abs(item.qty)


def _money_to_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _allocation_json(rows: list[dict]) -> str:
    serializable = []
    for row in rows:
        serializable.append(
            {
                key: (
                    _money_to_text(value)
                    if isinstance(value, Decimal)
                    else value
                )
                for key, value in row.items()
            }
        )
    return json.dumps(serializable, ensure_ascii=False)


def _normalize_allocation_rows(rows: list[Any]) -> list[dict]:
    decimal_fields = {
        "qty",
        "original_amount",
        "original_cost_price",
        "allocated_amount",
        "new_amount",
        "new_cost_price",
    }
    result: list[dict] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        normalized = dict(row)
        for field in decimal_fields:
            if normalized.get(field) is not None:
                try:
                    normalized[field] = Decimal(str(normalized[field]))
                except InvalidOperation:
                    normalized[field] = None
        result.append(normalized)
    return result


def _load_allocation(invoice: PurchaseInvoice) -> list[dict]:
    if not invoice.cost_allocation:
        return []
    try:
        rows = json.loads(invoice.cost_allocation)
    except json.JSONDecodeError:
        return []
    if not isinstance(rows, list):
        return []
    return _normalize_allocation_rows(rows)


def _cost_allocation_length_sql(dialect_name: str) -> str:
    if dialect_name == "sqlite":
        valid, kind, length, array_type = (
            "json_valid", "json_type", "json_array_length", "array",
        )
    elif dialect_name == "mysql":
        valid, kind, length, array_type = (
            "JSON_VALID", "JSON_TYPE", "JSON_LENGTH", "ARRAY",
        )
    else:
        raise RuntimeError(f"不支持的成本分摊分页数据库：{dialect_name}")
    return f"""
        SELECT CASE WHEN {valid}(invoice.cost_allocation) THEN
          CASE WHEN {kind}(invoice.cost_allocation) = '{array_type}'
            THEN {length}(invoice.cost_allocation)
            ELSE 0
          END
        ELSE 0 END
        FROM purchase_invoice AS invoice
        WHERE invoice.id = :invoice_id
    """


def _cost_allocation_page_sql(
    dialect_name: str,
    server_version: tuple[int, ...] | None = None,
) -> tuple[str, str]:
    """返回数据库端计数/分页SQL，避免把整段成本JSON拉进应用进程。"""
    if dialect_name == "sqlite":
        source = """
            CASE
              WHEN json_valid(invoice.cost_allocation) THEN
                CASE
                  WHEN json_type(invoice.cost_allocation, '$') = 'array'
                    THEN invoice.cost_allocation
                  ELSE '[]'
                END
              ELSE '[]'
            END
        """
        from_clause = f"""
            purchase_invoice AS invoice,
            json_each({source}) AS allocation
        """
        object_filter = "allocation.type = 'object'"
        row_value = "allocation.value"
        ordinal = "CAST(allocation.key AS INTEGER)"
    elif dialect_name == "mysql" and server_version is not None and server_version >= (8, 0):
        source = """
            CASE
              WHEN JSON_VALID(invoice.cost_allocation) THEN
                CASE
                  WHEN JSON_TYPE(CAST(invoice.cost_allocation AS JSON)) = 'ARRAY'
                    THEN CAST(invoice.cost_allocation AS JSON)
                  ELSE JSON_ARRAY()
                END
              ELSE JSON_ARRAY()
            END
        """
        from_clause = f"""
            purchase_invoice AS invoice
            CROSS JOIN JSON_TABLE(
              {source},
              '$[*]' COLUMNS (
                ordinal FOR ORDINALITY,
                row_value JSON PATH '$' NULL ON EMPTY NULL ON ERROR
              )
            ) AS allocation
        """
        object_filter = "JSON_TYPE(allocation.row_value) = 'OBJECT'"
        row_value = "CAST(allocation.row_value AS CHAR CHARACTER SET utf8mb4)"
        ordinal = "allocation.ordinal"
    elif dialect_name == "mysql":
        source = """
            CASE
              WHEN JSON_VALID(invoice.cost_allocation) THEN
                CASE
                  WHEN JSON_TYPE(invoice.cost_allocation) = 'ARRAY'
                    THEN invoice.cost_allocation
                  ELSE JSON_ARRAY()
                END
              ELSE JSON_ARRAY()
            END
        """
        digit_rows = " UNION ALL ".join(
            f"SELECT {value}" + (" AS digit" if value == 0 else "")
            for value in range(10)
        )
        digits = f"({digit_rows})"
        from_clause = f"""
            purchase_invoice AS invoice
            CROSS JOIN (
              SELECT d0.digit + d1.digit * 10 + d2.digit * 100
                     + d3.digit * 1000 AS ordinal
              FROM {digits} AS d0
              CROSS JOIN {digits} AS d1
              CROSS JOIN {digits} AS d2
              CROSS JOIN {digits} AS d3
            ) AS allocation_index
        """
        value = (
            "JSON_EXTRACT("
            f"{source}, CONCAT('$[', allocation_index.ordinal, ']'))"
        )
        object_filter = (
            f"allocation_index.ordinal < JSON_LENGTH({source}) "
            f"AND JSON_TYPE({value}) = 'OBJECT'"
        )
        row_value = f"CAST({value} AS CHAR CHARACTER SET utf8mb4)"
        ordinal = "allocation_index.ordinal"
    else:
        raise RuntimeError(f"不支持的成本分摊分页数据库：{dialect_name}")

    base = f"""
        FROM {from_clause}
        WHERE invoice.id = :invoice_id AND {object_filter}
    """
    return (
        f"SELECT COUNT(*) {base}",
        f"""
            SELECT {row_value} AS row_json {base}
            ORDER BY {ordinal}
            LIMIT :page_size OFFSET :offset
        """,
    )


def get_stock_in_cost_detail_page(
    db: Session, invoice_id: int, *, page: int, page_size: int,
) -> tuple[list[dict], int]:
    """重验父发票权限，并在数据库内展开、计数、分页成本分摊JSON。"""
    owner_row = db.execute(
        select(PurchaseInvoice.created_by).where(PurchaseInvoice.id == invoice_id)
    ).first()
    if owner_row is None or not document_visible(
        db, owner_row[0], DOC_TYPE, invoice_id,
    ):
        raise BizException(ErrorCode.NOT_FOUND, "采购发票不存在")

    params = {"invoice_id": invoice_id}
    dialect = db.get_bind().dialect
    array_length_sql = _cost_allocation_length_sql(dialect.name)
    array_length = int(db.scalar(text(array_length_sql), params) or 0)
    if array_length > MAX_COST_ALLOCATION_ROWS:
        raise BizException(ErrorCode.BAD_REQUEST, "成本分摊明细超过安全读取上限")
    count_sql, page_sql = _cost_allocation_page_sql(
        dialect.name, getattr(dialect, "server_version_info", None),
    )
    total = int(db.scalar(text(count_sql), params) or 0)
    rows = db.execute(
        text(page_sql),
        {
            **params,
            "page_size": page_size,
            "offset": (page - 1) * page_size,
        },
    ).scalars().all()
    normalized_rows = []
    for raw_row in rows:
        if isinstance(raw_row, (bytes, bytearray)):
            raw_row = raw_row.decode("utf-8")
        try:
            parsed = raw_row if isinstance(raw_row, dict) else json.loads(raw_row)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(parsed, dict):
            normalized_rows.append(parsed)
    return _normalize_allocation_rows(normalized_rows), total


def _build_cost_allocation(
    db: Session,
    *,
    invoice: PurchaseInvoice,
    stock_in_ids: list[int],
) -> list[dict]:
    stock_ins = _validate_stock_ins(
        db,
        supplier_id=invoice.supplier_id,
        stock_in_ids=stock_in_ids,
        exclude_invoice_id=invoice.id,
        allow_reused=invoice.invoice_type.startswith("RED_"),
    )
    lines: list[tuple[StockIn, StockInItem, Decimal]] = []
    total_weight = Decimal("0")
    for stock_in in stock_ins:
        for item in _stock_in_items(db, stock_in.id):
            weight = _allocation_weight(item)
            total_weight += weight
            lines.append((stock_in, item, weight))
    if total_weight <= Decimal("0"):
        raise BizException(ErrorCode.CONFLICT, "入库商品成本基数不能为 0")

    net_amount = round_money(invoice.amount - (invoice.tax_amount or Decimal("0.00")))
    allocations: list[dict] = []
    allocated_total = Decimal("0.00")
    for index, (stock_in, item, weight) in enumerate(lines):
        if index == len(lines) - 1:
            allocated = round_money(net_amount - allocated_total)
        else:
            allocated = round_money(net_amount * weight / total_weight)
            allocated_total += allocated

        original_amount = (
            item.amount
            if item.amount is not None
            else round_money(item.qty * (item.cost_price or Decimal("0.00")))
        )
        if invoice.invoice_type.startswith("RED_"):
            new_amount = round_money(original_amount + allocated)
            if new_amount < Decimal("0.00"):
                raise BizException(
                    ErrorCode.CONFLICT,
                    f"红字发票冲减后成本不能为负数：{stock_in.stock_in_no}",
                )
        else:
            new_amount = allocated
        new_cost_price = (
            round_money(new_amount / item.qty)
            if item.qty != Decimal("0.000")
            else Decimal("0.00")
        )
        allocations.append(
            {
                "stock_in_id": stock_in.id,
                "stock_in_no": stock_in.stock_in_no,
                "stock_in_item_id": item.id,
                "product_id": item.product_id,
                "sku_id": item.sku_id,
                "qty": item.qty,
                "original_amount": original_amount,
                "original_cost_price": item.cost_price,
                "allocated_amount": allocated,
                "new_amount": new_amount,
                "new_cost_price": new_cost_price,
            }
        )
    return allocations


def _apply_cost_accounting(
    db: Session, *, invoice: PurchaseInvoice, staff: Staff
) -> None:
    if invoice.cost_accounted_at is not None:
        return
    stock_in_ids = _parse_stock_in_ids(invoice)
    allocations = _build_cost_allocation(
        db, invoice=invoice, stock_in_ids=stock_in_ids
    )
    for row in allocations:
        item = db.get(StockInItem, row["stock_in_item_id"])
        if item is None:
            raise BizException(
                ErrorCode.CONFLICT,
                f"入库单明细不存在：{row['stock_in_item_id']}",
            )
        item.amount = row["new_amount"]
        item.cost_price = row["new_cost_price"]

    invoice.cost_allocation = _allocation_json(allocations)
    invoice.cost_accounted_at = _now()
    net_amount = round_money(
        invoice.amount - (invoice.tax_amount or Decimal("0.00"))
    )
    write_audit(
        db,
        action="COST_ACCOUNT",
        target_type=DOC_TYPE,
        target_id=str(invoice.id),
        summary=f"采购发票核算入库成本：{invoice.invoice_no}",
        detail={
            "stock_in_ids": stock_in_ids,
            "net_amount": str(net_amount),
            "allocation_count": len(allocations),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()


def _restore_cost_accounting(
    db: Session, *, invoice: PurchaseInvoice, staff: Staff
) -> None:
    allocations = _load_allocation(invoice)
    for row in allocations:
        item = db.get(StockInItem, row.get("stock_in_item_id"))
        if item is None:
            raise BizException(
                ErrorCode.CONFLICT,
                f"入库单明细不存在，无法恢复成本：{row.get('stock_in_item_id')}",
            )
        item.amount = row.get("original_amount")
        item.cost_price = row.get("original_cost_price")
    invoice.cost_allocation = None
    invoice.cost_accounted_at = None
    write_audit(
        db,
        action="RESTORE_COST",
        target_type=DOC_TYPE,
        target_id=str(invoice.id),
        summary=f"弃审采购发票并恢复入库成本：{invoice.invoice_no}",
        detail={"allocation_count": len(allocations)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    db.flush()


def _recalculate_write_off(invoice: PurchaseInvoice) -> None:
    capacity = abs(invoice.amount)
    amount = invoice.write_off_amount or Decimal("0.00")
    if amount <= Decimal("0.00"):
        invoice.write_off_status = WRITE_OFF_NONE
    elif amount < capacity:
        invoice.write_off_status = WRITE_OFF_PARTIAL
    else:
        invoice.write_off_status = WRITE_OFF_FULL


def invoice_dict(
    db: Session,
    invoice: PurchaseInvoice,
    *,
    with_approval: bool = False,
) -> dict:
    supplier = db.get(Supplier, invoice.supplier_id)
    creator = db.get(Staff, invoice.created_by) if invoice.created_by else None
    auditor = db.get(Staff, invoice.auditor_id) if invoice.auditor_id else None
    type_config = INVOICE_TYPES.get(invoice.invoice_type)
    stock_in_ids = _parse_stock_in_ids(invoice)
    stock_in_nos = list(
        db.scalars(
            select(StockIn.stock_in_no)
            .where(StockIn.id.in_(stock_in_ids))
            .order_by(StockIn.id)
        )
    ) if stock_in_ids else []
    tax_amount = invoice.tax_amount or Decimal("0.00")
    data = {
        "id": invoice.id,
        "invoice_no": invoice.invoice_no,
        "supplier_id": invoice.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "invoice_type": invoice.invoice_type,
        "invoice_type_name": type_config["name"] if type_config else None,
        "default_tax_rate": type_config["tax_rate"] if type_config else None,
        "tax_rate": invoice.tax_rate,
        "amount": invoice.amount,
        "tax_amount": tax_amount,
        "net_amount": round_money(invoice.amount - tax_amount),
        "write_off_amount": invoice.write_off_amount,
        "write_off_status": invoice.write_off_status,
        "business_type": invoice.business_type,
        "voucher_no": invoice.voucher_no,
        "description": invoice.description,
        "status": invoice.status,
        "audit_status": invoice.audit_status,
        "stock_in_ids": stock_in_ids,
        "stock_in_nos": stock_in_nos,
        "cost_allocation": _load_allocation(invoice),
        "cost_accounted_at": invoice.cost_accounted_at,
        "created_by": invoice.created_by,
        "created_by_name": creator.name if creator else None,
        "auditor_id": invoice.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": invoice.audit_time,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=DOC_TYPE, doc_id=invoice.id
        )
    return data


def list_invoices(
    db: Session,
    *,
    supplier_id: int | None = None,
    invoice_type: str | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    write_off_status: str | None = None,
    business_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, PurchaseInvoice.created_by)
    if supplier_id is not None:
        conditions.append(PurchaseInvoice.supplier_id == supplier_id)
    if invoice_type:
        conditions.append(
            PurchaseInvoice.invoice_type == _normalize_invoice_type(invoice_type)
        )
    if status:
        conditions.append(PurchaseInvoice.status == status.upper())
    if audit_status:
        conditions.append(PurchaseInvoice.audit_status == audit_status.upper())
    if write_off_status:
        conditions.append(
            PurchaseInvoice.write_off_status == write_off_status.upper()
        )
    if business_type:
        conditions.append(PurchaseInvoice.business_type == business_type)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                PurchaseInvoice.invoice_no.like(like),
                PurchaseInvoice.voucher_no.like(like),
                PurchaseInvoice.supplier_id.in_(
                    select(Supplier.id).where(Supplier.name.like(like))
                ),
            )
        )
    total = db.scalar(
        select(func.count(PurchaseInvoice.id)).where(*conditions)
    ) or 0
    rows = list(
        db.scalars(
            select(PurchaseInvoice)
            .where(*conditions)
            .order_by(PurchaseInvoice.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            invoice_dict(db, row, with_approval=False) for row in rows
        ],
    }


def create_invoice(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> PurchaseInvoice:
    _check_unknown_fields(data, INVOICE_FIELDS)
    supplier = party_service.get_supplier_or_404(
        db, _to_int_id(data.get("supplier_id"), "供应商")
    )
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用，不能新增采购发票")

    invoice_type = _normalize_invoice_type(data.get("invoice_type"))
    type_config = INVOICE_TYPES[invoice_type]
    amount = _to_invoice_amount(data.get("amount"))
    if type_config["red"] and amount >= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "红字发票金额必须为负数")
    if not type_config["red"] and amount <= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "非红字发票金额必须为正数")

    tax_rate = _to_tax_rate(data.get("tax_rate"), type_config["tax_rate"])
    tax_amount = _tax_amount(amount, tax_rate)
    stock_in_ids = _normalize_stock_in_ids(data.get("stock_in_ids"))
    if stock_in_ids:
        _validate_stock_ins(
            db,
            supplier_id=supplier.id,
            stock_in_ids=stock_in_ids,
            allow_reused=invoice_type.startswith("RED_"),
        )

    invoice = PurchaseInvoice(
        invoice_no=generate_serial(SerialPrefix.PURCHASE_INVOICE, dt=_now()),
        supplier_id=supplier.id,
        invoice_type=invoice_type,
        tax_rate=tax_rate,
        amount=amount,
        tax_amount=tax_amount,
        write_off_amount=Decimal("0.00"),
        write_off_status=WRITE_OFF_NONE,
        business_type=_optional_text(data.get("business_type"), "业务类型", 32),
        voucher_no=_optional_text(data.get("voucher_no"), "凭证编号", 64),
        description=_optional_text(data.get("description"), "发票描述", 255),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        stock_in_ids=_stock_in_ids_json(stock_in_ids),
        created_by=staff.id,
    )
    db.add(invoice)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(invoice.id),
        summary=f"新增采购发票：{invoice.invoice_no}",
        detail={
            "supplier_id": supplier.id,
            "invoice_type": invoice_type,
            "amount": str(amount),
            "tax_rate": str(tax_rate),
            "stock_in_ids": stock_in_ids,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return invoice


def _approval_state(db: Session, invoice_id: int) -> dict:
    return approval_service.get_state(
        db, doc_type=DOC_TYPE, doc_id=invoice_id
    )["state"]


def _ensure_editable(db: Session, invoice: PurchaseInvoice) -> None:
    if invoice.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购发票状态不允许修改")
    state = _approval_state(db, invoice.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "采购发票已进入审批流程，不能修改")


def update_invoice(
    db: Session,
    *,
    invoice_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchaseInvoice:
    _check_unknown_fields(data, INVOICE_FIELDS)
    invoice = get_invoice_or_404(db, invoice_id)
    _ensure_editable(db, invoice)

    if "supplier_id" in data:
        supplier = party_service.get_supplier_or_404(
            db, _to_int_id(data["supplier_id"], "供应商")
        )
        if not supplier.is_active:
            raise BizException(ErrorCode.CONFLICT, "供应商已停用")
        invoice.supplier_id = supplier.id

    if "amount" in data:
        invoice.amount = _to_invoice_amount(data["amount"])
    if "invoice_type" in data:
        invoice_type = _normalize_invoice_type(data["invoice_type"])
        type_config = INVOICE_TYPES[invoice_type]
        invoice.invoice_type = invoice_type
        if "tax_rate" not in data:
            invoice.tax_rate = type_config["tax_rate"]
    if "tax_rate" in data:
        invoice.tax_rate = _to_tax_rate(
            data["tax_rate"], INVOICE_TYPES[invoice.invoice_type]["tax_rate"]
        )
    if invoice.invoice_type.startswith("RED_") and invoice.amount >= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "红字发票金额必须为负数")
    if not invoice.invoice_type.startswith("RED_") and invoice.amount <= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "非红字发票金额必须为正数")
    invoice.tax_amount = _tax_amount(
        invoice.amount,
        invoice.tax_rate or Decimal("0.00"),
    )
    if "business_type" in data:
        invoice.business_type = _optional_text(
            data["business_type"], "业务类型", 32
        )
    if "voucher_no" in data:
        invoice.voucher_no = _optional_text(data["voucher_no"], "凭证编号", 64)
    if "description" in data:
        invoice.description = _optional_text(
            data["description"], "发票描述", 255
        )
    if "stock_in_ids" in data:
        stock_in_ids = _normalize_stock_in_ids(data["stock_in_ids"])
        if stock_in_ids:
            _validate_stock_ins(
                db,
                supplier_id=invoice.supplier_id,
                stock_in_ids=stock_in_ids,
                exclude_invoice_id=invoice.id,
                allow_reused=invoice.invoice_type.startswith("RED_"),
            )
        invoice.stock_in_ids = _stock_in_ids_json(stock_in_ids)

    if invoice.status == STATUS_REJECTED:
        invoice.status = STATUS_PENDING
        invoice.audit_status = AUDIT_PENDING
        invoice.auditor_id = None
        invoice.audit_time = None
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(invoice.id),
        summary=f"修改采购发票：{invoice.invoice_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return invoice


def delete_invoice(
    db: Session, *, invoice_id: int, staff: Staff, commit: bool = True
) -> None:
    invoice = get_invoice_or_404(db, invoice_id)
    _ensure_editable(db, invoice)
    if invoice.cost_accounted_at is not None:
        raise BizException(ErrorCode.CONFLICT, "采购发票已核算成本，不能删除")
    db.delete(invoice)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(invoice.id),
        summary=f"删除采购发票：{invoice.invoice_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(invoice: PurchaseInvoice, state: dict) -> None:
    if state["status"] == "APPROVED":
        invoice.audit_status = AUDIT_APPROVED
    elif state["status"] == "REJECTED":
        invoice.audit_status = AUDIT_REJECTED
    else:
        invoice.audit_status = AUDIT_PENDING


def submit_invoice(
    db: Session,
    *,
    invoice_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseInvoice:
    invoice = get_invoice_or_404(db, invoice_id)
    _ensure_editable(db, invoice)
    stock_in_ids = _parse_stock_in_ids(invoice)
    _validate_stock_ins(
        db,
        supplier_id=invoice.supplier_id,
        stock_in_ids=stock_in_ids,
        exclude_invoice_id=invoice.id,
        allow_reused=invoice.invoice_type.startswith("RED_"),
    )
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=invoice.id,
        doc_no=invoice.invoice_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    invoice.status = STATUS_PENDING
    _map_audit_status(invoice, state)
    invoice.auditor_id = None
    invoice.audit_time = None
    _commit(db, commit)
    return invoice


def _staff_roles(db: Session, staff: Staff) -> set[str]:
    return set(list_staff_role_codes(db, staff.id))


def _assert_finance_approver(db: Session, staff: Staff) -> None:
    if not _staff_roles(db, staff) & {"FINANCE", "ADMIN"}:
        raise BizException(
            ErrorCode.FORBIDDEN, "采购发票只能由财务人员审核"
        )


def approve_invoice(
    db: Session,
    *,
    invoice_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseInvoice:
    invoice = get_invoice_or_404(db, invoice_id, check_scope=False)
    if invoice.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购发票状态不能审核")
    try:
        _assert_finance_approver(db, staff)
        state = approval_service.approve(
            db,
            doc_type=DOC_TYPE,
            doc_id=invoice.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(invoice, state)
        if state["status"] == "APPROVED":
            invoice.status = STATUS_APPROVED
            invoice.auditor_id = staff.id
            invoice.audit_time = _now()
            _apply_cost_accounting(db, invoice=invoice, staff=staff)
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return invoice


def reject_invoice(
    db: Session,
    *,
    invoice_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchaseInvoice:
    invoice = get_invoice_or_404(db, invoice_id, check_scope=False)
    if invoice.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前采购发票状态不能审核")
    _assert_finance_approver(db, staff)
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=invoice.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    invoice.status = STATUS_REJECTED
    _map_audit_status(invoice, state)
    invoice.auditor_id = staff.id
    invoice.audit_time = _now()
    _commit(db, commit)
    return invoice


def unaudit_invoice(
    db: Session,
    *,
    invoice_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> PurchaseInvoice:
    invoice = get_invoice_or_404(db, invoice_id, check_scope=False)
    if invoice.status != STATUS_APPROVED or invoice.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购发票尚未审核通过，不能弃审")
    _assert_finance_approver(db, staff)
    try:
        _restore_cost_accounting(db, invoice=invoice, staff=staff)
        state = approval_service.unaudit(
            db,
            doc_type=DOC_TYPE,
            doc_id=invoice.id,
            staff=staff,
            reason=reason,
            step=step,
            commit=False,
        )
        invoice.status = STATUS_PENDING
        _map_audit_status(invoice, state)
        invoice.auditor_id = None
        invoice.audit_time = None
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return invoice


def record_write_off(
    db: Session,
    *,
    invoice_id: int,
    amount: Any,
    commit: bool = True,
) -> PurchaseInvoice:
    invoice = get_invoice_or_404(db, invoice_id)
    if invoice.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购发票审核通过后才能核销")
    try:
        write_off = round_money(amount)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, "核销金额必须为合法金额") from exc
    if write_off <= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, "核销金额必须大于 0")
    capacity = abs(invoice.amount)
    current = invoice.write_off_amount or Decimal("0.00")
    if current + write_off > capacity:
        raise BizException(ErrorCode.CONFLICT, "累计核销金额不能超过发票金额")
    invoice.write_off_amount = round_money(current + write_off)
    _recalculate_write_off(invoice)
    write_audit(
        db,
        action="WRITE_OFF",
        target_type=DOC_TYPE,
        target_id=str(invoice.id),
        summary=f"采购发票核销：{invoice.invoice_no}",
        detail={
            "write_off_amount": str(write_off),
            "total_write_off": str(invoice.write_off_amount),
            "write_off_status": invoice.write_off_status,
        },
        actor_id=invoice.auditor_id,
        actor_name=None,
    )
    _commit(db, commit)
    return invoice


def get_stock_in_cost_detail(db: Session, invoice_id: int) -> list[dict]:
    invoice = get_invoice_or_404(db, invoice_id)
    return _load_allocation(invoice)
