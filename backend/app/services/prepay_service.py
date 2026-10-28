"""采购预付款申请服务（TASK-026）。

覆盖申请 CRUD、关联采购订单、三级审批（副总 -> 财务 -> 总经理）以及
审核通过后的预付款会计分录生成。

分录口径：
- 借方：1123 预付账款
- 贷方：1002 银行存款
- 金额：采购预付款申请金额

当前 TASK-043 尚未实现，服务在最终审批通过后直接创建凭证草稿，
后续凭证模块可在此基础上补充审核、过账和金蝶同步。
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
from app.models.finance import Voucher
from app.models.org import Staff
from app.models.party import Supplier
from app.models.purchase import PurchaseOrder, PurchasePrepay
from app.services import approval_service, party_service, voucher_service

DOC_TYPE = "purchase_prepay"

STATUS_PENDING = "PENDING"
STATUS_APPROVED = "APPROVED"
STATUS_REJECTED = "REJECTED"
STATUS_TERMINATED = "TERMINATED"

AUDIT_PENDING = "PENDING"
AUDIT_APPROVED = "APPROVED"
AUDIT_REJECTED = "REJECTED"

APPLICATION_FIELDS = {
    "supplier_id",
    "order_id",
    "prepay_amount",
    "bank",
    "account_no",
    "account_balance",
    "pay_note",
    "write_off_note",
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


def _to_money(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < Decimal("0.00") or (result == Decimal("0.00") and not allow_zero):
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _optional_money(value: Any, label: str) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < Decimal("0.00"):
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


def get_prepay_or_404(db: Session, prepay_id: int, *, check_scope: bool = True) -> PurchasePrepay:
    prepay = db.get(PurchasePrepay, prepay_id)
    if prepay is None or (check_scope and not document_visible(db, prepay.created_by, DOC_TYPE, prepay.id)):
        raise BizException(ErrorCode.NOT_FOUND, "采购预付款申请不存在")
    return prepay


def _resolve_supplier_and_order(
    db: Session, data: dict
) -> tuple[Supplier, PurchaseOrder | None]:
    order_id = _optional_int_id(data.get("order_id"), "采购订单")
    order = db.get(PurchaseOrder, order_id) if order_id is not None else None
    if order_id is not None and order is None:
        raise BizException(ErrorCode.BAD_REQUEST, "采购订单不存在")

    supplier_id = _optional_int_id(data.get("supplier_id"), "结算供应商")
    if supplier_id is None and order is not None:
        supplier_id = order.supplier_id
    if supplier_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "结算供应商不能为空")

    supplier = party_service.get_supplier_or_404(db, supplier_id)
    if not supplier.is_active:
        raise BizException(ErrorCode.CONFLICT, "供应商已停用，不能提交预付款申请")
    if order is not None and order.supplier_id != supplier.id:
        raise BizException(ErrorCode.BAD_REQUEST, "采购订单与结算供应商不一致")
    return supplier, order


def prepay_dict(
    db: Session,
    prepay: PurchasePrepay,
    *,
    with_approval: bool = False,
    with_voucher: bool = False,
) -> dict:
    supplier = db.get(Supplier, prepay.supplier_id)
    order = db.get(PurchaseOrder, prepay.order_id) if prepay.order_id else None
    creator = db.get(Staff, prepay.created_by) if prepay.created_by else None
    auditor = db.get(Staff, prepay.auditor_id) if prepay.auditor_id else None
    data = {
        "id": prepay.id,
        "prepay_no": prepay.prepay_no,
        "supplier_id": prepay.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "order_id": prepay.order_id,
        "order_no": order.order_no if order else None,
        "order_amount": prepay.order_amount,
        "prepay_amount": prepay.prepay_amount,
        "write_off_amount": prepay.write_off_amount,
        "unwritten_amount": max(
            prepay.prepay_amount - prepay.write_off_amount, Decimal("0.00")
        ),
        "bank": prepay.bank,
        "account_no": prepay.account_no,
        "account_balance": prepay.account_balance,
        "pay_note": prepay.pay_note,
        "write_off_note": prepay.write_off_note,
        "status": prepay.status,
        "audit_status": prepay.audit_status,
        "created_by": prepay.created_by,
        "created_by_name": creator.name if creator else None,
        "auditor_id": prepay.auditor_id,
        "auditor_name": auditor.name if auditor else None,
        "audit_time": prepay.audit_time,
        "created_at": prepay.created_at,
        "updated_at": prepay.updated_at,
    }
    if with_approval:
        data["approval"] = approval_service.get_state(
            db, doc_type=DOC_TYPE, doc_id=prepay.id
        )
    if with_voucher:
        voucher = _active_voucher(db, prepay)
        data["voucher"] = voucher_dict(db, voucher) if voucher else None
    return data


def list_prepays(
    db: Session,
    *,
    supplier_id: int | None = None,
    order_id: int | None = None,
    status: str | None = None,
    audit_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = scope_conditions(db, PurchasePrepay.created_by)
    if supplier_id is not None:
        conditions.append(PurchasePrepay.supplier_id == supplier_id)
    if order_id is not None:
        conditions.append(PurchasePrepay.order_id == order_id)
    if status:
        conditions.append(PurchasePrepay.status == status.upper())
    if audit_status:
        conditions.append(PurchasePrepay.audit_status == audit_status.upper())
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                PurchasePrepay.prepay_no.like(like),
                PurchasePrepay.supplier_id.in_(
                    select(Supplier.id).where(Supplier.name.like(like))
                ),
                PurchasePrepay.order_id.in_(
                    select(PurchaseOrder.id).where(PurchaseOrder.order_no.like(like))
                ),
            )
        )
    total = db.scalar(select(func.count(PurchasePrepay.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(PurchasePrepay)
            .where(*conditions)
            .order_by(PurchasePrepay.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            prepay_dict(db, row, with_approval=False, with_voucher=False)
            for row in rows
        ],
    }


def create_prepay(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> PurchasePrepay:
    _check_unknown_fields(data, APPLICATION_FIELDS)
    supplier, order = _resolve_supplier_and_order(db, data)
    prepay_amount = _to_money(data.get("prepay_amount"), "预付金额")
    account_balance = _optional_money(data.get("account_balance"), "账户余额")
    prepay = PurchasePrepay(
        prepay_no=generate_serial(SerialPrefix.PURCHASE_PREPAY, dt=_now()),
        supplier_id=supplier.id,
        order_id=order.id if order else None,
        order_amount=order.total_amount if order else None,
        prepay_amount=prepay_amount,
        write_off_amount=Decimal("0.00"),
        bank=_optional_text(data.get("bank"), "开户银行", 64),
        account_no=_optional_text(data.get("account_no"), "开户账号", 64),
        account_balance=account_balance,
        pay_note=_optional_text(data.get("pay_note"), "付款说明", 255),
        write_off_note=_optional_text(data.get("write_off_note"), "核销说明", 255),
        status=STATUS_PENDING,
        audit_status=AUDIT_PENDING,
        created_by=staff.id,
    )
    db.add(prepay)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type=DOC_TYPE,
        target_id=str(prepay.id),
        summary=f"新增采购预付款申请：{prepay.prepay_no}",
        detail={
            "supplier_id": supplier.id,
            "order_id": prepay.order_id,
            "prepay_amount": str(prepay_amount),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return prepay


def _approval_state(db: Session, prepay_id: int) -> dict:
    return approval_service.get_state(
        db, doc_type=DOC_TYPE, doc_id=prepay_id
    )["state"]


def _ensure_editable(db: Session, prepay: PurchasePrepay) -> None:
    if prepay.status not in {STATUS_PENDING, STATUS_REJECTED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购预付款申请状态不允许修改")
    state = _approval_state(db, prepay.id)
    if state["status"] not in {"NOT_SUBMITTED", "REJECTED"}:
        raise BizException(ErrorCode.CONFLICT, "采购预付款申请已进入审批流程，不能修改")


def update_prepay(
    db: Session,
    *,
    prepay_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> PurchasePrepay:
    _check_unknown_fields(data, APPLICATION_FIELDS)
    prepay = get_prepay_or_404(db, prepay_id)
    _ensure_editable(db, prepay)

    merged = {
        "supplier_id": data.get("supplier_id", prepay.supplier_id),
        "order_id": data.get("order_id", prepay.order_id),
    }
    if "order_id" in data and data["order_id"] in (None, ""):
        merged["order_id"] = None
    supplier, order = _resolve_supplier_and_order(db, merged)
    prepay.supplier_id = supplier.id
    prepay.order_id = order.id if order else None
    prepay.order_amount = order.total_amount if order else None

    if "prepay_amount" in data:
        prepay.prepay_amount = _to_money(data["prepay_amount"], "预付金额")
    for field, label, max_length in (
        ("bank", "开户银行", 64),
        ("account_no", "开户账号", 64),
        ("pay_note", "付款说明", 255),
        ("write_off_note", "核销说明", 255),
    ):
        if field in data:
            setattr(prepay, field, _optional_text(data[field], label, max_length))
    if "account_balance" in data:
        prepay.account_balance = _optional_money(data["account_balance"], "账户余额")

    if prepay.audit_status == AUDIT_REJECTED:
        prepay.status = STATUS_PENDING
        prepay.audit_status = AUDIT_PENDING
        prepay.auditor_id = None
        prepay.audit_time = None
    write_audit(
        db,
        action="UPDATE",
        target_type=DOC_TYPE,
        target_id=str(prepay.id),
        summary=f"修改采购预付款申请：{prepay.prepay_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return prepay


def delete_prepay(
    db: Session, *, prepay_id: int, staff: Staff, commit: bool = True
) -> None:
    prepay = get_prepay_or_404(db, prepay_id)
    _ensure_editable(db, prepay)
    voucher = _active_voucher(db, prepay)
    if voucher is not None:
        raise BizException(ErrorCode.CONFLICT, "采购预付款已生成凭证，不能删除")
    db.delete(prepay)
    write_audit(
        db,
        action="DELETE",
        target_type=DOC_TYPE,
        target_id=str(prepay.id),
        summary=f"删除采购预付款申请：{prepay.prepay_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def _map_audit_status(prepay: PurchasePrepay, state: dict) -> None:
    status = state["status"]
    if status == "APPROVED":
        prepay.audit_status = AUDIT_APPROVED
    elif status == "REJECTED":
        prepay.audit_status = AUDIT_REJECTED
    else:
        prepay.audit_status = AUDIT_PENDING


def submit_prepay(
    db: Session, *, prepay_id: int, staff: Staff, comment: str | None = None,
    commit: bool = True,
) -> PurchasePrepay:
    prepay = get_prepay_or_404(db, prepay_id)
    _ensure_editable(db, prepay)
    state = approval_service.submit(
        db,
        doc_type=DOC_TYPE,
        doc_id=prepay.id,
        doc_no=prepay.prepay_no,
        staff=staff,
        comment=comment,
        commit=False,
    )
    prepay.status = STATUS_PENDING
    _map_audit_status(prepay, state)
    prepay.auditor_id = None
    prepay.audit_time = None
    _commit(db, commit)
    return prepay


def approve_prepay(
    db: Session,
    *,
    prepay_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchasePrepay:
    prepay = get_prepay_or_404(db, prepay_id, check_scope=False)
    if prepay.status not in {STATUS_PENDING, STATUS_APPROVED}:
        raise BizException(ErrorCode.CONFLICT, "当前采购预付款申请状态不能审核")
    state = approval_service.approve(
        db,
        doc_type=DOC_TYPE,
        doc_id=prepay.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    _map_audit_status(prepay, state)
    if state["status"] == "APPROVED":
        prepay.status = STATUS_APPROVED
        prepay.auditor_id = staff.id
        prepay.audit_time = _now()
        _ensure_voucher(db, prepay, staff)
    db.flush()
    _commit(db, commit)
    return prepay


def reject_prepay(
    db: Session,
    *,
    prepay_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> PurchasePrepay:
    prepay = get_prepay_or_404(db, prepay_id, check_scope=False)
    if prepay.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "当前采购预付款申请状态不能审核")
    state = approval_service.reject(
        db,
        doc_type=DOC_TYPE,
        doc_id=prepay.id,
        staff=staff,
        comment=comment,
        commit=False,
    )
    prepay.status = STATUS_REJECTED
    _map_audit_status(prepay, state)
    prepay.auditor_id = staff.id
    prepay.audit_time = _now()
    _commit(db, commit)
    return prepay


def unaudit_prepay(
    db: Session,
    *,
    prepay_id: int,
    staff: Staff,
    reason: str,
    step: int | None = None,
    commit: bool = True,
) -> PurchasePrepay:
    prepay = get_prepay_or_404(db, prepay_id, check_scope=False)
    if prepay.status != STATUS_APPROVED or prepay.audit_status != AUDIT_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "采购预付款申请尚未审核通过，不能弃审")
    state = approval_service.unaudit(
        db,
        doc_type=DOC_TYPE,
        doc_id=prepay.id,
        staff=staff,
        reason=reason,
        step=step,
        commit=False,
    )
    voucher = _active_voucher(db, prepay)
    if voucher is not None:
        voucher_service.invalidate_voucher_by_reference(
            db,
            reference=f"采购预付款申请:{prepay.prepay_no}",
            staff=staff,
            reason=reason,
            source_type=DOC_TYPE,
            source_id=str(prepay.id),
            commit=False,
        )
    prepay.status = STATUS_PENDING
    _map_audit_status(prepay, state)
    prepay.auditor_id = None
    prepay.audit_time = None
    _commit(db, commit)
    return prepay


def terminate_prepay(
    db: Session,
    *,
    prepay_id: int,
    staff: Staff,
    reason: str,
    commit: bool = True,
) -> PurchasePrepay:
    prepay = get_prepay_or_404(db, prepay_id)
    _ensure_editable(db, prepay)
    prepay.status = STATUS_TERMINATED
    write_audit(
        db,
        action="TERMINATE",
        target_type=DOC_TYPE,
        target_id=str(prepay.id),
        summary=f"终止采购预付款申请：{prepay.prepay_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return prepay


def _active_voucher(
    db: Session, prepay: PurchasePrepay
) -> Voucher | None:
    return voucher_service.get_active_voucher_by_reference(
        db, f"采购预付款申请:{prepay.prepay_no}"
    )


def _ensure_voucher(
    db: Session, prepay: PurchasePrepay, staff: Staff
) -> Voucher:
    supplier = db.get(Supplier, prepay.supplier_id)
    return voucher_service.create_prepay_voucher(
        db,
        prepay_no=prepay.prepay_no,
        amount=prepay.prepay_amount,
        supplier_name=supplier.name if supplier else str(prepay.supplier_id),
        staff=staff,
        serial_generator=generate_serial,
        commit=False,
    )


def voucher_dict(db: Session, voucher: Voucher | None) -> dict | None:
    if voucher is None:
        return None
    return voucher_service.voucher_dict(db, voucher)


def get_prepay_voucher(db: Session, prepay_id: int) -> dict | None:
    prepay = get_prepay_or_404(db, prepay_id)
    return voucher_dict(db, _active_voucher(db, prepay))
