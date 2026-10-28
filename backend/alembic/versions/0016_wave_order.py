"""波次订单关联表

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016"
down_revision: Union[str, None] = "0015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wave_order",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "wave_id", sa.BigInteger(), sa.ForeignKey("wave.id"), nullable=False
        ),
        sa.Column(
            "order_id", sa.BigInteger(), sa.ForeignKey("order.id"), nullable=False
        ),
        sa.Column("load_no", sa.String(64), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sort_key", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wave_order")),
        sa.UniqueConstraint(
            "order_id", name="uq_wave_order_order_id"
        ),
    )
    op.create_index(
        "ix_wave_order_wave_id",
        "wave_order",
        ["wave_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_wave_order_wave_id", table_name="wave_order")
    op.drop_table("wave_order")
