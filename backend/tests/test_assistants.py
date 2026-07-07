"""Custom assistants: CRUD, visibility, chat integration, and KB retrieval scoping."""
import pytest


def _make_assistant(client, **overrides):
    body = {
        "name": "IT Assistant",
        "description": "Helps with IT questions",
        "system_prompt": "You are Bridget, an IT architecture assistant.",
        "prompt_suggestions": ["Who are you?"],
        "capabilities": {"web_search": False},
        "visibility": "public",
        **overrides,
    }
    r = client.post("/api/assistants", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_assistant_crud_roundtrip(client):
    a = _make_assistant(client)
    assert a["name"] == "IT Assistant"
    assert a["capabilities"] == {"web_search": False}
    assert a["created_by"] == "local"
    assert a["n_documents"] == 0

    listed = client.get("/api/assistants").json()
    assert any(x["id"] == a["id"] for x in listed)

    # Partial PATCH only touches the provided fields.
    r = client.patch(f"/api/assistants/{a['id']}", json={"description": "Updated"})
    assert r.status_code == 200
    body = r.json()
    assert body["description"] == "Updated"
    assert body["system_prompt"] == "You are Bridget, an IT architecture assistant."

    assert client.delete(f"/api/assistants/{a['id']}").status_code == 200
    assert client.get(f"/api/assistants/{a['id']}").status_code == 404


def test_assistant_validators(client):
    r = client.post("/api/assistants", json={"name": "x", "visibility": "sneaky"})
    assert r.status_code == 422

    r = client.post("/api/assistants", json={"name": "x", "avatar": "not-an-image-" * 3})
    assert r.status_code == 422  # long non-data-URL avatar rejected

    r = client.post("/api/assistants", json={"name": "   "})
    assert r.status_code == 422

    # Emoji and data-URL avatars are accepted.
    a = _make_assistant(client, name="Emoji", avatar="🤖")
    client.delete(f"/api/assistants/{a['id']}")
    a = _make_assistant(client, name="Img", avatar="data:image/png;base64,AAAA")
    client.delete(f"/api/assistants/{a['id']}")


def test_require_admin_gates_writes():
    from fastapi import HTTPException

    from app.auth.deps import require_admin
    from app.models import User

    non_admin = User(id="u-plain", username="bob", role="user")
    with pytest.raises(HTTPException) as ei:
        require_admin(non_admin)
    assert ei.value.status_code == 403


def test_visibility_filtering(client, db):
    from app.models import User
    from app.routers.assistants import list_assistants

    pub = _make_assistant(client, name="Public A")
    priv = _make_assistant(client, name="Private A", visibility="private")

    viewer = User(id="u-viewer", username="viewer", role="user")
    names = {a.name for a in list_assistants(db=db, user=viewer)}
    assert "Public A" in names
    assert "Private A" not in names  # someone else's private assistant is hidden

    # The admin creator still sees both.
    admin = User(id="local", username="local", role="admin")
    names = {a.name for a in list_assistants(db=db, user=admin)}
    assert {"Public A", "Private A"} <= names

    client.delete(f"/api/assistants/{pub['id']}")
    client.delete(f"/api/assistants/{priv['id']}")


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


def test_chat_pins_and_snapshots_assistant(client, db, capture):
    from app.models import Conversation

    a = _make_assistant(client, name="Pinned", profile="test", model="test-model")

    r = client.post("/api/chat", json={"message": "hello", "assistant_id": a["id"]})
    assert r.status_code == 200
    assert capture, "provider was not called"

    # Assistant persona is the system prompt.
    assert capture[-1]["messages"][0]["content"].startswith(
        "You are Bridget, an IT architecture assistant."
    )
    # web_search capability False -> tool stripped even though default tools include it.
    assert "web_search" not in capture[-1]["tools"]

    conv = (
        db.query(Conversation).filter(Conversation.assistant_id == a["id"]).first()
    )
    assert conv is not None
    assert conv.profile == "test" and conv.model == "test-model"
    assert conv.system_prompt.startswith("You are Bridget")

    # req.assistant_id on an existing conversation is ignored (pin wins).
    b = _make_assistant(client, name="Other", system_prompt="You are someone else.")
    r = client.post(
        "/api/chat",
        json={"message": "again", "conversation_id": conv.id, "assistant_id": b["id"]},
    )
    assert r.status_code == 200
    assert capture[-1]["messages"][0]["content"].startswith("You are Bridget")

    client.delete(f"/api/assistants/{a['id']}")
    client.delete(f"/api/assistants/{b['id']}")
    client.delete(f"/api/conversations/{conv.id}")


def test_chat_capability_narrowing_tools_off(client, capture):
    a = _make_assistant(
        client, name="QA only", capabilities={"web_search": True, "tools": False}
    )
    r = client.post(
        "/api/chat", json={"message": "hi", "assistant_id": a["id"], "web_search": True}
    )
    assert r.status_code == 200
    # tools:false leaves at most web_search + search_documents.
    assert capture[-1]["tools"] <= {"web_search", "search_documents"}
    assert "web_search" in capture[-1]["tools"]  # allowed by capability + requested
    client.delete(f"/api/assistants/{a['id']}")


def test_chat_assistant_kb_forces_document_search(client, db, capture):
    from app.models import Document

    a = _make_assistant(client, name="KB bot")
    doc = Document(
        filename="kb.md", status="ready", n_chunks=1, assistant_id=a["id"], user_id=None
    )
    db.add(doc)
    db.commit()

    r = client.post("/api/chat", json={"message": "what do the docs say?", "assistant_id": a["id"]})
    assert r.status_code == 200
    assert "search_documents" in capture[-1]["tools"]
    assert "curated knowledge base" in capture[-1]["messages"][0]["content"]

    client.delete(f"/api/assistants/{a['id']}")


def test_chat_deleted_assistant_degrades_to_snapshot(client, db, capture):
    from app.models import Conversation

    a = _make_assistant(client, name="Ephemeral")
    r = client.post("/api/chat", json={"message": "hello", "assistant_id": a["id"]})
    assert r.status_code == 200
    conv = db.query(Conversation).filter(Conversation.assistant_id == a["id"]).first()
    assert conv is not None

    client.delete(f"/api/assistants/{a['id']}")

    r = client.post("/api/chat", json={"message": "still there?", "conversation_id": conv.id})
    assert r.status_code == 200
    # Runs on the snapshotted system prompt (live assistant is gone).
    assert capture[-1]["messages"][0]["content"].startswith("You are Bridget")
    client.delete(f"/api/conversations/{conv.id}")


def test_chat_resolves_assistant_model_before_budget(client, capture, monkeypatch):
    import app.budgets as budgets_mod

    seen_models = []
    monkeypatch.setattr(
        budgets_mod, "enforce_budget", lambda db, user, model: seen_models.append(model)
    )
    a = _make_assistant(client, name="Priced", profile="test", model="expensive-model")
    r = client.post("/api/chat", json={"message": "hi", "assistant_id": a["id"]})
    assert r.status_code == 200
    assert seen_models[-1] == "expensive-model"
    client.delete(f"/api/assistants/{a['id']}")


# -- retrieval scoping --------------------------------------------------------

def test_scope_filter_assistant_branches():
    """The Qdrant filter must OR user + assistant scopes, and keep strict user scope
    when no assistant is in play."""
    from app.rag.store import QdrantVectorStore

    # _scope_filter doesn't touch self, so call it unbound (no embedded store needed).
    f = QdrantVectorStore._scope_filter(None, "u1", None, assistant_id="a1")
    outer = f.must[0]
    kinds = {(c.key, c.match.value) for c in outer.should}
    assert kinds == {("user_id", "u1"), ("assistant_id", "a1")}

    f = QdrantVectorStore._scope_filter(None, "u1", None)
    assert f.must[0].key == "user_id" and f.must[0].match.value == "u1"
    # No OR branch: another user's search can never admit assistant-less foreign chunks.

    f = QdrantVectorStore._scope_filter(None, None, None, assistant_id="a1")
    assert f.must[0].key == "assistant_id" and f.must[0].match.value == "a1"


def test_search_chunks_passes_assistant_scope(db, monkeypatch):
    from app.rag import retrieve

    seen = {}

    class FakeStore:
        def search(self, dense, sparse, limit, conversation_id=None, user_id=None,
                   document_ids=None, assistant_id=None):  # noqa: ARG002
            seen.update(user_id=user_id, assistant_id=assistant_id)
            return []

    monkeypatch.setattr(retrieve, "get_vector_store", lambda: FakeStore())
    monkeypatch.setattr(retrieve, "embed_query", lambda q: [0.0])
    monkeypatch.setattr(retrieve, "sparse_embed", lambda q: {"indices": [], "values": []})

    retrieve.search_chunks(db, "q", user_id="u1", assistant_id="a1")
    assert seen == {"user_id": "u1", "assistant_id": "a1"}


def test_chunk_payload_includes_assistant_scope(db):
    """Ingest + reindex payloads must carry assistant_id, or an admin reindex would strip
    shared-KB scoping. Assistant docs are deployment-owned: no user_id in the payload."""
    from app.models import DocChunk, Document
    from app.rag.ingest import _payload
    from app.rag.retrieve import build_chunk_payload

    doc = Document(filename="kb.md", status="ready", assistant_id="a1", user_id=None)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    chunk = DocChunk(document_id=doc.id, ordinal=0, text="hello", embedding=[0.1])
    db.add(chunk)
    db.commit()
    db.refresh(chunk)

    for payload in (
        _payload(doc, chunk),
        build_chunk_payload(chunk, doc.filename, doc.conversation_id, doc.user_id, doc.assistant_id),
    ):
        assert payload["assistant_id"] == "a1"
        assert "user_id" not in payload
        assert "conversation_id" not in payload

    db.delete(doc)
    db.commit()


def test_tool_context_carries_assistant_id(db):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY
    from app.models import Conversation

    conv = Conversation(title="t", user_id="u1", assistant_id="a1")
    db.add(conv)
    db.commit()
    db.refresh(conv)

    gate = PermissionGate(db, REGISTRY, auto_approve=True)
    session = AgentSession(db, conv, provider=None, registry=REGISTRY, gate=gate,
                           params={}, profile="test", model=None)
    assert session.ctx.assistant_id == "a1"
    assert session.ctx.user_id == "u1"

    db.delete(conv)
    db.commit()


def test_assistant_delete_cascades_documents(client, db):
    from app.models import DocChunk, Document

    a = _make_assistant(client, name="Doomed")
    doc = Document(filename="kb.md", status="ready", assistant_id=a["id"], user_id=None)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    db.add(DocChunk(document_id=doc.id, ordinal=0, text="chunk"))
    db.commit()
    doc_id = doc.id

    r = client.delete(f"/api/assistants/{a['id']}")
    assert r.status_code == 200 and r.json()["documents_deleted"] == 1

    db.expire_all()
    assert db.get(Document, doc_id) is None
    assert db.query(DocChunk).filter(DocChunk.document_id == doc_id).count() == 0


def test_assistant_documents_hidden_from_personal_listing(client, db):
    from app.models import Document

    a = _make_assistant(client, name="Shared KB")
    doc = Document(filename="shared.md", status="ready", assistant_id=a["id"], user_id=None)
    db.add(doc)
    db.commit()

    # The personal documents endpoint (owner-scoped) must not include assistant docs.
    personal = client.get("/api/documents").json()
    assert all(d["id"] != doc.id for d in personal)

    # The assistant KB endpoint lists them (admin-only).
    kb = client.get(f"/api/assistants/{a['id']}/documents").json()
    assert any(d["id"] == doc.id for d in kb)

    client.delete(f"/api/assistants/{a['id']}")
