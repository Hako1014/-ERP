"""补充发票类型与供应商账户余额字段

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0020"
down_revision: Union[str, None] = "0019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "invoice",
        sa.Column(
            "document_type",
            sa.String(16),
            nullable=False,
            server_default="INVOICE",
        ),
    )
    with op.batch_alter_table("account") as batch_op:
        batch_op.add_column(
            sa.Column(
                "balance",
                sa.Numeric(18, 2),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("kingdee_account_code", sa.String(128), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "kingdee_sync_status",
                sa.String(16),
                nullable=False,
                server_default="NOT_SYNCED",
            )
        )
        batch_op.add_column(
            sa.Column("kingdee_sync_message", sa.String(255), nullable=True)
        )
        batch_op.add_column(
            sa.Column("balance_synced_at", sa.DateTime(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("account") as batch_op:
        batch_op.drop_column("balance_synced_at")
        batch_op.drop_column("kingdee_sync_message")
        batch_op.drop_column("kingdee_sync_status")
        batch_op.drop_column("kingdee_account_code")
        batch_op.drop_column("balance")
    op.drop_column("invoice", "document_type")
