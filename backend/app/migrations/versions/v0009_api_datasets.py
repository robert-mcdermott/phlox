"""Private resumable bulk API data, separate from bounded citation excerpts."""
import sqlalchemy as sa
from alembic import op

revision = '0009_api_datasets'
down_revision = '0008_artifacts'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('api_datasets',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('source_id', sa.String(32), sa.ForeignKey('sources.id', ondelete='CASCADE'), nullable=False),
        sa.Column('pages', sa.Text, nullable=False),
        sa.Column('manifest_source_id', sa.String(32), nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False))
    op.create_index('ix_api_datasets_source_id', 'api_datasets', ['source_id'])


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; do not discard retained datasets')
