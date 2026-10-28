"""当前用户站内消息查询、权限复核与游标工具。"""

from __future__ import annotations

import base64
import hashlib
import hmac
from collections.abc import Iterator
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models.notification import NotificationInbox, NotificationOutbox
from app.models.org import Staff
from app.repositories.rbac_repo import list_staff_role_codes
from app.services.notification_service import (
    APPROVAL_TASK_ASSIGNED,
    APPROVAL_TIMEOUT,
    NotificationContractError,
    _event_is_current,
    _recipient_can_view,
)

_BATCH_SIZE = 200
_CATEGORIES = {"all", "approval", "purchase"}


def encode_cursor(*, staff_id: int, company_id: int, inbox_id: int) -> str:
    """生成不可篡改且绑定员工/公司的游标。"""
    payload = f"1:{inbox_id}".encode("ascii")
    secret = get_settings().jwt_secret.encode("utf-8")
    scoped_payload = f"{company_id}:{staff_id}:".encode("ascii") + payload
    signature = hmac.new(secret, scoped_payload, hashlib.sha256).hexdigest()
    encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"{encoded}.{signature}"


def decode_cursor(value: str | None, *, staff_id: int, company_id: int) -> int:
    if value is None or value == "":
        return 0
    try:
        encoded, signature = value.split(".", maxsplit=1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        secret = get_settings().jwt_secret.encode("utf-8")
        scoped_payload = f"{company_id}:{staff_id}:".encode("ascii") + payload
        expected = hmac.new(secret, scoped_payload, hashlib.sha256).hexdigest()
        version, inbox_id = payload.decode("ascii").split(":")
        if (
            not hmac.compare_digest(signature, expected)
            or version != "1"
            or int(inbox_id) < 1
        ):
            raise ValueError
        return int(inbox_id)
    except (ValueError, UnicodeDecodeError, base64.binascii.Error):
        raise ValueError("消息游标无效或不属于当前用户") from None


def category_for(inbox: NotificationInbox) -> str:
    if inbox.event_code.startswith("approval."):
        return "approval"
    if inbox.target_type in {"purchase_plan", "purchase_order", "purchase_prepay", "purchase_return", "purchase_invoice"}:
        return "purchase"
    return "all"


def _matches_category(inbox: NotificationInbox, category: str) -> bool:
    return category == "all" or category_for(inbox) == category


def _notification_is_visible(
    db: Session,
    staff: Staff,
    inbox: NotificationInbox,
    event: NotificationOutbox,
) -> bool:
    if inbox.channel != "IN_APP" or inbox.company_id != staff.company_id:
        return False
    if inbox.recipient_staff_id != staff.id or event.company_id != staff.company_id:
        return False
    payload = event.payload_json
    doc_type = payload.get("doc_type")
    doc_id = payload.get("doc_id")
    if (
        not isinstance(doc_type, str)
        or isinstance(doc_id, bool)
        or not isinstance(doc_id, int)
        or doc_id < 1
        or inbox.target_type != doc_type
        or inbox.target_id != doc_id
    ):
        return False
    if event.event_code == APPROVAL_TASK_ASSIGNED and inbox.business_state != "OPEN":
        return False
    try:
        if not _event_is_current(db, event):
            return False
    except (NotificationContractError, KeyError, TypeError, ValueError):
        return False
    return _recipient_can_view(
        db,
        staff_id=staff.id,
        company_id=staff.company_id,
        doc_type=doc_type,
        doc_id=doc_id,
        require_approval=event.event_code in {APPROVAL_TASK_ASSIGNED, APPROVAL_TIMEOUT},
        event=event,
    )


def get_visible_notification(
    db: Session, staff: Staff, inbox_id: int
) -> NotificationInbox | None:
    row = db.execute(
        select(NotificationInbox, NotificationOutbox)
        .join(NotificationOutbox, NotificationOutbox.id == NotificationInbox.outbox_id)
        .where(
            NotificationInbox.id == inbox_id,
            NotificationInbox.company_id == staff.company_id,
            NotificationInbox.recipient_staff_id == staff.id,
            NotificationInbox.channel == "IN_APP",
        )
    ).one_or_none()
    if row is None:
        return None
    inbox, event = row
    return inbox if _notification_is_visible(db, staff, inbox, event) else None


def iter_visible_notifications(
    db: Session,
    staff: Staff,
    *,
    direction: str = "desc",
    cursor_id: int | None = None,
    category: str = "all",
    read_state: str | None = None,
) -> Iterator[tuple[NotificationInbox, NotificationOutbox]]:
    """按 Inbox ID 稳定扫描；游标范围始终由公司和接收人条件限定。"""
    if category not in _CATEGORIES or direction not in {"asc", "desc"}:
        return
    scanned_to = cursor_id
    while True:
        conditions = [
            NotificationInbox.company_id == staff.company_id,
            NotificationInbox.recipient_staff_id == staff.id,
            NotificationInbox.channel == "IN_APP",
        ]
        if read_state is not None:
            conditions.append(NotificationInbox.read_state == read_state)
        if scanned_to is not None:
            comparator = (
                NotificationInbox.id > scanned_to
                if direction == "asc"
                else NotificationInbox.id < scanned_to
            )
            conditions.append(comparator)
        ordering = (
            NotificationInbox.id.asc()
            if direction == "asc"
            else NotificationInbox.id.desc()
        )
        rows = db.execute(
            select(NotificationInbox, NotificationOutbox)
            .join(NotificationOutbox, NotificationOutbox.id == NotificationInbox.outbox_id)
            .where(*conditions)
            .order_by(ordering)
            .limit(_BATCH_SIZE)
        ).all()
        if not rows:
            return
        for inbox, event in rows:
            scanned_to = inbox.id
            if _matches_category(inbox, category) and _notification_is_visible(
                db, staff, inbox, event
            ):
                yield inbox, event
        if len(rows) < _BATCH_SIZE:
            return


def scan_stream_batch(
    db: Session,
    staff: Staff,
    *,
    after_id: int,
    category: str = "all",
    batch_size: int = _BATCH_SIZE,
) -> tuple[list[tuple[NotificationInbox, NotificationOutbox]], int, bool]:
    """SSE 按持久化 Inbox 增量恢复，并回传已扫描位置（含不可见行）。"""
    rows = db.execute(
        select(NotificationInbox, NotificationOutbox)
        .join(NotificationOutbox, NotificationOutbox.id == NotificationInbox.outbox_id)
        .where(
            NotificationInbox.company_id == staff.company_id,
            NotificationInbox.recipient_staff_id == staff.id,
            NotificationInbox.channel == "IN_APP",
            NotificationInbox.id > after_id,
        )
        .order_by(NotificationInbox.id.asc())
        .limit(batch_size)
    ).all()
    visible = [
        (inbox, event)
        for inbox, event in rows
        if _matches_category(inbox, category)
        and _notification_is_visible(db, staff, inbox, event)
    ]
    scanned_to = rows[-1][0].id if rows else after_id
    return visible, scanned_to, len(rows) == batch_size


def query_notifications_page(
    db: Session,
    staff: Staff,
    *,
    cursor_id: int,
    category: str = "all",
    read_state: str | None = None,
    page_size: int = 20,
) -> tuple[list[tuple[NotificationInbox, NotificationOutbox]], int | None, bool]:
    """有界倒序分页；游标绑定员工，公司范围和可见性逐条复核。"""
    if category not in _CATEGORIES or read_state not in {None, "READ", "UNREAD"}:
        raise ValueError("通知筛选条件无效")
    page_size = min(max(page_size, 1), 100)
    conditions = [
        NotificationInbox.company_id == staff.company_id,
        NotificationInbox.recipient_staff_id == staff.id,
        NotificationInbox.channel == "IN_APP",
    ]
    if cursor_id > 0:
        conditions.append(NotificationInbox.id < cursor_id)
    if read_state is not None:
        conditions.append(NotificationInbox.read_state == read_state)
    rows = db.execute(
        select(NotificationInbox, NotificationOutbox)
        .join(NotificationOutbox, NotificationOutbox.id == NotificationInbox.outbox_id)
        .where(*conditions)
        .order_by(NotificationInbox.id.desc())
        .limit(page_size + 1)
    ).all()
    visible = [
        (inbox, event)
        for inbox, event in rows
        if _matches_category(inbox, category)
        and _notification_is_visible(db, staff, inbox, event)
    ]
    has_more = len(rows) > page_size or len(visible) > page_size
    page = visible[:page_size]
    if not has_more:
        next_id = None
    elif len(visible) > page_size:
        next_id = page[-1][0].id
    else:
        # 即便该批记录均不可见，也推进到本批扫描末尾，避免永远重复扫描。
        next_id = rows[-1][0].id
    return page, next_id, has_more


def serialize_notification(
    inbox: NotificationInbox,
    *,
    staff_id: int,
    company_id: int,
) -> dict:
    return {
        "id": inbox.id,
        "event_code": inbox.event_code,
        "category": category_for(inbox),
        "title": inbox.title,
        "body": inbox.body,
        "target": {"type": inbox.target_type, "id": inbox.target_id},
        "read_state": inbox.read_state,
        "read_at": inbox.read_at,
        "business_state": inbox.business_state,
        "created_at": inbox.created_at,
        "cursor": encode_cursor(
            staff_id=staff_id, company_id=company_id, inbox_id=inbox.id
        ),
    }


def stream_identity_snapshot(db: Session, staff_id: int) -> tuple | None:
    staff = db.get(Staff, staff_id)
    if staff is None or not staff.is_active:
        return None
    roles = tuple(sorted(list_staff_role_codes(db, staff_id)))
    from app.models.rbac import DataScope

    scopes = tuple(
        sorted(
            (row.scope_type, "" if row.department_id is None else str(row.department_id))
            for row in db.scalars(
                select(DataScope).where(DataScope.staff_id == staff_id)
            ).all()
        )
    )
    return staff.company_id, roles, scopes


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
