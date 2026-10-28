"""采购业务数据模型

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def _audit_fields() -> list[sa.Column]:
    return [
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("auditor_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("audit_time", sa.DateTime(), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "purchase_plan",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_no", sa.String(32), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("plan_date", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("total_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_audit_fields(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_plan")),
        sa.UniqueConstraint("plan_no", name="uq_purchase_plan_plan_no"),
    )
    op.create_table(
        "purchase_plan_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("purchase_plan.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("planned_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("purchased_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("purchase_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_plan_item")),
    )
    op.create_table(
        "purchase_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(32), nullable=False),
        sa.Column("plan_id", sa.BigInteger(), sa.ForeignKey("purchase_plan.id"), nullable=True),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=True),
        sa.Column("purchaser_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("total_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("total_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("arrived_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("prepay_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("attachments", sa.Text(), nullable=True),
        *_audit_fields(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order")),
        sa.UniqueConstraint("order_no", name="uq_purchase_order_order_no"),
    )
    op.create_table(
        "purchase_order_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("purchase_order.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("brand_id", sa.BigInteger(), sa.ForeignKey("brand.id"), nullable=True),
        sa.Column("unit_id", sa.BigInteger(), sa.ForeignKey("unit.id"), nullable=True),
        sa.Column("purchase_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("in_transit_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("arrived_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_order_item")),
    )
    op.create_table(
        "purchase_prepay",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("prepay_no", sa.String(32), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("purchase_order.id"), nullable=True),
        sa.Column("order_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("prepay_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("write_off_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("bank", sa.String(64), nullable=True),
        sa.Column("account_no", sa.String(64), nullable=True),
        sa.Column("account_balance", sa.Numeric(18, 2), nullable=True),
        sa.Column("pay_note", sa.String(255), nullable=True),
        sa.Column("write_off_note", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        *_audit_fields(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_prepay")),
        sa.UniqueConstraint("prepay_no", name="uq_purchase_prepay_prepay_no"),
    )
    op.create_table(
        "purchase_return",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("return_no", sa.String(32), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("purchase_order.id"), nullable=True),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=True),
        sa.Column("auto_out", sa.Boolean(), nullable=False),
        sa.Column("return_reason", sa.String(128), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("total_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("returned_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        *_audit_fields(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_return")),
        sa.UniqueConstraint("return_no", name="uq_purchase_return_return_no"),
    )
    op.create_table(
        "purchase_return_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("return_id", sa.BigInteger(), sa.ForeignKey("purchase_return.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("unit_id", sa.BigInteger(), sa.ForeignKey("unit.id"), nullable=True),
        sa.Column("unit_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("return_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_return_item")),
    )
    op.create_table(
        "purchase_invoice",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("invoice_no", sa.String(32), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("invoice_type", sa.String(32), nullable=False),
        sa.Column("tax_rate", sa.Numeric(6, 2), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(18, 2), nullable=True),
        sa.Column("write_off_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("write_off_status", sa.String(16), nullable=False),
        sa.Column("business_type", sa.String(32), nullable=True),
        sa.Column("voucher_no", sa.String(64), nullable=True),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        *_audit_fields(),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_purchase_invoice")),
        sa.UniqueConstraint("invoice_no", name="uq_purchase_invoice_invoice_no"),
    )


def downgrade() -> None:
    for table in [
        "purchase_invoice",
        "purchase_return_item",
        "purchase_return",
        "purchase_prepay",
        "purchase_order_item",
        "purchase_order",
        "purchase_plan_item",
        "purchase_plan",
    ]:
        op.drop_table(table)
