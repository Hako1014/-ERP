"""往来单位数据模型

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "supplier",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("contact", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("fax", sa.String(32), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("settlement_type", sa.String(32), nullable=True),
        sa.Column("email", sa.String(64), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier")),
        sa.UniqueConstraint("code", name="uq_supplier_code"),
    )
    op.create_table(
        "customer",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("contact", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("fax", sa.String(32), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("settlement_type", sa.String(32), nullable=True),
        sa.Column("email", sa.String(64), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer")),
        sa.UniqueConstraint("code", name="uq_customer_code"),
    )
    op.create_table(
        "account",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), sa.ForeignKey("customer.id"), nullable=True),
        sa.Column("bank", sa.String(64), nullable=True),
        sa.Column("branch", sa.String(64), nullable=True),
        sa.Column("bank_no", sa.String(64), nullable=True),
        sa.Column("account_no", sa.String(64), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account")),
    )
    op.create_table(
        "supplier_price",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("price", sa.Numeric(18, 2), nullable=False),
        sa.Column("status", sa.String(16), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_supplier_price")),
        sa.UniqueConstraint("supplier_id", "product_id", "sku_id", name="uq_supplier_price"),
    )
    # 补充 TASK-010 遗留的逻辑外键：product.default_supplier_id -> supplier.id
    # SQLite 不支持直接 ALTER ADD CONSTRAINT，使用 batch 模式（copy-and-move）。
    with op.batch_alter_table("product") as batch_op:
        batch_op.create_foreign_key(
            "fk_product_default_supplier_id_supplier",
            "supplier",
            ["default_supplier_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("product") as batch_op:
        batch_op.drop_constraint("fk_product_default_supplier_id_supplier", type_="foreignkey")
    for table in ["supplier_price", "account", "customer", "supplier"]:
        op.drop_table(table)
