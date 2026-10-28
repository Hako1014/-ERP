"""订单收货区域与审核状态字段

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "order",
        sa.Column(
            "audit_status",
            sa.String(16),
            nullable=False,
            server_default="PENDING",
        ),
    )
    op.add_column(
        "order",
        sa.Column("receiver_province", sa.String(32), nullable=True),
    )
    op.add_column(
        "order",
        sa.Column("receiver_city", sa.String(32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("order", "receiver_city")
    op.drop_column("order", "receiver_province")
    op.drop_column("order", "audit_status")
