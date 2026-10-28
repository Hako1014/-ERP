from datetime import timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.core.errors import BizException, ErrorCode
from app.models.approval import ApprovalRecord
from app.models.audit_log import AuditLog
from app.models.notification import (
    NotificationDeliveryAttempt,
    NotificationInbox,
    NotificationOutbox,
)
from app.models.org import Company, Staff
from app.models.purchase import PurchaseOrder, PurchasePlan
from app.models.rbac import Role, StaffRole
from app.models.stock import StockIn
from app.services import approval_service, notification_service


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    yield factory
    engine.dispose()


def _seed(session_factory, *, approvers=None, third_role="AFTER_SALE"):
    with session_factory() as db:
        db.add(Company(id=1, full_name="通知测试公司"))
        staffs = [
            Staff(id=1, company_id=1, name="制单人", phone="13800000001"),
            Staff(id=2, company_id=1, name="审批人甲", phone="13800000002"),
            Staff(id=3, company_id=1, name="无采购权限", phone="13800000003"),
        ]
        roles = [
            Role(id=1, company_id=1, code="PURCHASER", name="采购员"),
            Role(id=2, company_id=1, code="VP", name="副总经理"),
            Role(id=3, company_id=1, code=third_role, name="角色三"),
        ]
        db.add_all(staffs + roles)
        db.flush()
        db.add_all(
            [
                StaffRole(staff_id=1, role_id=1),
                StaffRole(staff_id=2, role_id=2),
                StaffRole(staff_id=3, role_id=3),
            ]
        )
        db.add(
            PurchasePlan(
                id=100,
                plan_no="CGJH-TEST-100",
                supplier_id=900,
                created_by=1,
            )
        )
        db.commit()
        flow = approval_service.create_flow(
            db,
            name="通知验收流程",
            doc_type="purchase_plan",
            approvers=approvers or [
                {"step": 1, "name": "审批", "approver_ids": [2, 3]}
            ],
            actor=staffs[0],
        )
        return flow.id


def _submit(session_factory, *, commit=True):
    with session_factory() as db:
        return approval_service.submit(
            db,
            doc_type="purchase_plan",
            doc_id=100,
            staff=db.get(Staff, 1),
            doc_no="CGJH-TEST-100",
            comment="该审批意见不得进入通知正文",
            commit=commit,
        )


def test_business_rollback_also_rolls_back_outbox(session_factory):
    _seed(session_factory)

    with session_factory() as db:
        approval_service.submit(
            db,
            doc_type="purchase_plan",
            doc_id=100,
            staff=db.get(Staff, 1),
            doc_no="CGJH-TEST-100",
            commit=False,
        )
        assert db.scalar(select(NotificationOutbox.id)) is not None
        db.rollback()

    with session_factory() as db:
        assert db.scalar(select(NotificationOutbox.id)) is None
        assert db.scalar(select(ApprovalRecord.id)) is None


def test_outbox_delivery_is_idempotent_and_permission_filtered(session_factory):
    _seed(session_factory)
    _submit(session_factory)

    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        assert event.payload_json["recipient_staff_ids"] == [2, 3]
        duplicate = notification_service.enqueue_event(
            db,
            company_id=event.company_id,
            event_code=event.event_code,
            event_key=event.event_key,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            source_type=event.source_type,
            source_id=event.source_id,
            payload=event.payload_json,
            recipient_staff_ids=[2, 3],
        )
        assert duplicate.id == event.id

    first = notification_service.process_outbox_batch(session_factory=session_factory)
    second = notification_service.process_outbox_batch(session_factory=session_factory)
    assert first["published"] == 1
    assert first["inbox_created"] == 1
    assert second["claimed"] == 0

    with session_factory() as db:
        inboxes = list(db.scalars(select(NotificationInbox)))
        assert [inbox.recipient_staff_id for inbox in inboxes] == [2]
        assert "不得进入通知正文" not in inboxes[0].body
        assert inboxes[0].business_state == "OPEN"
        assert inboxes[0].read_state == "UNREAD"


def test_stale_approval_task_is_skipped(session_factory):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)
    with session_factory() as db:
        pending = db.scalar(
            select(ApprovalRecord).where(ApprovalRecord.status == "PENDING")
        )
        pending.status = "APPROVED"
        pending.approver_id = 2
        db.commit()

    counts = notification_service.process_outbox_batch(session_factory=session_factory)
    assert counts["skipped"] == 1
    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        assert event.status == "SKIPPED"
        assert event.last_error_code == "SOURCE_STATE_STALE"
        assert db.scalar(select(NotificationInbox.id)) is None


@pytest.mark.parametrize("change", ["inactive", "cross_company"])
def test_inactive_or_cross_company_recipient_is_skipped(session_factory, change):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)
    with session_factory() as db:
        recipient = db.get(Staff, 2)
        if change == "inactive":
            recipient.is_active = False
        else:
            db.add(Company(id=2, full_name="另一家公司"))
            recipient.company_id = 2
        db.commit()

    counts = notification_service.process_outbox_batch(session_factory=session_factory)
    assert counts["skipped"] == 1
    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        assert event.status == "SKIPPED"
        assert event.last_error_code == "NO_ELIGIBLE_RECIPIENT"
        assert db.scalar(select(NotificationInbox.id)) is None


def test_flow_change_does_not_redirect_event_time_recipient_snapshot(session_factory):
    flow_id = _seed(session_factory, approvers=[2])
    _submit(session_factory)
    with session_factory() as db:
        approval_service.update_flow(
            db,
            flow_id=flow_id,
            approvers=[3],
            actor=db.get(Staff, 1),
        )

    counts = notification_service.process_outbox_batch(session_factory=session_factory)
    assert counts["skipped"] == 1
    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        assert event.payload_json["recipient_staff_ids"] == [2]
        assert list(db.scalars(select(NotificationInbox))) == []


def test_expired_outbox_lease_is_reclaimed_but_live_lease_is_not(session_factory):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)
    with session_factory() as db:
        due_at = db.scalar(select(NotificationOutbox.available_at))

    first = notification_service._claim_outbox(
        session_factory,
        limit=1,
        now=due_at,
    )
    assert len(first) == 1
    assert notification_service._claim_outbox(
        session_factory,
        limit=1,
        now=due_at + timedelta(seconds=1),
    ) == []
    reclaimed = notification_service._claim_outbox(
        session_factory,
        limit=1,
        now=due_at + timedelta(seconds=notification_service.OUTBOX_LEASE_SECONDS + 1),
    )
    assert len(reclaimed) == 1
    assert reclaimed[0][1] != first[0][1]
    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        assert event.attempt_count == 2


def test_transient_outbox_failure_retries_then_exhausts(session_factory, monkeypatch):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)

    def fail_processing(*args, **kwargs):
        raise RuntimeError("diagnostic content is not persisted")

    monkeypatch.setattr(notification_service, "_create_inbox_for_event", fail_processing)
    with session_factory() as db:
        due_at = db.scalar(select(NotificationOutbox.available_at))

    for attempt_no in range(1, notification_service.MAX_ATTEMPTS + 1):
        counts = notification_service.process_outbox_batch(
            session_factory=session_factory,
            now=due_at,
        )
        with session_factory() as db:
            event = db.scalar(select(NotificationOutbox))
            assert event.attempt_count == attempt_no
            if attempt_no < notification_service.MAX_ATTEMPTS:
                assert counts["retried"] == 1
                assert event.status == "RETRY"
                assert event.last_error_code == "PROCESSING_ERROR"
                due_at = event.available_at
            else:
                assert counts["failed"] == 1
                assert event.status == "FAILED"
                assert event.last_error_code == "RETRY_EXHAUSTED"


def test_no_realtime_subscriber_does_not_affect_persisted_inbox(session_factory):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)
    notification_service.process_outbox_batch(session_factory=session_factory)

    class NoSubscribers:
        def publish(self, channel, message):
            assert channel == "erp:notifications:1:2"
            assert isinstance(message, str)
            assert "inbox_id" in message
            return 0

    counts = notification_service.dispatch_realtime_batch(
        session_factory=session_factory,
        redis_client=NoSubscribers(),
    )
    assert counts["no_connection"] == 1
    with session_factory() as db:
        inbox = db.scalar(select(NotificationInbox))
        attempt = db.scalar(select(NotificationDeliveryAttempt))
        assert inbox is not None
        assert inbox.read_state == "UNREAD"
        assert attempt.status == "NO_CONNECTION"


def test_realtime_failure_is_retried_and_can_recover(session_factory):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)
    notification_service.process_outbox_batch(session_factory=session_factory)

    class RedisUnavailable:
        def publish(self, channel, message):
            raise ConnectionError("do not persist exception text")

    now = notification_service._now()
    failed = notification_service.dispatch_realtime_batch(
        session_factory=session_factory,
        redis_client=RedisUnavailable(),
        now=now,
    )
    assert failed["retried"] == 1
    with session_factory() as db:
        attempt = db.scalar(select(NotificationDeliveryAttempt))
        assert attempt.status == "RETRY"
        assert attempt.failure_code == "REALTIME_PUBLISH_FAILED"
        due_at = attempt.available_at

    class Connected:
        def publish(self, channel, message):
            return 1

    recovered = notification_service.dispatch_realtime_batch(
        session_factory=session_factory,
        redis_client=Connected(),
        now=due_at + timedelta(seconds=1),
    )
    assert recovered["sent"] == 1
    with session_factory() as db:
        attempts = list(
            db.scalars(
                select(NotificationDeliveryAttempt).order_by(
                    NotificationDeliveryAttempt.attempt_no
                )
            )
        )
        assert [attempt.status for attempt in attempts] == [
            "RETRY",
            "SENT_TO_CONNECTION",
        ]


def test_approval_closes_same_step_inboxes_and_notifies_submitter(session_factory):
    _seed(session_factory, third_role="QUALITY")
    _submit(session_factory)
    notification_service.process_outbox_batch(session_factory=session_factory)

    with session_factory() as db:
        approval_service.approve(
            db,
            doc_type="purchase_plan",
            doc_id=100,
            staff=db.get(Staff, 2),
        )
        inboxes = list(db.scalars(select(NotificationInbox).order_by(NotificationInbox.id)))
        states = {inbox.recipient_staff_id: inbox.business_state for inbox in inboxes}
        assert states == {2: "HANDLED", 3: "RESOLVED"}
        assert db.scalar(
            select(NotificationOutbox).where(
                NotificationOutbox.event_code == notification_service.APPROVAL_APPROVED
            )
        ) is not None

    result = notification_service.process_outbox_batch(session_factory=session_factory)
    assert result["published"] == 1
    with session_factory() as db:
        inboxes = list(db.scalars(select(NotificationInbox).order_by(NotificationInbox.id)))
        assert len(inboxes) == 3
        assert inboxes[0].recipient_staff_id == 2
        assert inboxes[0].business_state == "HANDLED"
        assert inboxes[1].recipient_staff_id == 3
        assert inboxes[1].business_state == "RESOLVED"
        assert inboxes[2].recipient_staff_id == 1
        assert inboxes[2].event_code == notification_service.APPROVAL_APPROVED


def test_controlled_retry_requires_admin_permission_and_is_audited(session_factory):
    _seed(session_factory, approvers=[2])
    _submit(session_factory)
    with session_factory() as db:
        event = db.scalar(select(NotificationOutbox))
        event.status = "FAILED"
        event.attempt_count = notification_service.MAX_ATTEMPTS
        db.commit()

        with pytest.raises(BizException) as denied:
            notification_service.requeue_failed_outbox(
                db,
                outbox_id=event.id,
                staff=db.get(Staff, 2),
            )
        assert denied.value.code == ErrorCode.FORBIDDEN

        admin_role = Role(id=4, company_id=1, code="ADMIN", name="管理员")
        db.add(admin_role)
        db.flush()
        db.add(StaffRole(staff_id=2, role_id=4))
        db.flush()
        retried = notification_service.requeue_failed_outbox(
            db,
            outbox_id=event.id,
            staff=db.get(Staff, 2),
        )
        assert retried.status == "PENDING"
        assert retried.attempt_count == 0
        assert retried.event_key == event.event_key
        db.commit()

    with session_factory() as db:
        audit = db.scalar(
            select(AuditLog).where(
                AuditLog.target_type == "notification_outbox",
                AuditLog.action == "RETRY",
            )
        )
        assert audit is not None
        assert "previous_attempt_count" in (audit.detail or "")


def test_all_registered_finance_approval_types_have_read_scope_models():
    for doc_type in ("expense", "income", "payment", "receipt"):
        assert doc_type in notification_service.DOC_MODELS
        assert notification_service.DOC_READ_PERMISSIONS[doc_type] == (
            "module:finance",
        )


def test_purchase_arrival_event_notifies_order_participants(session_factory):
    _seed(session_factory, approvers=[2])
    with session_factory() as db:
        order = PurchaseOrder(
            id=200,
            order_no="CGDD-TEST-200",
            supplier_id=900,
            purchaser_id=2,
            created_by=1,
            status="RECEIVING",
        )
        stock_in = StockIn(
            id=300,
            stock_in_no="RKD-TEST-300",
            warehouse_id=900,
            stock_in_type="PURCHASE",
            purchase_order_id=200,
            status="APPROVED",
            created_by=1,
        )
        admin_role = Role(id=4, company_id=1, code="ADMIN", name="管理员")
        db.add_all([order, stock_in, admin_role])
        db.flush()
        db.add(StaffRole(staff_id=2, role_id=4))
        db.flush()
        notification_service.enqueue_purchase_order_arrival(
            db,
            company_id=1,
            stock_in_id=300,
            order=order,
        )
        db.commit()

    counts = notification_service.process_outbox_batch(session_factory=session_factory)
    assert counts["published"] == 1
    assert counts["inbox_created"] == 2
    with session_factory() as db:
        inboxes = list(db.scalars(select(NotificationInbox).order_by(NotificationInbox.id)))
        assert {inbox.recipient_staff_id for inbox in inboxes} == {1, 2}
        assert all(inbox.event_code == notification_service.PURCHASE_ORDER_ARRIVAL_CHANGED for inbox in inboxes)
        assert all("CGDD-TEST-200" in inbox.body for inbox in inboxes)
