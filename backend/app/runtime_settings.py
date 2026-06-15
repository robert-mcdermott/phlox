"""Runtime settings backed by the ``Setting`` table, seeded from config.yml defaults.

Keys: active_profile, model, theme, system_prompt, temperature, max_tokens,
max_tool_rounds. Use ``get_settings`` / ``update_settings``.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.config import default_generation_params, get_default_profile_name, get_profile
from app.models import Setting

_KEYS = (
    "active_profile",
    "model",
    "theme",
    "system_prompt",
    "temperature",
    "max_tokens",
    "max_tool_rounds",
    "max_context_tokens",
)


def _defaults() -> dict[str, Any]:
    gen = default_generation_params()
    profile = get_default_profile_name()
    pcfg = get_profile(profile) or {}
    return {
        "active_profile": profile,
        "model": pcfg.get("model"),
        "theme": "phlox-dark",
        "system_prompt": gen["system_prompt"],
        "temperature": gen["temperature"],
        "max_tokens": gen["max_tokens"],
        "max_tool_rounds": gen["max_tool_rounds"],
        "max_context_tokens": gen["max_context_tokens"],
    }


def _skey(user_id: str | None, key: str) -> str:
    """Namespace settings per user so each user keeps their own model/theme/params."""
    return f"{user_id}:{key}" if user_id else key


def get_settings(db: Session, user_id: str | None = None) -> dict[str, Any]:
    keys = [_skey(user_id, k) for k in _KEYS]
    rows = {s.key: s.value for s in db.query(Setting).filter(Setting.key.in_(keys)).all()}
    merged = _defaults()
    for k in _KEYS:
        v = rows.get(_skey(user_id, k))
        if v is not None:
            merged[k] = v
    merged["model"] = _heal_model(merged["active_profile"], merged.get("model"))
    return merged


def _heal_model(profile_name: str, model: str | None) -> str | None:
    """Keep the active model consistent with the profile catalog.

    If the stored model isn't valid for the active profile (e.g. the profile's models were
    changed in config.yml, leaving a stale DB value), fall back to the profile's configured
    model. Prevents a stale DB setting from silently overriding config edits.
    """
    pcfg = get_profile(profile_name)
    if not pcfg:
        return model
    known = set(pcfg.get("models") or [])
    if pcfg.get("model"):
        known.add(pcfg["model"])
    if model and known and model not in known:
        return pcfg.get("model")
    return model or pcfg.get("model")


def update_settings(db: Session, updates: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    for key, value in updates.items():
        if key not in _KEYS or value is None:
            continue
        sk = _skey(user_id, key)
        row = db.get(Setting, sk)
        if row is None:
            db.add(Setting(key=sk, value=value))
        else:
            row.value = value
    db.commit()
    return get_settings(db, user_id)


def generation_params(settings: dict[str, Any]) -> dict[str, Any]:
    return {
        "temperature": settings["temperature"],
        "max_tokens": settings["max_tokens"],
        "max_tool_rounds": settings["max_tool_rounds"],
    }
