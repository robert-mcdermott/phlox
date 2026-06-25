"""Application configuration.

Loads ``config.yml`` (provider profiles + defaults) and exposes paths used across
the app. Runtime-mutable settings (active profile, generation params, system prompt,
theme) live in the database ``Setting`` table — see ``app/routers/settings.py``.
``config.yml`` provides the *catalog* of profiles and the initial defaults.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BACKEND_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(os.environ.get("PHLOX_CONFIG", BACKEND_DIR / "config.yml"))

DATA_DIR = Path(os.environ.get("PHLOX_DATA", BACKEND_DIR / "data"))
WORKSPACES_DIR = DATA_DIR / "workspaces"
UPLOADS_DIR = DATA_DIR / "uploads"
ATTACHMENTS_DIR = DATA_DIR / "attachments"
DB_PATH = DATA_DIR / "phlox.db"

for _d in (DATA_DIR, WORKSPACES_DIR, UPLOADS_DIR, ATTACHMENTS_DIR):
    _d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# config.yml loading
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load and cache ``config.yml``. Returns {} if it does not exist."""
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def reload_config() -> dict[str, Any]:
    load_config.cache_clear()
    return load_config()


def get_profiles() -> dict[str, dict[str, Any]]:
    """Return the profile catalog keyed by profile name.

    If an admin has saved a ``profiles`` override (see :mod:`app.app_config`), it *replaces*
    the file catalog wholesale — so the UI can add, edit, and **delete** profiles, including
    ones originally seeded from config.yml.
    """
    from app import app_config

    overlay = app_config.get_section("profiles")
    if overlay is not None:
        return overlay
    return load_config().get("profiles", {}) or {}


def get_profile(name: str) -> dict[str, Any] | None:
    return get_profiles().get(name)


def get_defaults() -> dict[str, Any]:
    """Deployment-wide generation defaults; an admin ``generation`` override shallow-merges
    over the file ``defaults`` (so a partial override changes only the keys it sets)."""
    from app import app_config

    merged = dict(load_config().get("defaults", {}) or {})
    overlay = app_config.get_section("generation")
    if overlay:
        merged.update(overlay)
    return merged


def get_default_profile_name() -> str:
    cfg = load_config()
    explicit = cfg.get("default_profile")
    if explicit and explicit in get_profiles():
        return explicit
    profiles = get_profiles()
    return next(iter(profiles), "")


def get_embeddings_config() -> dict[str, Any]:
    return load_config().get("embeddings", {}) or {}


def get_observability_config() -> dict[str, Any]:
    """Observability settings: structured logging, model pricing, optional OTel.

    Only ``pricing`` is admin-editable (applied live for cost/chargeback); ``request_logging``
    and ``otel`` are wired once at startup and stay file-only.
    """
    from app import app_config

    cfg = dict(load_config().get("observability", {}) or {})
    cfg.setdefault("request_logging", True)
    cfg.setdefault("pricing", {})  # {"<model-id>": {"input": usd_per_1M, "output": usd_per_1M}}
    cfg.setdefault("otel", {})     # {"endpoint": "http://collector:4318", "service_name": "phlox"}
    pricing_overlay = app_config.get_section("pricing")
    if pricing_overlay is not None:
        cfg["pricing"] = pricing_overlay
    return cfg


def get_resilience_config() -> dict[str, Any]:
    """Provider resilience: timeouts, retries, and an optional fallback profile.

    Admin-editable: a ``resilience`` override shallow-merges over the file values; the
    ``setdefault``s then fill anything neither source supplied.
    """
    from app import app_config

    cfg = dict(load_config().get("resilience", {}) or {})
    overlay = app_config.get_section("resilience")
    if overlay:
        cfg.update(overlay)
    cfg.setdefault("timeout", 120)
    cfg.setdefault("max_retries", 2)
    cfg.setdefault("fallback_profile", None)
    return cfg


def get_web_search_config() -> dict[str, Any]:
    """Web search settings.

    By default Phlox uses ddgs with no configuration. Set ``web_search.searxng_url`` in
    config.yml, or the ``SEARXNG_URL`` environment variable, to use SearXNG instead.
    """
    cfg = dict(load_config().get("web_search", {}) or {})
    env_url = os.environ.get("SEARXNG_URL")
    if env_url:
        cfg["searxng_url"] = env_url
    elif cfg.get("SEARXNG_URL") and not cfg.get("searxng_url"):
        cfg["searxng_url"] = cfg["SEARXNG_URL"]
    cfg.setdefault("searxng_url", "")
    return cfg


def get_sandbox_config() -> dict[str, Any]:
    """Code/shell execution sandbox config. ``runner`` is ``local``, ``container``, or ``agentcore``.

    Admin-editable: the container **limits** (memory/cpus/pids_limit/network/images/engine)
    overlay over the file. The ``runner`` *type* is deliberately **not** overridable here —
    flipping isolation (local <-> container <-> agentcore) at runtime would be a security
    surprise, so it stays file-only. The ``agentcore`` settings are likewise file-only.
    """
    from app import app_config

    cfg = dict(load_config().get("sandbox", {}) or {})
    cfg.setdefault("runner", "local")  # file-only; overlay must never change this
    container = dict(cfg.get("container", {}) or {})
    overlay = app_config.get_section("sandbox") or {}
    container.update(overlay.get("container", {}))
    container.setdefault("engine", "auto")  # auto | podman | docker | <path>
    container.setdefault("python_image", "python:3.12-slim")
    container.setdefault("node_image", "node:22-slim")
    container.setdefault("memory", "512m")
    container.setdefault("cpus", "1.0")
    container.setdefault("pids_limit", 256)
    container.setdefault("network", "none")  # none | bridge
    cfg["container"] = container

    # AWS Bedrock AgentCore Code Interpreter (remote, off-host microVM). Only consulted
    # when runner=agentcore; defaults mirror the container block's file-only treatment.
    ac = dict(cfg.get("agentcore", {}) or {})
    ac.setdefault("identifier", "aws.codeinterpreter.v1")  # built-in Code Interpreter
    ac.setdefault("session_timeout_seconds", 900)
    cfg["agentcore"] = ac
    return cfg


def get_auth_config() -> dict[str, Any]:
    """Auth settings. ``enabled`` gates login; when off the app runs single-user (dev)."""
    cfg = dict(load_config().get("auth", {}) or {})
    cfg.setdefault("enabled", True)
    cfg.setdefault(
        "jwt_secret",
        os.environ.get("PHLOX_JWT_SECRET", "dev-insecure-change-me-please-set-a-real-32B+-secret"),
    )
    cfg.setdefault("session_hours", 12)
    cfg.setdefault("allow_registration", True)
    cfg.setdefault("default_admin", {"username": "admin", "password": "admin"})
    # entra (Microsoft Entra ID / Azure AD) — OIDC; wired but optional. When `tenant_id`
    # and `client_id` are present, the SSO login path becomes available.
    cfg.setdefault("entra", {})
    return cfg


def get_vector_store_config() -> dict[str, Any]:
    """Vector store config, defaulting to embedded Qdrant under data/qdrant."""
    cfg = dict(load_config().get("vector_store", {}) or {})
    cfg.setdefault("provider", "qdrant")
    cfg.setdefault("collection", "doc_chunks")
    # Resolve a relative `path` against DATA_DIR so it works regardless of CWD/OS.
    if not cfg.get("url"):
        raw = cfg.get("path") or str(DATA_DIR / "qdrant")
        p = Path(raw)
        if not p.is_absolute():
            # Treat "./data/qdrant" or "data/qdrant" as relative to the backend dir.
            p = (BACKEND_DIR / raw).resolve()
        cfg["path"] = str(p)
    return cfg


DEFAULT_SYSTEM_PROMPT = (
    "You are Phlox, a helpful AI assistant. "
    "Be clear, accurate, and concise."
)


def default_generation_params() -> dict[str, Any]:
    d = get_defaults()
    return {
        "temperature": d.get("temperature", 0.3),
        # Generous default: "thinking" models spend tokens reasoning, and agentic tasks
        # (writing files, code) need room for large tool-call output before truncation.
        "max_tokens": d.get("max_tokens", 8192),
        "max_tool_rounds": d.get("max_tool_rounds", 12),
        "max_context_tokens": d.get("max_context_tokens", 16000),
        "system_prompt": d.get("system_prompt", DEFAULT_SYSTEM_PROMPT),
    }
