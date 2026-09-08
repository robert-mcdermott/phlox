"""Widen the historical usage ledger receipt ID without changing stored usage."""
import sqlalchemy as sa
from alembic import op

revision = '0002_ledger_width'
down_revision = '0001_wave3'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    column = next(c for c in sa.inspect(conn).get_columns('usage_ledger') if c['name'] == 'message_id')
    if isinstance(column['type'], sa.String) and column['type'].length == 64:
        return
    if not isinstance(column['type'], sa.String) or column['type'].length != 32:
        raise ValueError('Unrecognized usage_ledger.message_id type; expected VARCHAR(32) or VARCHAR(64)')
    if conn.dialect.name == 'sqlite':
        triggers = conn.exec_driver_sql("SELECT 1 FROM sqlite_master WHERE type = 'trigger' "
                                       "AND tbl_name = 'usage_ledger' LIMIT 1").first()
        if triggers:
            raise ValueError('Custom usage ledger triggers need an explicit preservation migration')
    # Alembic copies SQLite rows transactionally, preserving reflected indexes and
    # constraints. A naming convention makes its anonymous UNIQUE constraint explicit.
    with op.batch_alter_table('usage_ledger', naming_convention={
        'uq': 'uq_%(table_name)s_%(column_0_name)s',
    }) as batch:
        batch.alter_column('message_id', type_=sa.String(64), existing_type=sa.String(32),
                           existing_nullable=column['nullable'])


def downgrade():
    raise RuntimeError('Do not narrow receipt IDs; restore a backup with its matching release')
