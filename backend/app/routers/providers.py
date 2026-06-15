"""Provider/profile discovery + connection testing."""
from __future__ import annotations

from fastapi import APIRouter

from app.providers.base import ToolSpec
from app.providers.registry import build_provider, list_models, list_profiles

router = APIRouter(prefix="/api/providers", tags=["providers"])


@router.get("")
def get_providers():
    return {"profiles": list_profiles()}


@router.get("/{profile}/models")
def get_models(profile: str):
    return {"profile": profile, "models": list_models(profile)}


@router.post("/{profile}/test")
def test_profile(profile: str):
    """Send a tiny prompt to verify the profile is reachable."""
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
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
