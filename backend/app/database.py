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


def init_db() -> None:
    """Upgrade the checked, versioned schema before any application bootstrap writes."""
    from app.migrations import upgrade

    upgrade(ENGINE)
