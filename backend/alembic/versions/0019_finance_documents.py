"""补充结算单导入与确认字段

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0019"
down_revision: Union[str, None] = "0018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "expense",
        sa.Column("voucher_no", sa.String(64), nullable=True),
    )
    op.add_column(
        "income",
        sa.Column("voucher_no", sa.String(64), nullable=True),
    )
    with op.batch_alter_table("settlement") as batch_op:
        batch_op.add_column(sa.Column("platform_no", sa.String(64), nullable=True))
        batch_op.add_column(sa.Column("settlement_date", sa.Date(), nullable=True))
        batch_op.add_column(sa.Column("source_file", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("remark", sa.String(255), nullable=True))
        batch_op.add_column(sa.Column("checked_by", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("checked_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("confirmed_by", sa.BigInteger(), nullable=True))
        batch_op.add_column(sa.Column("confirmed_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("receivable_id", sa.BigInteger(), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_settlement_checked_by_staff"),
            "staff",
            ["checked_by"],
            ["id"],
        )
        batch_op.create_foreign_key(
            op.f("fk_settlement_confirmed_by_staff"),
            "staff",
            ["confirmed_by"],
            ["id"],
        )
        batch_op.create_foreign_key(
            op.f("fk_settlement_receivable_id_invoice"),
            "invoice",
            ["receivable_id"],
            ["id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("settlement") as batch_op:
        batch_op.drop_constraint(
            op.f("fk_settlement_receivable_id_invoice"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f("fk_settlement_confirmed_by_staff"),
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            op.f("fk_settlement_checked_by_staff"),
            type_="foreignkey",
        )
        batch_op.drop_column("receivable_id")
        batch_op.drop_column("confirmed_at")
        batch_op.drop_column("confirmed_by")
        batch_op.drop_column("checked_at")
        batch_op.drop_column("checked_by")
        batch_op.drop_column("remark")
        batch_op.drop_column("source_file")
        batch_op.drop_column("settlement_date")
        batch_op.drop_column("platform_no")
    op.drop_column("income", "voucher_no")
    op.drop_column("expense", "voucher_no")
