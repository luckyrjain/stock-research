"""add app_state table for durable JSON state

Replaces the per-record JSON files this codebase used to write under
output/ — market-picks daily snapshots, source health/quality telemetry,
scraper-error and LLM-cost counters, CAS import archives, CLI reports.
output/ is now strictly a regenerable TTL cache; nothing durable lives there.

Revision ID: a7f2c1d09b34
Revises: 8613aafc2d9d
Create Date: 2026-08-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7f2c1d09b34'
down_revision: Union[str, Sequence[str], None] = '8613aafc2d9d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'app_state',
        sa.Column('namespace', sa.String(length=40), nullable=False),
        sa.Column('key', sa.String(length=200), nullable=False),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True),
                  server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        # The composite PK also serves every read — `WHERE namespace = ?
        # ORDER BY key` is a prefix scan, so no separate index is needed.
        sa.PrimaryKeyConstraint('namespace', 'key'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('app_state')
