"""Tests for the API-key gateway: key CRUD, bearer auth, and the OpenAI-compatible
``/v1/chat/completions`` + ``/v1/models`` endpoints (auth disabled -> single dev admin).

A scripted provider replaces ``build_provider`` so no network/model is needed; usage is
asserted to land in the durable ledger keyed by the gateway request id.
"""
import json

from app.providers.base import StreamDelta


class GatewayScriptedProvider:
    """Minimal provider: streams two text chunks + a usage delta + done."""

    model = "test-model"
    supports_tools = True

    def stream(self, messages, tools, params):  # noqa: ARG002
        yield StreamDelta(type="text", text="Hello, ")
        yield StreamDelta(type="text", text="world!")
        yield StreamDelta(type="usage", usage={"input": 11, "output": 7, "total": 18})
        yield StreamDelta(type="done", stop_reason="stop")


def _patch_provider(monkeypatch):
    import app.routers.gateway as gw

    monkeypatch.setattr(gw, "build_provider", lambda profile, model: GatewayScriptedProvider())


def _make_key(client, name="test key"):
    r = client.post("/api/api-keys", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()


# --- key CRUD ----------------------------------------------------------------
def test_api_key_create_returns_secret_once_then_hidden(client):
    created = _make_key(client, "my key")
    assert created["key"].startswith("phlox-sk-")
    assert created["name"] == "my key"
    assert created["prefix"] == created["key"][: len(created["prefix"])]

    # The secret is never returned again by the list endpoint.
    listed = client.get("/api/api-keys").json()
    row = next(k for k in listed if k["id"] == created["id"])
    assert "key" not in row
    assert row["is_active"] is True


def test_api_key_revoke(client):
    created = _make_key(client)
    assert client.delete(f"/api/api-keys/{created['id']}").status_code == 200
    row = next(k for k in client.get("/api/api-keys").json() if k["id"] == created["id"])
    assert row["is_active"] is False
    # Revoking a non-existent / unowned key is a 404 (no existence leak).
    assert client.delete("/api/api-keys/does-not-exist").status_code == 404


# --- bearer auth -------------------------------------------------------------
def test_gateway_requires_api_key(client):
    assert client.get("/v1/models").status_code == 401
    assert client.post("/v1/chat/completions", json={"model": "m", "messages": []}).status_code == 401


def test_gateway_rejects_revoked_key(client, monkeypatch):
    _patch_provider(monkeypatch)
    created = _make_key(client)
    client.delete(f"/api/api-keys/{created['id']}")
    r = client.post(
        "/v1/chat/completions",
        json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {created['key']}"},
    )
    assert r.status_code == 401


# --- /v1/models --------------------------------------------------------------
def test_v1_models_lists_catalog(client):
    created = _make_key(client)
    r = client.get("/v1/models", headers={"Authorization": f"Bearer {created['key']}"})
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "list"
    ids = [m["id"] for m in body["data"]]
    assert "test/test-model" in ids


# --- /v1/chat/completions ----------------------------------------------------
def test_chat_completions_buffered_and_records_usage(client, monkeypatch, db):
    from app.models import UsageLedger

    _patch_provider(monkeypatch)
    # Price the model so cost is computed and stored.
    monkeypatch.setattr(
        "app.routers.gateway.compute_cost", lambda model, usage: 0.000123
    )
    created = _make_key(client)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
        headers={"Authorization": f"Bearer {created['key']}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"]["content"] == "Hello, world!"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 18

    # A ledger row was written, keyed by the returned request id, billed to the dev admin.
    row = db.query(UsageLedger).filter(UsageLedger.message_id == body["id"]).first()
    assert row is not None
    assert row.total_tokens == 18
    assert row.cost_usd == 0.000123


def test_chat_completions_streaming(client, monkeypatch):
    _patch_provider(monkeypatch)
    created = _make_key(client)
    r = client.post(
        "/v1/chat/completions",
        json={
            "model": "test-model",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={"Authorization": f"Bearer {created['key']}"},
    )
    assert r.status_code == 200
    assert "text/event-stream" in r.headers["content-type"]
    chunks = [
        json.loads(line[len("data: "):])
        for line in r.text.splitlines()
        if line.startswith("data: ") and not line.endswith("[DONE]")
    ]
    assert chunks[0]["choices"][0]["delta"]["role"] == "assistant"
    content = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks)
    assert content == "Hello, world!"
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert "data: [DONE]" in r.text


def test_chat_completions_empty_messages(client, monkeypatch):
    _patch_provider(monkeypatch)
    created = _make_key(client)
    r = client.post(
        "/v1/chat/completions",
        json={"model": "test-model", "messages": []},
        headers={"Authorization": f"Bearer {created['key']}"},
    )
    assert r.status_code == 400
    assert r.json()["error"]["type"] == "invalid_request_error"


def test_resolve_model_qualified_bare_and_default():
    from app.providers.registry import resolve_model

    # Explicit "profile/model".
    assert resolve_model("test/test-model") == ("test", "test-model")
    # Bare id present in a profile's catalog.
    assert resolve_model("test-model") == ("test", "test-model")
    # Unknown bare id falls back to the default profile (model overridden).
    assert resolve_model("some-other-model") == ("test", "some-other-model")
