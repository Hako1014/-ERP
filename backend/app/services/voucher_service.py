"""会计凭证服务（TASK-043）。

负责手工凭证 CRUD、审核/反审、借贷平衡校验，以及业务单据的幂等分录生成。
金蝶真实协议未确认，本任务只提供可替换的传输适配入口，真实实现由
TASK-064 接入。
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import write_audit
from app.core.data_scope import owner_visible
from app.core.errors import BizException, ErrorCode
from app.core.money import round_money
from app.core.prefixes import SerialPrefix
from app.core.serial import generate_serial
from app.models.finance import AccountSubject, Voucher, VoucherItem
from app.models.org import Staff
from app.repositories import finance_repo

STATUS_DRAFT = "DRAFT"
STATUS_APPROVED = "APPROVED"
STATUS_INVALID = "INVALID"
VOUCHER_STATUSES = {STATUS_DRAFT, STATUS_APPROVED, STATUS_INVALID}

VOUCHER_FIELDS = {
    "voucher_word",
    "voucher_no",
    "business_date",
    "record_date",
    "reference",
    "items",
}
ITEM_FIELDS = {"subject_id", "summary", "debit", "credit"}
MAX_VOUCHER_ITEMS = 200

ZERO_MONEY = Decimal("0.00")

# 自动分录使用的系统默认科目。已存在但停用时不自动启用。
AUTO_SUBJECTS: dict[str, dict[str, Any]] = {
    "1123": {
        "code": "1123",
        "name": "预付账款",
        "category": "ASSET",
        "balance_direction": "DEBIT",
        "is_auxiliary": True,
    },
    "1002": {
        "code": "1002",
        "name": "银行存款",
        "category": "ASSET",
        "balance_direction": "DEBIT",
        "is_bank": True,
    },
    "1122": {
        "code": "1122",
        "name": "应收账款",
        "category": "ASSET",
        "balance_direction": "DEBIT",
        "is_auxiliary": True,
    },
    "6001": {
        "code": "6001",
        "name": "主营业务收入",
        "category": "PROFIT_LOSS",
        "balance_direction": "CREDIT",
    },
    "6601": {
        "code": "6601",
        "name": "销售费用",
        "category": "PROFIT_LOSS",
        "balance_direction": "DEBIT",
    },
    "1405": {
        "code": "1405",
        "name": "库存商品",
        "category": "ASSET",
        "balance_direction": "DEBIT",
    },
    "2202": {
        "code": "2202",
        "name": "应付账款",
        "category": "LIABILITY",
        "balance_direction": "CREDIT",
        "is_auxiliary": True,
    },
    "6401": {
        "code": "6401",
        "name": "主营业务成本",
        "category": "PROFIT_LOSS",
        "balance_direction": "DEBIT",
    },
    "1901": {
        "code": "1901",
        "name": "待处理财产损溢",
        "category": "ASSET",
        "balance_direction": "DEBIT",
    },
}

STOCK_IN_SUBJECT_MAP: dict[str, tuple[str, str]] = {
    "PURCHASE": ("1405", "2202"),
    "AFTER_SALE": ("1405", "6401"),
    "MANUAL": ("1405", "1901"),
    "CHECK": ("1405", "1901"),
    "PUSH": ("1405", "1901"),
    "ADJUST": ("1405", "1901"),
}

KingdeeTransporter = Callable[[dict[str, Any]], dict[str, Any]]
_kingdee_transporter: KingdeeTransporter | None = None


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
    if value is None:
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
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > max_length:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"{label}长度不能超过 {max_length}"
        )
    return text


def _to_int_id(value: Any, label: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为整数") from exc
    if result < 1:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须大于 0")
    return result


def _to_money(value: Any, label: str) -> Decimal:
    if value in (None, ""):
        value = 0
    try:
        result = round_money(value)
    except (TypeError, InvalidOperation, ValueError) as exc:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法金额") from exc
    if result < ZERO_MONEY:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}不能为负数")
    return result


def _optional_datetime(value: Any, label: str) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise BizException(
                ErrorCode.BAD_REQUEST, f"{label}必须为合法日期时间"
            ) from exc
    else:
        raise BizException(ErrorCode.BAD_REQUEST, f"{label}必须为合法日期时间")
    if result.tzinfo is not None:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def _normalize_items(raw_items: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_items, list) or not raw_items:
        raise BizException(ErrorCode.BAD_REQUEST, "凭证至少需要一条分录")
    if len(raw_items) > MAX_VOUCHER_ITEMS:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"单张凭证最多 {MAX_VOUCHER_ITEMS} 条分录",
        )

    result: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_items, start=1):
        if not isinstance(raw, dict):
            raise BizException(ErrorCode.BAD_REQUEST, "凭证分录格式错误")
        _check_unknown_fields(raw, ITEM_FIELDS)
        subject_id = _to_int_id(raw.get("subject_id"), f"第 {index} 行科目")
        debit = _to_money(raw.get("debit"), f"第 {index} 行借方")
        credit = _to_money(raw.get("credit"), f"第 {index} 行贷方")
        if debit > ZERO_MONEY and credit > ZERO_MONEY:
            raise BizException(
                ErrorCode.BAD_REQUEST, f"第 {index} 行借贷方不能同时有金额"
            )
        if debit == ZERO_MONEY and credit == ZERO_MONEY:
            raise BizException(
                ErrorCode.BAD_REQUEST, f"第 {index} 行借方或贷方必须填写金额"
            )
        result.append(
            {
                "subject_id": subject_id,
                "summary": _optional_text(
                    raw.get("summary"), f"第 {index} 行摘要", 255
                ),
                "debit": debit,
                "credit": credit,
            }
        )
    _ensure_balanced(result)
    return result


def _item_value(
    item: dict[str, Any] | VoucherItem, field: str
) -> Any:
    if isinstance(item, dict):
        return item[field]
    return getattr(item, field)


def _ensure_balanced(items: list[dict[str, Any]] | list[VoucherItem]) -> None:
    total_debit = sum(
        (Decimal(_item_value(item, "debit")) for item in items), ZERO_MONEY
    )
    total_credit = sum(
        (Decimal(_item_value(item, "credit")) for item in items), ZERO_MONEY
    )
    if total_debit == ZERO_MONEY and total_credit == ZERO_MONEY:
        raise BizException(ErrorCode.BAD_REQUEST, "凭证借贷合计不能为 0")
    if total_debit != total_credit:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"凭证借贷不平衡：借方 {total_debit}，贷方 {total_credit}",
        )


def _total_items(
    items: list[dict[str, Any]] | list[VoucherItem],
) -> tuple[Decimal, Decimal]:
    total_debit = sum(
        (Decimal(_item_value(item, "debit")) for item in items), ZERO_MONEY
    )
    total_credit = sum(
        (Decimal(_item_value(item, "credit")) for item in items), ZERO_MONEY
    )
    return total_debit, total_credit


def _get_subject_or_error(
    subjects: dict[int, AccountSubject], subject_id: int
) -> AccountSubject:
    subject = subjects.get(subject_id)
    if subject is None:
        raise BizException(
            ErrorCode.BAD_REQUEST, f"会计科目不存在：{subject_id}"
        )
    if not subject.is_enabled:
        raise BizException(
            ErrorCode.CONFLICT, f"会计科目已停用：{subject.code} {subject.name}"
        )
    return subject


def _validate_subjects(
    db: Session, items: list[dict[str, Any]] | list[VoucherItem]
) -> None:
    subjects = finance_repo.get_subjects_by_ids(
        db, {int(_item_value(item, "subject_id")) for item in items}
    )
    for item in items:
        _get_subject_or_error(
            subjects, int(_item_value(item, "subject_id"))
        )


def get_voucher_or_404(db: Session, voucher_id: int) -> Voucher:
    voucher = finance_repo.get_voucher_by_id(db, voucher_id)
    if voucher is None or not owner_visible(db, voucher.created_by):
        raise BizException(ErrorCode.NOT_FOUND, "会计凭证不存在")
    return voucher


def _staff_name(db: Session, staff_id: int | None) -> str | None:
    if staff_id is None:
        return None
    staff = db.get(Staff, staff_id)
    return staff.name if staff else None


def voucher_dict(db: Session, voucher: Voucher | None) -> dict | None:
    if voucher is None:
        return None
    items = finance_repo.list_voucher_items(db, voucher.id)
    subjects = finance_repo.get_subjects_by_ids(
        db, {item.subject_id for item in items}
    )
    return {
        "id": voucher.id,
        "voucher_word": voucher.voucher_word,
        "voucher_no": voucher.voucher_no,
        "business_date": voucher.business_date,
        "record_date": voucher.record_date,
        "reference": voucher.reference,
        "total_debit": voucher.total_debit,
        "total_credit": voucher.total_credit,
        "status": voucher.status,
        "created_by": voucher.created_by,
        "created_by_name": _staff_name(db, voucher.created_by),
        "auditor_id": voucher.auditor_id,
        "auditor_name": _staff_name(db, voucher.auditor_id),
        "posted_by": voucher.posted_by,
        "posted_by_name": _staff_name(db, voucher.posted_by),
        "cashier_id": voucher.cashier_id,
        "cashier_name": _staff_name(db, voucher.cashier_id),
        "general_office_id": voucher.general_office_id,
        "general_office_name": _staff_name(
            db, voucher.general_office_id
        ),
        "created_at": voucher.created_at,
        "updated_at": voucher.updated_at,
        "items": [
            {
                "id": item.id,
                "subject_id": item.subject_id,
                "subject_code": (
                    subjects[item.subject_id].code
                    if item.subject_id in subjects
                    else None
                ),
                "subject_name": (
                    subjects[item.subject_id].name
                    if item.subject_id in subjects
                    else None
                ),
                "summary": item.summary,
                "debit": item.debit,
                "credit": item.credit,
            }
            for item in items
        ],
    }


def list_vouchers(
    db: Session,
    *,
    status: str | None = None,
    keyword: str | None = None,
    business_date_from: datetime | None = None,
    business_date_to: datetime | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict:
    if (
        business_date_from is not None
        and business_date_to is not None
        and business_date_from > business_date_to
    ):
        raise BizException(ErrorCode.BAD_REQUEST, "凭证开始日期不能晚于结束日期")

    page = max(page, 1)
    page_size = min(max(page_size, 1), 200)
    normalized_status = None
    if status:
        normalized_status = status.strip().upper()
        if normalized_status not in VOUCHER_STATUSES:
            raise BizException(
                ErrorCode.BAD_REQUEST,
                f"凭证状态仅支持 {', '.join(sorted(VOUCHER_STATUSES))}",
            )
    total, rows = finance_repo.list_vouchers(
        db,
        status=normalized_status,
        keyword=keyword,
        business_date_from=business_date_from,
        business_date_to=business_date_to,
        page=page,
        page_size=page_size,
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [voucher_dict(db, row) for row in rows],
    }


def _ensure_voucher_no_available(
    db: Session, voucher_no: str, *, exclude_id: int | None = None
) -> None:
    existing = finance_repo.get_voucher_by_no(db, voucher_no)
    if existing is not None and existing.id != exclude_id:
        raise BizException(
            ErrorCode.CONFLICT, f"凭证号已存在：{voucher_no}"
        )


def _replace_items(
    db: Session, voucher: Voucher, items: list[dict[str, Any]]
) -> None:
    for row in finance_repo.list_voucher_items(db, voucher.id):
        db.delete(row)
    db.flush()
    for item in items:
        db.add(VoucherItem(voucher_id=voucher.id, **item))
    total_debit, total_credit = _total_items(items)
    voucher.total_debit = total_debit
    voucher.total_credit = total_credit
    db.flush()


def create_voucher(
    db: Session,
    *,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Voucher:
    _check_unknown_fields(data, VOUCHER_FIELDS)
    items = _normalize_items(data.get("items"))
    _validate_subjects(db, items)

    voucher_no = _optional_text(data.get("voucher_no"), "凭证号", 64)
    if voucher_no is None:
        voucher_no = generate_serial(SerialPrefix.VOUCHER, dt=_now())
    _ensure_voucher_no_available(db, voucher_no)

    now = _now()
    business_date = _optional_datetime(data.get("business_date"), "业务日期")
    record_date = _optional_datetime(data.get("record_date"), "记账日期")
    total_debit, total_credit = _total_items(items)
    voucher = Voucher(
        voucher_word=_optional_text(data.get("voucher_word"), "凭证字", 8)
        or "记",
        voucher_no=voucher_no,
        business_date=business_date or now,
        record_date=record_date or now,
        reference=_optional_text(data.get("reference"), "参考信息", 255),
        total_debit=total_debit,
        total_credit=total_credit,
        status=STATUS_DRAFT,
        created_by=staff.id,
    )
    db.add(voucher)
    db.flush()
    _replace_items(db, voucher, items)
    write_audit(
        db,
        action="CREATE",
        target_type="voucher",
        target_id=str(voucher.id),
        summary=f"新增会计凭证：{voucher.voucher_no}",
        detail={
            "total_debit": str(voucher.total_debit),
            "total_credit": str(voucher.total_credit),
            "item_count": len(items),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return voucher


def update_voucher(
    db: Session,
    *,
    voucher_id: int,
    data: dict,
    staff: Staff,
    commit: bool = True,
) -> Voucher:
    _check_unknown_fields(data, VOUCHER_FIELDS)
    voucher = get_voucher_or_404(db, voucher_id)
    if voucher.status != STATUS_DRAFT:
        raise BizException(ErrorCode.CONFLICT, "仅草稿凭证允许修改")

    if "voucher_word" in data:
        voucher.voucher_word = (
            _optional_text(data["voucher_word"], "凭证字", 8) or "记"
        )
    if "voucher_no" in data:
        voucher_no = _require_text(data["voucher_no"], "凭证号", 64)
        _ensure_voucher_no_available(
            db, voucher_no, exclude_id=voucher.id
        )
        voucher.voucher_no = voucher_no
    if "business_date" in data:
        voucher.business_date = _optional_datetime(
            data["business_date"], "业务日期"
        )
    if "record_date" in data:
        voucher.record_date = _optional_datetime(
            data["record_date"], "记账日期"
        )
    if "reference" in data:
        voucher.reference = _optional_text(
            data["reference"], "参考信息", 255
        )
    if "items" in data:
        items = _normalize_items(data["items"])
        _validate_subjects(db, items)
        _replace_items(db, voucher, items)

    write_audit(
        db,
        action="UPDATE",
        target_type="voucher",
        target_id=str(voucher.id),
        summary=f"修改会计凭证：{voucher.voucher_no}",
        detail={"fields": sorted(data)},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return voucher


def delete_voucher(
    db: Session,
    *,
    voucher_id: int,
    staff: Staff,
    reason: str | None = None,
    commit: bool = True,
) -> None:
    voucher = get_voucher_or_404(db, voucher_id)
    if voucher.status != STATUS_DRAFT:
        raise BizException(ErrorCode.CONFLICT, "仅草稿凭证允许作废")
    voucher.status = STATUS_INVALID
    write_audit(
        db,
        action="INVALIDATE",
        target_type="voucher",
        target_id=str(voucher.id),
        summary=f"作废会计凭证：{voucher.voucher_no}",
        reason=reason,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)


def audit_voucher(
    db: Session,
    *,
    voucher_id: int,
    staff: Staff,
    comment: str | None = None,
    commit: bool = True,
) -> Voucher:
    voucher = get_voucher_or_404(db, voucher_id)
    if voucher.status != STATUS_DRAFT:
        raise BizException(ErrorCode.CONFLICT, "仅草稿凭证允许审核")
    items = finance_repo.list_voucher_items(db, voucher.id)
    _ensure_balanced(items)
    _validate_subjects(db, items)
    voucher.status = STATUS_APPROVED
    voucher.auditor_id = staff.id
    write_audit(
        db,
        action="AUDIT",
        target_type="voucher",
        target_id=str(voucher.id),
        summary=f"审核会计凭证：{voucher.voucher_no}",
        detail={"comment": comment},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return voucher


def unaudit_voucher(
    db: Session,
    *,
    voucher_id: int,
    staff: Staff,
    reason: str,
    commit: bool = True,
) -> Voucher:
    voucher = get_voucher_or_404(db, voucher_id)
    if voucher.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "仅已审核凭证允许反审")
    if voucher.posted_by is not None:
        raise BizException(ErrorCode.CONFLICT, "凭证已过账，不能反审")
    reason_text = _require_text(reason, "反审原因", 255)
    voucher.status = STATUS_DRAFT
    voucher.auditor_id = None
    write_audit(
        db,
        action="UNAUDIT",
        target_type="voucher",
        target_id=str(voucher.id),
        summary=f"反审会计凭证：{voucher.voucher_no}",
        reason=reason_text,
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return voucher


def _get_or_create_auto_subject(
    db: Session, code: str
) -> AccountSubject:
    data = AUTO_SUBJECTS.get(code)
    if data is None:
        raise BizException(
            ErrorCode.INTERNAL_ERROR, f"未配置自动分录科目：{code}"
        )
    subject = finance_repo.get_subject_by_code(db, code)
    if subject is None:
        subject = AccountSubject(**data, is_enabled=True)
        db.add(subject)
        db.flush()
        return subject
    if not subject.is_enabled:
        raise BizException(
            ErrorCode.CONFLICT,
            f"会计科目已停用，无法生成自动分录：{subject.code} {subject.name}",
        )
    return subject


def get_active_voucher_by_reference(
    db: Session, reference: str
) -> Voucher | None:
    return finance_repo.get_active_voucher_by_reference(db, reference)


def generate_business_voucher(
    db: Session,
    *,
    reference: str,
    items: list[dict[str, Any]],
    staff: Staff,
    voucher_word: str = "记",
    business_date: datetime | None = None,
    source_type: str | None = None,
    source_id: str | None = None,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    """按业务参考号幂等生成凭证。

    同一参考号已有有效凭证时直接返回；作废后可重新生成新的凭证。
    """
    reference = _require_text(reference, "参考信息", 255)
    existing = finance_repo.get_active_voucher_by_reference(db, reference)
    if existing is not None:
        return existing

    normalized_items = _normalize_items(items)
    _validate_subjects(db, normalized_items)
    now = _now()
    make_serial = serial_generator or generate_serial
    voucher_no = make_serial(SerialPrefix.VOUCHER, dt=now)
    _ensure_voucher_no_available(db, voucher_no)
    total_debit, total_credit = _total_items(normalized_items)
    voucher = Voucher(
        voucher_word=voucher_word,
        voucher_no=voucher_no,
        business_date=business_date or now,
        record_date=now,
        reference=reference,
        total_debit=total_debit,
        total_credit=total_credit,
        status=STATUS_DRAFT,
        created_by=staff.id,
    )
    db.add(voucher)
    db.flush()
    for item in normalized_items:
        db.add(VoucherItem(voucher_id=voucher.id, **item))
    db.flush()
    write_audit(
        db,
        action="GENERATE_VOUCHER",
        target_type=source_type or "voucher",
        target_id=source_id or str(voucher.id),
        summary=f"业务单据生成会计分录：{reference}",
        detail={
            "voucher_id": voucher.id,
            "voucher_no": voucher.voucher_no,
            "total_debit": str(voucher.total_debit),
            "total_credit": str(voucher.total_credit),
        },
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return voucher


def create_prepay_voucher(
    db: Session,
    *,
    prepay_no: str,
    amount: Decimal,
    supplier_name: str | None,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    debit_subject = _get_or_create_auto_subject(db, "1123")
    credit_subject = _get_or_create_auto_subject(db, "1002")
    summary = f"采购预付款-{supplier_name or ''}".rstrip("-")
    return generate_business_voucher(
        db,
        reference=f"采购预付款申请:{prepay_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="purchase_prepay",
        source_id=prepay_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_stock_in_voucher(
    db: Session,
    *,
    stock_in_type: str,
    stock_in_no: str,
    amount: Decimal,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    normalized_type = str(stock_in_type).strip().upper()
    subject_codes = STOCK_IN_SUBJECT_MAP.get(normalized_type)
    if subject_codes is None:
        raise BizException(
            ErrorCode.BAD_REQUEST,
            f"入库类型不生成公司级会计凭证：{normalized_type}",
        )
    debit_subject = _get_or_create_auto_subject(db, subject_codes[0])
    credit_subject = _get_or_create_auto_subject(db, subject_codes[1])
    summary = f"{normalized_type}入库-{stock_in_no}"
    return generate_business_voucher(
        db,
        reference=f"采购入库单:{stock_in_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="stock_in",
        source_id=stock_in_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_purchase_stock_in_voucher(
    db: Session,
    *,
    stock_in_no: str,
    amount: Decimal,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    """兼容采购入库调用，统一委托给按类型生成器。"""
    return create_stock_in_voucher(
        db,
        stock_in_type="PURCHASE",
        stock_in_no=stock_in_no,
        amount=amount,
        staff=staff,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_payment_voucher(
    db: Session,
    *,
    payment_no: str,
    amount: Decimal,
    staff: Staff,
    use_prepay: bool = False,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    debit_subject = _get_or_create_auto_subject(db, "2202")
    credit_subject = _get_or_create_auto_subject(
        db, "1123" if use_prepay else "1002"
    )
    summary = f"付款单-{payment_no}"
    return generate_business_voucher(
        db,
        reference=f"付款单:{payment_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="payment",
        source_id=payment_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_receipt_voucher(
    db: Session,
    *,
    receipt_no: str,
    amount: Decimal,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    debit_subject = _get_or_create_auto_subject(db, "1002")
    credit_subject = _get_or_create_auto_subject(db, "1122")
    summary = f"收款单-{receipt_no}"
    return generate_business_voucher(
        db,
        reference=f"收款单:{receipt_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="receipt",
        source_id=receipt_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_expense_voucher(
    db: Session,
    *,
    expense_no: str,
    amount: Decimal,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    debit_subject = _get_or_create_auto_subject(db, "6601")
    credit_subject = _get_or_create_auto_subject(db, "1122")
    summary = f"费用单冲抵收款-{expense_no}"
    return generate_business_voucher(
        db,
        reference=f"费用单:{expense_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="expense",
        source_id=expense_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_income_voucher(
    db: Session,
    *,
    income_no: str,
    amount: Decimal,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    debit_subject = _get_or_create_auto_subject(db, "2202")
    credit_subject = _get_or_create_auto_subject(db, "1405")
    summary = f"供应商返利-{income_no}"
    return generate_business_voucher(
        db,
        reference=f"收入单:{income_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="income",
        source_id=income_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def create_settlement_voucher(
    db: Session,
    *,
    settlement_no: str,
    amount: Decimal,
    staff: Staff,
    serial_generator: Callable[..., str] | None = None,
    commit: bool = True,
) -> Voucher:
    debit_subject = _get_or_create_auto_subject(db, "1122")
    credit_subject = _get_or_create_auto_subject(db, "6001")
    summary = f"平台结算确认-{settlement_no}"
    return generate_business_voucher(
        db,
        reference=f"平台结算单:{settlement_no}",
        items=[
            {
                "subject_id": debit_subject.id,
                "summary": summary,
                "debit": amount,
                "credit": ZERO_MONEY,
            },
            {
                "subject_id": credit_subject.id,
                "summary": summary,
                "debit": ZERO_MONEY,
                "credit": amount,
            },
        ],
        staff=staff,
        source_type="settlement",
        source_id=settlement_no,
        serial_generator=serial_generator,
        commit=commit,
    )


def invalidate_voucher_by_reference(
    db: Session,
    *,
    reference: str,
    staff: Staff,
    reason: str,
    source_type: str | None = None,
    source_id: str | None = None,
    commit: bool = True,
) -> Voucher | None:
    voucher = finance_repo.get_active_voucher_by_reference(db, reference)
    if voucher is None:
        return None
    voucher.status = STATUS_INVALID
    write_audit(
        db,
        action="INVALIDATE_VOUCHER",
        target_type=source_type or "voucher",
        target_id=source_id or str(voucher.id),
        summary=f"业务单据弃审，凭证失效：{voucher.voucher_no}",
        reason=reason,
        detail={"reference": reference},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return voucher


def register_kingdee_transporter(transporter: KingdeeTransporter | None) -> None:
    """注册金蝶传输适配器；TASK-064 通过该入口接入真实实现。"""
    global _kingdee_transporter
    _kingdee_transporter = transporter


def build_kingdee_payload(db: Session, voucher: Voucher) -> dict:
    if voucher.status != STATUS_APPROVED:
        raise BizException(ErrorCode.CONFLICT, "仅已审核凭证允许传金蝶")
    items = finance_repo.list_voucher_items(db, voucher.id)
    if not items:
        raise BizException(ErrorCode.CONFLICT, "凭证没有分录，不能传金蝶")
    _ensure_balanced(items)
    _validate_subjects(db, items)
    return voucher_dict(db, voucher)


def send_voucher_to_kingdee(
    db: Session,
    *,
    voucher_id: int,
    staff: Staff,
    commit: bool = True,
) -> dict:
    voucher = get_voucher_or_404(db, voucher_id)
    payload = build_kingdee_payload(db, voucher)
    from app.integrations.kingdee import get_kingdee_adapter

    transporter = _kingdee_transporter or get_kingdee_adapter().send_voucher
    try:
        response = transporter(payload)
    except BizException:
        raise
    except Exception as exc:
        raise BizException(ErrorCode.CONFLICT, "金蝶凭证传输调用异常") from exc
    if not isinstance(response, dict) or response.get("success") is not True:
        raise BizException(ErrorCode.CONFLICT, "金蝶凭证传输失败")
    write_audit(
        db,
        action="TRANSMIT_KINGDEE",
        target_type="voucher",
        target_id=str(voucher.id),
        summary=f"会计凭证传输金蝶：{voucher.voucher_no}",
        detail={"response": response},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    _commit(db, commit)
    return {
        "voucher_id": voucher.id,
        "voucher_no": voucher.voucher_no,
        "payload": payload,
        "response": response,
    }
