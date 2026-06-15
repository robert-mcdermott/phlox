"""Build provider instances from named config profiles + list available models."""
from __future__ import annotations

import logging
from typing import Any

from app.config import get_embeddings_config, get_profile, get_profiles
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
