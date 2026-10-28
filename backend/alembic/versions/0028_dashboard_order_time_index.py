"""首页销售趋势按付款/下单时间范围查询索引。"""

from alembic import op


revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_order_sales_time",
        "order",
        ["pay_status", "pay_time", "order_time"],
    )


def downgrade() -> None:
    op.drop_index("ix_order_sales_time", table_name="order")
