"""Build provider instances from named config profiles + list available models."""
from __future__ import annotations

import logging
from typing import Any

from app.config import (
    get_default_profile_name,
    get_embeddings_config,
    get_profile,
    get_profiles,
)
from app.providers.base import LLMProvider
from app.providers.bedrock_provider import BedrockProvider
from app.providers.openai_provider import OpenAIProvider

logger = logging.getLogger(__name__)


def build_provider(profile_name: str, model: str | None = None) -> LLMProvider:
    """Construct a provider for the given profile, optionally overriding the model."""
    cfg = get_profile(profile_name)
    if cfg is None:
        raise ValueError(f"Unknown profile: {profile_name!r}")
    cfg = dict(cfg)  # copy so we can override
    if model:
        cfg["model"] = model

    ptype = cfg.get("type", "openai")
    if ptype == "bedrock":
        return BedrockProvider(cfg)
    if ptype == "openai":
        return OpenAIProvider(cfg)
    raise ValueError(f"Unknown provider type: {ptype!r}")


def list_profiles() -> list[dict[str, Any]]:
    """Return profile summaries for the UI (no secrets)."""
    out = []
    for name, cfg in get_profiles().items():
        out.append(
            {
                "name": name,
                "label": cfg.get("label", name),
                "type": cfg.get("type", "openai"),
                "model": cfg.get("model"),
                "models": cfg.get("models", []),
                "supports_tools": cfg.get("supports_tools", True),
            }
        )
    return out


def list_models(profile_name: str) -> list[str]:
    """List models for a profile.

    Prefers the explicit ``models`` list in config; for OpenAI-type profiles with no
    list, attempts a live ``/models`` query; falls back to the single configured model.
    """
    cfg = get_profile(profile_name)
    if not cfg:
        return []
    if cfg.get("models"):
        return list(cfg["models"])
    if cfg.get("type", "openai") == "openai":
        try:
            from openai import OpenAI

            client = OpenAI(
                base_url=cfg.get("endpoint", "https://api.openai.com/v1"),
                api_key=cfg.get("api_key") or "not-needed",
            )
            return sorted(m.id for m in client.models.list().data)
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not list models for %s: %s", profile_name, e)
    return [cfg["model"]] if cfg.get("model") else []


def gateway_models() -> list[dict[str, Any]]:
    """Flat list of callable models across all profiles, for the gateway ``GET /v1/models``.

    Each entry advertises both a bare ``model`` id and a fully-qualified ``profile/model``
    id (returned as the OpenAI ``id``), since the same model id (e.g. ``gpt-4o``) can exist
    under several profiles. Clients may call either form; ``resolve_model`` disambiguates.
    """
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name, cfg in get_profiles().items():
        models = list(cfg.get("models") or ([cfg["model"]] if cfg.get("model") else []))
        for m in models:
            qualified = f"{name}/{m}"
            if qualified in seen:
                continue
            seen.add(qualified)
            out.append({"id": qualified, "profile": name, "model": m})
    return out


def resolve_model(model: str) -> tuple[str, str]:
    """Resolve a gateway ``model`` string to a concrete ``(profile, model)`` pair.

    Accepts either ``"profile/model"`` (explicit) or a bare ``"model"`` (resolved against
    the profile catalog; falls back to the default profile so a bare id always works for the
    common single-profile deployment). Raises ``ValueError`` if it can't be resolved.
    """
    if not model:
        raise ValueError("A 'model' is required.")
    profiles = get_profiles()
    # Explicit "profile/model" form.
    if "/" in model:
        profile, _, bare = model.partition("/")
        if profile in profiles:
            return profile, bare
    # Bare model id: first profile that lists it (or whose single model matches).
    for name, cfg in profiles.items():
        catalog = cfg.get("models") or ([cfg["model"]] if cfg.get("model") else [])
        if model in catalog:
            return name, model
    # Last resort: hand the bare id to the default profile (overrides its configured model).
    default = get_default_profile_name()
    if default:
        return default, model
    raise ValueError(f"Unknown model: {model!r}")


def build_embedder() -> tuple[LLMProvider | None, str | None]:
    """Return (provider, model) for embeddings, or (None, None) to use the fallback."""
    ecfg = get_embeddings_config()
    profile = ecfg.get("profile")
    if not profile or not get_profile(profile):
        return None, None
    try:
        return build_provider(profile), ecfg.get("model", "text-embedding-3-small")
    except Exception as e:  # noqa: BLE001
        logger.warning("Embedder unavailable: %s", e)
        return None, None
