"""Recoverable document processing and versioned chunk provenance/embeddings."""
import sqlalchemy as sa
from alembic import op

revision = '0005_ingestion'
down_revision = '0004_sources'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('documents', sa.Column('ingestion', sa.JSON, nullable=True))
    op.add_column('doc_chunks', sa.Column('provenance', sa.JSON, nullable=True))
    op.add_column('doc_chunks', sa.Column('embedding_identity', sa.JSON, nullable=True))


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; do not discard ingestion metadata')
