"""订单、履约与平台物流数据模型

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: Union[str, None] = "0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _ts() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "platform",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("code", sa.String(32), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_platform")),
    )
    op.create_table(
        "shop",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("platform_id", sa.BigInteger(), sa.ForeignKey("platform.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("login_account", sa.String(64), nullable=True),
        sa.Column("url", sa.String(255), nullable=True),
        sa.Column("tag", sa.String(64), nullable=True),
        sa.Column("supplier_id", sa.BigInteger(), sa.ForeignKey("supplier.id"), nullable=True),
        sa.Column("default_address", sa.String(255), nullable=True),
        sa.Column("is_locked", sa.Boolean(), nullable=False),
        sa.Column("auto_sync_order", sa.Boolean(), nullable=False),
        sa.Column("auto_sync_stock", sa.Boolean(), nullable=False),
        sa.Column("download_mode", sa.String(16), nullable=True),
        sa.Column("sms_ship_template", sa.String(64), nullable=True),
        sa.Column("sms_deliver_template", sa.String(64), nullable=True),
        sa.Column("enable_logistics_params", sa.Boolean(), nullable=False),
        sa.Column("message_match", sa.String(255), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop")),
    )
    op.create_table(
        "shop_api",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shop.id"), nullable=False),
        sa.Column("api_code", sa.String(128), nullable=True),
        sa.Column("verify_code", sa.String(128), nullable=True),
        sa.Column("app_key", sa.String(128), nullable=True),
        sa.Column("app_secret", sa.String(128), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shop_api")),
    )
    op.create_table(
        "logistics",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("company", sa.String(64), nullable=True),
        sa.Column("contact", sa.String(64), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_logistics")),
    )
    op.create_table(
        "freight_template",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("logistics_id", sa.BigInteger(), sa.ForeignKey("logistics.id"), nullable=False),
        sa.Column("charge_type", sa.String(16), nullable=False),
        sa.Column("first_count", sa.Integer(), nullable=False),
        sa.Column("continue_count", sa.Integer(), nullable=False),
        sa.Column("first_weight", sa.Numeric(12, 3), nullable=True),
        sa.Column("continue_weight", sa.Numeric(12, 3), nullable=True),
        sa.Column("base_fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("continue_fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("deduct_weight", sa.Numeric(12, 3), nullable=True),
        sa.Column("operation_fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("consumable_fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("ewaybill_fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_fee", sa.Numeric(18, 2), nullable=False),
        sa.Column("tax_point", sa.Numeric(6, 2), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_freight_template")),
    )
    op.create_table(
        "area_rule",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("logistics_id", sa.BigInteger(), sa.ForeignKey("logistics.id"), nullable=False),
        sa.Column("province", sa.String(32), nullable=True),
        sa.Column("city", sa.String(32), nullable=True),
        sa.Column("match_weight_min", sa.Numeric(12, 3), nullable=True),
        sa.Column("match_weight_max", sa.Numeric(12, 3), nullable=True),
        sa.Column("freight_template_id", sa.BigInteger(), sa.ForeignKey("freight_template.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_area_rule")),
    )
    op.create_table(
        "print_template",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("template_type", sa.String(32), nullable=False),
        sa.Column("logistics_id", sa.BigInteger(), sa.ForeignKey("logistics.id"), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        sa.Column("paper_width", sa.Numeric(10, 2), nullable=True),
        sa.Column("paper_height", sa.Numeric(10, 2), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_print_template")),
    )
    op.create_table(
        "wave_strategy",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("logistics_id", sa.BigInteger(), sa.ForeignKey("logistics.id"), nullable=True),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=True),
        sa.Column("shop_ids", sa.String(255), nullable=True),
        sa.Column("wave_order_count", sa.Integer(), nullable=False),
        sa.Column("load_no_type", sa.String(16), nullable=True),
        sa.Column("sort_type", sa.String(32), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("description", sa.String(255), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wave_strategy")),
    )
    op.create_table(
        "wave",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("wave_no", sa.String(64), nullable=False),
        sa.Column("strategy_id", sa.BigInteger(), sa.ForeignKey("wave_strategy.id"), nullable=True),
        sa.Column("name", sa.String(64), nullable=True),
        sa.Column("express_type", sa.String(32), nullable=True),
        sa.Column("total_order_count", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), sa.ForeignKey("staff.id"), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wave")),
        sa.UniqueConstraint("wave_no", name="uq_wave_wave_no"),
    )
    op.create_table(
        "load_no",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("wave_id", sa.BigInteger(), sa.ForeignKey("wave.id"), nullable=False),
        sa.Column("load_no", sa.String(64), nullable=False),
        sa.Column("express_type", sa.String(32), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_load_no")),
    )
    op.create_table(
        "order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_no", sa.String(64), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shop.id"), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=True),
        sa.Column("logistics_id", sa.BigInteger(), sa.ForeignKey("logistics.id"), nullable=True),
        sa.Column("buyer_nick", sa.String(64), nullable=True),
        sa.Column("buyer_message", sa.String(255), nullable=True),
        sa.Column("seller_remark", sa.String(255), nullable=True),
        sa.Column("receiver_name", sa.String(64), nullable=True),
        sa.Column("receiver_phone", sa.String(32), nullable=True),
        sa.Column("receiver_address", sa.String(255), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("pay_status", sa.String(16), nullable=False),
        sa.Column("order_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("paid_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("freight", sa.Numeric(18, 2), nullable=False),
        sa.Column("platform_discount", sa.Numeric(18, 2), nullable=False),
        sa.Column("seller_discount", sa.Numeric(18, 2), nullable=False),
        sa.Column("weight", sa.Numeric(12, 3), nullable=True),
        sa.Column("flags", sa.String(255), nullable=True),
        sa.Column("merge_order_no", sa.String(64), nullable=True),
        sa.Column("parent_order_no", sa.String(64), nullable=True),
        sa.Column("logistics_company", sa.String(64), nullable=True),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("order_time", sa.DateTime(), nullable=True),
        sa.Column("pay_time", sa.DateTime(), nullable=True),
        sa.Column("ship_time", sa.DateTime(), nullable=True),
        sa.Column("remark", sa.String(255), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order")),
        sa.UniqueConstraint("order_no", name="uq_order_order_no"),
    )
    op.create_table(
        "order_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("sku_name", sa.String(128), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("price", sa.Numeric(18, 2), nullable=False),
        sa.Column("amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("is_gift", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_order_item")),
    )
    op.create_table(
        "return_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("return_no", sa.String(64), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shop.id"), nullable=False),
        sa.Column("warehouse_id", sa.BigInteger(), sa.ForeignKey("warehouse.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 2), nullable=False),
        sa.Column("return_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("logistics_company", sa.String(64), nullable=True),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("reason", sa.String(128), nullable=True),
        sa.Column("buyer_phone", sa.String(32), nullable=True),
        sa.Column("is_arrived", sa.Boolean(), nullable=False),
        sa.Column("freight", sa.Numeric(18, 2), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_order")),
        sa.UniqueConstraint("return_no", name="uq_return_order_return_no"),
    )
    op.create_table(
        "return_order_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("return_order_id", sa.BigInteger(), sa.ForeignKey("return_order.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("refund_amount", sa.Numeric(18, 2), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_order_item")),
    )
    op.create_table(
        "exchange_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("exchange_no", sa.String(64), nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=False),
        sa.Column("shop_id", sa.BigInteger(), sa.ForeignKey("shop.id"), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("exchange_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("reason", sa.String(128), nullable=True),
        sa.Column("is_cod", sa.Boolean(), nullable=False),
        sa.Column("is_return", sa.Boolean(), nullable=False),
        sa.Column("compensate_order_no", sa.String(64), nullable=True),
        sa.Column("compensate_amount", sa.Numeric(18, 2), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchange_order")),
        sa.UniqueConstraint("exchange_no", name="uq_exchange_order_exchange_no"),
    )
    op.create_table(
        "exchange_order_item",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("exchange_order_id", sa.BigInteger(), sa.ForeignKey("exchange_order.id"), nullable=False),
        sa.Column("product_id", sa.BigInteger(), sa.ForeignKey("product.id"), nullable=False),
        sa.Column("sku_id", sa.BigInteger(), sa.ForeignKey("product_sku.id"), nullable=True),
        sa.Column("qty", sa.Numeric(18, 3), nullable=False),
        sa.Column("exchange_qty", sa.Numeric(18, 3), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exchange_order_item")),
    )
    op.create_table(
        "return_register",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("return_order_id", sa.BigInteger(), sa.ForeignKey("return_order.id"), nullable=True),
        sa.Column("exchange_order_id", sa.BigInteger(), sa.ForeignKey("exchange_order.id"), nullable=True),
        sa.Column("logistics_company", sa.String(64), nullable=True),
        sa.Column("tracking_no", sa.String(64), nullable=True),
        sa.Column("sender", sa.String(64), nullable=True),
        sa.Column("sender_phone", sa.String(32), nullable=True),
        sa.Column("product_info", sa.Text(), nullable=True),
        sa.Column("image", sa.String(255), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_return_register")),
    )
    op.create_table(
        "waybill",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=False),
        sa.Column("waybill_no", sa.String(64), nullable=True),
        sa.Column("print_type", sa.String(16), nullable=False),
        sa.Column("batch_no", sa.String(64), nullable=True),
        sa.Column("is_printed", sa.Boolean(), nullable=False),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_waybill")),
    )
    op.create_table(
        "pick_list",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_no", sa.String(64), nullable=False),
        sa.Column("wave_id", sa.BigInteger(), sa.ForeignKey("wave.id"), nullable=True),
        sa.Column("load_no", sa.String(64), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pick_list")),
    )
    op.create_table(
        "delivery_list",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("batch_no", sa.String(64), nullable=False),
        sa.Column("wave_id", sa.BigInteger(), sa.ForeignKey("wave.id"), nullable=True),
        sa.Column("load_no", sa.String(64), nullable=True),
        sa.Column("content", sa.Text(), nullable=True),
        *_ts(),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_delivery_list")),
    )


def downgrade() -> None:
    for table in [
        "delivery_list",
        "pick_list",
        "waybill",
        "return_register",
        "exchange_order_item",
        "exchange_order",
        "return_order_item",
        "return_order",
        "order_item",
        "order",
        "load_no",
        "wave",
        "wave_strategy",
        "print_template",
        "area_rule",
        "freight_template",
        "logistics",
        "shop_api",
        "shop",
        "platform",
    ]:
        op.drop_table(table)
