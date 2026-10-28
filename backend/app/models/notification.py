from datetime import datetime, timezone

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, Base, CompanyOwnedBase, TimestampMixin


def _utc_timestamp() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class NotificationTimestampMixin(TimestampMixin):
    """应用写入 UTC，避免 MySQL SYSTEM 时区使通知展示时间偏移。"""

    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utc_timestamp(),
        server_default=func.current_timestamp(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: _utc_timestamp(),
        server_default=func.current_timestamp(), onupdate=lambda: _utc_timestamp(), nullable=False)


class NotificationReminderScan(Base, NotificationTimestampMixin):
    """每个公司/配置版本的扫描游标、租约和最近结果。"""

    __tablename__ = "notification_reminder_scan"
    __table_args__ = (UniqueConstraint("company_id", "rule_version", name="uq_reminder_scan_rule"),)

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("company.id"), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(20), nullable=False)
    cursor_id: Mapped[int] = mapped_column(BIGINT_ID, default=0, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="READY", nullable=False)
    scanned_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enqueued_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NotificationOutbox(Base, NotificationTimestampMixin):
    """与业务事务原子提交的通知事件，作为后续投递与补偿依据。"""

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "company_id", "event_key", name="uq_notification_outbox_company_event"
        ),
        Index("ix_notification_outbox_due", "status", "available_at", "id"),
        Index(
            "ix_notification_outbox_aggregate",
            "company_id",
            "aggregate_type",
            "aggregate_id",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    event_code: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    event_key: Mapped[str] = mapped_column(String(128), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(32), nullable=False)
    aggregate_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    payload_json: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)


class NotificationInbox(Base, NotificationTimestampMixin):
    """接收人维度的持久通知；已读与业务办理状态彼此独立。"""

    __tablename__ = "notification_inbox"
    __table_args__ = (
        UniqueConstraint(
            "outbox_id",
            "recipient_staff_id",
            "channel",
            name="uq_notification_inbox_delivery",
        ),
        Index(
            "ix_notification_inbox_recipient_read",
            "company_id",
            "recipient_staff_id",
            "read_state",
            "created_at",
        ),
        Index(
            "ix_notification_inbox_source",
            "source_type",
            "source_id",
            "event_code",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("company.id"), nullable=False
    )
    recipient_staff_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=False
    )
    outbox_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("notification_outbox.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False, default="IN_APP")
    event_code: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(String(512), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[int] = mapped_column(BIGINT_ID, nullable=False)
    template_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    read_state: Mapped[str] = mapped_column(String(16), nullable=False, default="UNREAD")
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    business_state: Mapped[str] = mapped_column(String(16), nullable=False, default="N_A")
    business_state_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class NotificationDeliveryAttempt(CompanyOwnedBase, NotificationTimestampMixin):
    """实时提示投递尝试；即使失败也不影响 Inbox 持久消息。"""

    __tablename__ = "notification_delivery_attempt"
    __table_args__ = (
        UniqueConstraint(
            "inbox_id", "channel", "attempt_no", name="uq_notification_delivery_attempt"
        ),
        Index(
            "ix_notification_delivery_due",
            "channel",
            "status",
            "available_at",
        ),
    )

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    inbox_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("notification_inbox.id"), nullable=False
    )
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="PROCESSING")
    attempted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    available_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lease_token: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
