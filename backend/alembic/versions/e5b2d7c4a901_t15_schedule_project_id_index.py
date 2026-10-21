"""t15 schedule project_id index

补上数据模型 §10 承诺的项目作用域索引(issue #43 review)。

Revision ID: e5b2d7c4a901
Revises: d4a1c8e9f652
Create Date: 2026-10-21

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e5b2d7c4a901'
down_revision: Union[str, None] = 'd4a1c8e9f652'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index('ix_schedule_project_id', 'schedule', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_schedule_project_id', table_name='schedule')
