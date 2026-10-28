"""基线迁移：建立 Alembic 版本基线。

业务表在阶段 2（TASK-009 ~ TASK-016）的数据模型任务卡中陆续添加。

Revision ID: 0001
Revises:
Create Date: 2026-09-23
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
