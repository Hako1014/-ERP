"""提醒扫描检查点与租约。"""

from alembic import op
import sqlalchemy as sa

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    id_type = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
    op.create_table(
        "notification_reminder_scan",
        sa.Column("id", id_type, primary_key=True, autoincrement=True),
        sa.Column("company_id", id_type, sa.ForeignKey("company.id"), nullable=False),
        sa.Column("rule_version", sa.String(20), nullable=False),
        sa.Column("cursor_id", id_type, nullable=False, server_default="0"),
        sa.Column("lease_token", sa.String(36)),
        sa.Column("lease_until", sa.DateTime()),
        sa.Column("last_run_at", sa.DateTime()),
        sa.Column("status", sa.String(16), nullable=False, server_default="READY"),
        sa.Column("scanned_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enqueued_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "rule_version", name="uq_reminder_scan_rule"),
    )


def downgrade() -> None:
    op.drop_table("notification_reminder_scan")
