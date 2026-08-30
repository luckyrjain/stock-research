"""add profiles ownership (client_id/user_id, matching watchlist_items)

Revision ID: ec7850b73d2f
Revises: 976659233671
Create Date: 2026-08-23 10:30:00.000000

A pre-existing deployment's `profiles` rows (from before this column
existed) have neither `client_id` nor `user_id` set and will violate
`ck_profiles_exactly_one_owner` below. Backfill ownership on those rows (or
delete them) before running this migration — see db/models.py's own comment
on the `profiles` table for the full reasoning.

The symmetric risk applies to `downgrade()`: it re-creates a single
deployment-wide `UNIQUE(name)` constraint, which fails if two different
owners have since created a profile with the same name (now legitimately
allowed by `uq_profiles_client_name`/`uq_profiles_user_name` above) — rename
or delete one side of any such collision before rolling back this far.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ec7850b73d2f'
down_revision: Union[str, Sequence[str], None] = '976659233671'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('profiles', sa.Column('client_id', sa.String(length=36), nullable=True))
    op.add_column('profiles', sa.Column('user_id', sa.Integer(), nullable=True))
    op.drop_constraint('profiles_name_key', 'profiles', type_='unique')
    op.create_foreign_key('profiles_user_id_fkey', 'profiles', 'users', ['user_id'], ['id'])
    op.create_check_constraint(
        'ck_profiles_exactly_one_owner', 'profiles', '(client_id IS NULL) <> (user_id IS NULL)',
    )
    op.create_unique_constraint('uq_profiles_client_name', 'profiles', ['client_id', 'name'])
    op.create_unique_constraint('uq_profiles_user_name', 'profiles', ['user_id', 'name'])
    op.create_index('idx_profiles_client', 'profiles', ['client_id'])
    op.create_index('idx_profiles_user', 'profiles', ['user_id'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('idx_profiles_user', table_name='profiles')
    op.drop_index('idx_profiles_client', table_name='profiles')
    op.drop_constraint('uq_profiles_user_name', 'profiles', type_='unique')
    op.drop_constraint('uq_profiles_client_name', 'profiles', type_='unique')
    op.drop_constraint('ck_profiles_exactly_one_owner', 'profiles', type_='check')
    op.drop_constraint('profiles_user_id_fkey', 'profiles', type_='foreignkey')
    op.create_unique_constraint('profiles_name_key', 'profiles', ['name'])
    op.drop_column('profiles', 'user_id')
    op.drop_column('profiles', 'client_id')
