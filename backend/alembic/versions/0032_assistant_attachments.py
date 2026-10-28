"""助手私有附件及消息关联；不修改业务表。"""

from alembic import op
import sqlalchemy as sa

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "assistant_attachment",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("assistant_conversation.id"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("assistant_message.id")),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("display_name", sa.String(160), nullable=False),
        sa.Column("media_type", sa.String(80), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(64), nullable=False, unique=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("purge_after", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index("ix_assistant_attachment_owner", "assistant_attachment", ["company_id", "staff_id", "conversation_id", "status"])
    op.create_index("ix_assistant_attachment_message", "assistant_attachment", ["message_id"])
    op.create_index("ix_assistant_attachment_cleanup", "assistant_attachment", ["purpose", "purge_after"])


def downgrade():
    op.drop_table("assistant_attachment")
