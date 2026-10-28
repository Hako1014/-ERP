from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BIGINT_ID, CompanyOwnedBase as Base, TimestampMixin


class ProjectCategory(Base, TimestampMixin):
    __tablename__ = "project_category"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)


class AccountSubject(Base, TimestampMixin):
    __tablename__ = "account_subject"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str | None] = mapped_column(String(32), nullable=True)
    balance_direction: Mapped[str] = mapped_column(String(8), default="DEBIT", nullable=False)
    is_auxiliary: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_qty_amount: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    measure_unit: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_cash: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_bank: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_journal: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_cash_equivalent: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    project_category_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("project_category.id"), nullable=True
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Voucher(Base, TimestampMixin):
    __tablename__ = "voucher"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    voucher_word: Mapped[str] = mapped_column(String(8), default="记", nullable=False)
    voucher_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    business_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    record_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    total_debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    total_credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    auditor_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    posted_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    cashier_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    general_office_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class VoucherItem(Base):
    __tablename__ = "voucher_item"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    voucher_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("voucher.id"), nullable=False)
    subject_id: Mapped[int] = mapped_column(
        BIGINT_ID, ForeignKey("account_subject.id"), nullable=False
    )
    summary: Mapped[str | None] = mapped_column(String(255), nullable=True)
    debit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    credit: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)


class Payment(Base, TimestampMixin):
    __tablename__ = "payment"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    payment_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("supplier.id"), nullable=False)
    stock_in_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("stock_in.id"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_invoice.id"), nullable=True
    )
    income_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("income.id"), nullable=True)
    prepay_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("purchase_prepay.id"), nullable=True
    )
    payment_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pay_method: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    auditor_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    audit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Receipt(Base, TimestampMixin):
    __tablename__ = "receipt"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    receipt_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    customer_id: Mapped[int] = mapped_column(BIGINT_ID, ForeignKey("customer.id"), nullable=False)
    order_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("order.id"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("invoice.id"), nullable=True)
    expense_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("expense.id"), nullable=True)
    sale_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    settle_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    auditor_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)
    audit_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Expense(Base, TimestampMixin):
    __tablename__ = "expense"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    expense_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    customer_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("customer.id"), nullable=True)
    expense_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voucher_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class Settlement(Base, TimestampMixin):
    __tablename__ = "settlement"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    settlement_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    shop_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("shop.id"), nullable=True)
    platform_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    settlement_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    checked_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    confirmed_by: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("staff.id"), nullable=True
    )
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    receivable_id: Mapped[int | None] = mapped_column(
        BIGINT_ID, ForeignKey("invoice.id"), nullable=True
    )
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class Income(Base, TimestampMixin):
    __tablename__ = "income"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    income_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    customer_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("customer.id"), nullable=True)
    supplier_id: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("supplier.id"), nullable=True)
    income_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    voucher_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    audit_status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    created_by: Mapped[int | None] = mapped_column(BIGINT_ID, ForeignKey("staff.id"), nullable=True)


class Invoice(Base, TimestampMixin):
    __tablename__ = "invoice"

    id: Mapped[int] = mapped_column(BIGINT_ID, primary_key=True, autoincrement=True)
    invoice_no: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    document_type: Mapped[str] = mapped_column(
        String(16), default="INVOICE", nullable=False
    )
    invoice_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tax_no: Mapped[str | None] = mapped_column(String(64), nullable=True)
    invoice_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    received_amount: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"), nullable=False)
    is_received: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    company: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    applicant: Mapped[str | None] = mapped_column(String(64), nullable=True)
