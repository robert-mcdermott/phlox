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
    # Catalogs are picker suggestions, not an invocation allowlist. Discovered and
    # custom IDs must survive even when absent from the profile's static models.
    pcfg = get_profile(merged["active_profile"]) or {}
    merged["model"] = rows.get(_skey(user_id, "model")) or pcfg.get("model")
    return merged


def update_settings(db: Session, updates: dict[str, Any], user_id: str | None = None) -> dict[str, Any]:
    updates = dict(updates)
    profile = updates.get("active_profile")
    if profile and profile != get_settings(db, user_id)["active_profile"] and not updates.get("model"):
        # A profile-only switch uses its default rather than carrying the previous
        # provider's model across. An explicit model in the same update takes priority.
        updates["model"] = ""
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
        "max_context_tokens": settings["max_context_tokens"],
    }


def resolve_generation(settings, assistant_params=None, conversation_params=None):
    """Current settings for each new turn; old conversation params are historical seeds.

    Explicit overrides are marked by the conversation PATCH API so they can be
    distinguished from the identical-looking snapshots saved when chats were created.
    """
    params = generation_params(settings)
    sources = dict.fromkeys(params, 'runtime')
    assistant_params = assistant_params or {}
    params.update(assistant_params)
    sources.update({key: 'assistant' for key in sources if key in assistant_params})
    overrides = (conversation_params or {}).get('_generation_overrides', {})
    for key in sources:
        if key in overrides:
            params[key] = overrides[key]
            sources[key] = 'conversation_override'
    params['_setting_sources'] = sources
    return params
