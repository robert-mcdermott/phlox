"""SQLAlchemy engine + session setup.

Defaults to SQLite (a single file under ``DATA_DIR``); set ``DATABASE_URL`` (or
``database.url`` in config.yml) to deploy against Postgres instead. See
``app.config.get_database_url``.
"""
from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_database_url

DATABASE_URL = get_database_url()
IS_SQLITE = DATABASE_URL.startswith("sqlite")

_connect_args = {"check_same_thread": False} if IS_SQLITE else {}

ENGINE = create_engine(
    DATABASE_URL,
    connect_args=_connect_args,
    future=True,
)

SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, autocommit=False, future=True)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Columns added after the initial schema. create_all() won't ALTER existing tables on
# either backend, so we add any missing columns idempotently at startup. (A real migration
# tool like Alembic is the Tier-3 upgrade; this keeps dev data intact in the meantime.)
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "messages": {"attachments": "JSON", "usage": "JSON"},
    "documents": {"conversation_id": "VARCHAR(32)", "user_id": "VARCHAR(32)"},
    "conversations": {"user_id": "VARCHAR(32)"},
    "memories": {"user_id": "VARCHAR(32)"},
    "users": {"department": "VARCHAR(200)"},
    "mcp_servers": {"headers": "JSON", "auth_token": "VARCHAR(2000)"},
}


def _existing_columns(conn, table: str) -> set[str]:
    from sqlalchemy import text

    if IS_SQLITE:
        return {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
    rows = conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :t"),
        {"t": table},
    )
    return {row[0] for row in rows}


def _ensure_columns() -> None:
    from sqlalchemy import text

    with ENGINE.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            existing = _existing_columns(conn, table)
            for name, sqltype in cols.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}"))


def init_db() -> None:
    """Create tables. Imported lazily to avoid circular imports at module load."""
    from app import models  # noqa: F401

    models.Base.metadata.create_all(bind=ENGINE)
    _ensure_columns()
