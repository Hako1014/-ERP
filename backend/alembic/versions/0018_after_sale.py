"""订单售后与退货管理补充字段

Revision ID: 0018
Revises: 0017
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0018"
down_revision: Union[str, None] = "0017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("return_order") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_rejected_sign",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("return_person", sa.String(64), nullable=True)
        )
        batch_op.add_column(
            sa.Column("goods_status", sa.String(32), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "need_return_goods",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.add_column(
            sa.Column("stock_in_id", sa.BigInteger(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("audit_time", sa.DateTime(), nullable=True)
        )
        batch_op.create_foreign_key(
            op.f("fk_return_order_stock_in_id_stock_in"),
            "stock_in",
            ["stock_in_id"],
            ["id"],
        )
    op.add_column(
        "exchange_order",
        sa.Column("compensate_direction", sa.String(16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("exchange_order", "compensate_direction")
    with op.batch_alter_table("return_order") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_return_order_stock_in_id_stock_in"),
            type_="foreignkey",
        )
        batch_op.drop_column("audit_time")
        batch_op.drop_column("stock_in_id")
        batch_op.drop_column("need_return_goods")
        batch_op.drop_column("goods_status")
        batch_op.drop_column("return_person")
        batch_op.drop_column("is_rejected_sign")
