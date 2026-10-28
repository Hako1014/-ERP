"""库存成本回写留痕表

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_cost_rewrite_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_no", sa.String(32), nullable=False),
        sa.Column("period_start", sa.DateTime(), nullable=True),
        sa.Column("period_end", sa.DateTime(), nullable=False),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "warehouse_id",
            sa.BigInteger(),
            sa.ForeignKey("warehouse.id"),
            nullable=True,
        ),
        sa.Column(
            "product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False
        ),
        sa.Column(
            "sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True
        ),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("cost_source", sa.String(16), nullable=False),
        sa.Column("old_cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("new_cost_price", sa.Numeric(18, 2), nullable=False),
        sa.Column("old_total_cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("new_total_cost", sa.Numeric(18, 2), nullable=False),
        sa.Column(
            "created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_cost_rewrite_log")),
    )
    op.create_index(
        "ix_stock_cost_rewrite_log_batch_no",
        "stock_cost_rewrite_log",
        ["batch_no"],
    )
    op.create_index(
        "ix_stock_cost_rewrite_log_sku_id",
        "stock_cost_rewrite_log",
        ["sku_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_stock_cost_rewrite_log_sku_id", table_name="stock_cost_rewrite_log")
    op.drop_index(
        "ix_stock_cost_rewrite_log_batch_no", table_name="stock_cost_rewrite_log"
    )
    op.drop_table("stock_cost_rewrite_log")
