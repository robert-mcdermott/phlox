"""Programmatic Alembic entry point, shared by startup and the operator CLI."""
from pathlib import Path
from threading import RLock

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

_MIGRATION_LOCK = RLock()  # Alembic's EnvironmentContext/op proxies are process-global.


class MigrationError(RuntimeError):
    pass


def config(connection=None):
    cfg = Config()
    cfg.set_main_option('script_location', str(Path(__file__).parent))
    cfg.attributes['connection'] = connection
    return cfg


def status(engine):
    with engine.connect() as conn:
        current = MigrationContext.configure(conn).get_current_revision()
    return {'current': current, 'head': ScriptDirectory.from_config(config()).get_current_head()}


def known_revision(revision):
    return revision is None or revision in {
        item.revision for item in ScriptDirectory.from_config(config()).walk_revisions()
    }


def check(engine):
    """Read-only schema compatibility check with a safe, concrete operator diagnostic."""
    from app.migrations.baseline import validate
    from app.models import Base

    revision = status(engine)
    if not known_revision(revision['current']):
        raise MigrationError('Unrecognized schema revision; use its matching Phlox release')
    try:
        with engine.connect() as conn:
            validate(conn, legacy=revision['current'] is None,
                     expected=Base.metadata if revision['current'] else None,
                     allow_legacy_ledger_width=revision['current'] == '0001_wave3')
    except ValueError as exc:
        raise MigrationError(f'Schema check failed: {exc}. Preserve a backup; do not stamp manually.') from None
    return {**revision, 'compatible': True}


def upgrade(engine):
    """Serialize and atomically upgrade, including SQLite DDL and baseline adoption."""
    with _MIGRATION_LOCK:
        _upgrade(engine)


def _upgrade(engine):
    try:
        with engine.connect() as conn:
            if conn.dialect.name == 'sqlite':
                conn.exec_driver_sql('BEGIN IMMEDIATE')
            elif conn.dialect.name == 'postgresql':
                conn.exec_driver_sql("SET LOCAL lock_timeout = '10s'")
                conn.exec_driver_sql('SELECT pg_advisory_xact_lock(1886154616)')
            else:
                raise ValueError('Only SQLite and Postgres are supported')
            try:
                command.upgrade(config(conn), 'head')
                from app.migrations.baseline import validate
                from app.models import Base
                validate(conn, expected=Base.metadata)
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
    except Exception as exc:
        # Avoid SQL/connection strings in the outer startup diagnostic.
        raise MigrationError('Database upgrade failed. Stop Phlox, preserve a backup, and run '
                             '`uv run -m app.ops db check`. Check schema compatibility and '
                             'use the matching release; see docs/BACKUP_RESTORE.md. '
                             f'Failure type: {type(exc).__name__}') from exc
