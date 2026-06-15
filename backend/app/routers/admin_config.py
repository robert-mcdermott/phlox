"""Admin-editable deployment configuration (the config.yml overlay).

Exposes the editable config *sections* (provider profiles, model pricing, resilience,
generation defaults, sandbox limits) for an admin-only UI. Reads return the **effective**
config (file merged with any DB overrides) so the form shows what's actually in force;
writes store an override via :mod:`app.app_config`, applied live with no restart.

Security:
- Admin-only (``require_admin`` on the whole router).
- Provider **secrets are never returned** — GET strips them and reports only a
  ``<field>_set`` boolean. PUT preserves the existing secret when the field is left blank,
  so editing a profile doesn't require re-entering its key.
- The sandbox **runner type** (local vs container) is read-only here — flipping isolation at
  runtime is unsafe, so it stays file-only.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app import app_config, config
from app.auth.deps import require_admin
from app.models import User
from app.schemas import (
    GenerationUpdate,
    PricingUpdate,
    ProfilesUpdate,
    ResilienceUpdate,
    SandboxUpdate,
)

# Every route is admin-only via the per-handler ``require_admin`` dependency, which also
# yields the acting user (for the ``updated_by`` audit field).
router = APIRouter(prefix="/api/admin/config", tags=["admin-config"])

# Profile fields that hold secrets — stripped from reads, preserved across writes.
SECRET_FIELDS = (
    "api_key",
    "aws_access_key_id",
    "aws_secret_access_key",
    "aws_session_token",
    "aws_bedrock_api_key",
)


def _mask_profile(name: str, cfg: dict) -> dict:
    """Return a profile dict safe to send to the client: secrets removed, ``<field>_set``
    booleans added so the UI can show '••• set' without ever seeing the value."""
    out = {"name": name}
    for k, v in cfg.items():
        if k not in SECRET_FIELDS:
            out[k] = v
    for f in SECRET_FIELDS:
        if cfg.get(f):
            out[f"{f}_set"] = True
    return out


def _effective_config() -> dict:
    """Build the masked, effective-config payload shared by GET and every PUT response."""
    profiles = config.get_profiles()
    sandbox = config.get_sandbox_config()
    return {
        "providers": [_mask_profile(n, c) for n, c in profiles.items()],
        "pricing": config.get_observability_config().get("pricing", {}),
        "resilience": config.get_resilience_config(),
        "generation": config.default_generation_params(),
        "sandbox": {"runner": sandbox.get("runner"), "container": sandbox.get("container", {})},
        "meta": {
            # Read-only context the UI surfaces but cannot change here.
            "sandbox_runner": sandbox.get("runner"),
            "profiles_overlay_active": app_config.get_section("profiles") is not None,
            "config_path": str(config.CONFIG_PATH),
            "default_profile": config.get_default_profile_name(),
        },
    }


@router.get("")
def read_config(_: User = Depends(require_admin)):
    """Effective config for the admin UI (file merged with overrides), secrets masked."""
    return _effective_config()


@router.put("/profiles")
def update_profiles(body: ProfilesUpdate, user: User = Depends(require_admin)):
    """Replace the profile catalog. Secrets left blank are preserved from current config."""
    names = [p.name.strip() for p in body.profiles]
    if any(not n for n in names):
        raise HTTPException(422, "Every profile needs a non-empty name")
    if len(set(names)) != len(names):
        raise HTTPException(422, "Duplicate profile names")

    current = config.get_profiles()  # effective (overlay or file) — holds the real secrets
    new: dict[str, dict] = {}
    for p in body.profiles:
        pd = p.model_dump()
        name = pd.pop("name").strip()
        if pd.get("type") not in ("openai", "bedrock"):
            raise HTTPException(422, f"Profile {name!r}: type must be 'openai' or 'bedrock'")
        # Non-secret fields: keep what was provided (drop unset/None to keep storage clean).
        cfg = {k: v for k, v in pd.items() if v is not None and k not in SECRET_FIELDS}
        # Secret-preserve: a non-empty incoming value overwrites; otherwise keep the existing.
        prev = current.get(name, {})
        for f in SECRET_FIELDS:
            incoming = (pd.get(f) or "").strip() if isinstance(pd.get(f), str) else pd.get(f)
            if incoming:
                cfg[f] = incoming
            elif prev.get(f):
                cfg[f] = prev[f]
        new[name] = cfg

    app_config.set_section("profiles", new, user.id)
    return _effective_config()


@router.put("/pricing")
def update_pricing(body: PricingUpdate, user: User = Depends(require_admin)):
    pricing = {model: rate.model_dump() for model, rate in body.pricing.items()}
    app_config.set_section("pricing", pricing, user.id)
    return _effective_config()


@router.put("/resilience")
def update_resilience(body: ResilienceUpdate, user: User = Depends(require_admin)):
    # Store all three keys (incl. an explicit null fallback) so clearing a fallback sticks.
    app_config.set_section("resilience", body.model_dump(), user.id)
    return _effective_config()


@router.put("/generation")
def update_generation(body: GenerationUpdate, user: User = Depends(require_admin)):
    # Partial override: only persist the keys the admin actually set.
    updates = body.model_dump(exclude_none=True)
    app_config.set_section("generation", updates, user.id)
    return _effective_config()


@router.put("/sandbox")
def update_sandbox(body: SandboxUpdate, user: User = Depends(require_admin)):
    """Update container limits only; the runner type is not changed here. Resets the cached
    runner so the new limits apply to the next execution without a restart."""
    container = {k: v for k, v in body.container.model_dump().items() if v is not None}
    app_config.set_section("sandbox", {"container": container}, user.id)
    from app.sandbox.runner import reset_runner

    reset_runner()
    return _effective_config()
