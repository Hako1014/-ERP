"""AI 附件识别结果及公司外发开关；不触碰 ERP 业务表。"""

from alembic import op
import sqlalchemy as sa

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "assistant_config",
        sa.Column("allow_attachment_external_send", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "assistant_extraction",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.BigInteger(), sa.ForeignKey("company.id"), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("assistant_conversation.id"), nullable=False),
        sa.Column("attachment_id", sa.String(36), sa.ForeignKey("assistant_attachment.id"), nullable=False),
        sa.Column("request_id", sa.String(36), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("input_mode", sa.String(16), nullable=False),
        sa.Column("model", sa.String(128)),
        sa.Column("selections", sa.JSON(), nullable=False),
        sa.Column("result", sa.JSON()),
        sa.Column("error_category", sa.String(64)),
        sa.Column("purge_after", sa.DateTime()),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.UniqueConstraint("company_id", "staff_id", "request_id", name="uq_assistant_extraction_request"),
        mysql_engine="InnoDB", mysql_charset="utf8mb4",
    )
    op.create_index("ix_assistant_extraction_attachment", "assistant_extraction", ["company_id", "staff_id", "attachment_id", "created_at"])
    op.create_index("ix_assistant_extraction_conversation", "assistant_extraction", ["company_id", "staff_id", "conversation_id", "created_at"])


def downgrade():
    op.drop_table("assistant_extraction")
    op.drop_column("assistant_config", "allow_attachment_external_send")
