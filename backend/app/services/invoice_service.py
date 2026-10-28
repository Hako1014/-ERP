"""发票管理与供应商账户余额服务（TASK-046）。

发票管理覆盖抬头、纳税人识别号、开票类型、金额、实际到账、开票状态和
申请人。TASK-045 生成的平台应收记录使用 ``document_type=RECEIVABLE``，
不会混入实际开票列表。

供应商账户余额来自供应商财务账户。金蝶真实协议未确认，本任务提供可替换
的同步适配入口和本地 mock；真实认证、字段映射和调用由 TASK-064 接入。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.finance import Invoice
from app.models.org import Staff
from app.models.party import Account, Supplier

DOCUMENT_INVOICE = "INVOICE"
DOCUMENT_RECEIVABLE = "RECEIVABLE"

STATUS_PENDING = "PENDING"
STATUS_ISSUED = "ISSUED"
STATUS_RECEIVED = "RECEIVED"
STATUS_CANCELLED = "CANCELLED"

SYNC_NOT_SYNCED = "NOT_SYNCED"
SYNC_SYNCED = "SYNCED"
SYNC_FAILED = "FAILED"

INVOICE_TYPES: dict[str, str] = {
    "NORMAL": "普通发票",
    "SPECIAL": "专用发票",
    "ELECTRONIC": "电子发票",
    "RED_SPECIAL": "红字专用发票",
    "RED_NORMAL": "红字普通发票",
}
TYPE_ALIASES = {name: code for code, name in INVOICE_TYPES.items()}

INVOICE_FIELDS = {
    "invoice_title",
    "tax_no",
    "invoice_type",
    "amount",
    "received_amount",
    "is_received",
    "company",
    "status",
    "applicant",
}
INVOICE_UPDATE_FIELDS = {
    "invoice_title",
    "tax_no",
    "invoice_type",
    "amount",
    "company",
    "applicant",
}

KingdeeBalanceSyncer = Callable[[dict[str, Any]], dict[str, Any]]
_kingdee_balance_syncer: KingdeeBalanceSyncer | None = None


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


def _require_text(value: Any, label: str, max_length: int) -> str:
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


def _to_money(value: Any, label: str, *, allow_zero: bool = False) -> Decimal:
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < Decimal("0.00") or (result == Decimal("0.00") and not allow_zero):
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _normalize_invoice_type(value: Any) -> str:
    if value in (None, ""):
        raise BizException(ErrorCode.BAD_REQUEST, "开票类型不能为空")
    raw = str(value).strip()
    code = raw.upper()
    if code in INVOICE_TYPES:
        return code
    if raw in TYPE_ALIASES:
        return TYPE_ALIASES[raw]
    raise BizException(ErrorCode.BAD_REQUEST, f"不支持的开票类型：{raw}")


def _normalize_status(value: Any) -> str:
    status = str(value or "").strip().upper()
    if status not in {STATUS_PENDING, STATUS_ISSUED, STATUS_RECEIVED, STATUS_CANCELLED}:
        raise BizException(ErrorCode.BAD_REQUEST, "开票状态不合法")
    return status


def get_invoice_or_404(db: Session, invoice_id: int) -> Invoice:
    invoice = db.get(Invoice, invoice_id)
    if invoice is None:
        raise BizException(ErrorCode.NOT_FOUND, "发票不存在")
    return invoice


def invoice_dict(invoice: Invoice) -> dict:
    return {
        "id": invoice.id,
        "invoice_no": invoice.invoice_no,
        "document_type": invoice.document_type,
        "invoice_title": invoice.invoice_title,
        "tax_no": invoice.tax_no,
        "invoice_type": invoice.invoice_type,
        "invoice_type_name": (
            INVOICE_TYPES.get(invoice.invoice_type)
            if invoice.invoice_type
            else None
        ),
        "amount": invoice.amount,
        "received_amount": invoice.received_amount,
        "unreceived_amount": max(
            invoice.amount - invoice.received_amount, Decimal("0.00")
        ),
        "is_received": invoice.is_received,
        "company": invoice.company,
        "status": invoice.status,
        "applicant": invoice.applicant,
        "saved_at": invoice.created_at,
        "created_at": invoice.created_at,
        "updated_at": invoice.updated_at,
    }


def list_invoices(
    db: Session,
    *,
    invoice_type: str | None = None,
    status: str | None = None,
    is_received: bool | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = [Invoice.document_type == DOCUMENT_INVOICE]
    if invoice_type:
        conditions.append(
            Invoice.invoice_type == _normalize_invoice_type(invoice_type)
        )
    if status:
        conditions.append(Invoice.status == _normalize_status(status))
    if is_received is not None:
        conditions.append(Invoice.is_received.is_(is_received))
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Invoice.invoice_no.like(like),
                Invoice.invoice_title.like(like),
                Invoice.tax_no.like(like),
                Invoice.company.like(like),
                Invoice.applicant.like(like),
            )
        )
    total = db.scalar(select(func.count(Invoice.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Invoice)
            .where(*conditions)
            .order_by(Invoice.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [invoice_dict(row) for row in rows],
    }


def create_invoice(
    db: Session, *, data: dict, staff: Staff, commit: bool = True
) -> Invoice:
    _check_unknown_fields(data, INVOICE_FIELDS)
    amount = _to_money(data.get("amount"), "开票金额")
    received_amount = _to_money(
        data.get("received_amount", Decimal("0.00")),
        "实际到账金额",
        allow_zero=True,
    )
    if received_amount > amount:
        raise BizException(ErrorCode.BAD_REQUEST, "实际到账金额不能超过开票金额")
    requested_status = (
        _normalize_status(data.get("status"))
        if data.get("status") not in (None, "")
        else None
    )
    if requested_status == STATUS_CANCELLED:
        raise BizException(ErrorCode.BAD_REQUEST, "新增发票不能直接设为已作废")
    is_received = bool(data.get("is_received", False))
    if is_received and received_amount != amount:
        raise BizException(
            ErrorCode.BAD_REQUEST, "标记已到账时实际到账金额必须等于开票金额"
        )
    if requested_status == STATUS_PENDING and received_amount > Decimal("0.00"):
        raise BizException(
            ErrorCode.BAD_REQUEST, "有待开票状态时实际到账金额必须为 0"
        )
    if requested_status == STATUS_RECEIVED and received_amount != amount:
        raise BizException(
            ErrorCode.BAD_REQUEST, "已到账状态要求实际到账金额等于开票金额"
        )
    if received_amount == amount:
        is_received = True
        status = STATUS_RECEIVED
    elif received_amount > Decimal("0.00"):
        status = STATUS_ISSUED
    else:
        status = requested_status or STATUS_PENDING
    invoice = Invoice(
        invoice_no=generate_serial(SerialPrefix.INVOICE, dt=_now()),
        document_type=DOCUMENT_INVOICE,
        invoice_title=_optional_text(data.get("invoice_title"), "发票抬头", 255),
        tax_no=_optional_text(data.get("tax_no"), "纳税人识别号", 64),
        invoice_type=_normalize_invoice_type(data.get("invoice_type")),
        amount=amount,
        received_amount=received_amount,
        is_received=is_received,
        company=_optional_text(data.get("company"), "开票公司", 128),
        status=status,
        applicant=(
            _optional_text(data.get("applicant"), "申请人姓名", 64)
            or staff.name
        ),
    )
    db.add(invoice)
    db.flush()
    write_audit(
        db,
        action="CREATE",
        target_type="invoice",
        target_id=str(invoice.id),
        summary=f"新增发票：{invoice.invoice_no}",
        detail={
            "invoice_type": invoice.invoice_type,
            "amount": str(invoice.amount),
            "received_amount": str(invoice.received_amount),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return invoice


def update_invoice(
    db: Session,
    *,
    invoice_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Invoice:
    _check_unknown_fields(data, INVOICE_UPDATE_FIELDS)
    invoice = get_invoice_or_404(db, invoice_id)
    if invoice.document_type != DOCUMENT_INVOICE:
        raise BizException(ErrorCode.CONFLICT, "平台应收记录不能按发票修改")
    if invoice.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "仅待开票状态允许修改")
    if "invoice_title" in data:
        invoice.invoice_title = _optional_text(
            data["invoice_title"], "发票抬头", 255
        )
    if "tax_no" in data:
        invoice.tax_no = _optional_text(
            data["tax_no"], "纳税人识别号", 64
        )
    if "invoice_type" in data:
        invoice.invoice_type = _normalize_invoice_type(data["invoice_type"])
    if "amount" in data:
        invoice.amount = _to_money(data["amount"], "开票金额")
    if "company" in data:
        invoice.company = _optional_text(data["company"], "开票公司", 128)
    if "applicant" in data:
        invoice.applicant = _optional_text(
            data["applicant"], "申请人姓名", 64
        )
    write_audit(
        db,
        action="UPDATE",
        target_type="invoice",
        target_id=str(invoice.id),
        summary=f"修改发票：{invoice.invoice_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return invoice


def issue_invoice(
    db: Session,
    *,
    invoice_id: int,
    staff: Staff,
    commit: bool = True,
) -> Invoice:
    invoice = get_invoice_or_404(db, invoice_id)
    if invoice.document_type != DOCUMENT_INVOICE:
        raise BizException(ErrorCode.CONFLICT, "平台应收记录不能执行开票")
    if invoice.status != STATUS_PENDING:
        raise BizException(ErrorCode.CONFLICT, "仅待开票发票可以执行开票")
    invoice.status = STATUS_ISSUED
    write_audit(
        db,
        action="ISSUE",
        target_type="invoice",
        target_id=str(invoice.id),
        summary=f"发票开票：{invoice.invoice_no}",
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return invoice


def receive_invoice(
    db: Session,
    *,
    invoice_id: int,
    received_amount: Any,
    is_received: bool | None = None,
    staff: Staff,
    commit: bool = True,
) -> Invoice:
    invoice = get_invoice_or_404(db, invoice_id)
    if invoice.document_type != DOCUMENT_INVOICE:
        raise BizException(ErrorCode.CONFLICT, "平台应收记录不能执行到账登记")
    if invoice.status == STATUS_CANCELLED:
        raise BizException(ErrorCode.CONFLICT, "已作废发票不能登记到账")
    amount = _to_money(received_amount, "实际到账金额")
    if amount > invoice.amount:
        raise BizException(ErrorCode.BAD_REQUEST, "实际到账金额不能超过开票金额")
    invoice.received_amount = amount
    invoice.is_received = (
        bool(is_received) if is_received is not None else amount == invoice.amount
    )
    invoice.status = STATUS_RECEIVED if invoice.is_received else STATUS_ISSUED
    write_audit(
        db,
        action="RECEIVE",
        target_type="invoice",
        target_id=str(invoice.id),
        summary=f"发票到账登记：{invoice.invoice_no}",
        detail={
            "received_amount": str(invoice.received_amount),
            "is_received": invoice.is_received,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return invoice


def cancel_invoice(
    db: Session,
    *,
    invoice_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    invoice = get_invoice_or_404(db, invoice_id)
    if invoice.document_type != DOCUMENT_INVOICE:
        raise BizException(ErrorCode.CONFLICT, "平台应收记录不能按发票作废")
    if invoice.received_amount > Decimal("0.00") or invoice.is_received:
        raise BizException(ErrorCode.CONFLICT, "已有到账记录的发票不能作废")
    if invoice.status == STATUS_CANCELLED:
        return
    invoice.status = STATUS_CANCELLED
    write_audit(
        db,
        action="CANCEL",
        target_type="invoice",
        target_id=str(invoice.id),
        summary=f"作废发票：{invoice.invoice_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def register_kingdee_balance_syncer(
    syncer: KingdeeBalanceSyncer | None,
) -> None:
    """注册金蝶账户余额适配器；TASK-064 通过该入口接入真实实现。"""
    global _kingdee_balance_syncer
    _kingdee_balance_syncer = syncer


def _account_supplier(db: Session, account: Account) -> Supplier:
    if account.supplier_id is None:
        raise BizException(ErrorCode.BAD_REQUEST, "仅供应商账户支持余额同步")
    supplier = db.get(Supplier, account.supplier_id)
    if supplier is None:
        raise BizException(ErrorCode.CONFLICT, "账户关联供应商不存在")
    return supplier


def supplier_account_balance_dict(
    db: Session, account: Account
) -> dict:
    supplier = _account_supplier(db, account)
    return {
        "id": account.id,
        "supplier_id": supplier.id,
        "supplier_code": supplier.code,
        "supplier_name": supplier.name,
        "bank": account.bank,
        "branch": account.branch,
        "bank_no": account.bank_no,
        "account_no": account.account_no,
        "is_default": account.is_default,
        "balance": account.balance,
        "kingdee_account_code": account.kingdee_account_code,
        "kingdee_sync_status": account.kingdee_sync_status,
        "kingdee_sync_message": account.kingdee_sync_message,
        "balance_synced_at": account.balance_synced_at,
        "created_at": account.created_at,
        "updated_at": account.updated_at,
    }


def list_supplier_account_balances(
    db: Session,
    *,
    supplier_id: int | None = None,
    sync_status: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    conditions = [Account.supplier_id.is_not(None)]
    if supplier_id is not None:
        conditions.append(Account.supplier_id == supplier_id)
    if sync_status:
        normalized = sync_status.strip().upper()
        if normalized not in {SYNC_NOT_SYNCED, SYNC_SYNCED, SYNC_FAILED}:
            raise BizException(ErrorCode.BAD_REQUEST, "同步状态不合法")
        conditions.append(Account.kingdee_sync_status == normalized)
    if keyword:
        like = f"%{keyword.strip()}%"
        conditions.append(
            or_(
                Account.bank.like(like),
                Account.branch.like(like),
                Account.account_no.like(like),
                Account.kingdee_account_code.like(like),
                Account.supplier_id.in_(
                    select(Supplier.id).where(
                        or_(Supplier.name.like(like), Supplier.code.like(like))
                    )
                ),
            )
        )
    total = db.scalar(select(func.count(Account.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Account)
            .where(*conditions)
            .order_by(Account.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [supplier_account_balance_dict(db, row) for row in rows],
    }


def get_supplier_account_balance(
    db: Session, account_id: int
) -> dict:
    account = db.get(Account, account_id)
    if account is None or account.supplier_id is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商账户不存在")
    return supplier_account_balance_dict(db, account)


def sync_supplier_account_balance(
    db: Session,
    *,
    account_id: int,
    staff: Staff,
    commit: bool = True,
) -> dict:
    account = db.get(Account, account_id)
    if account is None or account.supplier_id is None:
        raise BizException(ErrorCode.NOT_FOUND, "供应商账户不存在")
    supplier = _account_supplier(db, account)
    payload = {
        "supplier_id": supplier.id,
        "supplier_code": supplier.code,
        "supplier_name": supplier.name,
        "account_id": account.id,
        "kingdee_account_code": account.kingdee_account_code,
        "bank": account.bank,
        "account_no": account.account_no,
        "local_balance": str(account.balance),
    }
    from app.integrations.kingdee import get_kingdee_adapter

    syncer = _kingdee_balance_syncer or get_kingdee_adapter().get_balance
    try:
        response = syncer(payload)
        if not isinstance(response, dict) or not response.get("success", False):
            message = (
                response.get("message", "金蝶余额同步失败")
                if isinstance(response, dict)
                else "金蝶余额同步返回格式错误"
            )
            raise BizException(ErrorCode.CONFLICT, str(message))
        balance = round_money(response.get("balance"))
    except BizException as exc:
        account.kingdee_sync_status = SYNC_FAILED
        account.kingdee_sync_message = exc.message
        write_audit(
            db,
            action="SYNC_FAILED",
            target_type="supplier_account",
            target_id=str(account.id),
            summary=f"供应商账户余额同步失败：{supplier.name}",
            reason=exc.message,
            actor_id=staff.id,
            actor_name=staff.name,
        )
        _commit(db, commit)
        raise
    except (TypeError, InvalidOperation, ValueError) as exc:
        message = "金蝶返回的账户余额格式不正确"
        account.kingdee_sync_status = SYNC_FAILED
        account.kingdee_sync_message = message
        write_audit(
            db,
            action="SYNC_FAILED",
            target_type="supplier_account",
            target_id=str(account.id),
            summary=f"供应商账户余额同步失败：{supplier.name}",
            reason=message,
            actor_id=staff.id,
            actor_name=staff.name,
        )
        _commit(db, commit)
        raise BizException(ErrorCode.CONFLICT, message) from exc
    except Exception as exc:
        message = "金蝶余额同步调用异常"
        account.kingdee_sync_status = SYNC_FAILED
        account.kingdee_sync_message = message
        write_audit(
            db,
            action="SYNC_FAILED",
            target_type="supplier_account",
            target_id=str(account.id),
            summary=f"供应商账户余额同步失败：{supplier.name}",
            reason=message,
            actor_id=staff.id,
            actor_name=staff.name,
        )
        _commit(db, commit)
        raise BizException(ErrorCode.CONFLICT, message) from exc

    account.balance = balance
    account.kingdee_sync_status = SYNC_SYNCED
    account.kingdee_sync_message = str(
        response.get("message") or "账户余额同步成功"
    )[:255]
    account.balance_synced_at = _now()
    write_audit(
        db,
        action="SYNC_BALANCE",
        target_type="supplier_account",
        target_id=str(account.id),
        summary=f"同步供应商账户余额：{supplier.name}",
        detail={
            "old_balance": payload["local_balance"],
            "new_balance": str(account.balance),
            "kingdee_account_code": account.kingdee_account_code,
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return supplier_account_balance_dict(db, account)
