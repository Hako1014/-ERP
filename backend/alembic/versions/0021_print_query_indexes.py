"""为待处理订单与打印批次查询补充索引。

Revision ID: 0021
Revises: 0020
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0021"
down_revision: Union[str, None] = "0020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_order_status_audit_time", "order", ["status", "audit_status", "order_time"])
    op.create_index("ix_waybill_batch_no", "waybill", ["batch_no"])
    op.create_index("ix_pick_list_batch_no", "pick_list", ["batch_no"])
    op.create_index("ix_delivery_list_batch_no", "delivery_list", ["batch_no"])


def downgrade() -> None:
    op.drop_index("ix_delivery_list_batch_no", table_name="delivery_list")
    op.drop_index("ix_pick_list_batch_no", table_name="pick_list")
    op.drop_index("ix_waybill_batch_no", table_name="waybill")
    op.drop_index("ix_order_status_audit_time", table_name="order")
