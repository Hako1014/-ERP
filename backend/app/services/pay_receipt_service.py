"""付款单与收款单服务（TASK-044）。

覆盖付款单、收款单 CRUD、关联单据校验、审批、审核后自动生成凭证以及
发票金额不一致提醒。

凭证口径：
- 付款单：借 `2202 应付账款`；未关联预付款时贷 `1002 银行存款`，
  关联已审核预付款时贷 `1123 预付账款`。
- 收款单：借 `1002 银行存款`，贷 `1122 应收账款`。

收入单和费用单在本任务只做关联校验与引用，其自身分录由 TASK-045 负责，
避免本服务重复记账。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, scope_conditions
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.finance import (
    Expense,
    Income,
    Invoice,
    Payment,
    Receipt,
)
from app.models.order import Order
from app.models.org import Staff
from app.models.party import Customer, Supplier
from app.models.purchase import PurchaseInvoice, PurchaseOrder, PurchasePrepay
from app.models.stock import StockIn
from app.repositories.rbac_repo import list_staff_role_codes
from app.services import approval_service, party_service, voucher_service

PAYMENT_DOC_TYPE = "payment"
RECEIPT_DOC_TYPE = "receipt"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

PAYMENT_FIELDS = {
    "supplier_id",
    "stock_in_id",
    "invoice_id",
    "income_id",
    "prepay_id",
    "payment_type",
    "pay_method",
    "amount",
}

RECEIPT_FIELDS = {
    "customer_id",
    "order_id",
    "invoice_id",
    "expense_id",
    "sale_type",
    "settle_type",
    "amount",
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


def _optional_int_id(value: Any, label: str) -> int | None:
    if value in (None, ""):
        return None
    return _to_int_id(value, label)


def _to_money(value: Any, label: str) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result <= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


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


def _get_supplier(db: Session, supplier_id: int) -> Supplier:
    supplier = party_service.get_supplier_or_404(db, supplier_id)
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用")
    return supplier


def _get_customer(db: Session, customer_id: int) -> Customer:
    customer = party_service.get_customer_or_404(db, customer_id)
    if not customer.is_active:
        raise BizException(ErrorCode.CONFLICT, "客户已停用")
    return customer


def get_payment_or_404(db: Session, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    if payment is None or not document_visible(db, payment.created_by, PAYMENT_DOC_TYPE, payment.id):
        raise BizException(ErrorCode.NOT_FOUND, "付款单不存在")
    return payment


def get_receipt_or_404(db: Session, receipt_id: int) -> Receipt:
    receipt = db.get(Receipt, receipt_id)
    if receipt is None or not document_visible(db, receipt.created_by, RECEIPT_DOC_TYPE, receipt.id):
        raise BizException(ErrorCode.NOT_FOUND, "收款单不存在")
    return receipt


def _validate_stock_in(
    db: Session, *, stock_in_id: int | None, supplier_id: int
) -> StockIn | None:
    if stock_in_id is None:
        return None
    stock_in = db.get(StockIn, stock_in_id)
    if stock_in is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联入库单不存在")
    if stock_in.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "关联入库单尚未审核通过")
    if stock_in.purchase_order_id is None:
        raise BizException(
            ErrorCode.BAD_REQUEST, "关联入库单未关联采购订单，无法核对供应商"
        )
    order = db.get(PurchaseOrder, stock_in.purchase_order_id)
    if order is None or order.supplier_id != supplier_id:
        raise BizException(
            ErrorCode.BAD_REQUEST, "关联入库单与付款供应商不一致"
        )
    return stock_in


def _validate_purchase_invoice(
    db: Session, *, invoice_id: int | None, supplier_id: int
) -> PurchaseInvoice | None:
    if invoice_id is None:
        return None
    invoice = db.get(PurchaseInvoice, invoice_id)
    if invoice is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联采购发票不存在")
    if invoice.supplier_id != supplier_id:
        raise BizException(
            ErrorCode.BAD_REQUEST, "关联采购发票与付款供应商不一致"
        )
    if invoice.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "关联采购发票尚未审核通过")
    return invoice


def _validate_prepay(
    db: Session, *, prepay_id: int | None, supplier_id: int
) -> PurchasePrepay | None:
    if prepay_id is None:
        return None
    prepay = db.get(PurchasePrepay, prepay_id)
    if prepay is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联采购预付款申请不存在")
    if prepay.supplier_id != supplier_id:
        raise BizException(
            ErrorCode.BAD_REQUEST, "关联预付款申请与付款供应商不一致"
        )
    if prepay.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "关联采购预付款申请尚未审核通过")
    return prepay


def _validate_income(
    db: Session, *, income_id: int | None, supplier_id: int
) -> Income | None:
    if income_id is None:
        return None
    income = db.get(Income, income_id)
    if income is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联收入单不存在")
    if income.supplier_id != supplier_id:
        raise BizException(
            ErrorCode.BAD_REQUEST, "关联收入单与付款供应商不一致"
        )
    if income.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "关联收入单尚未审核通过")
    return income


def _validate_order(db: Session, order_id: int | None) -> Order | None:
    if order_id is None:
        return None
    order = db.get(Order, order_id)
    if order is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联订单不存在")
    return order


def _validate_sales_invoice(
    db: Session, invoice_id: int | None
) -> Invoice | None:
    if invoice_id is None:
        return None
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联发票不存在")
    return invoice


def _validate_expense(
    db: Session,
    *,
    expense_id: int | None,
    customer_id: int,
    require_approved: bool = True,
    exclude_receipt_id: int | None = None,
) -> Expense | None:
    if expense_id is None:
        return None
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联费用单不存在")
    if expense.status == STATUS_CANCELLED:
        raise BizException(ErrorCode.CONFLICT, "关联费用单已作废")
    if require_approved and expense.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "关联费用单尚未审核通过")
    if (
        expense.customer_id is not None
        and expense.customer_id != customer_id
    ):
        raise BizException(
            ErrorCode.BAD_REQUEST, "关联费用单与收款客户不一致"
        )
    conditions = [
        Receipt.expense_id == expense.id,
        Receipt.status.in_([STATUS_PENDING, STATUS_APPROVED]),
    ]
    if exclude_receipt_id is not None:
        conditions.append(Receipt.id != exclude_receipt_id)
    if db.scalar(select(Receipt.id).where(*conditions)) is not None:
        raise BizException(
            ErrorCode.CONFLICT, "同一费用单只能关联一张有效收款单"
        )
    return expense


def _amount_warning(
    *,
    code: str,
    label: str,
    document_no: str,
    document_amount: Decimal,
    settled_amount: Decimal,
    remaining_amount: Decimal | None = None,
) -> dict | None:
    if document_amount == settled_amount:
        return None
    data = {
        "code": code,
        "message": f"{label} {document_no} 金额与本次结算金额不一致",
        "document_amount": document_amount,
        "settled_amount": settled_amount,
    }
    if remaining_amount is not None:
        data["remaining_amount"] = remaining_amount
    return data


def _payment_warnings(db: Session, payment: Payment) -> list[dict]:
    if payment.invoice_id is None:
        return []
    invoice = db.get(PurchaseInvoice, payment.invoice_id)
    if invoice is None:
        return []
    invoice_amount = abs(invoice.amount)
    remaining = max(
        invoice_amount - (invoice.write_off_amount or Decimal("0.00")),
        Decimal("0.00"),
    )
    warning = _amount_warning(
        code="PURCHASE_INVOICE_AMOUNT_MISMATCH",
        label="采购发票",
        document_no=invoice.invoice_no,
        document_amount=invoice_amount,
        settled_amount=payment.amount,
        remaining_amount=remaining,
    )
    return [warning] if warning else []


def _receipt_warnings(db: Session, receipt: Receipt) -> list[dict]:
    if receipt.invoice_id is None:
        return []
    invoice = db.get(Invoice, receipt.invoice_id)
    if invoice is None:
        return []
    remaining = max(
        invoice.amount - (invoice.received_amount or Decimal("0.00")),
        Decimal("0.00"),
    )
    warning = _amount_warning(
        code="SALES_INVOICE_AMOUNT_MISMATCH",
        label="发票",
        document_no=invoice.invoice_no,
        document_amount=invoice.amount,
        settled_amount=receipt.amount,
        remaining_amount=remaining,
    )
    return [warning] if warning else []


def _staff_name(db: Session, staff_id: int | None) -> str | None:
    if staff_id is None:
        return None
    staff = db.get(Staff, staff_id)
    return staff.name if staff else None


def payment_dict(
    db: Session,
    payment: Payment,
    *,
    with_approval: bool = False,
    with_voucher: bool = False,
) -> dict:
    supplier = db.get(Supplier, payment.supplier_id)
    stock_in = db.get(StockIn, payment.stock_in_id) if payment.stock_in_id else None
    invoice = (
        db.get(PurchaseInvoice, payment.invoice_id)
        if payment.invoice_id
        else None
    )
    income = db.get(Income, payment.income_id) if payment.income_id else None
    prepay = (
        db.get(PurchasePrepay, payment.prepay_id)
        if payment.prepay_id
        else None
    )
    warnings = _payment_warnings(db, payment)
    data = {
        "id": payment.id,
        "payment_no": payment.payment_no,
        "supplier_id": payment.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "stock_in_id": payment.stock_in_id,
        "stock_in_no": stock_in.stock_in_no if stock_in else None,
        "invoice_id": payment.invoice_id,
        "invoice_no": invoice.invoice_no if invoice else None,
        "income_id": payment.income_id,
        "income_no": income.income_no if income else None,
        "prepay_id": payment.prepay_id,
        "prepay_no": prepay.prepay_no if prepay else None,
        "payment_type": payment.payment_type,
        "pay_method": payment.pay_method,
        "amount": payment.amount,
        "status": payment.status,
        "audit_status": payment.audit_status,
        "warnings": warnings,
        "has_warning": bool(warnings),
        "created_by": payment.created_by,
        "created_by_name": _staff_name(db, payment.created_by),
        "auditor_id": payment.auditor_id,
        "auditor_name": _staff_name(db, payment.auditor_id),
        "audit_time": payment.audit_time,
        "created_at": payment.created_at,
        "updated_at": payment.updated_at,
    }
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=PAYMENT_DOC_TYPE, doc_id=payment.id
        )
    if with_voucher:
        data["voucher"] = voucher_service.voucher_dict(
            db,
            voucher_service.get_active_voucher_by_reference(
                db, f"付款单:{payment.payment_no}"
            ),
        )
    return data


def receipt_dict(
    db: Session,
    receipt: Receipt,
    *,
    with_approval: bool = False,
    with_voucher: bool = False,
) -> dict:
    customer = db.get(Customer, receipt.customer_id)
    order = db.get(Order, receipt.order_id) if receipt.order_id else None
    invoice = db.get(Invoice, receipt.invoice_id) if receipt.invoice_id else None
    expense = db.get(Expense, receipt.expense_id) if receipt.expense_id else None
    warnings = _receipt_warnings(db, receipt)
    data = {
        "id": receipt.id,
        "receipt_no": receipt.receipt_no,
        "customer_id": receipt.customer_id,
        "customer_name": customer.name if customer else None,
        "order_id": receipt.order_id,
        "order_no": order.order_no if order else None,
        "invoice_id": receipt.invoice_id,
        "invoice_no": invoice.invoice_no if invoice else None,
        "expense_id": receipt.expense_id,
        "expense_no": expense.expense_no if expense else None,
        "sale_type": receipt.sale_type,
        "settle_type": receipt.settle_type,
        "amount": receipt.amount,
        "status": receipt.status,
        "audit_status": receipt.audit_status,
        "warnings": warnings,
        "has_warning": bool(warnings),
        "created_by": receipt.created_by,
        "created_by_name": _staff_name(db, receipt.created_by),
        "auditor_id": receipt.auditor_id,
        "auditor_name": _staff_name(db, receipt.auditor_id),
        "audit_time": receipt.audit_time,
        "created_at": receipt.created_at,
        "updated_at": receipt.updated_at,
    }
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=RECEIPT_DOC_TYPE, doc_id=receipt.id
        )
    if with_voucher:
        data["voucher"] = voucher_service.voucher_dict(
            db,
            voucher_service.get_active_voucher_by_reference(
                db, f"收款单:{receipt.receipt_no}"
            ),
        )
    return data


def list_payments(
    db: Session,
    *,
    supplier_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    payment_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, Payment.created_by)
    if supplier_id is not None:
        conditions.append(Payment.supplier_id == supplier_id)
    if status:
        conditions.append(Payment.status == status.upper())
    if audit_status:
        conditions.append(Payment.audit_status == audit_status.upper())
    if payment_type:
        conditions.append(Payment.payment_type == payment_type)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Payment.payment_no.like(like),
                Payment.supplier_id.in_(
                    select(Supplier.id).where(Supplier.name.like(like))
                ),
            )
        )
    total = db.scalar(select(func.count(Payment.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Payment)
            .where(*conditions)
            .order_by(Payment.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [payment_dict(db, row) for row in rows],
    }


def list_receipts(
    db: Session,
    *,
    customer_id: int | None = None,
    order_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    sale_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, Receipt.created_by)
    if customer_id is not None:
        conditions.append(Receipt.customer_id == customer_id)
    if order_id is not None:
        conditions.append(Receipt.order_id == order_id)
    if status:
        conditions.append(Receipt.status == status.upper())
    if audit_status:
        conditions.append(Receipt.audit_status == audit_status.upper())
    if sale_type:
        conditions.append(Receipt.sale_type == sale_type)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Receipt.receipt_no.like(like),
                Receipt.customer_id.in_(
                    select(Customer.id).where(Customer.name.like(like))
                ),
                Receipt.order_id.in_(
                    select(Order.id).where(Order.order_no.like(like))
                ),
            )
        )
    total = db.scalar(select(func.count(Receipt.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Receipt)
            .where(*conditions)
            .order_by(Receipt.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [receipt_dict(db, row) for row in rows],
    }


def _payment_link_values(db: Session, data: dict, supplier_id: int) -> dict:
    stock_in_id = _optional_int_id(data.get("stock_in_id"), "入库单")
    invoice_id = _optional_int_id(data.get("invoice_id"), "采购发票")
    income_id = _optional_int_id(data.get("income_id"), "收入单")
    prepay_id = _optional_int_id(data.get("prepay_id"), "预付款申请")
    _validate_stock_in(
        db, stock_in_id=stock_in_id, supplier_id=supplier_id
    )
    _validate_purchase_invoice(
        db, invoice_id=invoice_id, supplier_id=supplier_id
    )
    _validate_income(db, income_id=income_id, supplier_id=supplier_id)
    _validate_prepay(db, prepay_id=prepay_id, supplier_id=supplier_id)
    return {
        "stock_in_id": stock_in_id,
        "invoice_id": invoice_id,
        "income_id": income_id,
        "prepay_id": prepay_id,
    }


def _receipt_link_values(
    db: Session,
    data: dict,
    customer_id: int,
    *,
    exclude_receipt_id: int | None = None,
) -> dict:
    order_id = _optional_int_id(data.get("order_id"), "订单")
    invoice_id = _optional_int_id(data.get("invoice_id"), "发票")
    expense_id = _optional_int_id(data.get("expense_id"), "费用单")
    _validate_order(db, order_id)
    _validate_sales_invoice(db, invoice_id)
    _validate_expense(
        db,
        expense_id=expense_id,
        customer_id=customer_id,
        require_approved=False,
        exclude_receipt_id=exclude_receipt_id,
    )
    return {
        "order_id": order_id,
        "invoice_id": invoice_id,
        "expense_id": expense_id,
    }


def create_payment(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Payment:
    _check_unknown_fields(data, PAYMENT_FIELDS)
    supplier = _get_supplier(
        db, _to_int_id(data.get("supplier_id"), "供应商")
    )
    amount = _to_money(data.get("amount"), "付款金额")
    links = _payment_link_values(db, data, supplier.id)
    payment = Payment(
        payment_no=generate_serial(SerialPrefix.PAYMENT, dt=_now()),
        supplier_id=supplier.id,
        amount=amount,
        payment_type=_optional_text(data.get("payment_type"), "付款类型", 32),
        pay_method=_optional_text(data.get("pay_method"), "付款方式", 32),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
        **links,
    )
    db.add(payment)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=PAYMENT_DOC_TYPE,
        target_id=str(payment.id),
        summary=f"新增付款单：{payment.payment_no}",
        detail={"supplier_id": supplier.id, "amount": str(amount), **links},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return payment


def create_receipt(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Receipt:
    _check_unknown_fields(data, RECEIPT_FIELDS)
    customer = _get_customer(
        db, _to_int_id(data.get("customer_id"), "客户")
    )
    amount = _to_money(data.get("amount"), "收款金额")
    links = _receipt_link_values(db, data, customer.id)
    receipt = Receipt(
        receipt_no=generate_serial(SerialPrefix.RECEIPT, dt=_now()),
        customer_id=customer.id,
        amount=amount,
        sale_type=_optional_text(data.get("sale_type"), "销售类型", 32),
        settle_type=_optional_text(data.get("settle_type"), "结算方式", 32),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
        **links,
    )
    db.add(receipt)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=RECEIPT_DOC_TYPE,
        target_id=str(receipt.id),
        summary=f"新增收款单：{receipt.receipt_no}",
        detail={"customer_id": customer.id, "amount": str(amount), **links},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return receipt


def _approval_state(db: Session, *, doc_type: str, doc_id: int) -> dict:
    return approval_service.get_state(
        db, doc_type=doc_type, doc_id=doc_id
    )["state"]


def _ensure_editable(
    db: Session, *, doc_type: str, document: Payment | Receipt
) -> None:
    if document.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前单据状态不允许修改")
    state = _approval_state(db, doc_type=doc_type, doc_id=document.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "单据已进入审批流程，不能修改")


def update_payment(
    db: Session,
    *,
    payment_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Payment:
    _check_unknown_fields(data, PAYMENT_FIELDS)
    payment = get_payment_or_404(db, payment_id)
    _ensure_editable(db, doc_type=PAYMENT_DOC_TYPE, document=payment)

    supplier_id = payment.supplier_id
    if "supplier_id" in data:
        supplier = _get_supplier(
            db, _to_int_id(data["supplier_id"], "供应商")
        )
        supplier_id = supplier.id
        payment.supplier_id = supplier.id

    merged_links = {
        "stock_in_id": data.get("stock_in_id", payment.stock_in_id),
        "invoice_id": data.get("invoice_id", payment.invoice_id),
        "income_id": data.get("income_id", payment.income_id),
        "prepay_id": data.get("prepay_id", payment.prepay_id),
    }
    links = _payment_link_values(db, merged_links, supplier_id)
    for field, value in links.items():
        setattr(payment, field, value)

    if "amount" in data:
        payment.amount = _to_money(data["amount"], "付款金额")
    if "payment_type" in data:
        payment.payment_type = _optional_text(
            data["payment_type"], "付款类型", 32
        )
    if "pay_method" in data:
        payment.pay_method = _optional_text(
            data["pay_method"], "付款方式", 32
        )
    if payment.status == STATUS_REJECTED:
        payment.status = STATUS_PENDING
        payment.audit_status = AUDIT_PENDING
        payment.auditor_id = None
        payment.audit_time = None
    write_audit(
        db,
        action="UPDATE",
        target_type=PAYMENT_DOC_TYPE,
        target_id=str(payment.id),
        summary=f"修改付款单：{payment.payment_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return payment


def update_receipt(
    db: Session,
    *,
    receipt_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Receipt:
    _check_unknown_fields(data, RECEIPT_FIELDS)
    receipt = get_receipt_or_404(db, receipt_id)
    _ensure_editable(db, doc_type=RECEIPT_DOC_TYPE, document=receipt)

    customer_id = receipt.customer_id
    if "customer_id" in data:
        customer = _get_customer(
            db, _to_int_id(data["customer_id"], "客户")
        )
        customer_id = customer.id
        receipt.customer_id = customer.id

    merged_links = {
        "order_id": data.get("order_id", receipt.order_id),
        "invoice_id": data.get("invoice_id", receipt.invoice_id),
        "expense_id": data.get("expense_id", receipt.expense_id),
    }
    links = _receipt_link_values(
        db,
        merged_links,
        customer_id,
        exclude_receipt_id=receipt.id,
    )
    for field, value in links.items():
        setattr(receipt, field, value)

    if "amount" in data:
        receipt.amount = _to_money(data["amount"], "收款金额")
    if "sale_type" in data:
        receipt.sale_type = _optional_text(
            data["sale_type"], "销售类型", 32
        )
    if "settle_type" in data:
        receipt.settle_type = _optional_text(
            data["settle_type"], "结算方式", 32
        )
    if receipt.status == STATUS_REJECTED:
        receipt.status = STATUS_PENDING
        receipt.audit_status = AUDIT_PENDING
        receipt.auditor_id = None
        receipt.audit_time = None
    write_audit(
        db,
        action="UPDATE",
        target_type=RECEIPT_DOC_TYPE,
        target_id=str(receipt.id),
        summary=f"修改收款单：{receipt.receipt_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return receipt


def cancel_payment(
    db: Session,
    *,
    payment_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    payment = get_payment_or_404(db, payment_id)
    _ensure_editable(db, doc_type=PAYMENT_DOC_TYPE, document=payment)
    payment.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type=PAYMENT_DOC_TYPE,
        target_id=str(payment.id),
        summary=f"作废付款单：{payment.payment_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def cancel_receipt(
    db: Session,
    *,
    receipt_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    receipt = get_receipt_or_404(db, receipt_id)
    _ensure_editable(db, doc_type=RECEIPT_DOC_TYPE, document=receipt)
    receipt.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type=RECEIPT_DOC_TYPE,
        target_id=str(receipt.id),
        summary=f"作废收款单：{receipt.receipt_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(document: Payment | Receipt, state: dict) -> None:
    if state["status"] == "APPROVED":
        document.audit_status = AUDIT_APPROVED
    elif state["status"] == "REJECTED":
        document.audit_status = AUDIT_REJECTED
    else:
        document.audit_status = AUDIT_PENDING


def _assert_finance_approver(db: Session, staff: Staff) -> None:
    roles = set(list_staff_role_codes(db, staff.id))
    if not roles & {"FINANCE", "ADMIN"}:
        raise BizException(
            ErrorCode.FORBIDDEN, "付款单和收款单只能由财务人员审核"
        )


def _receipt_expense_offset(db: Session, receipt: Receipt) -> Decimal:
    if receipt.expense_id is None:
        return Decimal("0.00")
    expense = _validate_expense(
        db,
        expense_id=receipt.expense_id,
        customer_id=receipt.customer_id,
        require_approved=True,
        exclude_receipt_id=receipt.id,
    )
    if expense is None:
        return Decimal("0.00")
    if expense.amount >= receipt.amount:
        raise BizException(
            ErrorCode.CONFLICT, "费用金额必须小于关联收款单金额"
        )
    return expense.amount


def _validate_payment_income_write_off(
    db: Session, payment: Payment
) -> None:
    if payment.income_id is None:
        return
    income = db.get(Income, payment.income_id)
    if income is None:
        raise BizException(ErrorCode.BAD_REQUEST, "关联收入单不存在")
    if income.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "关联收入单尚未审核通过")
    written_off = db.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.income_id == income.id,
            Payment.status == STATUS_APPROVED,
            Payment.id != payment.id,
        )
    ) or Decimal("0.00")
    if round_money(written_off) + payment.amount > income.amount:
        raise BizException(
            ErrorCode.CONFLICT,
            "关联付款单累计核销金额不能超过收入单金额",
        )


def submit_payment(
    db: Session,
    *,
    payment_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Payment:
    payment = get_payment_or_404(db, payment_id)
    _ensure_editable(db, doc_type=PAYMENT_DOC_TYPE, document=payment)
    state = approval_service.submit(
        db,
        doc_type=PAYMENT_DOC_TYPE,
        doc_id=payment.id,
        doc_no=payment.payment_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    payment.status = STATUS_PENDING
    _map_audit_status(payment, state)
    payment.auditor_id = None
    payment.audit_time = None
    _commit(db, commit)
    return payment


def approve_payment(
    db: Session,
    *,
    payment_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Payment:
    payment = get_payment_or_404(db, payment_id)
    if payment.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前付款单状态不能审核")
    try:
        _assert_finance_approver(db, staff)
        _validate_payment_income_write_off(db, payment)
        state = approval_service.approve(
            db,
            doc_type=PAYMENT_DOC_TYPE,
            doc_id=payment.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(payment, state)
        if state["status"] == "APPROVED":
            payment.status = STATUS_APPROVED
            payment.auditor_id = staff.id
            payment.audit_time = _now()
            voucher_service.create_payment_voucher(
                db,
                payment_no=payment.payment_no,
                amount=payment.amount,
                staff=staff,
                use_prepay=payment.prepay_id is not None,
                serial_generator=generate_serial,
                commit=False,
            )
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return payment


def reject_payment(
    db: Session,
    *,
    payment_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Payment:
    payment = get_payment_or_404(db, payment_id)
    if payment.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前付款单状态不能审核")
    _assert_finance_approver(db, staff)
    state = approval_service.reject(
        db,
        doc_type=PAYMENT_DOC_TYPE,
        doc_id=payment.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    payment.status = STATUS_REJECTED
    _map_audit_status(payment, state)
    payment.auditor_id = staff.id
    payment.audit_time = _now()
    _commit(db, commit)
    return payment


def unaudit_payment(
    db: Session,
    *,
    payment_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> Payment:
    payment = get_payment_or_404(db, payment_id)
    if payment.status != STATUS_APPROVED or payment.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "付款单尚未审核通过，不能弃审")
    reason = _optional_text(reason, "弃审原因", 255)
    if not reason:
        raise BizException(ErrorCode.BAD_REQUEST, "弃审原因不能为空")
    try:
        _assert_finance_approver(db, staff)
        voucher_service.invalidate_voucher_by_reference(
            db,
            reference=f"付款单:{payment.payment_no}",
            staff=staff,
            reason=reason,
            source_type=PAYMENT_DOC_TYPE,
            source_id=str(payment.id),
            commit=False,
        )
        state = approval_service.unaudit(
            db,
            doc_type=PAYMENT_DOC_TYPE,
            doc_id=payment.id,
            staff=staff,
            reason=reason,
            step=step,
            commit=False,
        )
        payment.status = STATUS_PENDING
        _map_audit_status(payment, state)
        payment.auditor_id = None
        payment.audit_time = None
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return payment


def submit_receipt(
    db: Session,
    *,
    receipt_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Receipt:
    receipt = get_receipt_or_404(db, receipt_id)
    _ensure_editable(db, doc_type=RECEIPT_DOC_TYPE, document=receipt)
    _receipt_expense_offset(db, receipt)
    state = approval_service.submit(
        db,
        doc_type=RECEIPT_DOC_TYPE,
        doc_id=receipt.id,
        doc_no=receipt.receipt_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    receipt.status = STATUS_PENDING
    _map_audit_status(receipt, state)
    receipt.auditor_id = None
    receipt.audit_time = None
    _commit(db, commit)
    return receipt


def approve_receipt(
    db: Session,
    *,
    receipt_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Receipt:
    receipt = get_receipt_or_404(db, receipt_id)
    if receipt.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前收款单状态不能审核")
    try:
        _assert_finance_approver(db, staff)
        expense_offset = _receipt_expense_offset(db, receipt)
        state = approval_service.approve(
            db,
            doc_type=RECEIPT_DOC_TYPE,
            doc_id=receipt.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(receipt, state)
        if state["status"] == "APPROVED":
            receipt.status = STATUS_APPROVED
            receipt.auditor_id = staff.id
            receipt.audit_time = _now()
            voucher_service.create_receipt_voucher(
                db,
                receipt_no=receipt.receipt_no,
                amount=receipt.amount - expense_offset,
                staff=staff,
                serial_generator=generate_serial,
                commit=False,
            )
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return receipt


def reject_receipt(
    db: Session,
    *,
    receipt_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Receipt:
    receipt = get_receipt_or_404(db, receipt_id)
    if receipt.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前收款单状态不能审核")
    _assert_finance_approver(db, staff)
    state = approval_service.reject(
        db,
        doc_type=RECEIPT_DOC_TYPE,
        doc_id=receipt.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    receipt.status = STATUS_REJECTED
    _map_audit_status(receipt, state)
    receipt.auditor_id = staff.id
    receipt.audit_time = _now()
    _commit(db, commit)
    return receipt


def unaudit_receipt(
    db: Session,
    *,
    receipt_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> Receipt:
    receipt = get_receipt_or_404(db, receipt_id)
    if receipt.status != STATUS_APPROVED or receipt.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "收款单尚未审核通过，不能弃审")
    reason = _optional_text(reason, "弃审原因", 255)
    if not reason:
        raise BizException(ErrorCode.BAD_REQUEST, "弃审原因不能为空")
    try:
        _assert_finance_approver(db, staff)
        voucher_service.invalidate_voucher_by_reference(
            db,
            reference=f"收款单:{receipt.receipt_no}",
            staff=staff,
            reason=reason,
            source_type=RECEIPT_DOC_TYPE,
            source_id=str(receipt.id),
            commit=False,
        )
        state = approval_service.unaudit(
            db,
            doc_type=RECEIPT_DOC_TYPE,
            doc_id=receipt.id,
            staff=staff,
            reason=reason,
            step=step,
            commit=False,
        )
        receipt.status = STATUS_PENDING
        _map_audit_status(receipt, state)
        receipt.auditor_id = None
        receipt.audit_time = None
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return receipt


def get_payment_voucher(db: Session, payment_id: int) -> dict | None:
    payment = get_payment_or_404(db, payment_id)
    return voucher_service.voucher_dict(
        db,
        voucher_service.get_active_voucher_by_reference(
            db, f"付款单:{payment.payment_no}"
        ),
    )


def get_receipt_voucher(db: Session, receipt_id: int) -> dict | None:
    receipt = get_receipt_or_404(db, receipt_id)
    return voucher_service.voucher_dict(
        db,
        voucher_service.get_active_voucher_by_reference(
            db, f"收款单:{receipt.receipt_no}"
        ),
    )
