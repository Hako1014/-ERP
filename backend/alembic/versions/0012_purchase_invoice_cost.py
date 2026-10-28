"""采购发票成本核算字段

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0012"
down_revision: Union[str, None] = "0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "purchase_invoice",
        sa.Column("stock_in_ids", sa.Text(), nullable=True),
    )
    op.add_column(
        "purchase_invoice",
        sa.Column("cost_allocation", sa.Text(), nullable=True),
    )
    op.add_column(
        "purchase_invoice",
        sa.Column("cost_accounted_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("purchase_invoice", "cost_accounted_at")
    op.drop_column("purchase_invoice", "cost_allocation")
    op.drop_column("purchase_invoice", "stock_in_ids")
