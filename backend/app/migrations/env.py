"""Migrations run through app.migrations.upgrade with one owned transaction."""
from alembic import context

connection = context.config.attributes.get('connection')
if connection is None:
    raise RuntimeError('Use uv run -m app.ops db upgrade; a managed connection is required')
context.configure(connection=connection, transactional_ddl=True)
with context.begin_transaction():
    context.run_migrations()
