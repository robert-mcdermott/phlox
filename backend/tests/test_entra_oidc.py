"""Entra browser-flow security: state, nonce, PKCE, claims, replay, and handoff."""
from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest

from app.auth import entra


@pytest.fixture
def entra_cfg(monkeypatch):
    tenant = str(uuid.uuid4())
    cfg = {
        "tenant_id": tenant,
        "client_id": "phlox-client",
        "client_secret": "write-only-secret",
        "redirect_uri": "https://phlox.example/api/auth/entra/callback",
    }
    monkeypatch.setattr(entra, "entra_config", lambda: cfg)
    return cfg


def _begin(db):
    url = entra.begin_login(db)
    return parse_qs(urlparse(url).query), url


def test_login_generates_server_bound_state_nonce_and_pkce(db, entra_cfg):
    first, first_url = _begin(db)
    second, second_url = _begin(db)
    assert first_url != second_url
    for params in (first, second):
        assert len(params["state"][0]) >= 32
        assert len(params["nonce"][0]) >= 32
        assert params["code_challenge_method"] == ["S256"]
        assert len(params["code_challenge"][0]) >= 43
        assert params["redirect_uri"] == [entra_cfg["redirect_uri"]]


def test_validated_claims_binds_issuer_audience_tenant_and_nonce(monkeypatch, entra_cfg):
    captured = {}

    class FakeKey:
        key = "public-key"

    class FakeJwks:
        def __init__(self, url):
            captured["jwks"] = url

        def get_signing_key_from_jwt(self, token):
            captured["token"] = token
            return FakeKey()

    claims = {
        "sub": "subject",
        "tid": entra_cfg["tenant_id"],
        "nonce": "expected-nonce",
        "iss": f"https://login.microsoftonline.com/{entra_cfg['tenant_id']}/v2.0",
        "aud": entra_cfg["client_id"],
        "iat": 1,
        "exp": 9999999999,
    }

    def fake_decode(*args, **kwargs):
        captured["decode"] = (args, kwargs)
        return dict(claims)

    monkeypatch.setattr(entra.jwt, "PyJWKClient", FakeJwks)
    monkeypatch.setattr(entra.jwt, "decode", fake_decode)
    assert entra._validated_claims("id-token", "expected-nonce", entra_cfg)["sub"] == "subject"
    kwargs = captured["decode"][1]
    assert kwargs["audience"] == entra_cfg["client_id"]
    assert kwargs["issuer"].endswith(f"/{entra_cfg['tenant_id']}/v2.0")
    assert {"exp", "iat", "iss", "aud", "sub", "nonce", "tid"} <= set(
        kwargs["options"]["require"]
    )

    with pytest.raises(entra.EntraFlowError, match="tenant"):
        entra._validated_claims(
            "id-token", "expected-nonce", {**entra_cfg, "tenant_id": str(uuid.uuid4())}
        )
    with pytest.raises(entra.EntraFlowError, match="nonce"):
        entra._validated_claims("id-token", "wrong-nonce", entra_cfg)


def test_success_replay_wrong_state_collision_and_one_time_handoff(db, monkeypatch, entra_cfg):
    from app.auth import service

    params, _ = _begin(db)
    state = params["state"][0]

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"id_token": "signed-id-token"}

    import httpx

    monkeypatch.setattr(httpx, "post", lambda *_args, **_kwargs: Response())
    claims = {
        "sub": f"subject-{uuid.uuid4().hex}",
        "tid": entra_cfg["tenant_id"],
        "nonce": params["nonce"][0],
        "email": f"entra-{uuid.uuid4().hex}@example.org",
        "name": "Entra User",
    }
    monkeypatch.setattr(entra, "_validated_claims", lambda *_args: claims)
    user = entra.exchange_code_and_upsert(db, "authorization-code", state)
    assert user.auth_provider == "entra" and user.role == "user"

    with pytest.raises(entra.EntraFlowError, match="already used"):
        entra.exchange_code_and_upsert(db, "authorization-code", state)
    with pytest.raises(entra.EntraFlowError, match="invalid"):
        entra.exchange_code_and_upsert(db, "authorization-code", "wrong-state")

    handoff = entra.create_handoff(db, user.id)
    assert entra.consume_handoff(db, handoff).id == user.id
    with pytest.raises(entra.EntraFlowError, match="already used"):
        entra.consume_handoff(db, handoff)

    collision_email = f"collision-{uuid.uuid4().hex}@example.org"
    service.create_user(db, collision_email, "LocalPassword123!", email=collision_email)
    with pytest.raises(entra.EntraFlowError, match="conflicts"):
        entra._upsert_user(
            db,
            {"sub": f"new-{uuid.uuid4().hex}", "email": collision_email, "name": "Collision"},
        )


def test_callback_redirects_to_one_time_handoff_not_session_token(
    client, db, monkeypatch, entra_cfg
):
    from app.auth import service
    from app.routers import auth as auth_router

    monkeypatch.setattr(entra, "is_enabled", lambda: True)
    monkeypatch.setattr(auth_router.entra, "is_enabled", lambda: True)
    user = service.create_user(
        db,
        f"sso-{uuid.uuid4().hex}",
        role="user",
        auth_provider="entra",
        external_id=uuid.uuid4().hex,
    )
    monkeypatch.setattr(entra, "exchange_code_and_upsert", lambda *_args: user)
    response = client.get(
        "/api/auth/entra/callback?code=code&state=state", follow_redirects=False
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/#sso_handoff=")
    assert "token=" not in location and "Bearer" not in location
    handoff = location.split("=", 1)[1]
    complete = client.post("/api/auth/entra/complete", json={"handoff": handoff})
    assert complete.status_code == 200 and complete.json()["user"]["id"] == user.id
    assert client.post("/api/auth/entra/complete", json={"handoff": handoff}).status_code == 400
