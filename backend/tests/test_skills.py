"""Agent skills: CRUD, visibility, SKILL.md import/export, chat injection, use_skill."""
import pytest


def _make_skill(client, **overrides):
    body = {
        "name": "commit-poet",
        "description": "Write commit messages as haiku. Use when asked for a commit message.",
        "instructions": "# Commit Poet\n\nWrite every commit message as a haiku (5-7-5).",
        "visibility": "public",
        **overrides,
    }
    r = client.post("/api/skills", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _cleanup(client):
    for s in client.get("/api/skills").json():
        client.delete(f"/api/skills/{s['id']}")


@pytest.fixture(autouse=True)
def _isolate(client):
    _cleanup(client)
    yield
    _cleanup(client)


# -- CRUD ---------------------------------------------------------------------
def test_skill_crud_roundtrip(client):
    s = _make_skill(client)
    assert s["name"] == "commit-poet"
    assert s["auto_activate"] is True
    assert s["created_by"] == "local"

    listed = client.get("/api/skills").json()
    assert any(x["id"] == s["id"] for x in listed)

    r = client.patch(f"/api/skills/{s['id']}", json={"description": "Updated trigger"})
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "Updated trigger"
    assert body["instructions"].startswith("# Commit Poet")  # untouched by partial PATCH

    assert client.delete(f"/api/skills/{s['id']}").status_code == 200
    assert client.get(f"/api/skills/{s['id']}").status_code == 404


def test_skill_name_slugified_and_unique(client):
    s = _make_skill(client, name="  Commit Poet!! ")
    assert s["name"] == "commit-poet"

    r = client.post(
        "/api/skills",
        json={"name": "Commit  Poet", "description": "dup", "instructions": "x"},
    )
    assert r.status_code == 409

    r = client.post("/api/skills", json={"name": "!!!", "description": "d", "instructions": "x"})
    assert r.status_code == 422


def test_skill_validators(client):
    r = client.post("/api/skills", json={"name": "x", "description": "d", "instructions": "i",
                                         "visibility": "sneaky"})
    assert r.status_code == 422
    r = client.post("/api/skills", json={"name": "x", "description": "   ", "instructions": "i"})
    assert r.status_code == 422


# -- visibility ----------------------------------------------------------------
def test_visibility_filtering(client, db):
    from app.models import User
    from app.routers.skills import list_skills
    from app.skills import resolve_skill

    _make_skill(client, name="public-skill")
    _make_skill(client, name="private-skill", visibility="private")

    viewer = User(id="u-viewer", username="viewer", role="user")
    names = {s.name for s in list_skills(db=db, user=viewer)}
    assert "public-skill" in names
    assert "private-skill" not in names  # someone else's private skill is hidden

    # resolve_skill (the chat/tool injection path) enforces the same boundary.
    assert resolve_skill(db, "private-skill", "u-viewer") is None
    assert resolve_skill(db, "public-skill", "u-viewer") is not None

    admin = User(id="local", username="local", role="admin")
    names = {s.name for s in list_skills(db=db, user=admin)}
    assert {"public-skill", "private-skill"} <= names


def test_non_admin_cannot_publish(client, db):
    from app.models import User
    from app.routers.skills import _create
    from app.schemas import SkillCreate

    plain = User(id="u-plain", username="bob", role="user")
    s = _create(
        db,
        SkillCreate(name="bobs-skill", description="d", instructions="i", visibility="public"),
        plain,
    )
    assert s.visibility == "private"  # silently forced private for non-admins
    db.delete(s)
    db.commit()


# -- SKILL.md interop -----------------------------------------------------------
SKILL_MD = """---
name: PDF Wrangler
description: Extract text and tables from PDF files. Use when the user provides a PDF.
---

# PDF Wrangler

Use `execute_python` with pypdf to extract text.
"""


def test_import_and_export_skill_md(client):
    r = client.post("/api/skills/import", files={"file": ("SKILL.md", SKILL_MD, "text/markdown")})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["name"] == "pdf-wrangler"
    assert s["description"].startswith("Extract text and tables")
    assert s["instructions"].startswith("# PDF Wrangler")

    r = client.get(f"/api/skills/{s['id']}/export")
    assert r.status_code == 200
    text = r.text
    assert text.startswith("---\n")
    assert "name: pdf-wrangler" in text
    assert "# PDF Wrangler" in text

    # Exported file round-trips through the parser.
    from app.skills import parse_skill_md

    parsed = parse_skill_md(text)
    assert parsed["name"] == "pdf-wrangler"


def test_import_rejects_bad_files(client):
    r = client.post("/api/skills/import", files={"file": ("x.md", "no frontmatter here", "text/markdown")})
    assert r.status_code == 422
    r = client.post(
        "/api/skills/import",
        files={"file": ("x.md", "---\ndescription: only\n---\nbody", "text/markdown")},
    )
    assert r.status_code == 422  # missing name


# -- chat integration ------------------------------------------------------------
class CaptureProvider:
    model = "capture"
    supports_tools = True

    def __init__(self, log):
        self.log = log

    def stream(self, messages, tools, params):  # noqa: ARG002
        from app.providers.base import StreamDelta

        self.log.append({"messages": messages, "tools": {t.name for t in tools}, "params": params})
        yield StreamDelta(type="text", text="ok")
        yield StreamDelta(type="done", stop_reason="stop")


@pytest.fixture
def capture(monkeypatch):
    import app.routers.chat as chat_router

    log = []
    monkeypatch.setattr(chat_router, "build_provider", lambda profile, model=None: CaptureProvider(log))
    monkeypatch.setattr(chat_router, "_build_fallback", lambda active_profile: None)
    return log


def test_explicit_skill_injects_instructions(client, capture):
    _make_skill(client)

    r = client.post("/api/chat", json={"message": "commit msg please", "skills": ["commit-poet"]})
    assert r.status_code == 200
    assert capture

    system = capture[-1]["messages"][0]["content"]
    assert 'invoked the skill "commit-poet"' in system
    assert "Write every commit message as a haiku" in system  # full instructions, this turn

    # The user message records the invocation so history keeps a marker + UI shows a chip.
    conv_id = [m for m in capture[-1]["messages"] if m["role"] == "user"]
    assert conv_id  # sanity
    convs = client.get("/api/conversations").json()
    detail = client.get(f"/api/conversations/{convs[0]['id']}").json()
    user_msg = [m for m in detail["messages"] if m["role"] == "user"][-1]
    assert {"type": "skill", "name": "commit-poet"}.items() <= {
        k: v for k, v in (user_msg["attachments"] or [{}])[0].items() if k in ("type", "name")
    }.items()


def test_auto_skills_preamble_and_tool(client, capture):
    _make_skill(client)

    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    system = capture[-1]["messages"][0]["content"]
    assert "## Skills" in system
    assert "- commit-poet:" in system
    assert "Write every commit message as a haiku" not in system  # description only
    assert "use_skill" in capture[-1]["tools"]


def test_skills_disabled_strips_preamble_and_tool(client, capture):
    _make_skill(client)

    r = client.post("/api/chat", json={"message": "hello", "skills_enabled": False})
    assert r.status_code == 200
    system = capture[-1]["messages"][0]["content"]
    assert "## Skills" not in system
    assert "use_skill" not in capture[-1]["tools"]


def test_no_skills_hides_use_skill_tool(client, capture):
    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert "## Skills" not in capture[-1]["messages"][0]["content"]
    assert "use_skill" not in capture[-1]["tools"]


def test_auto_activate_false_not_advertised_but_invocable(client, capture):
    _make_skill(client, auto_activate=False)

    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert "- commit-poet:" not in capture[-1]["messages"][0]["content"]

    r = client.post("/api/chat", json={"message": "msg", "skills": ["commit-poet"]})
    assert r.status_code == 200
    assert 'invoked the skill "commit-poet"' in capture[-1]["messages"][0]["content"]


# -- use_skill tool ---------------------------------------------------------------
def test_use_skill_tool(client, db, tmp_path):
    from app.agent.tools.base import ToolContext
    from app.agent.tools.skills import UseSkill

    _make_skill(client)
    ctx = ToolContext(
        conversation_id="c1", workspace=tmp_path, db=db, runner=None, user_id="local"
    )
    result = UseSkill().run(ctx, name="commit-poet")
    assert not result.is_error
    assert "haiku" in result.content

    result = UseSkill().run(ctx, name="nope")
    assert result.is_error
    assert "commit-poet" in result.content  # lists what IS available
