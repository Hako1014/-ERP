from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from app.db.base import Base

from app.core.reminder_config import ReminderRule
from app.models.approval import ApprovalFlow, ApprovalRecord
from app.models.notification import NotificationInbox, NotificationOutbox, NotificationReminderScan
from app.models.org import Staff
from app.services import notification_reminder_service as reminders
from app.services import notification_service as notices
from app.services.notification_query_service import _notification_is_visible
from tests.test_notification_service import session_factory, _seed, _submit  # noqa: F401


NOW = datetime(2026, 10, 10, 2)


def rule(**changes):
    return ReminderRule(**{
        "key": "approval", "company_id": 1, "actor_staff_id": 2,
        "kind": "approval_timeout", "enabled": True, "sla_seconds": 120,
        "repeat_seconds": 3600, "active_from": (NOW - timedelta(hours=1)).replace(tzinfo=timezone.utc),
        **changes,
    })


def configure(monkeypatch, rules):
    monkeypatch.setattr(reminders, "get_settings", lambda: SimpleNamespace(notification_reminder_rules=rules))
    monkeypatch.setattr(notices, "_now", lambda: NOW)


def seed(factory, age=120):
    _seed(factory)
    _submit(factory)
    with factory() as db:
        record = db.scalar(select(ApprovalRecord).where(ApprovalRecord.action == "APPROVE"))
        record.created_at = NOW - timedelta(seconds=age)
        source = db.scalar(select(NotificationOutbox))
        source.occurred_at = record.created_at
        db.commit()


def count_reminders(factory):
    with factory() as db:
        return len(list(db.scalars(select(NotificationOutbox).where(NotificationOutbox.event_code == notices.APPROVAL_TIMEOUT))))


def test_disabled_rules_never_open_database(monkeypatch):
    configure(monkeypatch, [rule(enabled=False), rule(key="missing", sla_seconds=None),
        rule(key="purchase", kind="purchase_overdue"), rule(key="stock", kind="low_stock")])
    def forbidden():
        raise AssertionError("不允许访问数据库")
    result = reminders.scan_reminders(session_factory=forbidden, now=NOW)
    assert [item["reason"] for item in result] == ["RULE_DISABLED", "MISSING_CONFIRMED_THRESHOLDS",
        "MISSING_EXPECTED_ARRIVAL_FIELD", "MISSING_COMPANY_AND_RECIPIENT_RULE"]


@pytest.mark.parametrize("age,expected", [(119, 0), (120, 1), (121, 1), (3601, 0)])
def test_boundary_and_no_history(session_factory, monkeypatch, age, expected):
    configure(monkeypatch, [rule()])
    seed(session_factory, age)
    result = reminders.scan_reminders(session_factory=session_factory, now=NOW)
    assert result[0]["status"] == "DONE"
    assert count_reminders(session_factory) == expected


def test_repeated_scan_and_next_period(session_factory, monkeypatch):
    configure(monkeypatch, [rule()])
    seed(session_factory)
    reminders.scan_reminders(session_factory=session_factory, now=NOW)
    reminders.scan_reminders(session_factory=session_factory, now=NOW)
    assert count_reminders(session_factory) == 1
    reminders.scan_reminders(session_factory=session_factory, now=NOW + timedelta(hours=1))
    assert count_reminders(session_factory) == 2


def test_server_local_created_time_does_not_delay_utc_sla(session_factory, monkeypatch):
    configure(monkeypatch, [rule()])
    seed(session_factory)
    with session_factory() as db:
        record = db.scalar(select(ApprovalRecord).where(ApprovalRecord.action == "APPROVE"))
        record.created_at += timedelta(hours=8)
        db.commit()
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]["enqueued"] == 1
    notices.process_outbox_batch(session_factory=session_factory, now=NOW)
    with session_factory() as db:
        assert db.scalar(select(NotificationInbox.id).where(NotificationInbox.event_code == notices.APPROVAL_TIMEOUT)) is not None


@pytest.mark.parametrize("change", ["resolved", "disabled", "recipient", "permission"])
def test_delivery_rechecks_current_state(session_factory, monkeypatch, change):
    configure(monkeypatch, [rule()])
    seed(session_factory)
    reminders.scan_reminders(session_factory=session_factory, now=NOW)
    with session_factory() as db:
        if change == "resolved":
            db.scalar(select(ApprovalRecord).where(ApprovalRecord.action == "APPROVE")).status = "APPROVED"
        elif change == "recipient":
            db.scalar(select(ApprovalFlow)).approvers = '[{"step":1,"approver_ids":[3]}]'
        elif change == "permission":
            db.get(Staff, 2).is_active = False
        db.commit()
    if change == "disabled":
        configure(monkeypatch, [rule(enabled=False)])
    notices.process_outbox_batch(session_factory=session_factory, now=NOW)
    with session_factory() as db:
        assert db.scalar(select(NotificationInbox.id).where(NotificationInbox.event_code == notices.APPROVAL_TIMEOUT)) is None


def test_live_delivery_only_current_authorized_approver(session_factory, monkeypatch):
    configure(monkeypatch, [rule()])
    seed(session_factory)
    reminders.scan_reminders(session_factory=session_factory, now=NOW)
    notices.process_outbox_batch(session_factory=session_factory, now=NOW)
    with session_factory() as db:
        rows = list(db.scalars(select(NotificationInbox).where(NotificationInbox.event_code == notices.APPROVAL_TIMEOUT)))
        assert [row.recipient_staff_id for row in rows] == [2]
        inbox = rows[0]
        event = db.get(NotificationOutbox, inbox.outbox_id)
        staff = db.get(Staff, 2)
        assert _notification_is_visible(db, staff, inbox, event)
        record = db.scalar(select(ApprovalRecord).where(ApprovalRecord.action == "APPROVE"))
        assert record.status == "PENDING"  # 扫描和投递不办理业务。
        db.scalar(select(ApprovalFlow)).approvers = '[{"step":1,"approver_ids":[3]}]'
        db.flush()
        assert not _notification_is_visible(db, staff, inbox, event)


def test_lease_exclusion_and_expiry(session_factory, monkeypatch):
    configure(monkeypatch, [rule()])
    seed(session_factory)
    assert reminders._claim(session_factory, rule(), NOW)
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]["status"] == "BUSY"
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW + timedelta(seconds=301))[0]["status"] == "DONE"


def test_company_actor_isolation_and_failure_recovery(session_factory, monkeypatch):
    configure(monkeypatch, [rule()])
    seed(session_factory)
    with session_factory() as db:
        db.get(Staff, 2).is_active = False
        db.commit()
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]["status"] == "FAILED"
    with session_factory() as db:
        state = db.scalar(select(NotificationReminderScan))
        assert state.failure_code == "SCAN_FAILED" and state.lease_token is None
    with session_factory() as db:
        db.get(Staff, 2).is_active = True
        db.commit()
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]["status"] == "DONE"
    assert count_reminders(session_factory) == 1


def test_timezone_and_duplicate_configuration(monkeypatch):
    with pytest.raises(ValidationError):
        rule(active_from=NOW)
    with pytest.raises(ValidationError):
        rule(batch_limit=501)
    with pytest.raises(ValidationError):
        rule(timezone="Invalid/Timezone")
    a = rule()
    b = rule(active_from=a.active_from.astimezone(timezone(timedelta(hours=8))))
    assert notices._as_utc_naive(a.active_from) == notices._as_utc_naive(b.active_from)
    configure(monkeypatch, [a, a])
    with pytest.raises(ValueError, match="key 重复"):
        reminders.scan_reminders(now=NOW)


def test_company_and_actor_scope_deny_other_objects(session_factory, monkeypatch):
    seed(session_factory)
    # 公司 2 的规则不能消费公司 1 的事件，也不能使用公司 1 的身份。
    configure(monkeypatch, [rule(company_id=2)])
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]["status"] == "FAILED"
    configure(monkeypatch, [rule(actor_staff_id=3)])
    assert reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]["enqueued"] == 0
    assert count_reminders(session_factory) == 0


def test_pagination_caps_work_and_wraps(session_factory, monkeypatch):
    seed(session_factory)
    configure(monkeypatch, [rule(batch_limit=1)])
    first = reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]
    second = reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]
    third = reminders.scan_reminders(session_factory=session_factory, now=NOW)[0]
    assert first["scanned"] == 1 and second["scanned"] == 0 and third["scanned"] == 1
    assert count_reminders(session_factory) == 1


def test_simultaneous_claims_only_one_worker(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'leases.db').as_posix()}",
                           connect_args={"check_same_thread": False, "timeout": 10})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    seed(factory)
    barrier = Barrier(2)
    def claim():
        barrier.wait()
        return reminders._claim(factory, rule(), NOW)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: claim(), range(2)))
        assert sum(value is not None for value in claims) == 1
    finally:
        engine.dispose()
