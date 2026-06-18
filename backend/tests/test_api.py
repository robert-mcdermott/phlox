"""API tests via FastAPI TestClient (auth disabled -> single dev admin)."""


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_auth_config_disabled(client):
    r = client.get("/api/auth/config")
    assert r.status_code == 200 and r.json()["enabled"] is False


def test_providers_list_includes_test_profile(client):
    r = client.get("/api/providers")
    assert r.status_code == 200
    names = [p["name"] for p in r.json()["profiles"]]
    assert "test" in names


def test_conversation_crud_and_truncate(client):
    created = client.post("/api/conversations", json={"title": "My chat"}).json()
    cid = created["id"]
    assert client.get(f"/api/conversations/{cid}").status_code == 200

    # Seed a couple of messages directly, then truncate from the first.
    from app.database import SessionLocal
    from app.models import Message

    s = SessionLocal()
    try:
        m1 = Message(conversation_id=cid, role="user", content="hi")
        s.add(m1)
        s.commit()
        s.refresh(m1)
        s.add(Message(conversation_id=cid, role="assistant", content="hello"))
        s.commit()
        first_id = m1.id
    finally:
        s.close()

    assert len(client.get(f"/api/conversations/{cid}").json()["messages"]) == 2
    r = client.delete(f"/api/conversations/{cid}/messages/{first_id}")
    assert r.status_code == 200 and r.json()["deleted"] == 2
    assert client.get(f"/api/conversations/{cid}").json()["messages"] == []

    assert client.delete(f"/api/conversations/{cid}").status_code == 200


def test_settings_get_and_update(client):
    s = client.get("/api/settings").json()
    assert "max_tokens" in s and "max_context_tokens" in s
    updated = client.patch("/api/settings", json={"max_tokens": 9001}).json()
    assert updated["max_tokens"] == 9001


def test_tools_list_admin(client):
    # auth disabled -> dev admin -> admin routes allowed
    r = client.get("/api/tools")
    assert r.status_code == 200
    names = [t["name"] for t in r.json()]
    assert "write_file" in names and "execute_python" in names and "web_search" in names


def test_chat_web_search_advertised_only_when_requested(client, monkeypatch):
    from app.providers.base import StreamDelta
    import app.routers.chat as chat_router

    seen_tools = []

    class CaptureProvider:
        model = "capture"
        supports_tools = True

        def stream(self, messages, tools, params):  # noqa: ARG002
            seen_tools.append({tool.name for tool in tools})
            yield StreamDelta(type="text", text="ok")
            yield StreamDelta(type="done", stop_reason="stop")

    monkeypatch.setattr(chat_router, "build_provider", lambda profile, model=None: CaptureProvider())
    monkeypatch.setattr(chat_router, "_build_fallback", lambda active_profile: None)

    r = client.post("/api/chat", json={"message": "hello"})
    assert r.status_code == 200
    assert seen_tools and "web_search" not in seen_tools[-1]

    r = client.post("/api/chat", json={"message": "hello", "web_search": True})
    assert r.status_code == 200
    assert "web_search" in seen_tools[-1]


def test_usage_summary(client):
    r = client.get("/api/usage")
    assert r.status_code == 200
    body = r.json()
    assert "total_tokens" in body and "cost_usd" in body


def test_usage_by_user_aggregates_by_month_and_model(client):
    # auth disabled -> dev admin -> admin-gated /by-user is reachable. The report reads the
    # durable usage ledger, so we populate it via the real backfill path after seeding.
    from app.auth.deps import LOCAL_USER_ID
    from app.database import SessionLocal
    from app.models import Conversation, Message
    from app.usage_ledger import backfill_usage_ledger

    s = SessionLocal()
    try:
        conv = Conversation(user_id=LOCAL_USER_ID, title="Acct")
        s.add(conv)
        s.commit()
        s.refresh(conv)
        # Two priced turns on the same model -> one (month, user, model) cell.
        s.add(Message(conversation_id=conv.id, role="assistant", content="a",
                      model="test-model", usage={"input": 100, "output": 20, "total": 120, "cost": 0.5}))
        s.add(Message(conversation_id=conv.id, role="assistant", content="b",
                      model="test-model", usage={"input": 40, "output": 10, "total": 50, "cost": 0.25}))
        s.commit()
        backfill_usage_ledger(s)
        cid = conv.id
    finally:
        s.close()

    body = client.get("/api/usage/by-user").json()
    assert "rows" in body and "totals" in body
    cell = next(r for r in body["rows"]
                if r["user_id"] == LOCAL_USER_ID and r["model"] == "test-model")
    assert cell["input_tokens"] == 140 and cell["output_tokens"] == 30
    assert cell["total_tokens"] == 170 and cell["turns"] == 2
    assert abs(cell["cost_usd"] - 0.75) < 1e-9
    assert len(cell["month"]) == 7 and cell["month"][4] == "-"  # YYYY-MM
    assert "department" in cell  # chargeback dimension present

    # A future-only window excludes everything.
    empty = client.get("/api/usage/by-user", params={"start": "2999-01-01"}).json()
    assert empty["rows"] == [] and empty["totals"]["total_tokens"] == 0

    client.delete(f"/api/conversations/{cid}")


def test_usage_ledger_survives_user_deletion(client):
    """The core chargeback guarantee: a user's usage + department remain billable after the
    account (and all their chats) are deleted."""
    from app.auth import service
    from app.database import SessionLocal
    from app.models import Conversation, Message, UsageLedger
    from app.usage_ledger import backfill_usage_ledger

    s = SessionLocal()
    try:
        user = service.create_user(s, username="leaver", password="x", role="user",
                                    email="leaver@example.org", department="Genomics")
        uid = user.id
        conv = Conversation(user_id=uid, title="Soon-gone")
        s.add(conv)
        s.commit()
        s.refresh(conv)
        s.add(Message(conversation_id=conv.id, role="assistant", content="hi",
                      model="test-model", usage={"input": 1000, "output": 200, "total": 1200, "cost": 1.5}))
        s.commit()
        backfill_usage_ledger(s)  # snapshots username+department into the ledger

        # Sanity: ledger row exists with the identity snapshot.
        led = s.query(UsageLedger).filter(UsageLedger.user_id == uid).first()
        assert led is not None and led.username == "leaver" and led.department == "Genomics"

        # Now delete the user's data (chats + messages) and the account itself.
        service.delete_user_data(s, uid)
        from app.models import User
        s.delete(s.get(User, uid))
        s.commit()

        # The conversation/messages are gone...
        assert s.query(Conversation).filter(Conversation.user_id == uid).count() == 0
        # ...but the ledger row survives with the frozen identity.
        led2 = s.query(UsageLedger).filter(UsageLedger.user_id == uid).first()
        assert led2 is not None and led2.department == "Genomics"
    finally:
        s.close()

    # And the chargeback report still bills Genomics for the departed user.
    body = client.get("/api/usage/by-user").json()
    row = next(r for r in body["rows"] if r["user_id"] == uid)
    assert row["department"] == "Genomics"
    assert row["username"] == "leaver"
    assert row["total_tokens"] == 1200
    assert abs(row["cost_usd"] - 1.5) < 1e-9
