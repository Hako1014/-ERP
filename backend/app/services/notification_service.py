"""通知事件 Outbox、权限复核、幂等收件箱与实时提示投递。"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import uuid4

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.audit import write_audit
from app.core.data_scope import (
    DATA_SCOPE_POLICY_KEY,
    clear_scope_policy,
    document_visible,
    initialize_scope_policy,
    require_scope_policy,
)
from app.core.errors import BizException, ErrorCode
from app.core.permissions import permissions_for_roles
from app.db.redis import get_redis
from app.db.session import SessionLocal
from app.models.approval import ApprovalRecord
from app.models.finance import Expense, Income, Payment, Receipt
from app.models.notification import (
    NotificationDeliveryAttempt,
    NotificationInbox,
    NotificationOutbox,
)
from app.models.order import Order
from app.models.org import Staff
from app.models.purchase import (
    PurchaseInvoice,
    PurchaseOrder,
    PurchasePlan,
    PurchasePrepay,
    PurchaseReturn,
)
from app.models.stock import (
    LocationAdjust,
    StockCheck,
    StockIn,
    StockOut,
    Transfer,
    TransferIn,
    TransferOut,
)
from app.repositories.rbac_repo import list_staff_role_codes


APPROVAL_TASK_ASSIGNED = "approval.task_assigned"
APPROVAL_TIMEOUT = "approval.timeout"
APPROVAL_APPROVED = "approval.approved"
APPROVAL_REJECTED = "approval.rejected"
APPROVAL_REOPENED = "approval.reopened"
PURCHASE_ORDER_ARRIVAL_CHANGED = "purchase_order.arrival_progress_changed"
SUPPORTED_EVENT_CODES = frozenset(
    {
        APPROVAL_TASK_ASSIGNED,
        APPROVAL_TIMEOUT,
        APPROVAL_APPROVED,
        APPROVAL_REJECTED,
        APPROVAL_REOPENED,
        PURCHASE_ORDER_ARRIVAL_CHANGED,
    }
)

OUTBOX_PENDING = "PENDING"
OUTBOX_PROCESSING = "PROCESSING"
OUTBOX_RETRY = "RETRY"
OUTBOX_PUBLISHED = "PUBLISHED"
OUTBOX_FAILED = "FAILED"
OUTBOX_SKIPPED = "SKIPPED"

MAX_ATTEMPTS = 8
BASE_RETRY_SECONDS = 10
MAX_RETRY_SECONDS = 3600
OUTBOX_LEASE_SECONDS = 300
DELIVERY_LEASE_SECONDS = 60

DOC_LABELS = {
    "purchase_plan": "采购计划",
    "purchase_order": "采购订单",
    "purchase_prepay": "采购预付款申请",
    "purchase_return": "采购退货单",
    "purchase_invoice": "采购发票",
    "stock_in": "采购入库单",
    "stock_out": "采购出库单",
    "transfer": "调拨单",
    "transfer_out": "调拨出库单",
    "transfer_in": "调拨入库单",
    "stock_check": "库存盘点单",
    "location_adjust": "库位调整单",
    "order": "销售订单",
    "expense": "费用单",
    "income": "收入单",
    "payment": "付款单",
    "receipt": "收款单",
}

DOC_MODELS = {
    "purchase_plan": PurchasePlan,
    "purchase_order": PurchaseOrder,
    "purchase_prepay": PurchasePrepay,
    "purchase_return": PurchaseReturn,
    "purchase_invoice": PurchaseInvoice,
    "stock_in": StockIn,
    "stock_out": StockOut,
    "transfer": Transfer,
    "transfer_out": TransferOut,
    "transfer_in": TransferIn,
    "stock_check": StockCheck,
    "location_adjust": LocationAdjust,
    "order": Order,
    "expense": Expense,
    "income": Income,
    "payment": Payment,
    "receipt": Receipt,
}

DOC_READ_PERMISSIONS = {
    "purchase_plan": ("module:purchase",),
    "purchase_order": ("module:purchase",),
    "purchase_prepay": ("module:purchase", "module:finance"),
    "purchase_return": ("module:purchase",),
    "purchase_invoice": ("module:purchase", "module:finance"),
    "stock_in": ("module:stock",),
    "stock_out": ("module:stock",),
    "transfer": ("module:stock",),
    "transfer_out": ("module:stock",),
    "transfer_in": ("module:stock",),
    "stock_check": ("module:stock",),
    "location_adjust": ("module:stock",),
    "order": ("module:order",),
    "expense": ("module:finance",),
    "income": ("module:finance",),
    "payment": ("module:finance",),
    "receipt": ("module:finance",),
}


class NotificationContractError(ValueError):
    """Outbox 内容违反注册事件契约，重试也不会修复。"""


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _as_utc_naive(value: datetime | None) -> datetime:
    value = value or _now()
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def _retry_delay(attempt_no: int) -> int:
    return min(BASE_RETRY_SECONDS * (2 ** max(0, attempt_no - 1)), MAX_RETRY_SECONDS)


def _recipient_ids(values: list[int | None] | tuple[int | None, ...]) -> list[int]:
    result: list[int] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            continue
        if value not in result:
            result.append(value)
    return result


def enqueue_event(
    db: Session,
    *,
    company_id: int,
    event_code: str,
    event_key: str,
    aggregate_type: str,
    aggregate_id: int,
    source_type: str,
    source_id: int,
    payload: dict[str, Any],
    recipient_staff_ids: list[int | None] | tuple[int | None, ...],
    occurred_at: datetime | None = None,
) -> NotificationOutbox:
    """在调用方当前业务事务中登记一个白名单事件，不提交事务。"""
    if event_code not in SUPPORTED_EVENT_CODES:
        raise ValueError(f"不支持的通知事件：{event_code}")
    if company_id < 1 or aggregate_id < 1 or source_id < 1:
        raise ValueError("通知事件缺少有效的公司或来源 ID")
    if not event_key or len(event_key) > 128:
        raise ValueError("通知事件键非法")

    existing = db.scalar(
        select(NotificationOutbox).where(
            NotificationOutbox.company_id == company_id,
            NotificationOutbox.event_key == event_key,
        )
    )
    if existing is not None:
        return existing

    safe_payload = dict(payload)
    safe_payload["recipient_staff_ids"] = _recipient_ids(recipient_staff_ids)
    event = NotificationOutbox(
        company_id=company_id,
        event_code=event_code,
        schema_version=1,
        event_key=event_key,
        aggregate_type=aggregate_type,
        aggregate_id=aggregate_id,
        source_type=source_type,
        source_id=source_id,
        occurred_at=_as_utc_naive(occurred_at),
        payload_json=safe_payload,
        status=OUTBOX_PENDING,
        attempt_count=0,
        available_at=_as_utc_naive(occurred_at),
    )
    db.add(event)
    db.flush()
    return event


def enqueue_approval_task(
    db: Session,
    *,
    company_id: int,
    doc_type: str,
    doc_id: int,
    doc_no: str | None,
    pending_record_id: int,
    step: int,
    phase: str,
    recipient_staff_ids: list[int] | tuple[int, ...],
) -> NotificationOutbox:
    return enqueue_event(
        db,
        company_id=company_id,
        event_code=APPROVAL_TASK_ASSIGNED,
        event_key=f"approval-record:{pending_record_id}:assigned",
        aggregate_type=doc_type,
        aggregate_id=doc_id,
        source_type="approval_record",
        source_id=pending_record_id,
        payload={
            "doc_type": doc_type,
            "doc_id": doc_id,
            "doc_no": doc_no,
            "approval_record_id": pending_record_id,
            "step": step,
            "phase": phase,
        },
        recipient_staff_ids=recipient_staff_ids,
    )


def enqueue_approval_result(
    db: Session,
    *,
    company_id: int,
    event_code: str,
    doc_type: str,
    doc_id: int,
    doc_no: str | None,
    terminal_record_id: int,
    submitter_id: int | None,
) -> NotificationOutbox:
    if event_code not in {APPROVAL_APPROVED, APPROVAL_REJECTED}:
        raise ValueError("审批终态事件代码非法")
    return enqueue_event(
        db,
        company_id=company_id,
        event_code=event_code,
        event_key=f"approval-record:{terminal_record_id}:{event_code.rsplit('.', 1)[-1]}",
        aggregate_type=doc_type,
        aggregate_id=doc_id,
        source_type="approval_record",
        source_id=terminal_record_id,
        payload={
            "doc_type": doc_type,
            "doc_id": doc_id,
            "doc_no": doc_no,
            "terminal_record_id": terminal_record_id,
        },
        recipient_staff_ids=[submitter_id],
    )


def enqueue_approval_reopened(
    db: Session,
    *,
    company_id: int,
    doc_type: str,
    doc_id: int,
    doc_no: str | None,
    unaudit_record_id: int,
    pending_record_id: int,
    step: int,
    submitter_id: int | None,
) -> NotificationOutbox:
    return enqueue_event(
        db,
        company_id=company_id,
        event_code=APPROVAL_REOPENED,
        event_key=f"approval-record:{unaudit_record_id}:reopened",
        aggregate_type=doc_type,
        aggregate_id=doc_id,
        source_type="approval_record",
        source_id=unaudit_record_id,
        payload={
            "doc_type": doc_type,
            "doc_id": doc_id,
            "doc_no": doc_no,
            "unaudit_record_id": unaudit_record_id,
            "pending_record_id": pending_record_id,
            "step": step,
        },
        recipient_staff_ids=[submitter_id],
    )


def enqueue_purchase_order_arrival(
    db: Session,
    *,
    company_id: int,
    stock_in_id: int,
    order: PurchaseOrder,
) -> NotificationOutbox:
    return enqueue_event(
        db,
        company_id=company_id,
        event_code=PURCHASE_ORDER_ARRIVAL_CHANGED,
        event_key=f"stock-in:{stock_in_id}:purchase-order-arrival",
        aggregate_type="purchase_order",
        aggregate_id=order.id,
        source_type="stock_in",
        source_id=stock_in_id,
        payload={
            "doc_type": "purchase_order",
            "doc_id": order.id,
            "doc_no": order.order_no,
            "stock_in_id": stock_in_id,
        },
        recipient_staff_ids=[order.purchaser_id, order.created_by],
    )


def resolve_approval_task_notifications(
    db: Session,
    *,
    approval_record_id: int,
    handler_staff_id: int | None = None,
    superseded: bool = False,
) -> int:
    """随审批事务关闭已投递的旧待办，不改 read_state。"""
    now = _now()
    base = update(NotificationInbox).where(
        NotificationInbox.source_type == "approval_record",
        NotificationInbox.source_id == approval_record_id,
        NotificationInbox.event_code == APPROVAL_TASK_ASSIGNED,
        NotificationInbox.business_state == "OPEN",
    )
    if superseded:
        result = db.execute(
            base.values(
                business_state="SUPERSEDED",
                business_state_at=now,
                updated_at=now,
            )
        )
        return int(result.rowcount or 0)

    if handler_staff_id is not None:
        handled = db.execute(
            base.where(NotificationInbox.recipient_staff_id == handler_staff_id).values(
                business_state="HANDLED",
                business_state_at=now,
                updated_at=now,
            )
        )
        resolved = db.execute(
            base.where(NotificationInbox.recipient_staff_id != handler_staff_id).values(
                business_state="RESOLVED",
                business_state_at=now,
                updated_at=now,
            )
        )
        return int(handled.rowcount or 0) + int(resolved.rowcount or 0)

    resolved = db.execute(
        base.values(
            business_state="RESOLVED",
            business_state_at=now,
            updated_at=now,
        )
    )
    return int(resolved.rowcount or 0)


def _approval_record(db: Session, record_id: Any) -> ApprovalRecord | None:
    if isinstance(record_id, bool) or not isinstance(record_id, int) or record_id < 1:
        return None
    return db.get(ApprovalRecord, record_id)


def _latest_pending_record_id(db: Session, doc_type: str, doc_id: int) -> int | None:
    return db.scalar(
        select(ApprovalRecord.id)
        .where(
            ApprovalRecord.doc_type == doc_type,
            ApprovalRecord.doc_id == doc_id,
            ApprovalRecord.action == "APPROVE",
            ApprovalRecord.status == "PENDING",
        )
        .order_by(ApprovalRecord.step.desc(), ApprovalRecord.id.desc())
        .limit(1)
    )


def _event_is_current(db: Session, event: NotificationOutbox) -> bool:
    payload = event.payload_json
    doc_type = payload.get("doc_type")
    doc_id = payload.get("doc_id")
    if not isinstance(doc_type, str) or isinstance(doc_id, bool) or not isinstance(doc_id, int):
        raise NotificationContractError("INVALID_DOCUMENT_REFERENCE")

    if event.event_code == APPROVAL_TIMEOUT:
        from app.services.notification_reminder_service import reminder_is_current

        return reminder_is_current(db, event)

    if event.event_code == APPROVAL_TASK_ASSIGNED:
        record = _approval_record(db, payload.get("approval_record_id"))
        return bool(
            record
            and record.doc_type == doc_type
            and record.doc_id == doc_id
            and record.action == "APPROVE"
            and record.status == "PENDING"
            and _latest_pending_record_id(db, doc_type, doc_id) == record.id
        )

    if event.event_code in {APPROVAL_APPROVED, APPROVAL_REJECTED}:
        record = _approval_record(db, payload.get("terminal_record_id"))
        wanted_status = "APPROVED" if event.event_code == APPROVAL_APPROVED else "REJECTED"
        return bool(
            record
            and record.doc_type == doc_type
            and record.doc_id == doc_id
            and record.status == wanted_status
            and record.action in {"APPROVE", "REJECT"}
        )

    if event.event_code == APPROVAL_REOPENED:
        record = _approval_record(db, payload.get("unaudit_record_id"))
        pending_id = payload.get("pending_record_id")
        pending = _approval_record(db, pending_id)
        return bool(
            record
            and record.doc_type == doc_type
            and record.doc_id == doc_id
            and record.action == "UNAUDIT"
            and record.status == "DONE"
            and pending
            and pending.status == "PENDING"
            and _latest_pending_record_id(db, doc_type, doc_id) == pending.id
        )

    if event.event_code == PURCHASE_ORDER_ARRIVAL_CHANGED:
        stock_in_id = payload.get("stock_in_id")
        if isinstance(stock_in_id, bool) or not isinstance(stock_in_id, int):
            raise NotificationContractError("INVALID_STOCK_IN_REFERENCE")
        stock_in = db.get(StockIn, stock_in_id)
        return bool(
            stock_in
            and stock_in.status == "APPROVED"
            and stock_in.purchase_order_id == doc_id
            and event.aggregate_type == "purchase_order"
            and event.aggregate_id == doc_id
        )

    raise NotificationContractError("UNREGISTERED_EVENT")


def _has_permission(codes: set[str], required: tuple[str, ...]) -> bool:
    return "*" in codes or any(code in codes for code in required)


def _recipient_can_view(
    db: Session,
    *,
    staff_id: int,
    company_id: int,
    doc_type: str,
    doc_id: int,
    require_approval: bool,
    event: NotificationOutbox | None = None,
) -> bool:
    if event is not None and event.event_code == APPROVAL_TIMEOUT:
        from app.services.notification_reminder_service import recipient_is_current

        if not recipient_is_current(db, event, staff_id):
            return False
    staff = db.get(Staff, staff_id)
    if staff is None or not staff.is_active or staff.company_id != company_id:
        return False
    model = DOC_MODELS.get(doc_type)
    module_permissions = DOC_READ_PERMISSIONS.get(doc_type)
    if model is None or module_permissions is None:
        return False

    roles = list_staff_role_codes(db, staff.id)
    permission_codes = permissions_for_roles(roles)
    if not _has_permission(permission_codes, module_permissions):
        return False
    if require_approval and not _has_permission(permission_codes, ("approval:approve",)):
        return False

    document = db.get(model, doc_id)
    if document is None:
        return False
    owner_id = getattr(document, "created_by", None)

    initialized_here = DATA_SCOPE_POLICY_KEY not in db.info
    try:
        if initialized_here:
            initialize_scope_policy(db, staff)
        else:
            require_scope_policy(db, staff_id=staff.id, company_id=company_id)
        return document_visible(db, owner_id, doc_type, doc_id)
    except (BizException, RuntimeError, ValueError):
        return False
    finally:
        if initialized_here:
            clear_scope_policy(db)


def _safe_doc_no(value: Any, doc_id: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return f"单据 #{doc_id}"
    normalized = re.sub(r"[\x00-\x1f\x7f]", "", value).strip()
    return normalized[:64] or f"单据 #{doc_id}"


def _render_message(event: NotificationOutbox) -> tuple[str, str, str, int, str]:
    payload = event.payload_json
    doc_type = payload["doc_type"]
    doc_id = payload["doc_id"]
    doc_no = _safe_doc_no(payload.get("doc_no"), doc_id)
    label = DOC_LABELS.get(doc_type, "业务单据")
    step = payload.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step < 1:
        step = 1

    if event.event_code == APPROVAL_TIMEOUT:
        return "审批待办已超时", f"{label} {doc_no} 已超过配置的处理时限，请处理。", doc_type, doc_id, "OPEN"
    if event.event_code == APPROVAL_TASK_ASSIGNED:
        phase = payload.get("phase")
        if phase == "SUBMITTED":
            body = f"{label} {doc_no} 已提交审批，请处理。"
        elif phase == "REOPENED":
            body = f"{label} {doc_no} 已退回第 {step} 环节重新处理，请处理。"
        else:
            body = f"{label} {doc_no} 已流转至第 {step} 环节，请处理。"
        return "有新的待审批单据", body, doc_type, doc_id, "OPEN"
    if event.event_code == APPROVAL_APPROVED:
        return "单据审批结果", f"{label} {doc_no} 已审批通过。", doc_type, doc_id, "N_A"
    if event.event_code == APPROVAL_REJECTED:
        return "单据审批结果", f"{label} {doc_no} 已被驳回。", doc_type, doc_id, "N_A"
    if event.event_code == APPROVAL_REOPENED:
        return (
            "单据审批已退回",
            f"{label} {doc_no} 已退回第 {step} 环节重新处理。",
            doc_type,
            doc_id,
            "N_A",
        )
    if event.event_code == PURCHASE_ORDER_ARRIVAL_CHANGED:
        return (
            "采购订单到货进度更新",
            f"采购订单 {doc_no} 的到货进度已更新，请查看单据。",
            "purchase_order",
            doc_id,
            "N_A",
        )
    raise NotificationContractError("UNREGISTERED_EVENT")


def _create_inbox_for_event(db: Session, event: NotificationOutbox) -> tuple[int, list[int]]:
    payload = event.payload_json
    recipients = payload.get("recipient_staff_ids")
    if not isinstance(recipients, list) or any(
        isinstance(item, bool) or not isinstance(item, int) or item < 1
        for item in recipients
    ):
        raise NotificationContractError("INVALID_RECIPIENT_SNAPSHOT")

    if not _event_is_current(db, event):
        event.status = OUTBOX_SKIPPED
        event.last_error_code = "SOURCE_STATE_STALE"
        event.lease_token = None
        event.lease_until = None
        return 0, []

    doc_type = payload["doc_type"]
    doc_id = payload["doc_id"]
    require_approval = event.event_code in {APPROVAL_TASK_ASSIGNED, APPROVAL_TIMEOUT}
    title, body, target_type, target_id, business_state = _render_message(event)
    created_ids: list[int] = []
    eligible_count = 0
    for recipient_id in _recipient_ids(recipients):
        if not _recipient_can_view(
            db,
            staff_id=recipient_id,
            company_id=event.company_id,
            doc_type=doc_type,
            doc_id=doc_id,
            require_approval=require_approval,
            event=event,
        ):
            continue
        eligible_count += 1
        existing_id = db.scalar(
            select(NotificationInbox.id).where(
                NotificationInbox.outbox_id == event.id,
                NotificationInbox.recipient_staff_id == recipient_id,
                NotificationInbox.channel == "IN_APP",
            )
        )
        if existing_id is not None:
            continue
        inbox = NotificationInbox(
            company_id=event.company_id,
            recipient_staff_id=recipient_id,
            outbox_id=event.id,
            channel="IN_APP",
            event_code=event.event_code,
            title=title,
            body=body,
            target_type=target_type,
            target_id=target_id,
            source_type=event.source_type,
            source_id=event.source_id,
            template_version=1,
            read_state="UNREAD",
            business_state=business_state,
        )
        db.add(inbox)
        db.flush()
        created_ids.append(inbox.id)

    if eligible_count:
        event.status = OUTBOX_PUBLISHED
        event.published_at = _now()
        event.last_error_code = None
    else:
        event.status = OUTBOX_SKIPPED
        event.last_error_code = "NO_ELIGIBLE_RECIPIENT"
    event.lease_token = None
    event.lease_until = None
    return eligible_count, created_ids


def _eligible_outbox_clause(now: datetime):
    return or_(
        and_(
            NotificationOutbox.status.in_([OUTBOX_PENDING, OUTBOX_RETRY]),
            NotificationOutbox.available_at <= now,
        ),
        and_(
            NotificationOutbox.status == OUTBOX_PROCESSING,
            NotificationOutbox.lease_until.is_not(None),
            NotificationOutbox.lease_until <= now,
        ),
    )


def _claim_outbox(
    session_factory: Callable[[], Session], *, limit: int, now: datetime,
    company_id: int | None = None,
) -> list[tuple[int, str]]:
    claims: list[tuple[int, str]] = []
    with session_factory() as db:
        eligible = _eligible_outbox_clause(now)
        if company_id is not None:
            eligible = and_(eligible, NotificationOutbox.company_id == company_id)
        db.execute(
            update(NotificationOutbox)
            .where(eligible, NotificationOutbox.attempt_count >= MAX_ATTEMPTS)
            .values(
                status=OUTBOX_FAILED,
                last_error_code="RETRY_EXHAUSTED",
                lease_token=None,
                lease_until=None,
                updated_at=now,
            )
        )
        candidate_ids = list(
            db.scalars(
                select(NotificationOutbox.id)
                .where(
                    eligible,
                    NotificationOutbox.attempt_count < MAX_ATTEMPTS,
                )
                .order_by(NotificationOutbox.id)
                .limit(limit)
            )
        )
        for event_id in candidate_ids:
            lease_token = str(uuid4())
            result = db.execute(
                update(NotificationOutbox)
                .where(
                    NotificationOutbox.id == event_id,
                    _eligible_outbox_clause(now),
                    NotificationOutbox.attempt_count < MAX_ATTEMPTS,
                )
                .values(
                    status=OUTBOX_PROCESSING,
                    lease_token=lease_token,
                    lease_until=now + timedelta(seconds=OUTBOX_LEASE_SECONDS),
                    attempt_count=NotificationOutbox.attempt_count + 1,
                    updated_at=now,
                )
            )
            if result.rowcount == 1:
                claims.append((event_id, lease_token))
        db.commit()
    return claims


def _record_outbox_failure(
    session_factory: Callable[[], Session],
    *,
    event_id: int,
    lease_token: str,
    now: datetime,
    error_code: str,
    permanent: bool = False,
) -> str:
    with session_factory() as db:
        event = db.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.id == event_id,
                NotificationOutbox.status == OUTBOX_PROCESSING,
                NotificationOutbox.lease_token == lease_token,
            )
        )
        if event is None:
            return "LEASE_LOST"
        if permanent or event.attempt_count >= MAX_ATTEMPTS:
            event.status = OUTBOX_FAILED
            event.last_error_code = error_code if permanent else "RETRY_EXHAUSTED"
            result = OUTBOX_FAILED
        else:
            event.status = OUTBOX_RETRY
            event.last_error_code = error_code
            event.available_at = now + timedelta(seconds=_retry_delay(event.attempt_count))
            result = OUTBOX_RETRY
        event.lease_token = None
        event.lease_until = None
        event.updated_at = now
        db.commit()
        return result


def _process_claimed_outbox(
    session_factory: Callable[[], Session],
    *,
    event_id: int,
    lease_token: str,
    now: datetime,
) -> tuple[str, int]:
    try:
        with session_factory() as db:
            event = db.scalar(
                select(NotificationOutbox).where(
                    NotificationOutbox.id == event_id,
                    NotificationOutbox.status == OUTBOX_PROCESSING,
                    NotificationOutbox.lease_token == lease_token,
                )
            )
            if event is None:
                return "lease_lost", 0
            if event.event_code not in SUPPORTED_EVENT_CODES or not isinstance(
                event.payload_json, dict
            ):
                raise NotificationContractError("INVALID_EVENT_ENVELOPE")
            _, created_ids = _create_inbox_for_event(db, event)
            final_status = event.status
            db.commit()
            return final_status.lower(), len(created_ids)
    except NotificationContractError as exc:
        result = _record_outbox_failure(
            session_factory,
            event_id=event_id,
            lease_token=lease_token,
            now=now,
            error_code=str(exc)[:64],
            permanent=True,
        )
        return result.lower(), 0
    except Exception:
        result = _record_outbox_failure(
            session_factory,
            event_id=event_id,
            lease_token=lease_token,
            now=now,
            error_code="PROCESSING_ERROR",
        )
        return result.lower(), 0


def process_outbox_batch(
    *,
    session_factory: Callable[[], Session] | None = None,
    limit: int = 100,
    now: datetime | None = None,
    company_id: int | None = None,
) -> dict[str, int]:
    """条件更新租约领取待办；不依赖 MySQL 8 的 SKIP LOCKED。"""
    factory = session_factory or SessionLocal
    current_time = _as_utc_naive(now)
    if company_id is not None and (isinstance(company_id, bool) or company_id < 1):
        raise ValueError("通知扫描公司 ID 无效")
    claims = _claim_outbox(factory, limit=max(1, min(limit, 500)), now=current_time, company_id=company_id)
    counts = {"claimed": len(claims), "published": 0, "skipped": 0, "retried": 0, "failed": 0, "inbox_created": 0}
    for event_id, lease_token in claims:
        status, inbox_count = _process_claimed_outbox(
            factory,
            event_id=event_id,
            lease_token=lease_token,
            now=current_time,
        )
        if status == OUTBOX_PUBLISHED.lower():
            counts["published"] += 1
        elif status == OUTBOX_SKIPPED.lower():
            counts["skipped"] += 1
        elif status == OUTBOX_RETRY.lower():
            counts["retried"] += 1
        elif status == OUTBOX_FAILED.lower():
            counts["failed"] += 1
        counts["inbox_created"] += inbox_count
    return counts


def _claim_realtime_attempt(
    session_factory: Callable[[], Session], *, inbox_id: int, now: datetime
) -> tuple[int, int, str] | None:
    with session_factory() as db:
        inbox = db.get(NotificationInbox, inbox_id)
        if inbox is None:
            return None
        latest = db.scalar(
            select(NotificationDeliveryAttempt)
            .where(
                NotificationDeliveryAttempt.inbox_id == inbox_id,
                NotificationDeliveryAttempt.channel == "REALTIME",
            )
            .order_by(NotificationDeliveryAttempt.attempt_no.desc())
            .limit(1)
        )
        if latest is not None:
            if latest.status in {"SENT_TO_CONNECTION", "NO_CONNECTION", "FAILED", "SKIPPED"}:
                return None
            if latest.status == "RETRY" and latest.available_at > now:
                return None
            if (
                latest.status == "PROCESSING"
                and latest.lease_until is not None
                and latest.lease_until > now
            ):
                return None
            if latest.status == "PROCESSING":
                latest.status = "FAILED"
                latest.failure_code = "DELIVERY_LEASE_EXPIRED"
                latest.completed_at = now
                latest.updated_at = now
            attempt_no = latest.attempt_no + 1
        else:
            attempt_no = 1

        if attempt_no > MAX_ATTEMPTS:
            db.commit()
            return None
        lease_token = str(uuid4())
        attempt = NotificationDeliveryAttempt(
            inbox_id=inbox_id,
            channel="REALTIME",
            attempt_no=attempt_no,
            status="PROCESSING",
            attempted_at=now,
            available_at=now,
            lease_until=now + timedelta(seconds=DELIVERY_LEASE_SECONDS),
            lease_token=lease_token,
        )
        db.add(attempt)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return attempt.id, attempt_no, lease_token


def _finish_realtime_attempt(
    session_factory: Callable[[], Session],
    *,
    attempt_id: int,
    lease_token: str,
    now: datetime,
    status: str,
    failure_code: str | None = None,
) -> None:
    with session_factory() as db:
        attempt = db.scalar(
            select(NotificationDeliveryAttempt).where(
                NotificationDeliveryAttempt.id == attempt_id,
                NotificationDeliveryAttempt.status == "PROCESSING",
                NotificationDeliveryAttempt.lease_token == lease_token,
            )
        )
        if attempt is None:
            return
        attempt.status = status
        attempt.failure_code = failure_code
        attempt.completed_at = now
        attempt.lease_token = None
        attempt.lease_until = None
        if status == "RETRY":
            attempt.available_at = now + timedelta(seconds=_retry_delay(attempt.attempt_no))
        attempt.updated_at = now
        db.commit()


def _publish_realtime_attempt(
    session_factory: Callable[[], Session],
    *,
    inbox_id: int,
    attempt_id: int,
    attempt_no: int,
    lease_token: str,
    now: datetime,
    redis_client: Any,
) -> str:
    skip_failure: str | None = None
    with session_factory() as db:
        inbox = db.get(NotificationInbox, inbox_id)
        event = db.get(NotificationOutbox, inbox.outbox_id) if inbox else None
        if inbox is None or event is None:
            skip_failure = "SOURCE_MISSING"
        else:
            payload = event.payload_json
            recipient_id = inbox.recipient_staff_id
            if not _event_is_current(db, event) or (
                event.event_code == APPROVAL_TASK_ASSIGNED
                and inbox.business_state != "OPEN"
            ):
                skip_failure = "SOURCE_STATE_STALE"
            elif not _recipient_can_view(
                db,
                staff_id=recipient_id,
                company_id=event.company_id,
                doc_type=payload.get("doc_type", ""),
                doc_id=payload.get("doc_id", 0),
                require_approval=event.event_code in {APPROVAL_TASK_ASSIGNED, APPROVAL_TIMEOUT},
                event=event,
            ):
                skip_failure = "PERMISSION_REVOKED"
            else:
                channel = f"erp:notifications:{event.company_id}:{recipient_id}"
                signal = json.dumps(
                    {"inbox_id": inbox.id, "event_code": inbox.event_code},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )

    if skip_failure is not None:
        _finish_realtime_attempt(
            session_factory,
            attempt_id=attempt_id,
            lease_token=lease_token,
            now=now,
            status="SKIPPED",
            failure_code=skip_failure,
        )
        return "skipped"

    try:
        subscriber_count = redis_client.publish(channel, signal)
        status = "SENT_TO_CONNECTION" if subscriber_count > 0 else "NO_CONNECTION"
        _finish_realtime_attempt(
            session_factory,
            attempt_id=attempt_id,
            lease_token=lease_token,
            now=now,
            status=status,
        )
        return "sent" if subscriber_count > 0 else "no_connection"
    except Exception:
        status = "RETRY" if attempt_no < MAX_ATTEMPTS else "FAILED"
        _finish_realtime_attempt(
            session_factory,
            attempt_id=attempt_id,
            lease_token=lease_token,
            now=now,
            status=status,
            failure_code="REALTIME_PUBLISH_FAILED",
        )
        return "retried" if status == "RETRY" else "failed"


def dispatch_realtime_batch(
    *,
    session_factory: Callable[[], Session] | None = None,
    limit: int = 100,
    now: datetime | None = None,
    redis_client: Any | None = None,
    company_id: int | None = None,
) -> dict[str, int]:
    """向 Redis Pub/Sub 发布仅含 Inbox ID 的提示；Inbox 不依赖实时通道。"""
    factory = session_factory or SessionLocal
    if company_id is not None and (isinstance(company_id, bool) or company_id < 1):
        raise ValueError("通知扫描公司 ID 无效")
    current_time = _as_utc_naive(now)
    with factory() as db:
        latest_attempt_no = (
            select(func.max(NotificationDeliveryAttempt.attempt_no))
            .where(
                NotificationDeliveryAttempt.inbox_id == NotificationInbox.id,
                NotificationDeliveryAttempt.channel == "REALTIME",
            )
            .correlate(NotificationInbox)
            .scalar_subquery()
        )
        latest_status = (
            select(NotificationDeliveryAttempt.status)
            .where(
                NotificationDeliveryAttempt.inbox_id == NotificationInbox.id,
                NotificationDeliveryAttempt.channel == "REALTIME",
                NotificationDeliveryAttempt.attempt_no == latest_attempt_no,
            )
            .correlate(NotificationInbox)
            .limit(1)
            .scalar_subquery()
        )
        latest_available_at = (
            select(NotificationDeliveryAttempt.available_at)
            .where(
                NotificationDeliveryAttempt.inbox_id == NotificationInbox.id,
                NotificationDeliveryAttempt.channel == "REALTIME",
                NotificationDeliveryAttempt.attempt_no == latest_attempt_no,
            )
            .correlate(NotificationInbox)
            .limit(1)
            .scalar_subquery()
        )
        latest_lease_until = (
            select(NotificationDeliveryAttempt.lease_until)
            .where(
                NotificationDeliveryAttempt.inbox_id == NotificationInbox.id,
                NotificationDeliveryAttempt.channel == "REALTIME",
                NotificationDeliveryAttempt.attempt_no == latest_attempt_no,
            )
            .correlate(NotificationInbox)
            .limit(1)
            .scalar_subquery()
        )
        delivery_due = or_(
            latest_attempt_no.is_(None),
            and_(latest_status == "RETRY", latest_available_at <= current_time),
            and_(
                latest_status == "PROCESSING",
                or_(latest_lease_until.is_(None), latest_lease_until <= current_time),
            ),
        )
        inbox_ids = list(
            db.scalars(
                select(NotificationInbox.id)
                .join(NotificationOutbox, NotificationOutbox.id == NotificationInbox.outbox_id)
                .where(
                    NotificationOutbox.status == OUTBOX_PUBLISHED,
                    NotificationInbox.company_id == company_id if company_id is not None else True,
                    delivery_due,
                )
                .order_by(NotificationInbox.id)
                .limit(max(1, min(limit, 500)))
            )
        )

    counts = {"claimed": 0, "sent": 0, "no_connection": 0, "retried": 0, "failed": 0, "skipped": 0}
    client = redis_client
    for inbox_id in inbox_ids:
        claim = _claim_realtime_attempt(factory, inbox_id=inbox_id, now=current_time)
        if claim is None:
            continue
        counts["claimed"] += 1
        if client is None:
            client = get_redis()
        attempt_id, attempt_no, lease_token = claim
        result = _publish_realtime_attempt(
            factory,
            inbox_id=inbox_id,
            attempt_id=attempt_id,
            attempt_no=attempt_no,
            lease_token=lease_token,
            now=current_time,
            redis_client=client,
        )
        counts[result] += 1
    return counts


def requeue_failed_outbox(
    db: Session,
    *,
    outbox_id: int,
    staff: Staff,
    commit: bool = True,
) -> NotificationOutbox:
    """有 notification:manage 权限者可审计后重投；不改变事件键/收件人。"""
    roles = list_staff_role_codes(db, staff.id)
    permissions = permissions_for_roles(roles)
    if not staff.is_active or ("*" not in permissions and "notification:manage" not in permissions):
        raise BizException(ErrorCode.FORBIDDEN, "无权重投失败通知")
    event = db.get(NotificationOutbox, outbox_id)
    if event is None or event.company_id != staff.company_id:
        raise BizException(ErrorCode.NOT_FOUND, "通知事件不存在")
    if event.status != OUTBOX_FAILED:
        raise BizException(ErrorCode.CONFLICT, "仅可重投失败状态的通知事件")

    previous_attempts = event.attempt_count
    event.status = OUTBOX_PENDING
    event.attempt_count = 0
    event.available_at = _now()
    event.lease_token = None
    event.lease_until = None
    event.published_at = None
    event.last_error_code = None
    write_audit(
        db,
        action="RETRY",
        target_type="notification_outbox",
        target_id=str(event.id),
        summary=f"受控重投通知事件：{event.event_code}",
        detail={"event_code": event.event_code, "previous_attempt_count": previous_attempts},
        actor_id=staff.id,
        actor_name=staff.name,
    )
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return event
