"""Provider/profile discovery + connection testing."""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends

from app.auth.deps import get_current_user, require_admin
from app.models import User
from app.providers.base import ToolSpec
from app.providers.registry import build_provider, list_models, list_profiles
from app.rate_limit import check_rate_limit

router = APIRouter(prefix="/api/providers", tags=["providers"])
logger = logging.getLogger(__name__)


@router.get("")
def get_providers(_: User = Depends(get_current_user)):
    return {"profiles": list_profiles()}


@router.get("/{profile}/models")
def get_models(profile: str, _: User = Depends(get_current_user)):
    return {"profile": profile, "models": list_models(profile)}


@router.post("/{profile}/test")
def test_profile(profile: str, admin: User = Depends(require_admin)):
    """Send a tiny prompt to verify the profile is reachable."""
    check_rate_limit("provider-test", admin.id, limit=5, window_seconds=60)
    try:
        provider = build_provider(profile)
        text = ""
        for delta in provider.stream(
            [{"role": "user", "content": "Reply with the single word: ok"}],
            [] if not provider.supports_tools else [ToolSpec("noop", "no-op", {"type": "object", "properties": {}})],
            {"temperature": 0, "max_tokens": 16},
        ):
            if delta.type == "text":
                text += delta.text or ""
        return {"ok": True, "model": provider.model, "sample": text.strip()[:200]}
    except Exception:  # noqa: BLE001
        # Provider exceptions may include endpoint URLs, request headers, or credential
        # fragments. Keep diagnostic detail in server logs and return a stable safe error.
        logger.exception("Provider connection test failed for profile %s", profile)
        return {"ok": False, "error": "Provider connection test failed. Check server logs."}
