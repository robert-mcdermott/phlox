"""Private projects and inspectable per-turn context."""
import sqlalchemy as sa
from alembic import op

revision = '0006_projects'
down_revision = '0005_ingestion'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('projects',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), nullable=False),
        sa.Column('name', sa.String(120), nullable=False),
        sa.Column('description', sa.Text, nullable=False),
        sa.Column('instructions', sa.Text, nullable=False),
        sa.Column('document_ids', sa.JSON, nullable=False),
        sa.Column('archived', sa.Boolean, nullable=False),
        sa.Column('revision', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False))
    op.create_index('ix_projects_user_id', 'projects', ['user_id'])
    op.add_column('conversations', sa.Column('project_id', sa.String(32), nullable=True))
    op.create_index('ix_conversations_project_id', 'conversations', ['project_id'])
    op.create_table('context_records',
        sa.Column('turn_id', sa.String(32), primary_key=True),
        sa.Column('conversation_id', sa.String(32), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('data', sa.JSON, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False))
    op.create_index('ix_context_records_conversation_id', 'context_records', ['conversation_id'])


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; do not discard project context')
