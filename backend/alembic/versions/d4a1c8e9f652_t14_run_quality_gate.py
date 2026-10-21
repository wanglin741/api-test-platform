"""t14 run quality_gate

Revision ID: d4a1c8e9f652
Revises: c3f7a91b2d44
Create Date: 2026-10-21 15:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "d4a1c8e9f652"
down_revision: Union[str, None] = "c3f7a91b2d44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 门禁配置持久化在 run 上(spec #35 决策 #10)
    op.add_column("run", sa.Column("quality_gate", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("run", "quality_gate")
