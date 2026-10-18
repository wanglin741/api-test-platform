"""t9 folder and test_case.folder_id

Revision ID: a3f9c41d7b52
Revises: e2d22792afea
Create Date: 2026-10-18 10:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'a3f9c41d7b52'
down_revision: Union[str, None] = 'e2d22792afea'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'folder',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('project_id', sa.BigInteger(), nullable=False),
        sa.Column('scope', sa.String(length=16), nullable=False),
        sa.Column('parent_id', sa.BigInteger(), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('sort_order', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['parent_id'], ['folder.id'], name=op.f('fk_folder_parent_id_folder'), ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['project_id'], ['project.id'], name=op.f('fk_folder_project_id_project')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_folder')),
        sa.UniqueConstraint('project_id', 'scope', 'parent_id', 'name', name=op.f('uq_folder_project_id')),
    )
    # 存量用例 folder_id 为空 = 「未分类」,不做回填(ADR-0001)
    op.add_column(
        'test_case',
        sa.Column('folder_id', sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        'fk_test_case_folder_id_folder', 'test_case', 'folder', ['folder_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_test_case_folder_id_folder', 'test_case', type_='foreignkey')
    op.drop_column('test_case', 'folder_id')
    op.drop_table('folder')
