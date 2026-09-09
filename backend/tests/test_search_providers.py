"""Search selection, masked admin configuration, fallback and bounded public transport."""

import json
import threading
from types import SimpleNamespace

import pytest

from app import app_config, search
from app.config import get_web_search_config
from app.models import AppConfig


@pytest.fixture(autouse=True)
def clean(db, monkeypatch):
    db.query(AppConfig).filter_by(section="web_search").delete()
    db.commit()
    app_config.invalidate()
    search.reset_health()
    monkeypatch.setattr(search, "_next", 0)
    yield
    db.query(AppConfig).filter_by(section="web_search").delete()
    db.commit()
    app_config.invalidate()
    search.reset_health()


def test_ddg_default_and_explicit_duckduckgo_backend(monkeypatch):
    from app.agent.tools.web import WebSearch

    calls = []

    class DDG:
        def __init__(self, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def text(self, query, **kw):
            calls.append(kw)
            return []

    monkeypatch.setattr("ddgs.DDGS", DDG)
    assert get_web_search_config()["engine"] == "ddg"
    WebSearch()._search_ddgs("q", 3)
    assert calls == [{"max_results": 3, "backend": "duckduckgo"}]


def test_admin_save_masks_preserves_and_clears_key(client):
    data = {"engine": "serper", "serper_api_key": "SECRET-NEVER-RETURN"}
    r = client.put("/api/admin/config/web-search", json=data)
    assert r.status_code == 200 and data["serper_api_key"] not in r.text
    assert r.json()["web_search"]["serper_api_key_set"]
    client.put("/api/admin/config/web-search", json={"engine": "serper", "interval_seconds": 3})
    assert get_web_search_config()["serper_api_key"] == data["serper_api_key"]
    assert data["serper_api_key"] not in client.get("/api/admin/config").text
    r = client.put(
        "/api/admin/config/web-search", json={"engine": "ddg", "clear_serper_api_key": True}
    )
    assert not r.json()["web_search"]["serper_api_key_set"]
    assert not get_web_search_config()["serper_api_key"]


@pytest.mark.parametrize(
    "data",
    [
        {"engine": "serper"},
        {"engine": "other"},
        {"engine": "searxng"},
        {"engine": "searxng", "searxng_url": "http://example.com"},
        {"engine": "searxng", "searxng_url": "https://key@example.com"},
        {"engine": "searxng", "searxng_url": "https://example.com/?token=key"},
        {"interval_seconds": 0},
        {"interval_seconds": 100},
    ],
)
def test_invalid_configuration_rejected(client, data):
    assert client.put("/api/admin/config/web-search", json=data).status_code == 422


def test_overlay_replaces_legacy_file_and_environment(client, monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "https://legacy.test")
    assert get_web_search_config()["engine"] == "searxng"
    client.put("/api/admin/config/web-search", json={"engine": "ddg"})
    app_config.invalidate()
    assert get_web_search_config()["engine"] == "ddg"


def test_serper_and_searx_request_contract(monkeypatch):
    calls = []

    def request(url, cancel=None, payload=None, headers=None):
        calls.append((url, payload, headers))
        return {"organic": [], "results": []}

    monkeypatch.setattr(search, "json_request", request)
    def ddg(*a):
        pytest.fail("Unexpected fallback")
    search._attempt("serper", {"serper_api_key": "secret"}, "question", 3, None, ddg)
    search._next = 0
    search._attempt(
        "searxng", {"searxng_url": "https://public.example/search"}, "question", 3, None, ddg
    )
    assert calls[0] == (
        "https://google.serper.dev/search",
        {"q": "question", "num": 3},
        {"X-API-KEY": "secret"},
    )
    assert calls[1][0] == "https://public.example/search?q=question&format=json&categories=general"
    assert calls[1][2] is None


@pytest.mark.parametrize("engine", ["serper", "searxng"])
def test_failure_fallback_is_once_and_cooldown_skips_primary(monkeypatch, engine):
    calls = []

    def attempt(name, *a):
        calls.append(name)
        if name != "ddg":
            raise RuntimeError("secret api key in remote error")
        return []

    monkeypatch.setattr(search, "_attempt", attempt)
    cfg = {"engine": engine}
    rows, used, reason = search.search(cfg, "q", 3, None, None)
    assert used == "ddg" and reason and "secret" not in reason
    search.search(cfg, "q2", 3, None, None)
    assert calls == [engine, "ddg", "ddg"]


def test_valid_empty_results_do_not_trigger_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(search, "_attempt", lambda engine, *a: calls.append(engine) or [])
    assert search.search({"engine": "serper"}, "q", 3, None, None) == ([], "serper", None)
    assert calls == ["serper"]


def test_active_searches_do_not_extend_cooldown(monkeypatch):
    now = [100.0]
    calls = []

    def attempt(engine, *args):
        calls.append(engine)
        if engine == "serper" and now[0] == 100:
            raise TimeoutError("private query and key")
        return []

    monkeypatch.setattr(search.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(search, "_attempt", attempt)
    cfg = {"engine": "serper"}
    search.search(cfg, "private query", 3, None, None)
    for second in (110, 130, 159):
        now[0] = second
        assert search.search(cfg, "private query", 3, None, None)[1] == "ddg"
    now[0] = 161
    assert search.search(cfg, "private query", 3, None, None) == ([], "serper", None)
    assert calls == ["serper", "ddg", "ddg", "ddg", "ddg", "serper"]
    assert not search._failed_until


def test_failure_logs_identify_both_providers_without_secrets(monkeypatch, caplog):
    def attempt(engine, *args):
        if engine == "serper":
            raise search.SearchError("PRIVATE-REMOTE-BODY", http_status=429)
        raise RuntimeError("PRIVATE-QUERY PRIVATE-KEY")

    monkeypatch.setattr(search, "_attempt", attempt)
    with pytest.raises(search.SearchError, match="fallback are unavailable"):
        search.search({"engine": "serper", "serper_api_key": "PRIVATE-KEY"},
                      "PRIVATE-QUERY", 3, None, None)
    assert "engine=serper role=primary error_type=SearchError http_status=429" in caplog.text
    assert "engine=ddg role=fallback error_type=RuntimeError" in caplog.text
    assert "PRIVATE" not in caplog.text


def test_stop_during_primary_never_falls_back(monkeypatch):
    cancel = threading.Event()
    calls = []

    def fail(engine, *a):
        calls.append(engine)
        cancel.set()
        raise RuntimeError()

    monkeypatch.setattr(search, "_attempt", fail)
    with pytest.raises(search.SearchError, match="stopped"):
        search.search({"engine": "serper"}, "q", 3, cancel, None)
    assert calls == ["serper"]


def test_pacing_wait_is_cancellable(monkeypatch):
    monkeypatch.setattr(search, "_next", search.time.monotonic() + 30)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(search.SearchError, match="stopped"):
        search._attempt("ddg", {}, "q", 3, cancel, lambda *a: pytest.fail("Search dispatched"))


def test_test_endpoint_uses_unsaved_settings_without_saving(client, monkeypatch):
    used = []
    monkeypatch.setattr(
        search, "search", lambda cfg, *a: used.append(cfg) or ([], "ddg", "Primary HTTP 403")
    )
    r = client.post(
        "/api/admin/config/web-search/test", json={"engine": "serper", "serper_api_key": "secret"}
    )
    assert r.json()["fallback_reason"] == "Primary HTTP 403"
    assert used[0]["serper_api_key"] == "secret"
    assert get_web_search_config()["engine"] == "ddg"
    assert "secret" not in r.text


def test_user_cannot_read_write_or_test_search(client, monkeypatch):
    from fastapi import HTTPException
    from app.main import app
    from app.auth.deps import require_admin

    def refuse():
        raise HTTPException(403, "Admin only")

    app.dependency_overrides[require_admin] = refuse
    try:
        assert client.get("/api/admin/config").status_code == 403
        assert client.put("/api/admin/config/web-search", json={}).status_code == 403
        assert client.post("/api/admin/config/web-search/test", json={}).status_code == 403
    finally:
        app.dependency_overrides.pop(require_admin)


@pytest.mark.parametrize(
    "status,body",
    [
        (403, b"private server error"),
        (200, b"<html>Captcha</html>"),
        (200, b"[]"),
        (200, b"x" * 1048577),
    ],
)
def test_json_transport_fails_closed_and_closes_connection(monkeypatch, status, body):
    import io

    stream = io.BytesIO(body)
    closed = []
    requests = []
    response = SimpleNamespace(status=status, getheader=lambda *a: "identity", read1=stream.read)
    conn = SimpleNamespace(
        sock=SimpleNamespace(settimeout=lambda value: None),
        request=lambda *a, **kw: requests.append(kw),
        getresponse=lambda: response,
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(search.web_fetch, "checked_addresses", lambda *a: [])
    monkeypatch.setattr(search.web_fetch, "connection", lambda *a: conn)
    with pytest.raises((ValueError, json.JSONDecodeError)):
        search.json_request("https://example.com/search")
    assert closed == [True]
    assert requests[0]['headers']['User-Agent'] == search.web_fetch.USER_AGENT


@pytest.mark.parametrize("cancelled", [False, True])
def test_search_waits_for_slow_headers_but_stop_interrupts(monkeypatch, cancelled):
    """Exercise actual HTTP socket reads beyond the old three-second timeout."""
    import http.client
    import socket

    client_sock, server_sock = socket.socketpair()
    cancel = threading.Event()
    finished = threading.Event()

    def server():
        try:
            server_sock.recv(8192)
            if cancelled:
                cancel.set()
                finished.wait(2)
            elif not finished.wait(3.2):
                server_sock.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 14\r\n\r\n{"organic":[]}')
        finally:
            server_sock.close()

    def connection(url, addresses, deadline):
        client_sock.settimeout(3)
        deadline.attach(client_sock)
        conn = http.client.HTTPConnection("example.com")
        conn.auto_open = 0
        conn.sock = client_sock
        return conn

    monkeypatch.setattr(search.web_fetch, "checked_addresses", lambda *a: [])
    monkeypatch.setattr(search.web_fetch, "connection", connection)
    worker = threading.Thread(target=server, daemon=True)
    worker.start()
    try:
        if cancelled:
            with pytest.raises(search.web_fetch.FetchError) as failure:
                search.json_request("https://example.com/search", cancel)
            assert failure.value.status == "cancelled"
        else:
            assert search.json_request("https://example.com/search", cancel) == {"organic": []}
    finally:
        finished.set()
        client_sock.close()
        worker.join(timeout=5)
    assert not worker.is_alive()


def test_public_search_transport_ignores_private_fetch_allowlist(monkeypatch):
    import socket
    from app import web_fetch

    monkeypatch.setattr(web_fetch, "get_web_fetch_config", lambda: {"allow_private_networks": True})
    monkeypatch.setattr(
        web_fetch,
        "resolve",
        lambda *a: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))],
    )
    with pytest.raises(web_fetch.FetchError, match="private"):
        search.json_request("https://example.com/search")
