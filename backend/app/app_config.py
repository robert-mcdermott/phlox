"""DB-backed overlay for deployment configuration, applied live (no restart).

``config.yml`` is the seed/bootstrap; the ``app_config`` table holds admin overrides for
whole config *sections*. The getters in :mod:`app.config` merge these over the file values,
so every existing consumer (``build_provider``, ``compute_cost``, the providers' resilience
reads, the sandbox runner, etc.) transparently picks up admin edits.

This module is read on nearly every config access, so values are cached in-process and the
cache is invalidated on write — mirroring ``config.load_config``'s ``@lru_cache`` and the
sandbox ``_runner`` singleton. The app is effectively single-process (embedded Qdrant locks
its dir to one process), so cross-process cache invalidation is intentionally out of scope.

``SessionLocal`` is imported lazily inside the functions to avoid a circular import:
``config.py`` imports this module, and ``database.py`` imports ``config`` for ``DB_PATH``.
"""
from __future__ import annotations

from typing import Any

# Section names that may be overridden. Kept here so config.py and the router agree.
SECTIONS = ("profiles", "pricing", "resilience", "generation", "sandbox", "suggestions")

# Process-wide cache: section name -> stored value (dict/list). Absent key => not loaded;
# present-but-None => loaded and confirmed to have no DB override.
_cache: dict[str, Any] = {}
_loaded = False


def invalidate() -> None:
    """Drop the in-process cache so the next read reloads from the DB."""
    global _loaded
    _cache.clear()
    _loaded = False


def _load() -> None:
    global _loaded
    if _loaded:
        return
    from sqlalchemy.exc import OperationalError

    from app.database import SessionLocal
    from app.models import AppConfig

    db = SessionLocal()
    try:
        for row in db.query(AppConfig).all():
            _cache[row.section] = row.value
    except OperationalError:
        # The app_config table doesn't exist yet — a config getter ran before init_db()
        # created the schema (e.g. setup_observability at import time on a fresh database).
        # There can be no overrides yet, so treat it as absence and retry on the next read
        # (don't set _loaded) once the tables exist.
        return
    finally:
        db.close()
    _loaded = True


def get_section(name: str) -> Any | None:
    """Return the admin override for ``name``, or ``None`` if there is no override.

    ``None`` means "fall back to config.yml" — callers must treat it as absence, not as an
    empty override.
    """
    _load()
    return _cache.get(name)


def all_sections() -> dict[str, Any]:
    """Return all stored overrides keyed by section (for diagnostics/metadata)."""
    _load()
    return dict(_cache)


def set_section(name: str, value: Any, updated_by: str | None = None) -> None:
    """Upsert the override for ``name`` and invalidate the cache."""
    from app.database import SessionLocal
    from app.models import AppConfig

    db = SessionLocal()
    try:
        row = db.get(AppConfig, name)
        if row is None:
            db.add(AppConfig(section=name, value=value, updated_by=updated_by))
        else:
            row.value = value
            row.updated_by = updated_by
        db.commit()
    finally:
        db.close()
    invalidate()
