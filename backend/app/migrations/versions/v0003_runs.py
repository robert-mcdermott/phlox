"""Private run queue and bounded replay log. Frozen schema: do not import ORM models."""
import sqlalchemy as sa
from alembic import op

revision = '0003_runs'
down_revision = '0002_ledger_width'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('runs',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('user_id', sa.String(32), nullable=False),
        sa.Column('conversation_id', sa.String(32), sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False),
        sa.Column('active_conversation_id', sa.String(32), nullable=True, unique=True),
        sa.Column('request_key', sa.String(100), nullable=False),
        sa.Column('request_hash', sa.String(64), nullable=False),
        sa.Column('context_version', sa.Integer, nullable=False),
        sa.Column('payload', sa.JSON, nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('reason', sa.Text, nullable=True),
        sa.Column('pending_id', sa.String(32), nullable=True),
        sa.Column('message_id', sa.String(32), nullable=True),
        sa.Column('last_seq', sa.Integer, nullable=False),
        sa.Column('event_bytes', sa.Integer, nullable=False),
        sa.Column('events_expired', sa.Boolean, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.UniqueConstraint('user_id', 'request_key', name='uq_run_request'),
    )
    for column in ('user_id', 'conversation_id', 'status'):
        op.create_index(f'ix_runs_{column}', 'runs', [column])
    op.create_table('run_events',
        sa.Column('run_id', sa.String(32), sa.ForeignKey('runs.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('seq', sa.Integer, primary_key=True),
        sa.Column('data', sa.JSON, nullable=False),
    )
    op.create_table('tool_executions',
        sa.Column('id', sa.String(32), primary_key=True),
        sa.Column('run_id', sa.String(32), sa.ForeignKey('runs.id', ondelete='CASCADE'), nullable=False),
        sa.Column('call_id', sa.String(200), nullable=False),
        sa.Column('name', sa.String(200), nullable=False),
        sa.Column('status', sa.String(24), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
    )
    op.create_index('ix_tool_executions_run_id', 'tool_executions', ['run_id'])


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; private run data must not be silently dropped')
