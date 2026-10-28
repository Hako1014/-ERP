"""受控扫描：只使用公司内已经登记的审批待办事件，不补发旧业务。"""

from datetime import datetime, timedelta
import logging
from uuid import uuid4
from collections.abc import Callable

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.data_scope import clear_scope_policy, initialize_scope_policy
from app.core.reminder_config import ReminderRule
from app.core.errors import BizException
from app.db.session import SessionLocal
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.notification import NotificationOutbox, NotificationReminderScan
from app.models.org import Staff
from app.services import notification_service as notices
from app.services.approval_service import steps_of

logger = logging.getLogger(__name__)
SessionFactory = Callable[[], Session]


def current_approvers(db: Session, record: ApprovalRecord) -> list[int]:
    flow = db.get(ApprovalFlow, record.flow_id) if record.flow_id else None
    if flow is None or flow.doc_type != record.doc_type:
        return []
    try:
        return next((list(step.approver_ids) for step in steps_of(flow) if step.step == record.step), [])
    except (BizException, ValueError, TypeError):
        return []


def current_rule(version: str) -> ReminderRule | None:
    return next((rule for rule in get_settings().notification_reminder_rules
                 if rule.fingerprint() == version and rule.disabled_reason() is None), None)


def reminder_is_current(db: Session, event: NotificationOutbox) -> bool:
    rule = current_rule(event.payload_json.get("rule_version", ""))
    if rule is None or rule.company_id != event.company_id:
        return False
    record = db.get(ApprovalRecord, event.source_id)
    source_id = event.payload_json.get("task_outbox_id")
    if isinstance(source_id, bool) or not isinstance(source_id, int) or source_id < 1:
        return False
    source = db.get(NotificationOutbox, source_id)
    return bool(
        record and record.action == "APPROVE" and record.status == "PENDING"
        and record.doc_type == event.payload_json.get("doc_type")
        and record.doc_id == event.payload_json.get("doc_id")
        and notices._latest_pending_record_id(db, record.doc_type, record.doc_id) == record.id
        and source and source.company_id == rule.company_id
        and source.event_code == notices.APPROVAL_TASK_ASSIGNED and source.source_id == record.id
        and source.occurred_at >= notices._as_utc_naive(rule.active_from)
        and source.occurred_at + timedelta(seconds=rule.sla_seconds) <= notices._now()
    )


def recipient_is_current(db: Session, event: NotificationOutbox, staff_id: int) -> bool:
    if event.event_code != notices.APPROVAL_TIMEOUT:
        return True
    record = db.get(ApprovalRecord, event.source_id)
    return bool(record and staff_id in current_approvers(db, record))


def _claim(factory: SessionFactory, rule: ReminderRule, now: datetime) -> tuple[int, str] | None:
    version = rule.fingerprint()
    with factory() as db:
        row = db.scalar(select(NotificationReminderScan).where(
            NotificationReminderScan.company_id == rule.company_id,
            NotificationReminderScan.rule_version == version,
        ))
        if row is None:
            try:
                with db.begin_nested():
                    row = NotificationReminderScan(company_id=rule.company_id, rule_version=version)
                    db.add(row)
                    db.flush()
            except IntegrityError:
                row = db.scalar(select(NotificationReminderScan).where(
                    NotificationReminderScan.company_id == rule.company_id,
                    NotificationReminderScan.rule_version == version,
                ))
        token = str(uuid4())
        result = db.execute(update(NotificationReminderScan).where(
            NotificationReminderScan.id == row.id,
            or_(NotificationReminderScan.lease_until.is_(None), NotificationReminderScan.lease_until <= now),
        ).values(lease_token=token, lease_until=now + timedelta(seconds=300), status="RUNNING"))
        row_id = row.id
        db.commit()
        return (row_id, token) if result.rowcount else None


def _scan(factory: SessionFactory, rule: ReminderRule, now: datetime, claim: tuple[int, str]) -> dict:
    row_id, token = claim
    with factory() as db:
        state = db.get(NotificationReminderScan, row_id)
        actor = db.get(Staff, rule.actor_staff_id)
        if actor is None or not actor.is_active or actor.company_id != rule.company_id:
            raise ValueError("INVALID_SYSTEM_ACTOR")
        initialize_scope_policy(db, actor)
        start = notices._as_utc_naive(rule.active_from)
        sources = list(db.scalars(select(NotificationOutbox).where(
            NotificationOutbox.company_id == rule.company_id,
            NotificationOutbox.event_code == notices.APPROVAL_TASK_ASSIGNED,
            NotificationOutbox.id > state.cursor_id,
            NotificationOutbox.occurred_at >= start,
        ).order_by(NotificationOutbox.id).limit(rule.batch_limit)))
        created = 0
        for source in sources:
            if not notices._event_is_current(db, source):
                continue
            record = db.get(ApprovalRecord, source.payload_json["approval_record_id"])
            # occurred_at 由应用写入 UTC；MySQL 的 CURRENT_TIMESTAMP 可能随会话时区变化。
            if source.occurred_at + timedelta(seconds=rule.sla_seconds) > now:
                continue
            if not notices._recipient_can_view(db, staff_id=actor.id, company_id=rule.company_id,
                    doc_type=record.doc_type, doc_id=record.doc_id, require_approval=True):
                continue
            recipients = current_approvers(db, record)
            if not recipients:
                continue
            bucket = int((now - start).total_seconds()) // rule.repeat_seconds
            key = f"reminder:{rule.fingerprint()}:{record.id}:{bucket}"
            if db.scalar(select(NotificationOutbox.id).where(
                NotificationOutbox.company_id == rule.company_id, NotificationOutbox.event_key == key,
            )) is not None:
                continue
            notices.enqueue_event(db, company_id=rule.company_id, event_code=notices.APPROVAL_TIMEOUT,
                event_key=key, aggregate_type=record.doc_type, aggregate_id=record.doc_id,
                source_type="approval_record", source_id=record.id,
                payload={"doc_type": record.doc_type, "doc_id": record.doc_id, "doc_no": record.doc_no,
                         "rule_version": rule.fingerprint(), "approval_record_id": record.id,
                         "task_outbox_id": source.id},
                recipient_staff_ids=recipients, occurred_at=now)
            created += 1
        clear_scope_policy(db)
        result = db.execute(update(NotificationReminderScan).where(
            NotificationReminderScan.id == row_id, NotificationReminderScan.lease_token == token,
        ).values(cursor_id=sources[-1].id if len(sources) == rule.batch_limit else 0,
                 lease_token=None, lease_until=None, status="DONE", last_run_at=now,
                 scanned_count=len(sources), enqueued_count=created, failure_code=None))
        if not result.rowcount:
            db.rollback()
            return {"status": "LEASE_LOST", "scanned": 0, "enqueued": 0}
        db.commit()
        return {"status": "DONE", "scanned": len(sources), "enqueued": created}


def scan_reminders(*, session_factory: SessionFactory | None = None, now: datetime | None = None) -> list[dict]:
    factory = session_factory or SessionLocal
    now = notices._as_utc_naive(now)
    results = []
    rules = get_settings().notification_reminder_rules
    identities = [(rule.company_id, rule.key) for rule in rules]
    if len(set(identities)) != len(identities):
        raise ValueError("同公司提醒规则 key 重复")
    for rule in rules:
        reason = rule.disabled_reason()
        if reason or now < notices._as_utc_naive(rule.active_from):
            results.append({"rule": rule.key, "status": "DISABLED", "reason": reason or "NOT_ACTIVE_YET"})
            continue
        claim = None
        try:
            claim = _claim(factory, rule, now)
            result = _scan(factory, rule, now, claim) if claim else {"status": "BUSY"}
        except Exception:
            logger.exception("提醒扫描失败：company=%s rule=%s", rule.company_id, rule.key)
            if claim:
                with factory() as db:
                    db.execute(update(NotificationReminderScan).where(
                        NotificationReminderScan.id == claim[0], NotificationReminderScan.lease_token == claim[1],
                    ).values(status="FAILED", failure_code="SCAN_FAILED", last_run_at=now,
                             lease_token=None, lease_until=None))
                    db.commit()
            result = {"status": "FAILED", "reason": "SCAN_FAILED"}
        results.append({"rule": rule.key, **result})
    return results
