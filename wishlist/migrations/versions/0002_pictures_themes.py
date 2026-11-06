"""item pictures, list themes and app settings

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-06 12:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('app_settings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('design', sa.String(length=20), nullable=False),
    sa.Column('default_theme', sa.String(length=20), nullable=False),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_app_settings'))
    )
    app_settings = sa.table('app_settings', sa.column('id'), sa.column('design'), sa.column('default_theme'))
    op.bulk_insert(app_settings, [{'id': 1, 'design': 'a', 'default_theme': 'classic'}])
    with op.batch_alter_table('item', schema=None) as batch_op:
        batch_op.add_column(sa.Column('image', sa.String(length=64), nullable=True))

    with op.batch_alter_table('wishlist', schema=None) as batch_op:
        batch_op.add_column(sa.Column('theme', sa.String(length=20), server_default='classic', nullable=False))


def downgrade() -> None:
    # keep AUTOINCREMENT: ids must never be reused
    with op.batch_alter_table('wishlist', schema=None, table_kwargs={'sqlite_autoincrement': True}) as batch_op:
        batch_op.drop_column('theme')

    with op.batch_alter_table('item', schema=None, table_kwargs={'sqlite_autoincrement': True}) as batch_op:
        batch_op.drop_column('image')

    op.drop_table('app_settings')
