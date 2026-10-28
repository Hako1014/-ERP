"""店铺独享库存与库存设置

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0013"
down_revision: Union[str, None] = "0012"
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
        "shop_stock",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shop.id"), nullable=False),
        sa.Column(
            "warehouse_id",
            sa.BigInteger(),
            sa.ForeignKey("warehouse.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False
        ),
        sa.Column(
            "sku_id",
            sa.BigInteger(),
            sa.ForeignKey("product_sku.id"),
            nullable=False,
        ),
        sa.Column("exclusive_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("is_sync", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("effective_start", sa.DateTime(), nullable=True),
        sa.Column("effective_end", sa.DateTime(), nullable=True),
        sa.Column("last_sync_qty", sa.Numeric(18, 3), nullable=True),
        sa.Column("last_sync_at", sa.DateTime(), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column(
            "created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True
        ),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_stock")),
        sa.UniqueConstraint(
            "shop_id",
            "warehouse_id",
            "sku_id",
            name="uq_shop_stock_shop_warehouse_sku",
        ),
    )
    op.create_table(
        "stock_setting",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "company_id",
            sa.BigInteger(),
            sa.ForeignKey("company.id"),
            nullable=False,
        ),
        sa.Column("sync_stock_default", sa.Boolean(), nullable=False),
        sa.Column("shop_stock_enabled", sa.Boolean(), nullable=False),
        sa.Column("floating_cost_enabled", sa.Boolean(), nullable=False),
        sa.Column("off_shelf_below_warning", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_setting")),
        sa.UniqueConstraint("company_id", name="uq_stock_setting_company_id"),
    )
    # SQLite 不支持 ALTER 增加约束，batch 模式在 SQLite 下复制重建、MySQL 下走原生 ALTER。
    with op.batch_alter_table("stock_settlement") as batch_op:
        batch_op.create_unique_constraint(
            "uq_stock_settlement_date_sku",
            ["settlement_date", "sku_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("stock_settlement") as batch_op:
        batch_op.drop_constraint(
            "uq_stock_settlement_date_sku",
            type_="unique",
        )
    op.drop_table("stock_setting")
    op.drop_table("shop_stock")
