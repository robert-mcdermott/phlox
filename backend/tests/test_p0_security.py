"""P0 authorization, safe-default, and deployment security regression coverage."""
from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path

import pytest


@pytest.fixture
def auth_enabled(monkeypatch, db):
    from app.auth import deps, service
    from app.auth.security import create_access_token
    from app.routers import auth as auth_router

    base = {
        "enabled": True,
        "allow_registration": False,
        "session_hours": 12,
        "jwt_secret": os.environ["PHLOX_JWT_SECRET"],
        "entra": {},
    }
    monkeypatch.setattr(deps, "get_auth_config", lambda: dict(base))
    monkeypatch.setattr(auth_router, "get_auth_config", lambda: dict(base))
    suffix = uuid.uuid4().hex[:8]
    users = {
        "alice": service.create_user(db, f"alice-{suffix}", "AlicePassword123!", role="user"),
        "bob": service.create_user(db, f"bob-{suffix}", "BobPassword123!", role="user"),
        "admin": service.create_user(db, f"admin-{suffix}", "AdminPassword123!", role="admin"),
    }
    return {
        **users,
        **{
            f"{name}_headers": {
                "Authorization": f"Bearer {create_access_token(user.id, user.role)}"
            }
            for name, user in users.items()
        },
        "config": base,
    }


def _conversation(db, user_id: str):
    from app.models import Conversation

    row = Conversation(user_id=user_id, title="private")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def test_owner_scoped_files_attachments_and_checkpoints(client, db, auth_enabled):
    from app.config import ATTACHMENTS_DIR
    from app.models import Message
    from app.workspace import checkpoints
    from app.workspace.manager import workspace_dir

    alice = auth_enabled["alice"]
    ah = auth_enabled["alice_headers"]
    bh = auth_enabled["bob_headers"]
    admin_h = auth_enabled["admin_headers"]
    conversation = _conversation(db, alice.id)
    workspace = workspace_dir(conversation.id)
    (workspace / "secret.txt").write_text("alice-only", encoding="utf-8")

    message = Message(conversation_id=conversation.id, role="user", content="image")
    db.add(message)
    db.commit()
    db.refresh(message)
    attachment_dir = ATTACHMENTS_DIR / message.id
    attachment_dir.mkdir(parents=True, exist_ok=True)
    (attachment_dir / "0.png").write_bytes(b"private-image")

    sha = checkpoints.create_checkpoint(workspace, "private checkpoint")
    assert sha

    routes = [
        f"/api/files/{conversation.id}/list",
        f"/api/files/{conversation.id}?path=secret.txt",
        f"/api/attachments/{message.id}/0",
        f"/api/checkpoints/{conversation.id}",
    ]
    for route in routes:
        assert client.get(route).status_code == 401
        assert client.get(route, headers=bh).status_code == 404
        assert client.get(route, headers=admin_h).status_code == 404
        assert client.get(route, headers=ah).status_code == 200

    restore = f"/api/checkpoints/{conversation.id}/restore"
    assert client.post(restore, json={"sha": sha}).status_code == 401
    assert client.post(restore, json={"sha": sha}, headers=bh).status_code == 404
    assert client.post(restore, json={"sha": sha}, headers=admin_h).status_code == 404
    assert client.post(restore, json={"sha": sha}, headers=ah).status_code == 200

    missing = uuid.uuid4().hex
    assert client.get(f"/api/files/{missing}/list", headers=ah).status_code == 404
    assert client.get(f"/api/attachments/{missing}/0", headers=ah).status_code == 404
    assert client.get(f"/api/checkpoints/{missing}", headers=ah).status_code == 404


def test_provider_routes_are_authenticated_admin_limited_and_sanitized(
    client, auth_enabled, monkeypatch
):
    from app.providers.base import StreamDelta
    from app.rate_limit import reset_rate_limits
    from app.routers import providers

    reset_rate_limits()
    assert client.get("/api/providers").status_code == 401
    assert client.get("/api/providers/test/models").status_code == 401
    assert client.get("/api/providers", headers=auth_enabled["alice_headers"]).status_code == 200
    assert (
        client.post("/api/providers/test/test", headers=auth_enabled["alice_headers"]).status_code
        == 403
    )

    class SafeProvider:
        model = "safe"
        supports_tools = False

        def stream(self, *_args, **_kwargs):
            yield StreamDelta(type="text", text="ok")

    monkeypatch.setattr(providers, "build_provider", lambda _profile: SafeProvider())
    admin_h = auth_enabled["admin_headers"]
    for _ in range(5):
        assert client.post("/api/providers/test/test", headers=admin_h).status_code == 200
    limited = client.post("/api/providers/test/test", headers=admin_h)
    assert limited.status_code == 429 and int(limited.headers["retry-after"]) > 0

    reset_rate_limits()
    secret = "sk-super-secret at http://internal-provider:9999"
    monkeypatch.setattr(providers, "build_provider", lambda _profile: (_ for _ in ()).throw(RuntimeError(secret)))
    body = client.post("/api/providers/test/test", headers=admin_h).json()
    assert body["ok"] is False
    assert secret not in body["error"] and "internal-provider" not in body["error"]


def test_auth_enabled_two_user_isolation_matrix(client, db, auth_enabled):
    from app.models import Assistant, Document, Message, PendingApproval, Skill

    alice, admin = auth_enabled["alice"], auth_enabled["admin"]
    ah, bh, admin_h = (
        auth_enabled["alice_headers"],
        auth_enabled["bob_headers"],
        auth_enabled["admin_headers"],
    )
    conversation = _conversation(db, alice.id)
    message = Message(conversation_id=conversation.id, role="user", content="private message")
    document = Document(user_id=alice.id, filename="private.txt", status="ready", size_bytes=1)
    private_skill = Skill(
        name=f"private-{uuid.uuid4().hex[:8]}", description="private", instructions="secret",
        visibility="private", created_by=alice.id,
    )
    private_assistant = Assistant(
        name="private assistant", visibility="private", created_by=admin.id, is_active=True
    )
    pending = PendingApproval(conversation_id=conversation.id, state={"profile": "test"})
    db.add_all([message, document, private_skill, private_assistant, pending])
    db.commit()

    # Conversations include message content; neither another user nor an admin can read it.
    assert client.get(f"/api/conversations/{conversation.id}", headers=ah).status_code == 200
    assert client.get(f"/api/conversations/{conversation.id}", headers=bh).status_code == 404
    assert client.get(f"/api/conversations/{conversation.id}", headers=admin_h).status_code == 404
    assert client.post("/api/chat", json={"conversation_id": uuid.uuid4().hex, "message": "x"}, headers=ah).status_code == 404

    # Documents and memories remain per-user.
    assert document.id in {d["id"] for d in client.get("/api/documents", headers=ah).json()}
    assert document.id not in {d["id"] for d in client.get("/api/documents", headers=bh).json()}
    assert client.delete(f"/api/documents/{document.id}", headers=bh).status_code == 404
    memory = client.post("/api/memories", json={"content": "alice memory"}, headers=ah).json()
    assert memory["id"] not in {m["id"] for m in client.get("/api/memories", headers=bh).json()}
    assert client.delete(f"/api/memories/{memory['id']}", headers=bh).status_code == 404

    # Private skill content is invisible even to admins; public assistant visibility remains explicit.
    for headers in (bh, admin_h):
        assert client.get(f"/api/skills/{private_skill.id}", headers=headers).status_code == 404
        assert private_skill.id not in {s["id"] for s in client.get("/api/skills", headers=headers).json()}
    assert client.get(f"/api/skills/{private_skill.id}", headers=ah).status_code == 200
    assert client.get(f"/api/assistants/{private_assistant.id}", headers=bh).status_code == 404
    assert private_assistant.id not in {a["id"] for a in client.get("/api/assistants", headers=bh).json()}

    # API keys and settings are independently scoped.
    key = client.post("/api/api-keys", json={"name": "alice"}, headers=ah).json()
    assert key["id"] not in {k["id"] for k in client.get("/api/api-keys", headers=bh).json()}
    assert client.delete(f"/api/api-keys/{key['id']}", headers=bh).status_code == 404
    assert client.patch("/api/settings", json={"max_tokens": 4321}, headers=ah).status_code == 200
    assert client.get("/api/settings", headers=bh).json()["max_tokens"] != 4321

    # Approval state cannot be probed or resumed by another user or an admin.
    for headers in (bh, admin_h):
        assert client.post(
            "/api/chat/approve",
            json={"pending_id": pending.id, "decisions": {}},
            headers=headers,
        ).status_code == 404


def test_registration_is_opt_in_rate_limited_and_never_promotes(client, auth_enabled, monkeypatch):
    from app.rate_limit import reset_rate_limits
    from app.routers import auth as auth_router

    reset_rate_limits()
    assert client.post("/api/auth/register", json={"username": "closed", "password": "password"}).status_code == 403
    enabled = {**auth_enabled["config"], "allow_registration": True}
    monkeypatch.setattr(auth_router, "get_auth_config", lambda: enabled)
    escalation = client.post(
        "/api/auth/register",
        json={"username": f"registered-{uuid.uuid4().hex}", "password": "LongPassword123!", "role": "admin"},
    )
    assert escalation.status_code == 422
    first = client.post(
        "/api/auth/register",
        json={"username": f"registered-{uuid.uuid4().hex}", "password": "LongPassword123!"},
    )
    assert first.status_code == 200 and first.json()["user"]["role"] == "user"
    for i in range(4):
        client.post("/api/auth/register", json={"username": "duplicate", "password": f"password-{i}"})
    limited = client.post("/api/auth/register", json={"username": "sixth", "password": "password"})
    assert limited.status_code == 429


def test_compose_secret_contract_and_entrypoint_validation():
    root = Path(__file__).resolve().parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "${PHLOX_JWT_SECRET:?" in compose
    assert "PHLOX_JWT_SECRET:-" not in compose

    entrypoint = root / "docker" / "entrypoint.sh"
    for value in (None, "change-me", "please-change-me-set-a-real-32B+-secret", "short"):
        env = dict(os.environ)
        if value is None:
            env.pop("PHLOX_JWT_SECRET", None)
        else:
            env["PHLOX_JWT_SECRET"] = value
        result = subprocess.run(["sh", str(entrypoint), "true"], capture_output=True, text=True, env=env)
        assert result.returncode != 0
    valid = subprocess.run(
        ["sh", str(entrypoint), "true"], capture_output=True, text=True,
        env={**os.environ, "PHLOX_JWT_SECRET": "valid-high-entropy-secret-material-1234567890"},
    )
    assert valid.returncode == 0


def test_dockerfile_non_root_contract():
    root = Path(__file__).resolve().parents[2]
    dockerfile = (root / "Dockerfile").read_text(encoding="utf-8")
    assert "USER 10001:10001" in dockerfile
    assert "chown -R phlox:phlox /app/backend/data" in dockerfile


def test_production_local_sandbox_is_rejected(monkeypatch):
    from app import config
    from app.sandbox import runner

    monkeypatch.setenv("PHLOX_ENV", "production")
    monkeypatch.setattr(config, "get_sandbox_config", lambda: {"runner": "local"})
    monkeypatch.setattr(config, "get_auth_config", lambda: {"enabled": True})
    runner.reset_runner()
    with pytest.raises(RuntimeError, match="requires sandbox.runner=container or agentcore"):
        runner.validate_sandbox_startup()
    runner.reset_runner()


def test_configured_container_without_usable_engine_fails_closed(monkeypatch):
    from app import config
    from app.sandbox import runner

    monkeypatch.setattr(
        config,
        "get_sandbox_config",
        lambda: {"runner": "container", "container": {"engine": "missing-phlox-engine"}},
    )
    runner.reset_runner()
    with pytest.raises(RuntimeError, match="No container engine"):
        runner.get_runner()
    assert runner._runner is None
    runner.reset_runner()

    monkeypatch.setattr(runner, "detect_container_engine", lambda _preference: "/bin/false")
    with pytest.raises(RuntimeError, match="not usable"):
        runner.ContainerRunner({"engine": "auto"})


def test_agentcore_missing_credentials_reports_not_ready(monkeypatch):
    import boto3

    from app.sandbox.runner import AgentCoreCodeInterpreterRunner

    class NoCredentialsSession:
        def __init__(self, **_kwargs):
            pass

        def get_credentials(self):
            return None

    monkeypatch.setattr(boto3, "Session", NoCredentialsSession)
    available, detail = AgentCoreCodeInterpreterRunner({}).readiness()
    assert available is False and "credentials" in detail.lower()


def test_container_execution_exposes_only_workspace_not_application_secrets(tmp_path, monkeypatch):
    from app.sandbox import runner

    captured = {}
    sandbox = object.__new__(runner.ContainerRunner)
    sandbox.engine = "/usr/bin/container-engine"
    sandbox.cfg = {
        "network": "none",
        "memory": "512m",
        "cpus": "1",
        "pids_limit": 64,
        "python_image": "python:test",
        "node_image": "node:test",
    }
    monkeypatch.setenv("PHLOX_JWT_SECRET", "must-never-enter-the-sandbox")
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-never-enter-the-sandbox")

    def fake_run(argv, cwd, timeout, **kwargs):
        captured.update(argv=argv, cwd=cwd, timeout=timeout, kwargs=kwargs)
        return runner._RawResult(stdout="", stderr="", exit_code=0)

    monkeypatch.setattr(runner, "_run_popen", fake_run)
    result = sandbox.run_command(["sh", "-c", "true"], tmp_path)
    command = " ".join(captured["argv"])
    assert result.exit_code == 0
    assert f"{tmp_path}:/work" in command
    assert "/app/backend" not in command
    assert "PHLOX_JWT_SECRET" not in command and "DATABASE_URL" not in command
    assert captured["kwargs"].get("env") is None


def test_agent_mode_defaults_off_and_ask_tool_pauses(client, db, monkeypatch):
    from app.models import ToolPref
    from app.providers.base import StreamDelta, ToolCall
    from app.routers import chat as chat_router
    from app.schemas import ChatRequest

    assert ChatRequest(message="default check").auto_approve is False
    pref = db.get(ToolPref, "write_file")
    if pref is None:
        pref = ToolPref(name="write_file", enabled=True, permission="ask")
        db.add(pref)
    pref.permission = "ask"
    pref.enabled = True
    db.commit()

    class AskToolProvider:
        model = "ask-default"
        supports_tools = True

        def stream(self, _messages, _tools, _params):
            yield StreamDelta(
                type="tool_calls",
                tool_calls=[
                    ToolCall(
                        id="default-ask",
                        name="write_file",
                        arguments={"path": "should-not-exist.txt", "content": "unsafe"},
                    )
                ],
            )

    monkeypatch.setattr(chat_router, "build_provider", lambda *_args, **_kwargs: AskToolProvider())
    monkeypatch.setattr(chat_router, "_build_fallback", lambda _profile: None)
    response = client.post("/api/chat", json={"message": "write a file"})
    assert response.status_code == 200
    assert "approval_request" in response.text and '"type": "paused"' in response.text

    root = Path(__file__).resolve().parents[2]
    composer = (root / "frontend/src/components/chat/Composer.jsx").read_text(encoding="utf-8")
    store = (root / "frontend/src/store/useStore.js").read_text(encoding="utf-8")
    assert "useState(false)" in composer
    assert "autoApprove = false" in store
    assert "auto_approve: true" not in store
