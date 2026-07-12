"""Application configuration.

Loads ``config.yml`` (provider profiles + defaults) and exposes paths used across
the app. Runtime-mutable settings (active profile, generation params, system prompt,
theme) live in the database ``Setting`` table — see ``app/routers/settings.py``.
``config.yml`` provides the *catalog* of profiles and the initial defaults.
"""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

_EPHEMERAL_JWT_SECRET = secrets.token_urlsafe(48)
_INSECURE_JWT_SECRETS = {
    "dev-insecure-change-me-please-set-a-real-32B+-secret",
    "please-change-me-set-a-real-32B+-secret",
    "change-me",
}

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
SQLITE_URL = f"sqlite:///{DB_PATH}"

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


def get_web_fetch_config() -> dict[str, Any]:
    """``web_fetch`` SSRF guard settings.

    By default, ``web_fetch`` refuses to reach private/loopback/link-local addresses
    (including the cloud metadata IP, 169.254.169.254) so an uploaded prompt can't use the
    agent as a pivot into your internal network. Set ``web_fetch.allow_private_networks:
    true`` to disable the guard entirely, or list specific hostnames/IPs/CIDRs under
    ``web_fetch.allowlist_hosts`` to punch through the guard for just those targets (e.g.
    an internal API you want the agent to be able to call).
    """
    cfg = dict(load_config().get("web_fetch", {}) or {})
    cfg.setdefault("allow_private_networks", False)
    cfg.setdefault("allowlist_hosts", [])
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


# Starter prompts on the default (no-assistant) welcome screen. Each showcases a distinct
# capability that works in a fresh, empty workspace: code execution with inline charts,
# the HTML artifact canvas, document RAG, and opt-in web search. Assistants carry their own
# ``prompt_suggestions`` and are unaffected by this list.
DEFAULT_SUGGESTIONS = [
    {"text": "Simulate 1,000 dice rolls in Python and chart the distribution"},
    {"text": "Build an interactive HTML dashboard with sample sales data and open it in the canvas"},
    {"text": "Summarize the key findings across my uploaded documents", "document_search": True},
    {"text": "Search the web for today's top AI news and give me a short briefing", "web_search": True},
]


def get_suggestions() -> list[dict[str, Any]]:
    """Welcome-screen prompt suggestions for the default assistant.

    Admin-editable: a ``suggestions`` override (see :mod:`app.app_config`) *replaces* the
    file list wholesale — so the UI can add, edit, reorder, and delete suggestions,
    including clearing them entirely (an empty override list is respected, not treated as
    absence). Falls back to the file ``suggestions:`` key, then ``DEFAULT_SUGGESTIONS``.
    """
    from app import app_config

    overlay = app_config.get_section("suggestions")
    raw = overlay if overlay is not None else load_config().get("suggestions")
    if raw is None:
        raw = DEFAULT_SUGGESTIONS
    out: list[dict[str, Any]] = []
    for s in raw:
        if not isinstance(s, dict) or not str(s.get("text", "")).strip():
            continue
        out.append({
            "text": str(s["text"]).strip(),
            "document_search": bool(s.get("document_search")),
            "web_search": bool(s.get("web_search")),
        })
    return out


def get_guardrails_config() -> dict[str, Any]:
    """Guardrails policy: PII/custom-pattern redaction & blocking (see :mod:`app.guardrails`).

    Admin-editable: a ``guardrails`` override (see :mod:`app.app_config`) *replaces* the
    file section wholesale — the admin UI always saves the complete policy. Disabled by
    default; the file section is only a seed for fresh deployments.
    """
    from app import app_config

    overlay = app_config.get_section("guardrails")
    cfg = dict(overlay if overlay is not None else (load_config().get("guardrails", {}) or {}))
    cfg.setdefault("enabled", False)
    cfg.setdefault("input_action", "redact")   # off | redact | block
    cfg.setdefault("output_action", "off")     # off | redact | block
    cfg.setdefault("redaction_text", "[REDACTED]")
    cfg.setdefault("builtin", {})              # detector id -> bool (missing = enabled)
    cfg.setdefault("custom_patterns", [])      # [{name, regex, action, replacement, enabled}]
    return cfg


def get_auth_config() -> dict[str, Any]:
    """Auth settings. ``enabled`` gates login; when off the app runs single-user (dev)."""
    cfg = dict(load_config().get("auth", {}) or {})
    cfg.setdefault("enabled", True)
    # Environment wins so orchestrators can inject the secret without modifying config.
    # For local development only, use a strong per-process secret rather than a committed
    # shared fallback. Production startup rejects the absence of PHLOX_JWT_SECRET below.
    cfg["jwt_secret"] = (
        os.environ.get("PHLOX_JWT_SECRET")
        or cfg.get("jwt_secret")
        or _EPHEMERAL_JWT_SECRET
    )
    cfg.setdefault("session_hours", 12)
    cfg.setdefault("allow_registration", False)
    admin = dict(cfg.get("default_admin") or {})
    admin.pop("password", None)  # legacy values are intentionally ignored
    admin.setdefault("username", "admin")
    cfg["default_admin"] = admin
    # entra (Microsoft Entra ID / Azure AD) — OIDC; wired but optional. When `tenant_id`
    # and `client_id` are present, the SSO login path becomes available.
    cfg.setdefault("entra", {})
    return cfg


def validate_auth_startup() -> None:
    """Fail closed when an auth-enabled production process lacks a strong JWT secret."""
    cfg = get_auth_config()
    if not cfg["enabled"] or os.environ.get("PHLOX_ENV", "").lower() not in {
        "prod",
        "production",
    }:
        return

    secret = os.environ.get("PHLOX_JWT_SECRET", "")
    if (
        len(secret.encode("utf-8")) < 32
        or secret.lower() in _INSECURE_JWT_SECRETS
        or len(set(secret)) < 8
    ):
        raise RuntimeError(
            "Auth-enabled production startup requires PHLOX_JWT_SECRET with at least "
            "32 bytes of high-entropy secret material. Keep it stable across restarts."
        )


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


def get_database_url() -> str:
    """The SQLAlchemy database URL. SQLite (under ``DATA_DIR``) out of the box.

    To deploy against Postgres instead, set the ``DATABASE_URL`` env var (e.g.
    ``postgresql+psycopg://user:pass@host:5432/phlox``) or a ``database.url`` in
    config.yml. This is a bootstrap/deployment choice like ``vector_store``, so it is
    file/env-only — not admin-editable from the UI.
    """
    env_url = os.environ.get("DATABASE_URL")
    if env_url:
        return env_url
    cfg = load_config().get("database", {}) or {}
    return cfg.get("url") or SQLITE_URL


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
