"""允许 MCP 凭据不设置到期时间。"""

from alembic import op
import sqlalchemy as sa


revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("mcp_credential") as batch_op:
        batch_op.alter_column(
            "expires_at", existing_type=sa.DateTime(), nullable=True,
        )


def downgrade() -> None:
    connection = op.get_bind()
    permanent_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM mcp_credential WHERE expires_at IS NULL")
    ).scalar_one()
    if permanent_count:
        raise RuntimeError("存在永久 MCP 凭据，撤销或设定到期时间后才能降级")
    with op.batch_alter_table("mcp_credential") as batch_op:
        batch_op.alter_column(
            "expires_at", existing_type=sa.DateTime(), nullable=False,
        )
