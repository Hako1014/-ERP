"""库存业务数据模型

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def _audit() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("auditor_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("audit_time", sa.DateTime(), nullable=True),
    ]


def upgrade() -> None:
    # stock_in / stock_in_item
    op.create_table(
        "stock_in",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stock_in_no", sa.String(32), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("stock_in_type", sa.String(16), nullable=False),
        sa.Column("purchase_order_id", sa.BigInteger(), sa.ForeignKey("purchase_order.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("total_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_in")),
        sa.UniqueConstraint("stock_in_no", name="uq_stock_in_stock_in_no"),
    )
    op.create_table(
        "stock_in_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stock_in_id", sa.BigInteger(), sa.ForeignKey("stock_in.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_in_item")),
    )
    # stock_out / stock_out_item
    op.create_table(
        "stock_out",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stock_out_no", sa.String(32), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("stock_out_type", sa.String(16), nullable=False),
        sa.Column("purchase_return_id", sa.BigInteger(), sa.ForeignKey("purchase_return.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("off_shelf_status", sa.String(16), nullable=False),
        sa.Column("total_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_out")),
        sa.UniqueConstraint("stock_out_no", name="uq_stock_out_stock_out_no"),
    )
    op.create_table(
        "stock_out_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("stock_out_id", sa.BigInteger(), sa.ForeignKey("stock_out.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_out_item")),
    )
    # transfer / transfer_item
    op.create_table(
        "transfer",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_no", sa.String(32), nullable=False),
        sa.Column("from_warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("to_warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("sync_stock", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("total_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer")),
        sa.UniqueConstraint("transfer_no", name="uq_transfer_transfer_no"),
    )
    op.create_table(
        "transfer_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), sa.ForeignKey("transfer.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_item")),
    )
    # transfer_out / transfer_out_item
    op.create_table(
        "transfer_out",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_out_no", sa.String(32), nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), sa.ForeignKey("transfer.id"), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_out")),
        sa.UniqueConstraint("transfer_out_no", name="uq_transfer_out_transfer_out_no"),
    )
    op.create_table(
        "transfer_out_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_out_id", sa.BigInteger(), sa.ForeignKey("transfer_out.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_out_item")),
    )
    # transfer_in / transfer_in_item
    op.create_table(
        "transfer_in",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_in_no", sa.String(32), nullable=False),
        sa.Column("transfer_id", sa.BigInteger(), sa.ForeignKey("transfer.id"), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_in")),
        sa.UniqueConstraint("transfer_in_no", name="uq_transfer_in_transfer_in_no"),
    )
    op.create_table(
        "transfer_in_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("transfer_in_id", sa.BigInteger(), sa.ForeignKey("transfer_in.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_in_item")),
    )
    # stock_check / stock_check_item
    op.create_table(
        "stock_check",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("check_no", sa.String(32), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("lock_warehouse", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_check")),
        sa.UniqueConstraint("check_no", name="uq_stock_check_check_no"),
    )
    op.create_table(
        "stock_check_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("check_id", sa.BigInteger(), sa.ForeignKey("stock_check.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=True),
        sa.Column("current_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("actual_qty", sa.Numeric(18, 3), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_check_item")),
    )
    # location_adjust / location_adjust_item
    op.create_table(
        "location_adjust",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("adjust_no", sa.String(32), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit(),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location_adjust")),
        sa.UniqueConstraint("adjust_no", name="uq_location_adjust_adjust_no"),
    )
    op.create_table(
        "location_adjust_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("adjust_id", sa.BigInteger(), sa.ForeignKey("location_adjust.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("from_location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=False),
        sa.Column("to_location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_location_adjust_item")),
    )
    # putaway
    op.create_table(
        "putaway",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("source_type", sa.String(16), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("operator_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_putaway")),
    )
    # stock_flow
    op.create_table(
        "stock_flow",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("location_id", sa.BigInteger(), sa.ForeignKey("location.id"), nullable=True),
        sa.Column("unit_id", sa.BigInteger(), sa.ForeignKey("unit.id"), nullable=True),
        sa.Column("direction", sa.String(8), nullable=False),
        sa.Column("biz_type", sa.String(16), nullable=False),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("total_cost", sa.Numeric(18, 2), nullable=True),
        sa.Column("source_no", sa.String(64), nullable=True),
        sa.Column("order_no", sa.String(64), nullable=True),
        sa.Column("flow_time", sa.DateTime(), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_flow")),
    )
    # stock_warning / stock_cost / stock_settlement
    op.create_table(
        "stock_warning",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("current_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("warn_qty", sa.Numeric(18, 3), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_warning")),
    )
    op.create_table(
        "stock_cost",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("total_cost", sa.Numeric(18, 2), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_cost")),
    )
    op.create_table(
        "stock_settlement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("settlement_date", sa.DateTime(), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stock_settlement")),
    )


def downgrade() -> None:
    for table in [
        "stock_settlement",
        "stock_cost",
        "stock_warning",
        "stock_flow",
        "putaway",
        "location_adjust_item",
        "location_adjust",
        "stock_check_item",
        "stock_check",
        "transfer_in_item",
        "transfer_in",
        "transfer_out_item",
        "transfer_out",
        "transfer_item",
        "transfer",
        "stock_out_item",
        "stock_out",
        "stock_in_item",
        "stock_in",
    ]:
        op.drop_table(table)
