"""持久私人会话、稳定消息序号、幂等与延迟清理索引。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import MEDIUMTEXT

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assistant_conversation",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("title", sa.String(120), nullable=False),
        sa.Column("is_pinned", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("latest_message_seq", sa.Integer(), nullable=False),
        sa.Column("cleared_through_seq", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index("ix_assistant_conversation_owner_status_updated", "assistant_conversation", ["company_id", "staff_id", "status", "updated_at"])
    op.create_index("ix_assistant_conversation_deleted", "assistant_conversation", ["deleted_at"])
    op.create_table(
        "assistant_message",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("assistant_conversation.id"), nullable=False),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text().with_variant(MEDIUMTEXT(), "mysql"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("client_message_id", sa.String(36), nullable=True),
        sa.Column("reply_to_id", sa.String(36), nullable=True),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("run_version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        sa.Column("session_version", sa.Integer(), nullable=False),
        sa.Column("login_fingerprint", sa.String(64), nullable=False),
        sa.Column("run_until", sa.DateTime(), nullable=False),
        sa.Column("page_context", sa.JSON(), nullable=True),
        sa.Column("visibility_guard", sa.JSON(), nullable=True),
        sa.Column("sources", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(5), nullable=True),
        sa.Column("purge_after", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_assistant_message_seq"),
        sa.UniqueConstraint("company_id", "staff_id", "conversation_id", "client_message_id", name="uq_assistant_message_client"),
        sa.UniqueConstraint("company_id", "staff_id", "request_id", "role", name="uq_assistant_message_request_role"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index("ix_assistant_message_owner_conversation_seq", "assistant_message", ["company_id", "staff_id", "conversation_id", "seq"])
    op.create_index("ix_assistant_message_purge", "assistant_message", ["purge_after"])


def downgrade() -> None:
    op.drop_table("assistant_message")
    op.drop_table("assistant_conversation")
