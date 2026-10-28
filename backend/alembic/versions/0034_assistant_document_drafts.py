"""Create persistent, owner-scoped AI document drafts."""

from alembic import op
import sqlalchemy as sa


revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_document_draft",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("assistant_conversation.id"), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("source_attachment_id", sa.String(36), sa.ForeignKey("assistant_attachment.id", ondelete="SET NULL")),
        sa.Column("source_extraction_id", sa.String(36), sa.ForeignKey("assistant_extraction.id", ondelete="SET NULL")),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("draft_data", sa.JSON(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("creation_request_id", sa.String(36)),
        sa.Column("creation_payload_hash", sa.String(64)),
        sa.Column("creation_payload", sa.JSON()),
        sa.Column("created_plan_id", sa.BigInteger(), sa.ForeignKey("purchase_plan.id", ondelete="SET NULL")),
        sa.Column("creation_result", sa.JSON()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "staff_id", "request_id", name="uq_assistant_document_draft_request"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index(
        "ix_assistant_document_draft_conversation",
        "assistant_document_draft",
        ["company_id", "staff_id", "conversation_id", "updated_at"],
    )
    op.create_index(
        "ix_assistant_document_draft_expiry",
        "assistant_document_draft",
        ["status", "expires_at"],
    )
    op.create_table(
        "purchase_plan_attachment",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("purchase_plan.id"), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("source_assistant_attachment_id", sa.String(36), sa.ForeignKey("assistant_attachment.id", ondelete="SET NULL")),
        sa.Column("display_name", sa.String(150), nullable=False),
        sa.Column("media_type", sa.String(128), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("storage_key", name="uq_purchase_plan_attachment_storage_key"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
    )
    op.create_index("ix_purchase_plan_attachment_plan", "purchase_plan_attachment", ["company_id", "plan_id"])


def downgrade():
    op.drop_table("purchase_plan_attachment")
    op.drop_table("assistant_document_draft")
