"""Tests for the admin deployment-config overlay (config.yml + DB overrides).

Auth is disabled in the test config, so the TestClient acts as the synthetic dev admin and
the admin-gated routes are reachable. The overlay store is process-wide and the test DB is
session-scoped, so every test here cleans up its overrides via the ``clean_config`` fixture
to avoid leaking state into other tests (a stray ``profiles`` override would replace the
'test' profile catalog the rest of the suite relies on).
"""
import pytest


@pytest.fixture
def clean_config():
    """Remove any app_config overrides + reset caches before and after the test."""
    def _reset():
        from app import app_config
        from app.database import SessionLocal
        from app.models import AppConfig
        s = SessionLocal()
        try:
            s.query(AppConfig).delete()
            s.commit()
        finally:
            s.close()
        app_config.invalidate()
        from app.sandbox import runner
        runner.reset_runner()

    _reset()
    yield
    _reset()


# -- overlay merge at the getter layer ------------------------------------
def test_resilience_overlay_merges_over_file(clean_config):
    from app import app_config, config

    base = config.get_resilience_config()
    assert base["timeout"] == 120  # file/default
    app_config.set_section("resilience", {"timeout": 7})
    merged = config.get_resilience_config()
    assert merged["timeout"] == 7              # overlaid
    assert merged["max_retries"] == 2          # still filled by setdefault
    assert "fallback_profile" in merged


def test_pricing_overlay_changes_compute_cost_live(clean_config):
    from app import app_config
    from app.observability import compute_cost

    assert compute_cost("priced-x", {"input": 1_000_000, "output": 0}) is None
    app_config.set_section("pricing", {"priced-x": {"input": 1000.0, "output": 2000.0}})
    assert compute_cost("priced-x", {"input": 1_000_000, "output": 1_000_000}) == 3000.0


def test_profiles_overlay_can_delete_file_profile(clean_config):
    from app import app_config, config

    assert "test" in config.get_profiles()  # from config.yml
    app_config.set_section("profiles", {"only": {"type": "openai", "model": "m"}})
    profiles = config.get_profiles()
    assert set(profiles) == {"only"}  # replace-when-present => file 'test' is gone


def test_sandbox_overlay_limits_only_never_runner(clean_config):
    from app import app_config, config

    file_runner = config.get_sandbox_config()["runner"]
    # Even a hand-crafted overlay with a 'runner' key must not flip the runner type.
    app_config.set_section("sandbox", {"runner": "container",
                                       "container": {"memory": "256m", "runner": "container"}})
    s = config.get_sandbox_config()
    assert s["container"]["memory"] == "256m"
    assert s["runner"] == file_runner


# -- the HTTP surface ------------------------------------------------------
def test_get_config_masks_secrets(client, clean_config):
    from app import app_config
    app_config.set_section("profiles", {
        "p": {"type": "openai", "model": "m", "endpoint": "http://x/v1", "api_key": "TOPSECRET"}
    })
    body = client.get("/api/admin/config").json()
    prof = next(p for p in body["providers"] if p["name"] == "p")
    assert "api_key" not in prof              # secret never serialized
    assert prof.get("api_key_set") is True    # but its presence is signaled
    assert "TOPSECRET" not in client.get("/api/admin/config").text


def test_put_profiles_preserves_blank_secret(client, clean_config):
    from app import app_config, config

    # Seed a profile with a key directly in the overlay.
    app_config.set_section("profiles", {
        "p": {"type": "openai", "model": "m", "endpoint": "http://x/v1", "api_key": "KEEPME"}
    })
    # Re-save the profile WITHOUT a key (as the UI does when the user leaves it blank).
    client.put("/api/admin/config/profiles", json={"profiles": [
        {"name": "p", "type": "openai", "model": "m2", "endpoint": "http://x/v1"}
    ]})
    stored = config.get_profiles()["p"]
    assert stored["model"] == "m2"            # non-secret edit applied
    assert stored["api_key"] == "KEEPME"      # secret preserved across the blank save

    # And a new non-empty key overwrites.
    client.put("/api/admin/config/profiles", json={"profiles": [
        {"name": "p", "type": "openai", "model": "m2", "endpoint": "http://x/v1", "api_key": "NEW"}
    ]})
    assert config.get_profiles()["p"]["api_key"] == "NEW"


def test_bedrock_all_secret_fields_masked_and_preserved(client, clean_config):
    """Bedrock has several credential fields (IAM keys, session token, single bearer API
    key). All must be masked on read and preserved across a blank save."""
    from app import app_config, config

    secrets = {
        "aws_access_key_id": "AKIAEXAMPLE",
        "aws_secret_access_key": "SECRETKEY",
        "aws_session_token": "SESSIONTOK",
        "aws_bedrock_api_key": "BEARERTOKEN",
    }
    app_config.set_section("profiles", {
        "bd": {"type": "bedrock", "model": "us.anthropic.claude-sonnet-4-6",
               "aws_region": "us-west-2", **secrets}
    })

    # GET masks every secret, returns a *_set flag for each, and leaks none of the values.
    text = client.get("/api/admin/config").text
    body = client.get("/api/admin/config").json()
    bd = next(p for p in body["providers"] if p["name"] == "bd")
    for field, value in secrets.items():
        assert field not in bd, f"{field} must not be serialized"
        assert bd.get(f"{field}_set") is True, f"{field}_set flag missing"
        assert value not in text, f"{field} value leaked in response"
    assert bd["aws_region"] == "us-west-2"  # non-secret still visible

    # Re-save with all secrets blank (UI sends only changed fields) -> all preserved.
    client.put("/api/admin/config/profiles", json={"profiles": [
        {"name": "bd", "type": "bedrock", "model": "us.anthropic.claude-opus-4-1",
         "aws_region": "us-east-1"}
    ]})
    stored = config.get_profiles()["bd"]
    assert stored["model"] == "us.anthropic.claude-opus-4-1"  # non-secret edit applied
    assert stored["aws_region"] == "us-east-1"
    for field, value in secrets.items():
        assert stored[field] == value, f"{field} lost across blank save"

    # Rotating just the bearer key overwrites only it, preserving the IAM secrets.
    client.put("/api/admin/config/profiles", json={"profiles": [
        {"name": "bd", "type": "bedrock", "model": "us.anthropic.claude-opus-4-1",
         "aws_region": "us-east-1", "aws_bedrock_api_key": "NEWBEARER"}
    ]})
    stored = config.get_profiles()["bd"]
    assert stored["aws_bedrock_api_key"] == "NEWBEARER"
    assert stored["aws_secret_access_key"] == "SECRETKEY"  # untouched


def test_put_sandbox_resets_runner(client, clean_config):
    from app.sandbox import runner

    runner.get_runner()                       # populate the singleton
    assert runner._runner is not None
    r = client.put("/api/admin/config/sandbox", json={"container": {"memory": "300m"}})
    assert r.status_code == 200
    assert runner._runner is None             # reset so new limits take effect


def test_put_pricing_roundtrips(client, clean_config):
    r = client.put("/api/admin/config/pricing",
                   json={"pricing": {"mdl": {"input": 1.5, "output": 4.0}}})
    assert r.status_code == 200
    assert r.json()["pricing"]["mdl"]["output"] == 4.0


# -- access control --------------------------------------------------------
def test_admin_config_requires_admin():
    """A non-admin user is rejected by the gate (auth is disabled in the client fixture,
    so we exercise the dependency directly with a 'user'-role account)."""
    from fastapi import HTTPException
    from app.auth.deps import require_admin
    from app.models import User

    non_admin = User(id="u1", username="bob", role="user")
    with pytest.raises(HTTPException) as ei:
        require_admin(non_admin)
    assert ei.value.status_code == 403


def test_app_config_survives_user_deletion(client, clean_config):
    """The overlay is deployment-wide (no user_id), so deleting a user must not remove it."""
    from app import app_config
    from app.auth import service
    from app.database import SessionLocal
    from app.models import AppConfig

    app_config.set_section("pricing", {"m": {"input": 1.0, "output": 1.0}})
    s = SessionLocal()
    try:
        u = service.create_user(s, username="tmp-cfg", password="x", role="user")
        service.delete_user_data(s, u.id)
        from app.models import User
        s.delete(s.get(User, u.id))
        s.commit()
        assert s.query(AppConfig).filter(AppConfig.section == "pricing").first() is not None
    finally:
        s.close()
