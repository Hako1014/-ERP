"""Add private non-AI quick-entry drafts."""

from alembic import op
import sqlalchemy as sa


revision = "0037"
down_revision = "0036"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "quick_entry_draft",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("module_key", sa.String(32), nullable=False),
        sa.Column("document_type", sa.String(64), nullable=False),
        sa.Column("source_mode", sa.String(16), nullable=False),
        sa.Column("title", sa.String(160), nullable=False),
        sa.Column("input_text", sa.Text(), nullable=False),
        sa.Column("draft_data", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("applied_version", sa.Integer()),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "staff_id", "request_id", name="uq_quick_entry_draft_request"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_quick_entry_draft_owner_updated",
        "quick_entry_draft",
        ["company_id", "staff_id", "updated_at"],
    )
    op.create_index(
        "ix_quick_entry_draft_expiry",
        "quick_entry_draft",
        ["status", "expires_at"],
    )


def downgrade():
    op.drop_table("quick_entry_draft")
