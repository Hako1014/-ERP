"""财务与审批数据模型

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "project_category",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("remark", sa.String(255), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_category")),
        sa.UniqueConstraint("code", name="uq_project_category_code"),
    )
    op.create_table(
        "account_subject",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("balance_direction", sa.String(8), nullable=False),
        sa.Column("is_auxiliary", sa.Boolean(), nullable=False),
        sa.Column("is_qty_amount", sa.Boolean(), nullable=False),
        sa.Column("measure_unit", sa.String(32), nullable=True),
        sa.Column("is_cash", sa.Boolean(), nullable=False),
        sa.Column("is_bank", sa.Boolean(), nullable=False),
        sa.Column("is_journal", sa.Boolean(), nullable=False),
        sa.Column("is_cash_equivalent", sa.Boolean(), nullable=False),
        sa.Column("project_category_id", sa.BigInteger(), sa.ForeignKey("project_category.id"), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_subject")),
        sa.UniqueConstraint("code", name="uq_account_subject_code"),
    )
    op.create_table(
        "voucher",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("voucher_word", sa.String(8), nullable=False),
        sa.Column("voucher_no", sa.String(64), nullable=False),
        sa.Column("business_date", sa.DateTime(), nullable=True),
        sa.Column("record_date", sa.DateTime(), nullable=True),
        sa.Column("reference", sa.String(255), nullable=True),
        sa.Column("total_debit", sa.Numeric(18, 2), nullable=False),
        sa.Column("total_credit", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("auditor_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("posted_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("cashier_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("general_office_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voucher")),
        sa.UniqueConstraint("voucher_no", name="uq_voucher_voucher_no"),
    )
    op.create_table(
        "voucher_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("voucher_id", sa.BigInteger(), sa.ForeignKey("voucher.id"), nullable=False),
        sa.Column("subject_id", sa.BigInteger(), sa.ForeignKey("account_subject.id"), nullable=False),
        sa.Column("summary", sa.String(255), nullable=True),
        sa.Column("debit", sa.Numeric(18, 2), nullable=False),
        sa.Column("credit", sa.Numeric(18, 2), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voucher_item")),
    )
    # income 先于 payment 创建（payment 引用 income）
    op.create_table(
        "income",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("income_no", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customer.id"), nullable=True),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=True),
        sa.Column("income_type", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_income")),
        sa.UniqueConstraint("income_no", name="uq_income_income_no"),
    )
    op.create_table(
        "payment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("payment_no", sa.String(64), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("stock_in_id", sa.BigInteger(), sa.ForeignKey("stock_in.id"), nullable=True),
        sa.Column("invoice_id", sa.BigInteger(), sa.ForeignKey("purchase_invoice.id"), nullable=True),
        sa.Column("income_id", sa.BigInteger(), sa.ForeignKey("income.id"), nullable=True),
        sa.Column("prepay_id", sa.BigInteger(), sa.ForeignKey("purchase_prepay.id"), nullable=True),
        sa.Column("payment_type", sa.String(32), nullable=True),
        sa.Column("pay_method", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("auditor_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("audit_time", sa.DateTime(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment")),
        sa.UniqueConstraint("payment_no", name="uq_payment_payment_no"),
    )
    op.create_table(
        "expense",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("expense_no", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customer.id"), nullable=True),
        sa.Column("expense_type", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense")),
        sa.UniqueConstraint("expense_no", name="uq_expense_expense_no"),
    )
    op.create_table(
        "invoice",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("invoice_no", sa.String(64), nullable=False),
        sa.Column("invoice_title", sa.String(255), nullable=True),
        sa.Column("tax_no", sa.String(64), nullable=True),
        sa.Column("invoice_type", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("received_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("is_received", sa.Boolean(), nullable=False),
        sa.Column("company", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("applicant", sa.String(64), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_invoice")),
        sa.UniqueConstraint("invoice_no", name="uq_invoice_invoice_no"),
    )
    op.create_table(
        "receipt",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("receipt_no", sa.String(64), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customer.id"), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=True),
        sa.Column("invoice_id", sa.BigInteger(), sa.ForeignKey("invoice.id"), nullable=True),
        sa.Column("expense_id", sa.BigInteger(), sa.ForeignKey("expense.id"), nullable=True),
        sa.Column("sale_type", sa.String(32), nullable=True),
        sa.Column("settle_type", sa.String(32), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("audit_status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("auditor_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("audit_time", sa.DateTime(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_receipt")),
        sa.UniqueConstraint("receipt_no", name="uq_receipt_receipt_no"),
    )
    op.create_table(
        "settlement",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("settlement_no", sa.String(64), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shop.id"), nullable=True),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement")),
        sa.UniqueConstraint("settlement_no", name="uq_settlement_settlement_no"),
    )
    op.create_table(
        "approval_flow",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("approvers", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_flow")),
        sa.UniqueConstraint("name", name="uq_approval_flow_name"),
    )
    op.create_table(
        "approval_record",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("flow_id", sa.BigInteger(), sa.ForeignKey("approval_flow.id"), nullable=True),
        sa.Column("doc_type", sa.String(32), nullable=False),
        sa.Column("doc_id", sa.BigInteger(), nullable=False),
        sa.Column("doc_no", sa.String(64), nullable=True),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("approver_id", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("comment", sa.String(255), nullable=True),
        sa.Column("reason", sa.String(255), nullable=True),
        sa.Column("audit_time", sa.DateTime(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_record")),
    )


def downgrade() -> None:
    for table in [
        "approval_record",
        "approval_flow",
        "settlement",
        "receipt",
        "invoice",
        "expense",
        "payment",
        "income",
        "voucher_item",
        "voucher",
        "account_subject",
        "project_category",
    ]:
        op.drop_table(table)
