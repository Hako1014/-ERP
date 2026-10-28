"""组合商品数据模型

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: Union[str, None] = "0010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "combo_product",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("combo_no", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column(
            "main_product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False
        ),
        sa.Column(
            "main_sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True
        ),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_combo_product")),
        sa.UniqueConstraint("combo_no", name="uq_combo_product_combo_no"),
    )
    op.create_table(
        "combo_product_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "combo_id", sa.BigInteger(), sa.ForeignKey("combo_product.id"), nullable=False
        ),
        sa.Column(
            "product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False
        ),
        sa.Column(
            "sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True
        ),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_combo_product_item")),
    )


def downgrade() -> None:
    for table in ["combo_product_item", "combo_product"]:
        op.drop_table(table)
