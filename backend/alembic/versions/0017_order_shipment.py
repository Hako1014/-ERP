"""订单包裹发货与扫描明细

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0017"
down_revision: Union[str, None] = "0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "order_shipment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shipment_no", sa.String(32), nullable=False),
        sa.Column(
            "order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=False
        ),
        sa.Column(
            "warehouse_id",
            sa.BigInteger(),
            sa.ForeignKey("warehouse.id"),
            nullable=False,
        ),
        sa.Column(
            "stock_out_id",
            sa.BigInteger(),
            sa.ForeignKey("stock_out.id"),
            nullable=True,
        ),
        sa.Column("package_no", sa.Integer(), nullable=False),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("shipping_type", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("expected_weight_g", sa.Numeric(12, 3), nullable=True),
        sa.Column("actual_weight_g", sa.Numeric(12, 3), nullable=True),
        sa.Column("weight_tolerance_g", sa.Numeric(12, 3), nullable=True),
        sa.Column("weight_out_of_range", sa.Boolean(), nullable=False),
        sa.Column("weight_overridden", sa.Boolean(), nullable=False),
        sa.Column("platform_sync_status", sa.String(16), nullable=False),
        sa.Column("platform_sync_message", sa.String(255), nullable=True),
        sa.Column(
            "shipped_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True
        ),
        sa.Column("shipped_at", sa.DateTime(), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_shipment")),
        sa.UniqueConstraint(
            "shipment_no", name=op.f("uq_order_shipment_shipment_no")
        ),
    )
    op.create_index(
        "ix_order_shipment_order_id",
        "order_shipment",
        ["order_id"],
    )
    op.create_index(
        "ix_order_shipment_tracking_no",
        "order_shipment",
        ["tracking_no"],
    )

    op.create_table(
        "order_shipment_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "shipment_id",
            sa.BigInteger(),
            sa.ForeignKey("order_shipment.id"),
            nullable=False,
        ),
        sa.Column(
            "order_item_id",
            sa.BigInteger(),
            sa.ForeignKey("order_item.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False
        ),
        sa.Column(
            "sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True
        ),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_shipment_item")),
        sa.UniqueConstraint(
            "shipment_id",
            "order_item_id",
            name="uq_order_shipment_item_shipment_order_item",
        ),
    )


def downgrade() -> None:
    op.drop_table("order_shipment_item")
    op.drop_index(
        "ix_order_shipment_tracking_no",
        table_name="order_shipment",
    )
    op.drop_index(
        "ix_order_shipment_order_id",
        table_name="order_shipment",
    )
    op.drop_table("order_shipment")
