"""费用单、结算单与收入单服务（TASK-045）。

费用单通过收款单的 ``expense_id`` 建立冲抵关系，审核时生成
``借：销售费用 / 贷：应收账款`` 分录。收款单审核时按扣除费用后的
实际到账金额生成收款分录，从而与费用分录共同冲平应收账款。

收入单用于登记供应商返利，审核时生成
``借：应付账款 / 贷：库存商品`` 分录；已审核付款单可按累计金额核销，
但不得超过收入单金额。

结算单提供平台 Excel 导入、核对和确认；确认后生成应收发票与
``借：应收账款 / 贷：主营业务收入`` 分录。
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import document_visible, owner_visible, scope_conditions
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
    Settlement,
)
from app.models.org import Staff
from app.models.party import Customer, Supplier
from app.models.platform import Platform, Shop
from app.repositories.rbac_repo import list_staff_role_codes
from app.services import approval_service, party_service, voucher_service
from app.services.excel import Column, SheetSpec, parse_data

EXPENSE_DOC_TYPE = "expense"
INCOME_DOC_TYPE = "income"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_CANCELLED = "CANCELLED"

SETTLEMENT_IMPORTED = "IMPORTED"
SETTLEMENT_CHECKED = "CHECKED"
SETTLEMENT_CONFIRMED = "CONFIRMED"
SETTLEMENT_CANCELLED = "CANCELLED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

WRITE_OFF_NONE = "NONE"
WRITE_OFF_PARTIAL = "PARTIAL"
WRITE_OFF_FULL = "FULL"

EXPENSE_FIELDS = {
    "customer_id",
    "expense_type",
    "amount",
    "description",
}
INCOME_FIELDS = {
    "supplier_id",
    "income_type",
    "amount",
    "description",
}
SETTLEMENT_FIELDS = {
    "platform_no",
    "settlement_date",
    "shop_id",
    "amount",
    "remark",
}

SETTLEMENT_TEMPLATE = SheetSpec(
    name="platform_settlement",
    sheet_title="平台结算单",
    columns=[
        Column("platform_no", "平台结算单号", required=True, width=24),
        Column("settlement_date", "结算日期", required=True, type="date", width=16),
        Column("shop_id", "店铺ID", required=True, type="int", width=12),
        Column("amount", "结算金额", required=True, type="decimal", width=16),
        Column("remark", "备注", width=32),
    ],
)


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
        raise BizException(
            ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}"
        )
    return text


def _to_money(value: Any, label: str) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result <= Decimal("0.00"):
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_date(value: Any, label: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}格式应为 YYYY-MM-DD") from exc


def _staff_name(db: Session, staff_id: int | None) -> str | None:
    if staff_id is None:
        return None
    staff = db.get(Staff, staff_id)
    return staff.name if staff else None


def _approval_state(db: Session, *, doc_type: str, doc_id: int) -> dict:
    return approval_service.get_state(
        db, doc_type=doc_type, doc_id=doc_id
    )["state"]


def _ensure_editable(
    db: Session, *, doc_type: str, document: Expense | Income
) -> None:
    if document.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前单据状态不允许修改")
    state = _approval_state(db, doc_type=doc_type, doc_id=document.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "单据已进入审批流程，不能修改")


def _map_audit_status(document: Expense | Income, state: dict) -> None:
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
            ErrorCode.FORBIDDEN, "费用单和收入单只能由财务人员审核"
        )


def _active_receipts(
    db: Session, *, expense_id: int, exclude_receipt_id: int | None = None
) -> list[Receipt]:
    conditions = [
        Receipt.expense_id == expense_id,
        Receipt.status.in_([STATUS_PENDING, STATUS_APPROVED]),
    ]
    if exclude_receipt_id is not None:
        conditions.append(Receipt.id != exclude_receipt_id)
    return list(
        db.scalars(
            select(Receipt)
            .where(*conditions)
            .order_by(Receipt.id)
        )
    )


def _validate_expense_receipt(db: Session, expense: Expense) -> Receipt:
    receipts = _active_receipts(db, expense_id=expense.id)
    if not receipts:
        raise BizException(
            ErrorCode.CONFLICT, "费用单必须关联一张待审核或已审核收款单"
        )
    if len(receipts) > 1:
        raise BizException(
            ErrorCode.CONFLICT, "同一费用单只能关联一张有效收款单"
        )
    receipt = receipts[0]
    if receipt.customer_id != expense.customer_id:
        raise BizException(
            ErrorCode.BAD_REQUEST, "费用单客户与关联收款单客户不一致"
        )
    if expense.amount >= receipt.amount:
        raise BizException(
            ErrorCode.CONFLICT, "费用金额必须小于关联收款单金额"
        )
    return receipt


def get_expense_or_404(db: Session, expense_id: int) -> Expense:
    expense = db.get(Expense, expense_id)
    if expense is None or not document_visible(db, expense.created_by, EXPENSE_DOC_TYPE, expense.id):
        raise BizException(ErrorCode.NOT_FOUND, "费用单不存在")
    return expense


def expense_dict(
    db: Session,
    expense: Expense,
    *,
    with_approval: bool = False,
    with_voucher: bool = False,
) -> dict:
    customer = db.get(Customer, expense.customer_id) if expense.customer_id else None
    receipts = _active_receipts(db, expense_id=expense.id)
    receipt = receipts[0] if len(receipts) == 1 else None
    data = {
        "id": expense.id,
        "expense_no": expense.expense_no,
        "customer_id": expense.customer_id,
        "customer_name": customer.name if customer else None,
        "expense_type": expense.expense_type,
        "amount": expense.amount,
        "description": expense.description,
        "voucher_no": expense.voucher_no,
        "status": expense.status,
        "audit_status": expense.audit_status,
        "receipt_id": receipt.id if receipt else None,
        "receipt_no": receipt.receipt_no if receipt else None,
        "receipt_amount": receipt.amount if receipt else None,
        "offset_amount": (
            expense.amount
            if expense.status == STATUS_APPROVED and receipt is not None
            else Decimal("0.00")
        ),
        "created_by": expense.created_by,
        "created_by_name": _staff_name(db, expense.created_by),
        "created_at": expense.created_at,
        "updated_at": expense.updated_at,
    }
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=EXPENSE_DOC_TYPE, doc_id=expense.id
        )
    if with_voucher:
        data["voucher"] = voucher_service.voucher_dict(
            db,
            voucher_service.get_active_voucher_by_reference(
                db, f"费用单:{expense.expense_no}"
            ),
        )
    return data


def list_expenses(
    db: Session,
    *,
    customer_id: int | None = None,
    status: str | None = None,
    expense_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, Expense.created_by)
    if customer_id is not None:
        conditions.append(Expense.customer_id == customer_id)
    if status:
        conditions.append(Expense.status == status.upper())
    if expense_type:
        conditions.append(Expense.expense_type == expense_type)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Expense.expense_no.like(like),
                Expense.description.like(like),
                Expense.customer_id.in_(
                    select(Customer.id).where(Customer.name.like(like))
                ),
            )
        )
    total = db.scalar(select(func.count(Expense.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Expense)
            .where(*conditions)
            .order_by(Expense.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [expense_dict(db, row) for row in rows],
    }


def create_expense(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Expense:
    _check_unknown_fields(data, EXPENSE_FIELDS)
    customer = party_service.get_customer_or_404(
        db, _to_int_id(data.get("customer_id"), "结算客户")
    )
    if not customer.is_active:
        raise BizException(ErrorCode.CONFLICT, "客户已停用")
    expense = Expense(
        expense_no=generate_serial(SerialPrefix.EXPENSE, dt=_now()),
        customer_id=customer.id,
        expense_type=_optional_text(data.get("expense_type"), "支出类型", 32),
        amount=_to_money(data.get("amount"), "支出金额"),
        description=_optional_text(data.get("description"), "描述", 255),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
    )
    db.add(expense)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=EXPENSE_DOC_TYPE,
        target_id=str(expense.id),
        summary=f"新增费用单：{expense.expense_no}",
        detail={
            "customer_id": customer.id,
            "amount": str(expense.amount),
            "expense_type": expense.expense_type,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return expense


def update_expense(
    db: Session,
    *,
    expense_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Expense:
    _check_unknown_fields(data, EXPENSE_FIELDS)
    expense = get_expense_or_404(db, expense_id)
    _ensure_editable(db, doc_type=EXPENSE_DOC_TYPE, document=expense)
    if "customer_id" in data:
        customer = party_service.get_customer_or_404(
            db, _to_int_id(data["customer_id"], "结算客户")
        )
        if not customer.is_active:
            raise BizException(ErrorCode.CONFLICT, "客户已停用")
        expense.customer_id = customer.id
    if "expense_type" in data:
        expense.expense_type = _optional_text(
            data["expense_type"], "支出类型", 32
        )
    if "amount" in data:
        expense.amount = _to_money(data["amount"], "支出金额")
    if "description" in data:
        expense.description = _optional_text(data["description"], "描述", 255)
    if expense.status == STATUS_REJECTED:
        expense.status = STATUS_PENDING
        expense.audit_status = AUDIT_PENDING
    write_audit(
        db,
        action="UPDATE",
        target_type=EXPENSE_DOC_TYPE,
        target_id=str(expense.id),
        summary=f"修改费用单：{expense.expense_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return expense


def cancel_expense(
    db: Session,
    *,
    expense_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    expense = get_expense_or_404(db, expense_id)
    _ensure_editable(db, doc_type=EXPENSE_DOC_TYPE, document=expense)
    approved_receipt = db.scalar(
        select(Receipt).where(
            Receipt.expense_id == expense.id,
            Receipt.status == STATUS_APPROVED,
        )
    )
    if approved_receipt is not None:
        raise BizException(ErrorCode.CONFLICT, "关联收款单已审核，费用单不能作废")
    expense.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type=EXPENSE_DOC_TYPE,
        target_id=str(expense.id),
        summary=f"作废费用单：{expense.expense_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def submit_expense(
    db: Session,
    *,
    expense_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Expense:
    expense = get_expense_or_404(db, expense_id)
    _ensure_editable(db, doc_type=EXPENSE_DOC_TYPE, document=expense)
    _validate_expense_receipt(db, expense)
    state = approval_service.submit(
        db,
        doc_type=EXPENSE_DOC_TYPE,
        doc_id=expense.id,
        doc_no=expense.expense_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    expense.status = STATUS_PENDING
    _map_audit_status(expense, state)
    _commit(db, commit)
    return expense


def approve_expense(
    db: Session,
    *,
    expense_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Expense:
    expense = get_expense_or_404(db, expense_id)
    if expense.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前费用单状态不能审核")
    try:
        _assert_finance_approver(db, staff)
        _validate_expense_receipt(db, expense)
        state = approval_service.approve(
            db,
            doc_type=EXPENSE_DOC_TYPE,
            doc_id=expense.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(expense, state)
        if state["status"] == "APPROVED":
            expense.status = STATUS_APPROVED
            voucher = voucher_service.create_expense_voucher(
                db,
                expense_no=expense.expense_no,
                amount=expense.amount,
                staff=staff,
                serial_generator=generate_serial,
                commit=False,
            )
            expense.voucher_no = voucher.voucher_no
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return expense


def reject_expense(
    db: Session,
    *,
    expense_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Expense:
    expense = get_expense_or_404(db, expense_id)
    if expense.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前费用单状态不能审核")
    _assert_finance_approver(db, staff)
    state = approval_service.reject(
        db,
        doc_type=EXPENSE_DOC_TYPE,
        doc_id=expense.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    expense.status = STATUS_REJECTED
    _map_audit_status(expense, state)
    _commit(db, commit)
    return expense


def unaudit_expense(
    db: Session,
    *,
    expense_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> Expense:
    expense = get_expense_or_404(db, expense_id)
    if expense.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "费用单尚未审核通过，不能弃审")
    approved_receipt = db.scalar(
        select(Receipt).where(
            Receipt.expense_id == expense.id,
            Receipt.status == STATUS_APPROVED,
        )
    )
    if approved_receipt is not None:
        raise BizException(ErrorCode.CONFLICT, "关联收款单已审核，不能弃审费用单")
    reason_text = _optional_text(reason, "弃审原因", 255)
    if not reason_text:
        raise BizException(ErrorCode.BAD_REQUEST, "弃审原因不能为空")
    try:
        _assert_finance_approver(db, staff)
        voucher_service.invalidate_voucher_by_reference(
            db,
            reference=f"费用单:{expense.expense_no}",
            staff=staff,
            reason=reason_text,
            source_type=EXPENSE_DOC_TYPE,
            source_id=str(expense.id),
            commit=False,
        )
        state = approval_service.unaudit(
            db,
            doc_type=EXPENSE_DOC_TYPE,
            doc_id=expense.id,
            staff=staff,
            reason=reason_text,
            step=step,
            commit=False,
        )
        expense.status = STATUS_PENDING
        _map_audit_status(expense, state)
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return expense


def get_expense_voucher(db: Session, expense_id: int) -> dict | None:
    expense = get_expense_or_404(db, expense_id)
    return voucher_service.voucher_dict(
        db,
        voucher_service.get_active_voucher_by_reference(
            db, f"费用单:{expense.expense_no}"
        ),
    )


def _payment_write_off_amount(
    db: Session,
    *,
    income_id: int,
    exclude_payment_id: int | None = None,
) -> Decimal:
    conditions = [
        Payment.income_id == income_id,
        Payment.status == STATUS_APPROVED,
    ]
    if exclude_payment_id is not None:
        conditions.append(Payment.id != exclude_payment_id)
    total = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(*conditions))
    return round_money(total or Decimal("0.00"))


def assert_income_write_off(
    db: Session,
    *,
    income: Income,
    amount: Decimal,
    exclude_payment_id: int | None = None,
) -> None:
    if income.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "收入单审核通过后才能核销")
    written_off = _payment_write_off_amount(
        db, income_id=income.id, exclude_payment_id=exclude_payment_id
    )
    if written_off + amount > income.amount:
        raise BizException(
            ErrorCode.CONFLICT,
            "关联付款单累计核销金额不能超过收入单金额",
        )


def get_income_or_404(db: Session, income_id: int) -> Income:
    income = db.get(Income, income_id)
    if income is None or not document_visible(db, income.created_by, INCOME_DOC_TYPE, income.id):
        raise BizException(ErrorCode.NOT_FOUND, "收入单不存在")
    return income


def income_dict(
    db: Session,
    income: Income,
    *,
    with_approval: bool = False,
    with_voucher: bool = False,
) -> dict:
    supplier = db.get(Supplier, income.supplier_id) if income.supplier_id else None
    written_off = _payment_write_off_amount(db, income_id=income.id)
    remaining = max(income.amount - written_off, Decimal("0.00"))
    if written_off <= Decimal("0.00"):
        write_off_status = WRITE_OFF_NONE
    elif written_off < income.amount:
        write_off_status = WRITE_OFF_PARTIAL
    else:
        write_off_status = WRITE_OFF_FULL
    data = {
        "id": income.id,
        "income_no": income.income_no,
        "supplier_id": income.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "income_type": income.income_type,
        "amount": income.amount,
        "voucher_no": income.voucher_no,
        "write_off_amount": written_off,
        "remaining_amount": remaining,
        "write_off_status": write_off_status,
        "description": income.description,
        "status": income.status,
        "audit_status": income.audit_status,
        "created_by": income.created_by,
        "created_by_name": _staff_name(db, income.created_by),
        "created_at": income.created_at,
        "updated_at": income.updated_at,
    }
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=INCOME_DOC_TYPE, doc_id=income.id
        )
    if with_voucher:
        data["voucher"] = voucher_service.voucher_dict(
            db,
            voucher_service.get_active_voucher_by_reference(
                db, f"收入单:{income.income_no}"
            ),
        )
    return data


def list_incomes(
    db: Session,
    *,
    supplier_id: int | None = None,
    status: str | None = None,
    income_type: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, Income.created_by)
    if supplier_id is not None:
        conditions.append(Income.supplier_id == supplier_id)
    if status:
        conditions.append(Income.status == status.upper())
    if income_type:
        conditions.append(Income.income_type == income_type)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Income.income_no.like(like),
                Income.description.like(like),
                Income.supplier_id.in_(
                    select(Supplier.id).where(Supplier.name.like(like))
                ),
            )
        )
    total = db.scalar(select(func.count(Income.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Income)
            .where(*conditions)
            .order_by(Income.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [income_dict(db, row) for row in rows],
    }


def create_income(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Income:
    _check_unknown_fields(data, INCOME_FIELDS)
    supplier = party_service.get_supplier_or_404(
        db, _to_int_id(data.get("supplier_id"), "供应商")
    )
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用")
    income = Income(
        income_no=generate_serial(SerialPrefix.INCOME, dt=_now()),
        supplier_id=supplier.id,
        income_type=_optional_text(data.get("income_type"), "收入类型", 32),
        amount=_to_money(data.get("amount"), "收入金额"),
        description=_optional_text(data.get("description"), "描述", 255),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
    )
    db.add(income)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=INCOME_DOC_TYPE,
        target_id=str(income.id),
        summary=f"新增收入单：{income.income_no}",
        detail={
            "supplier_id": supplier.id,
            "amount": str(income.amount),
            "income_type": income.income_type,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return income


def update_income(
    db: Session,
    *,
    income_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Income:
    _check_unknown_fields(data, INCOME_FIELDS)
    income = get_income_or_404(db, income_id)
    _ensure_editable(db, doc_type=INCOME_DOC_TYPE, document=income)
    if "supplier_id" in data:
        supplier = party_service.get_supplier_or_404(
            db, _to_int_id(data["supplier_id"], "供应商")
        )
        if not supplier.is_active:
            raise BizException(ErrorCode.CONFLICT, "供应商已停用")
        income.supplier_id = supplier.id
    if "income_type" in data:
        income.income_type = _optional_text(data["income_type"], "收入类型", 32)
    if "amount" in data:
        income.amount = _to_money(data["amount"], "收入金额")
    if "description" in data:
        income.description = _optional_text(data["description"], "描述", 255)
    if income.status == STATUS_REJECTED:
        income.status = STATUS_PENDING
        income.audit_status = AUDIT_PENDING
    write_audit(
        db,
        action="UPDATE",
        target_type=INCOME_DOC_TYPE,
        target_id=str(income.id),
        summary=f"修改收入单：{income.income_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return income


def cancel_income(
    db: Session,
    *,
    income_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    income = get_income_or_404(db, income_id)
    _ensure_editable(db, doc_type=INCOME_DOC_TYPE, document=income)
    if _payment_write_off_amount(db, income_id=income.id) > Decimal("0.00"):
        raise BizException(ErrorCode.CONFLICT, "收入单已有付款核销，不能作废")
    income.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type=INCOME_DOC_TYPE,
        target_id=str(income.id),
        summary=f"作废收入单：{income.income_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def submit_income(
    db: Session,
    *,
    income_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Income:
    income = get_income_or_404(db, income_id)
    _ensure_editable(db, doc_type=INCOME_DOC_TYPE, document=income)
    state = approval_service.submit(
        db,
        doc_type=INCOME_DOC_TYPE,
        doc_id=income.id,
        doc_no=income.income_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    income.status = STATUS_PENDING
    _map_audit_status(income, state)
    _commit(db, commit)
    return income


def approve_income(
    db: Session,
    *,
    income_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Income:
    income = get_income_or_404(db, income_id)
    if income.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前收入单状态不能审核")
    if income.supplier_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "供应商返利必须关联供应商")
    try:
        _assert_finance_approver(db, staff)
        state = approval_service.approve(
            db,
            doc_type=INCOME_DOC_TYPE,
            doc_id=income.id,
            staff=staff,
            comment=comment,
            commit=False,
        )
        _map_audit_status(income, state)
        if state["status"] == "APPROVED":
            income.status = STATUS_APPROVED
            voucher = voucher_service.create_income_voucher(
                db,
                income_no=income.income_no,
                amount=income.amount,
                staff=staff,
                serial_generator=generate_serial,
                commit=False,
            )
            income.voucher_no = voucher.voucher_no
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return income


def reject_income(
    db: Session,
    *,
    income_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Income:
    income = get_income_or_404(db, income_id)
    if income.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前收入单状态不能审核")
    _assert_finance_approver(db, staff)
    state = approval_service.reject(
        db,
        doc_type=INCOME_DOC_TYPE,
        doc_id=income.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    income.status = STATUS_REJECTED
    _map_audit_status(income, state)
    _commit(db, commit)
    return income


def unaudit_income(
    db: Session,
    *,
    income_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> Income:
    income = get_income_or_404(db, income_id)
    if income.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "收入单尚未审核通过，不能弃审")
    if _payment_write_off_amount(db, income_id=income.id) > Decimal("0.00"):
        raise BizException(ErrorCode.CONFLICT, "收入单已有付款核销，不能弃审")
    reason_text = _optional_text(reason, "弃审原因", 255)
    if not reason_text:
        raise BizException(ErrorCode.BAD_REQUEST, "弃审原因不能为空")
    try:
        _assert_finance_approver(db, staff)
        voucher_service.invalidate_voucher_by_reference(
            db,
            reference=f"收入单:{income.income_no}",
            staff=staff,
            reason=reason_text,
            source_type=INCOME_DOC_TYPE,
            source_id=str(income.id),
            commit=False,
        )
        state = approval_service.unaudit(
            db,
            doc_type=INCOME_DOC_TYPE,
            doc_id=income.id,
            staff=staff,
            reason=reason_text,
            step=step,
            commit=False,
        )
        income.status = STATUS_PENDING
        _map_audit_status(income, state)
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return income


def get_income_voucher(db: Session, income_id: int) -> dict | None:
    income = get_income_or_404(db, income_id)
    return voucher_service.voucher_dict(
        db,
        voucher_service.get_active_voucher_by_reference(
            db, f"收入单:{income.income_no}"
        ),
    )


def get_settlement_or_404(db: Session, settlement_id: int) -> Settlement:
    settlement = db.get(Settlement, settlement_id)
    if settlement is None or not owner_visible(db, settlement.created_by):
        raise BizException(ErrorCode.NOT_FOUND, "结算单不存在")
    return settlement


def settlement_dict(
    db: Session,
    settlement: Settlement,
    *,
    with_voucher: bool = False,
) -> dict:
    shop = db.get(Shop, settlement.shop_id) if settlement.shop_id else None
    platform = db.get(Platform, shop.platform_id) if shop else None
    receivable = (
        db.get(Invoice, settlement.receivable_id)
        if settlement.receivable_id
        else None
    )
    data = {
        "id": settlement.id,
        "settlement_no": settlement.settlement_no,
        "platform_no": settlement.platform_no,
        "settlement_date": settlement.settlement_date,
        "shop_id": settlement.shop_id,
        "shop_name": shop.name if shop else None,
        "platform_name": platform.name if platform else None,
        "amount": settlement.amount,
        "status": settlement.status,
        "source_file": settlement.source_file,
        "remark": settlement.remark,
        "checked_by": settlement.checked_by,
        "checked_by_name": _staff_name(db, settlement.checked_by),
        "checked_at": settlement.checked_at,
        "confirmed_by": settlement.confirmed_by,
        "confirmed_by_name": _staff_name(db, settlement.confirmed_by),
        "confirmed_at": settlement.confirmed_at,
        "receivable_id": settlement.receivable_id,
        "receivable_no": receivable.invoice_no if receivable else None,
        "created_by": settlement.created_by,
        "created_by_name": _staff_name(db, settlement.created_by),
        "created_at": settlement.created_at,
        "updated_at": settlement.updated_at,
    }
    if with_voucher:
        data["voucher"] = voucher_service.voucher_dict(
            db,
            voucher_service.get_active_voucher_by_reference(
                db, f"平台结算单:{settlement.settlement_no}"
            ),
        )
    return data


def list_settlements(
    db: Session,
    *,
    shop_id: int | None = None,
    status: str | None = None,
    settlement_date_from: date | None = None,
    settlement_date_to: date | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, Settlement.created_by)
    if shop_id is not None:
        conditions.append(Settlement.shop_id == shop_id)
    if status:
        conditions.append(Settlement.status == status.upper())
    if settlement_date_from is not None:
        conditions.append(Settlement.settlement_date >= settlement_date_from)
    if settlement_date_to is not None:
        conditions.append(Settlement.settlement_date <= settlement_date_to)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Settlement.settlement_no.like(like),
                Settlement.platform_no.like(like),
                Settlement.remark.like(like),
                Settlement.shop_id.in_(
                    select(Shop.id).where(Shop.name.like(like))
                ),
            )
        )
    total = db.scalar(select(func.count(Settlement.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Settlement)
            .where(*conditions)
            .order_by(Settlement.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [settlement_dict(db, row, with_voucher=True) for row in rows],
    }


def _validate_shop(db: Session, shop_id: int) -> Shop:
    shop = db.get(Shop, shop_id)
    if shop is None:
        raise BizException(ErrorCode.BAD_REQUEST, f"店铺不存在：{shop_id}")
    return shop


def _ensure_platform_no_available(
    db: Session,
    *,
    platform_no: str | None,
    exclude_id: int | None = None,
) -> None:
    if platform_no is None:
        return
    conditions = [
        Settlement.platform_no == platform_no,
        Settlement.status != SETTLEMENT_CANCELLED,
    ]
    if exclude_id is not None:
        conditions.append(Settlement.id != exclude_id)
    if db.scalar(select(Settlement.id).where(*conditions)) is not None:
        raise BizException(
            ErrorCode.CONFLICT, f"平台结算单号已存在：{platform_no}"
        )


def create_settlement(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    source_file: str | None = None,
    commit: bool = True,
) -> Settlement:
    _check_unknown_fields(data, SETTLEMENT_FIELDS)
    shop = _validate_shop(db, _to_int_id(data.get("shop_id"), "店铺"))
    platform_no = _optional_text(data.get("platform_no"), "平台结算单号", 64)
    _ensure_platform_no_available(db, platform_no=platform_no)
    settlement = Settlement(
        settlement_no=generate_serial(SerialPrefix.SETTLEMENT, dt=_now()),
        platform_no=platform_no,
        settlement_date=_optional_date(data.get("settlement_date"), "结算日期"),
        shop_id=shop.id,
        amount=_to_money(data.get("amount"), "结算金额"),
        status=SETTLEMENT_IMPORTED,
        source_file=source_file,
        remark=_optional_text(data.get("remark"), "备注", 255),
        created_by=staff.id,
    )
    db.add(settlement)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="settlement",
        target_id=str(settlement.id),
        summary=f"新增平台结算单：{settlement.settlement_no}",
        detail={
            "platform_no": settlement.platform_no,
            "shop_id": shop.id,
            "amount": str(settlement.amount),
            "source_file": source_file,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return settlement


def import_settlements(
    db: Session,
    *,
    file_bytes: bytes,
    file_name: str | None,
    staff: Staff,
    commit: bool = True,
) -> dict:
    parsed = parse_data(file_bytes, SETTLEMENT_TEMPLATE)
    created: list[dict] = []
    errors = [dict(item) for item in parsed.errors]

    for row_number, row in enumerate(parsed.rows, start=2):
        try:
            with db.begin_nested():
                settlement = create_settlement(
                    db,
                    data=row,
                    staff=staff,
                    source_file=file_name,
                    commit=False,
                )
            created.append(settlement_dict(db, settlement))
        except BizException as exc:
            errors.append(
                {
                    "row": row_number,
                    "field": "row",
                    "message": exc.message,
                }
            )

    if commit:
        db.commit()
    else:
        db.flush()
    return {
        "total": len(parsed.rows),
        "created_count": len(created),
        "error_count": len(errors),
        "created": created,
        "errors": errors,
    }


def check_settlement(
    db: Session,
    *,
    settlement_id: int,
    staff: Staff,
    remark: str | None = None,
    commit: bool = True,
) -> Settlement:
    settlement = get_settlement_or_404(db, settlement_id)
    if settlement.status != SETTLEMENT_IMPORTED:
        raise BizException(ErrorCode.CONFLICT, "仅待核对结算单可以执行核对")
    settlement.status = SETTLEMENT_CHECKED
    settlement.checked_by = staff.id
    settlement.checked_at = _now()
    if remark is not None:
        settlement.remark = _optional_text(remark, "核对备注", 255)
    write_audit(
        db,
        action="CHECK",
        target_type="settlement",
        target_id=str(settlement.id),
        summary=f"核对平台结算单：{settlement.settlement_no}",
        detail={"amount": str(settlement.amount)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return settlement


def confirm_settlement(
    db: Session,
    *,
    settlement_id: int,
    staff: Staff,
    remark: str | None = None,
    commit: bool = True,
) -> Settlement:
    settlement = get_settlement_or_404(db, settlement_id)
    if settlement.status == SETTLEMENT_CONFIRMED:
        return settlement
    if settlement.status != SETTLEMENT_CHECKED:
        raise BizException(ErrorCode.CONFLICT, "结算单核对完成后才能确认")
    shop = _validate_shop(db, settlement.shop_id) if settlement.shop_id else None
    platform = db.get(Platform, shop.platform_id) if shop else None
    try:
        receivable = Invoice(
            invoice_no=generate_serial(SerialPrefix.INVOICE, dt=_now()),
            document_type="RECEIVABLE",
            invoice_title=shop.name if shop else None,
            invoice_type="SETTLEMENT_RECEIVABLE",
            amount=settlement.amount,
            received_amount=Decimal("0.00"),
            is_received=False,
            company=platform.name if platform else None,
            status=SETTLEMENT_CONFIRMED,
            applicant=staff.name,
        )
        db.add(receivable)
        db.flush()
        settlement.receivable_id = receivable.id
        settlement.confirmed_by = staff.id
        settlement.confirmed_at = _now()
        settlement.status = SETTLEMENT_CONFIRMED
        if remark is not None:
            settlement.remark = _optional_text(remark, "确认备注", 255)
        voucher_service.create_settlement_voucher(
            db,
            settlement_no=settlement.settlement_no,
            amount=settlement.amount,
            staff=staff,
            serial_generator=generate_serial,
            commit=False,
        )
        write_audit(
            db,
            action="CONFIRM",
            target_type="settlement",
            target_id=str(settlement.id),
            summary=f"确认平台结算单：{settlement.settlement_no}",
            detail={
                "amount": str(settlement.amount),
                "receivable_id": receivable.id,
                "receivable_no": receivable.invoice_no,
            },
            actor_id=staff.id,
            actor_name=staff.name,
        )
        db.flush()
    except Exception:
        db.rollback()
        raise
    _commit(db, commit)
    return settlement


def cancel_settlement(
    db: Session,
    *,
    settlement_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    settlement = get_settlement_or_404(db, settlement_id)
    if settlement.status not in {SETTLEMENT_IMPORTED, SETTLEMENT_CHECKED}:
        raise BizException(ErrorCode.CONFLICT, "已确认结算单不能作废")
    settlement.status = SETTLEMENT_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type="settlement",
        target_id=str(settlement.id),
        summary=f"作废平台结算单：{settlement.settlement_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def get_settlement_voucher(db: Session, settlement_id: int) -> dict | None:
    settlement = get_settlement_or_404(db, settlement_id)
    return voucher_service.voucher_dict(
        db,
        voucher_service.get_active_voucher_by_reference(
            db, f"平台结算单:{settlement.settlement_no}"
        ),
    )
