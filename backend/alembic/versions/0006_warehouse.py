"""仓库与库位数据模型

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "warehouse",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("region", sa.String(64), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("warehouse_type", sa.String(16), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("sync_stock", sa.Boolean(), nullable=False),
        sa.Column("enable_location", sa.Boolean(), nullable=False),
        sa.Column("zero_stock_out", sa.Boolean(), nullable=False),
        sa.Column("is_virtual", sa.Boolean(), nullable=False),
        sa.Column("ship_regions", sa.String(128), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("lock_type", sa.String(16), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warehouse")),
        sa.UniqueConstraint("code", name="uq_warehouse_code"),
    )
    op.create_table(
        "zone",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone")),
        sa.UniqueConstraint("warehouse_id", "code", name="uq_zone_warehouse_code"),
    )
    op.create_table(
        "location",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("zone_id", sa.BigInteger(), sa.ForeignKey("zone.id"), nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("row_no", sa.Integer(), nullable=False),
        sa.Column("col_no", sa.Integer(), nullable=False),
        sa.Column("layer_no", sa.Integer(), nullable=False),
        sa.Column("capacity", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_virtual", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location")),
        sa.UniqueConstraint("code", name="uq_location_code"),
    )
    op.create_table(
        "stock_location",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=False),
        sa.Column("product_sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_location")),
        sa.UniqueConstraint("location_id", "product_sku_id", "status", name="uq_stock_location"),
    )


def downgrade() -> None:
    for table in ["stock_location", "location", "zone", "warehouse"]:
        op.drop_table(table)
