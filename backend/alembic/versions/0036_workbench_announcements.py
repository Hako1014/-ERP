"""Add company-scoped announcement records for the workbench."""

from alembic import op
import sqlalchemy as sa


revision = "0036"
down_revision = "0035"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "announcement",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("company_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(128), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="DRAFT"),
        sa.Column("audience_roles", sa.JSON(), nullable=True),
        sa.Column("audience_department_ids", sa.JSON(), nullable=True),
        sa.Column("starts_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("published_at", sa.DateTime(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.current_timestamp(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], name="fk_announcement_company_id"),
        sa.ForeignKeyConstraint(["created_by"], ["staff.id"], name="fk_announcement_created_by"),
    )
    op.create_index(
        "ix_announcement_company_status_published",
        "announcement",
        ["company_id", "status", "published_at"],
    )
    op.create_index(
        "ix_announcement_effective_period",
        "announcement",
        ["starts_at", "expires_at"],
    )


def downgrade():
    op.drop_index("ix_announcement_effective_period", table_name="announcement")
    op.drop_index("ix_announcement_company_status_published", table_name="announcement")
    op.drop_table("announcement")
