"""商品与基础资料数据模型

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "category",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_category")),
    )
    op.create_table(
        "brand",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("image", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_brand")),
    )
    op.create_table(
        "unit",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("code", sa.String(32), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit")),
    )
    op.create_table(
        "unit_group",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("main_unit_id", sa.BigInteger(), sa.ForeignKey("unit.id"), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_group")),
    )
    op.create_table(
        "unit_group_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("unit_group_id", sa.BigInteger(), sa.ForeignKey("unit_group.id"), nullable=False),
        sa.Column("unit_id", sa.BigInteger(), sa.ForeignKey("unit.id"), nullable=False),
        sa.Column("conversion_rate", sa.Numeric(18, 6), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_unit_group_item")),
        sa.UniqueConstraint("unit_group_id", "unit_id", name="uq_unit_group_item_group_unit"),
    )
    op.create_table(
        "spec_type",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("category_id", sa.BigInteger(), sa.ForeignKey("category.id"), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_type")),
    )
    op.create_table(
        "spec_value",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("spec_type_id", sa.BigInteger(), sa.ForeignKey("spec_type.id"), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("code", sa.String(64), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spec_value")),
        sa.UniqueConstraint("spec_type_id", "code", name="uq_spec_value_type_code"),
    )
    op.create_table(
        "product",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("art_no", sa.String(64), nullable=True),
        sa.Column("category_id", sa.BigInteger(), sa.ForeignKey("category.id"), nullable=True),
        sa.Column("brand_id", sa.BigInteger(), sa.ForeignKey("brand.id"), nullable=True),
        sa.Column("default_supplier_id", sa.BigInteger(), nullable=True),
        sa.Column("measure_type", sa.String(16), nullable=False),
        sa.Column("unit_group_id", sa.BigInteger(), sa.ForeignKey("unit_group.id"), nullable=True),
        sa.Column("main_unit_id", sa.BigInteger(), sa.ForeignKey("unit.id"), nullable=True),
        sa.Column("online_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("tag_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("min_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("weight", sa.Numeric(12, 3), nullable=True),
        sa.Column("min_stock", sa.Integer(), nullable=False),
        sa.Column("max_stock", sa.Integer(), nullable=False),
        sa.Column("warn_stock", sa.Integer(), nullable=False),
        sa.Column("tax_rate", sa.Numeric(6, 2), nullable=True),
        sa.Column("tax_code", sa.String(64), nullable=True),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("is_gift", sa.Boolean(), nullable=False),
        sa.Column("volume", sa.Numeric(12, 4), nullable=True),
        sa.Column("use_float_cost", sa.Boolean(), nullable=False),
        sa.Column("is_disabled", sa.Boolean(), nullable=False),
        sa.Column("listed_at", sa.DateTime(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("image", sa.String(255), nullable=True),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product")),
        sa.UniqueConstraint("code", name="uq_product_code"),
    )
    op.create_index("ix_product_art_no", "product", ["art_no"], unique=False)
    op.create_index("ix_product_barcode", "product", ["barcode"], unique=False)
    op.create_table(
        "product_sku",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("spec_name", sa.String(128), nullable=True),
        sa.Column("spec_code", sa.String(64), nullable=False),
        sa.Column("barcode", sa.String(64), nullable=True),
        sa.Column("tag_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("sale_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("cost_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("min_price", sa.Numeric(18, 2), nullable=True),
        sa.Column("image", sa.String(255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_sku")),
        sa.UniqueConstraint("spec_code", name="uq_product_sku_spec_code"),
        sa.UniqueConstraint("barcode", name="uq_product_sku_barcode"),
    )


def downgrade() -> None:
    for table in [
        "product_sku",
        "product",
        "spec_value",
        "spec_type",
        "unit_group_item",
        "unit_group",
        "unit",
        "brand",
        "category",
    ]:
        op.drop_table(table)
