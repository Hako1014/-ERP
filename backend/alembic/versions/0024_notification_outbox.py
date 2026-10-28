"""通知事务 Outbox、站内收件箱和实时投递尝试。

Revision ID: 0024
Revises: 0023
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024"
down_revision: Union[str, None] = "0023"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _id_type() -> sa.types.TypeEngine:
    return sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade() -> None:
    id_type = _id_type()
    op.create_table(
        "notification_outbox",
        sa.Column("id", id_type, autoincrement=True, nullable=False),
        sa.Column("company_id", id_type, nullable=False),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", id_type, nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", id_type, nullable=False),
        sa.Column("occurred_at", sa.DateTime(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_outbox")),
        sa.UniqueConstraint(
            "company_id",
            "event_key",
            name="uq_notification_outbox_company_event",
        ),
    )
    op.create_index(
        "ix_notification_outbox_due",
        "notification_outbox",
        ["status", "available_at", "id"],
    )
    op.create_index(
        "ix_notification_outbox_aggregate",
        "notification_outbox",
        ["company_id", "aggregate_type", "aggregate_id"],
    )

    op.create_table(
        "notification_inbox",
        sa.Column("id", id_type, autoincrement=True, nullable=False),
        sa.Column("company_id", id_type, nullable=False),
        sa.Column("recipient_staff_id", id_type, nullable=False),
        sa.Column("outbox_id", id_type, nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False, server_default="IN_APP"),
        sa.Column("event_code", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=128), nullable=False),
        sa.Column("body", sa.String(length=512), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", id_type, nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", id_type, nullable=False),
        sa.Column("template_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("read_state", sa.String(length=16), nullable=False, server_default="UNREAD"),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.Column("business_state", sa.String(length=16), nullable=False, server_default="N_A"),
        sa.Column("business_state_at", sa.DateTime(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"]),
        sa.ForeignKeyConstraint(["recipient_staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["outbox_id"], ["notification_outbox.id"]),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_inbox")),
        sa.UniqueConstraint(
            "outbox_id",
            "recipient_staff_id",
            "channel",
            name="uq_notification_inbox_delivery",
        ),
    )
    op.create_index(
        "ix_notification_inbox_recipient_read",
        "notification_inbox",
        ["company_id", "recipient_staff_id", "read_state", "created_at"],
    )
    op.create_index(
        "ix_notification_inbox_source",
        "notification_inbox",
        ["source_type", "source_id", "event_code"],
    )

    op.create_table(
        "notification_delivery_attempt",
        sa.Column("id", id_type, autoincrement=True, nullable=False),
        sa.Column("inbox_id", id_type, nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="PROCESSING"),
        sa.Column("attempted_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("available_at", sa.DateTime(), nullable=False),
        sa.Column("lease_token", sa.String(length=36), nullable=True),
        sa.Column("lease_until", sa.DateTime(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("connection_id", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["inbox_id"], ["notification_inbox.id"]),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_delivery_attempt")),
        sa.UniqueConstraint(
            "inbox_id",
            "channel",
            "attempt_no",
            name="uq_notification_delivery_attempt",
        ),
    )
    op.create_index(
        "ix_notification_delivery_due",
        "notification_delivery_attempt",
        ["channel", "status", "available_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_delivery_due", table_name="notification_delivery_attempt")
    op.drop_table("notification_delivery_attempt")
    op.drop_index("ix_notification_inbox_source", table_name="notification_inbox")
    op.drop_index("ix_notification_inbox_recipient_read", table_name="notification_inbox")
    op.drop_table("notification_inbox")
    op.drop_index("ix_notification_outbox_aggregate", table_name="notification_outbox")
    op.drop_index("ix_notification_outbox_due", table_name="notification_outbox")
    op.drop_table("notification_outbox")
