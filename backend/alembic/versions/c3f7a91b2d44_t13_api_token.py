"""t13 api_token

Revision ID: c3f7a91b2d44
Revises: 1aa4b3bb4db0
Create Date: 2026-10-21 14:02:11.483002

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'c3f7a91b2d44'
down_revision: Union[str, None] = '1aa4b3bb4db0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('api_token',
    sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
    sa.Column('project_id', sa.BigInteger(), nullable=False),
    sa.Column('user_id', sa.BigInteger(), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('scopes', JSONB(), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['project_id'], ['project.id'], name=op.f('fk_api_token_project_id_project')),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], name=op.f('fk_api_token_user_id_user')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_api_token')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_api_token_token_hash'))
    )
    op.create_index(op.f('ix_api_token_project_id'), 'api_token', ['project_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_api_token_project_id'), table_name='api_token')
    op.drop_table('api_token')
