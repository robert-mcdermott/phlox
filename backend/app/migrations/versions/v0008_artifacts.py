"""Versioned text artifacts; existing answer snapshots remain unchanged."""
import sqlalchemy as sa
from alembic import op

revision = '0008_artifacts'
down_revision = '0007_branches'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('artifacts',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('conversation_id', sa.String(32), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('path', sa.String(1000), nullable=False),
        sa.Column('path_key', sa.String(64), nullable=False),
        sa.Column('head_version_id', sa.String(32), nullable=True),
        sa.UniqueConstraint('conversation_id', 'path_key'))
    op.create_index('ix_artifacts_conversation_id', 'artifacts', ['conversation_id'])
    op.create_table('artifact_versions',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('artifact_id', sa.String(32), sa.ForeignKey('artifacts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('number', sa.Integer, nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('sha256', sa.String(64), nullable=False),
        sa.Column('parent_version_id', sa.String(32), nullable=True),
        sa.Column('origin', sa.String(30), nullable=False),
        sa.Column('source_message_id', sa.String(32), nullable=True),
        sa.Column('details', sa.JSON, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('artifact_id', 'number'))
    op.create_index('ix_artifact_versions_artifact_id', 'artifact_versions', ['artifact_id'])


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; do not discard artifact versions')
