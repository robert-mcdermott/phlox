"""Preserve conversation alternatives with immutable message ancestry."""
import sqlalchemy as sa
from alembic import op

revision = '0007_branches'
down_revision = '0006_projects'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('messages', sa.Column('parent_id', sa.String(32), nullable=True))
    op.create_index('ix_messages_parent_id', 'messages', ['parent_id'])
    op.add_column('conversations', sa.Column('active_leaf_id', sa.String(32), nullable=True))
    op.add_column('conversations', sa.Column('branch_choices', sa.JSON, nullable=True))
    conn = op.get_bind()
    # Stream the old linear histories; do not load all transcript contents into memory.
    messages = sa.table('messages', sa.column('id'), sa.column('conversation_id'),
                        sa.column('created_at'), sa.column('parent_id'))
    conversations = sa.table('conversations', sa.column('id'), sa.column('active_leaf_id'))
    previous, conversation = None, None
    for row in conn.execute(sa.select(messages.c.id, messages.c.conversation_id).order_by(
            messages.c.conversation_id, messages.c.created_at, messages.c.id)):
        if row.conversation_id != conversation:
            if conversation:
                conn.execute(conversations.update().where(conversations.c.id == conversation).values(active_leaf_id=previous))
            conversation, previous = row.conversation_id, None
        conn.execute(messages.update().where(messages.c.id == row.id).values(parent_id=previous))
        previous = row.id
    if conversation:
        conn.execute(conversations.update().where(conversations.c.id == conversation).values(active_leaf_id=previous))


def downgrade():
    raise RuntimeError('Restore a backup with its matching release; do not discard conversation alternatives')
