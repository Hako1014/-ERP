"""我的审批列表候选及历史查询索引。"""

from alembic import op


revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_approval_record_approver_action_doc",
        "approval_record",
        ["approver_id", "action", "doc_type", "doc_id"],
    )
    op.create_index(
        "ix_approval_record_action_status_doc",
        "approval_record",
        ["action", "status", "doc_type", "doc_id"],
    )
    op.create_index(
        "ix_approval_record_doc_history",
        "approval_record",
        ["doc_type", "doc_id", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_approval_record_doc_history", table_name="approval_record")
    op.drop_index("ix_approval_record_action_status_doc", table_name="approval_record")
    op.drop_index("ix_approval_record_approver_action_doc", table_name="approval_record")
