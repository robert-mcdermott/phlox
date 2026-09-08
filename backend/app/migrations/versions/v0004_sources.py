"""Captured document evidence and typed message citations."""
import sqlalchemy as sa
from alembic import op

revision = '0004_sources'
down_revision = '0003_runs'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('citations', sa.JSON, nullable=True))
    op.create_table('sources',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('conversation_id', sa.String(32), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('number', sa.Integer, nullable=False),
        sa.Column('fingerprint', sa.String(64), nullable=False),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('document_id', sa.String(32), nullable=True),
        sa.Column('chunk_id', sa.String(32), nullable=True),
        sa.Column('title', sa.String(500), nullable=True),
        sa.Column('url', sa.Text, nullable=True),
        sa.Column('excerpt', sa.Text, nullable=True),
        sa.Column('content_hash', sa.String(64), nullable=False),
        sa.Column('location', sa.JSON, nullable=True),
        sa.Column('captured_at', sa.DateTime, nullable=False),
        sa.Column('expires_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('conversation_id', 'number', name='uq_source_number'),
        sa.UniqueConstraint('conversation_id', 'fingerprint', name='uq_source_fingerprint'),
    )
    for name in ('conversation_id', 'document_id', 'expires_at'):
        op.create_index(f'ix_sources_{name}', 'sources', [name])
    op.create_table('source_uses',
        sa.Column('turn_id', sa.String(64), primary_key=True),
        sa.Column('source_id', sa.String(32), sa.ForeignKey('sources.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('query', sa.String(500), nullable=False),
    )


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; do not silently discard citation evidence')
