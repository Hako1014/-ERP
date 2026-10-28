"""Add safe self-service profile fields for staff."""

from alembic import op
import sqlalchemy as sa


revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("staff", sa.Column("display_name", sa.String(64), nullable=True))
    op.add_column("staff", sa.Column("avatar_storage_key", sa.String(64), nullable=True))
    op.add_column("staff", sa.Column("avatar_media_type", sa.String(64), nullable=True))
    op.add_column("staff", sa.Column("avatar_size", sa.BigInteger(), nullable=True))
    op.add_column("staff", sa.Column("avatar_sha256", sa.String(64), nullable=True))


def downgrade():
    op.drop_column("staff", "avatar_sha256")
    op.drop_column("staff", "avatar_size")
    op.drop_column("staff", "avatar_media_type")
    op.drop_column("staff", "avatar_storage_key")
    op.drop_column("staff", "display_name")
