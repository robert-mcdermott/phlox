"""Wave 8: real bounded HTTP, DNS pinning, private web evidence and scripted chat recovery."""
import hashlib
import json
import socket
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from app import sources, web_fetch
from app.agent.tools.base import ToolContext
from app.agent.tools.web import WebFetch, WebSearch
from app.models import Conversation, Message, Source, SourceUse


@pytest.fixture
def web_context(db, tmp_path):
    conv = Conversation(user_id='local', title='Web evidence')
    db.add(conv)
    db.commit()
    ctx = ToolContext(conversation_id=conv.id, user_id='local', workspace=tmp_path, db=db,
                      runner=None, accounting=SimpleNamespace(turn_id='web-turn-' + conv.id))
    yield ctx, conv
    db.rollback()
    current = db.get(Conversation, conv.id)
    if current:
        db.delete(current)
        db.commit()


@pytest.fixture
def pages(monkeypatch):
    requests = []
    slow_started = threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append((self.path, dict(self.headers)))
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/article#part')
                self.end_headers()
                return
            if self.path == '/private-redirect':
                self.send_response(302)
                self.send_header('Location', 'http://169.254.169.254/metadata')
                self.end_headers()
                return
            status = 403 if self.path == '/denied' else 200
            body = b'<html><head><title>Travel &amp; Meals</title><script>secret script</script></head><body><h1>Allowance</h1><p>Meals cost $45.</p><div hidden>hidden token</div></body></html>'
            if self.path == '/paywall':
                body = b'<p>Subscribe to read this article</p>'
            if self.path == '/empty':
                body = b'<script>document.write("not rendered")</script>'
            if self.path == '/long':
                body = b'<p>' + b'fact ' * 6000 + b'</p>'
            self.send_response(status)
            self.send_header('Content-Type', 'application/pdf' if self.path == '/pdf' else 'text/html; charset=utf-8')
            if self.path == '/encoded':
                self.send_header('Content-Encoding', 'gzip')
            if self.path != '/unbounded':
                self.send_header('Content-Length', str(web_fetch.MAX_BYTES + 1 if self.path == '/large' else (9999 if self.path in {'/short', '/slow'} else len(body))))
            self.end_headers()
            if self.path == '/slow':
                slow_started.set()
                time.sleep(3)
                return
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {'allowlist_hosts': ['127.0.0.1']})
    yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', requests=requests, slow_started=slow_started)
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_fetch_redirect_extract_capture_and_export(db, client, pages, web_context):
    ctx, conv = web_context
    result = WebFetch().run(ctx, url=pages.url + '/redirect')
    assert not result.is_error and '[S1]' in result.content and 'Meals cost $45.' in result.content
    assert 'secret script' not in result.content and 'hidden token' not in result.content
    row = db.query(Source).filter_by(conversation_id=conv.id).one()
    assert row.kind == 'web' and row.url == pages.url + '/article' and row.title == 'Travel & Meals'
    assert row.document_id is None and row.location['status'] == 'fetched'
    repeated = WebFetch().run(ctx, url=pages.url + '/article#different')
    assert '[S1]' in repeated.content and db.query(Source).filter_by(conversation_id=conv.id).count() == 1
    assert all('Cookie' not in headers and 'Authorization' not in headers for _, headers in pages.requests)
    ref = {'label': 'S1', 'source_id': row.id}
    db.add(Message(conversation_id=conv.id, role='assistant', content='Meals cost $45 [S1].', citations=[ref]))
    db.commit()
    inspected = client.get(f'/api/conversations/{conv.id}/sources/{row.id}').json()
    assert inspected['available'] and inspected['location']['fetched_at']
    exported = client.get(f'/api/conversations/{conv.id}/export').json()['markdown']
    assert row.url in exported and 'web page' in exported and 'Meals cost $45.' in exported


@pytest.mark.parametrize('path,status', [('/denied', 'http_error'), ('/paywall', 'access_limited'),
    ('/empty', 'empty'), ('/pdf', 'unsupported_type'), ('/encoded', 'unsupported_encoding'),
    ('/large', 'too_large'), ('/short', 'incomplete'), ('/private-redirect', 'blocked')])
def test_failed_fetch_is_a_record_not_evidence(db, client, pages, web_context, path, status):
    ctx, conv = web_context
    result = WebFetch().run(ctx, url=pages.url + path)
    assert result.is_error and 'No supporting passage' in result.content
    row = db.query(Source).filter_by(conversation_id=conv.id).one()
    assert row.excerpt is None and row.location['status'] == status
    inspected = client.get(f'/api/conversations/{conv.id}/sources/{row.id}').json()
    assert not inspected['available'] and inspected['reason'] and 'excerpt' not in inspected
    assert not any(path == '/metadata' for path, _ in pages.requests)


def test_long_page_passages_are_bounded_deduplicated_and_limits_explicit(db, pages, web_context, monkeypatch):
    ctx, conv = web_context
    result = WebFetch().run(ctx, url=pages.url + '/long')
    rows = db.query(Source).filter_by(conversation_id=conv.id).order_by(Source.number).all()
    assert len(rows) == 4 and sum(len(r.excerpt) for r in rows) == web_fetch.MAX_CHARS
    assert all(len(r.excerpt) <= sources.MAX_EXCERPT_CHARS and r.location['truncated'] for r in rows)
    assert 'shortened' in result.content
    monkeypatch.setattr(sources, 'MAX_TURN_SOURCES', 4)
    limited = WebFetch().run(ctx, url=pages.url + '/article')
    assert 'limit reached' in limited.content and 'Meals cost' not in limited.content


def test_cancel_during_read_returns_promptly_without_snapshot(db, pages, web_context):
    ctx, conv = web_context
    ctx.cancel_event = threading.Event()
    def stop():
        assert pages.slow_started.wait(2)
        ctx.cancel_event.set()
    thread = threading.Thread(target=stop)
    thread.start()
    started = time.monotonic()
    result = WebFetch().run(ctx, url=pages.url + '/slow')
    thread.join()
    assert time.monotonic() - started < 2
    assert result.is_error and 'stopped' in result.content
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 0


def test_total_deadline_interrupts_slow_response(pages, monkeypatch):
    monkeypatch.setattr(web_fetch, 'MAX_SECONDS', 0.15)
    with pytest.raises(web_fetch.FetchError, match='time limit'):
        web_fetch.fetch(pages.url + '/slow')


@pytest.mark.parametrize('url', ['file:///etc/passwd', 'http://u:secret@example.com/', 'https://example.com:0/',
    'http://example.com/\\evil', 'http://example.com/\nheader', 'http://[fe80::1%25eth0]/'])
def test_invalid_urls_never_resolve(monkeypatch, url):
    monkeypatch.setattr(web_fetch, 'resolve', lambda *a: pytest.fail('Invalid URL reached DNS'))
    with pytest.raises(web_fetch.FetchError):
        web_fetch.fetch(url)


def test_connection_pins_ip_and_preserves_tls_hostname(monkeypatch):
    connected, names = [], []
    class Sock:
        def settimeout(self, value):
            pass
        def connect(self, address):
            connected.append(address)
        def getpeername(self):
            return ('93.184.216.34', 443)
        def do_handshake(self):
            pass
        def close(self):
            pass
    monkeypatch.setattr(socket, 'socket', lambda *a: Sock())
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: pytest.fail('Connection performed a second DNS lookup'))
    def wrap(sock, server_hostname, do_handshake_on_connect):
        names.append(server_hostname)
        assert do_handshake_on_connect is False
        return sock
    monkeypatch.setattr(web_fetch.ssl, 'create_default_context', lambda: SimpleNamespace(wrap_socket=wrap))
    conn = web_fetch.connection('https://example.com/article', [(2, 1, 6, '', ('93.184.216.34', 443))], web_fetch.Deadline())
    assert connected == [('93.184.216.34', 443)] and names == ['example.com']
    assert conn.host == 'example.com' and conn.auto_open == 0
    conn.close()


def test_dns_mixed_private_answers_fail_closed(monkeypatch):
    monkeypatch.setattr(web_fetch, 'resolve', lambda *a: [(2, 1, 6, '', ('93.184.216.34', 80)), (2, 1, 6, '', ('127.0.0.1', 80))])
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {})
    monkeypatch.setattr(web_fetch, 'connection', lambda *a: pytest.fail('Blocked DNS reached connection'))
    with pytest.raises(web_fetch.FetchError, match='private'):
        web_fetch.fetch('http://example.com/')


@pytest.mark.parametrize('address', ['fec0::1', '::ffff:127.0.0.1', '64:ff9b::7f00:1',
    '64:ff9b:1::a00:1', '2002:7f00:1::', '2001:0:4136:e378:8000:63bf:3fff:fdd2'])
def test_nonpublic_and_transition_ipv6_are_blocked(address):
    assert web_fetch.blocked_ip(address)
    assert not web_fetch.blocked_ip('2606:4700:4700::1111')


def test_actual_body_limit_without_content_length(pages, monkeypatch):
    monkeypatch.setattr(web_fetch, 'MAX_BYTES', 32)
    with pytest.raises(web_fetch.FetchError, match='download limit'):
        web_fetch.fetch(pages.url + '/unbounded')


def test_stop_does_not_wait_for_os_dns_resolution(monkeypatch):
    started, release, cancel = threading.Event(), threading.Event(), threading.Event()
    def lookup(*a, **k):
        started.set()
        release.wait(3)
        return [(2, 1, 6, '', ('93.184.216.34', 80))]
    monkeypatch.setattr(socket, 'getaddrinfo', lookup)
    def stop():
        started.wait(2)
        cancel.set()
    thread = threading.Thread(target=stop)
    thread.start()
    try:
        begin = time.monotonic()
        with pytest.raises(web_fetch.FetchError, match='stopped'):
            web_fetch.fetch('http://example.com/', cancel)
        assert time.monotonic() - begin < 2
    finally:
        release.set()
        thread.join()


def test_page_parse_limits_and_hidden_text():
    parser = web_fetch.PageParser()
    parser.feed('<h1>Budget</h1><table><tr><td>Meals</td><td>$45</td></tr></table><div aria-hidden="true">Private</div>')
    assert 'Meals | $45' in parser.text() and 'Private' not in parser.text()
    with pytest.raises(web_fetch.FetchError, match='nesting'):
        parser.feed('<div>' * 129)


def test_ownership_changed_during_fetch_never_publishes(db, web_context, monkeypatch):
    ctx, conv = web_context
    def fetch(*args):
        conv.user_id = 'other'
        db.commit()
        return web_fetch.Page('https://example.com/', 'Secret', 'DO NOT PUBLISH', 'a' * 64, False, 200)
    monkeypatch.setattr(web_fetch, 'fetch', fetch)
    result = WebFetch().run(ctx, url='https://example.com/')
    assert result.is_error and 'DO NOT PUBLISH' not in result.content
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 0


def test_discovery_snippets_are_not_registered(db, web_context, monkeypatch):
    ctx, conv = web_context
    monkeypatch.setattr('app.agent.tools.web.get_web_search_config', lambda: {})
    monkeypatch.setattr(WebSearch, '_search_ddgs', lambda *a: [{'title': 'Discovery', 'url': 'https://example.com/', 'snippet': 'Unfetched claim'}])
    result = json.loads(WebSearch().run(ctx, query='example').content)
    assert result['kind'] == 'discovery' and 'not fetched evidence' in result['notice']
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 0


def test_changed_content_forget_expiry_and_privacy(db, client, web_context):
    ctx, conv = web_context
    def capture(text):
        return sources.capture_web(db, conversation_id=conv.id, user_id='local', turn_id=ctx.accounting.turn_id,
            url='https://example.com/', title='Policy', text=text, content_hash=hashlib.sha256(text.encode()).hexdigest())
    capture('Original policy.')
    row = db.query(Source).filter_by(conversation_id=conv.id).one()
    capture('Revised policy.')
    assert sources.inspect_source(db, conv, row.id)['changed']
    from app.main import app
    from app.auth.deps import get_current_user
    from app.models import User
    app.dependency_overrides[get_current_user] = lambda: User(id='foreign', username='foreign', role='admin')
    try:
        path = f'/api/conversations/{conv.id}/sources/{row.id}'
        assert client.get(path).status_code == 404
        assert client.delete(path).status_code == 404
        assert client.get(f'/api/conversations/{conv.id}/export').status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    assert client.delete(path).status_code == 200
    db.refresh(row)
    assert row.excerpt is None and row.url is None
    assert not client.get(path).json()['available']
    assert db.get(SourceUse, (ctx.accounting.turn_id, row.id)).query == ''
    capture('Original policy.')
    assert client.get(path).json()['available']
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert not client.get(path).json()['available']
    sources.cleanup(db)
    db.refresh(row)
    assert row.location is None and row.title is None and row.url is None


@pytest.mark.parametrize('durable', [False, True])
def test_web_citations_survive_approval_and_run_replay(client, db, web_context, monkeypatch, durable):
    from app import runs
    from app.agent.registry import ToolRegistry
    from app.agent.tools.base import Tool, ToolResult
    from app.providers.base import StreamDelta, ToolCall
    from app.routers import chat
    _, conv = web_context
    registry = ToolRegistry()
    registry.register(WebFetch())

    class Pause(Tool):
        name = 'web_citation_pause'
        default_permission = 'ask'
        def run(self, ctx):
            return ToolResult(content='Approved')
    registry.register(Pause())

    class Provider:
        supports_tools = True
        model = 'web-citation-test'
        def stream(self, messages, tools, params):
            count = sum(m['role'] == 'tool' for m in messages)
            yield StreamDelta(type='usage', usage={'input': 4, 'output': 3, 'total': 7})
            if count < 3:
                url = 'https://example.com/' + ('policy' if count < 2 else 'other')
                yield StreamDelta(type='tool_calls', tool_calls=[ToolCall(f'fetch-{count}', 'web_fetch', {'url': url})])
            elif count == 3:
                yield StreamDelta(type='text', text='Found [S1] and [S2].')
                yield StreamDelta(type='tool_calls', tool_calls=[ToolCall('pause', 'web_citation_pause', {})])
            else:
                yield StreamDelta(type='text', text='Comparison [S1] and [S2]. Unknown [S99].')
                yield StreamDelta(type='done')

    def fetch(url, cancel):
        text = f'Verified fixture passage at {url}'
        return web_fetch.Page(url, 'Fixture policy', text, hashlib.sha256(text.encode()).hexdigest(), False, 200)
    monkeypatch.setattr(web_fetch, 'fetch', fetch)
    monkeypatch.setattr(chat, 'REGISTRY', registry)
    monkeypatch.setattr(chat, 'build_provider', lambda *a: Provider())
    monkeypatch.setattr(chat, '_build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    payload = {'conversation_id': conv.id, 'message': 'Compare these pages and cite the evidence', 'web_search': True}
    if durable:
        run = client.post('/api/runs', headers={'Idempotency-Key': uuid.uuid4().hex}, json=payload).json()
        worker.step()
    else:
        assert client.post('/api/chat', json=payload).status_code == 200
    pending = client.get(f'/api/chat/approvals/{conv.id}').json()[0]
    assert [r['label'] for r in pending['sources']] == ['S1', 'S2']
    decisions = {'pending_id': pending['pending_id'], 'decisions': {'pause': 'allow'}}
    if durable:
        assert client.post(f'/api/runs/{run["id"]}/approve', json=decisions).status_code == 200
        worker.step()
        replay = client.get(f'/api/runs/{run["id"]}/events').text
        assert 'sources' in replay and pending['sources'][0]['source_id'] in replay
    else:
        assert client.post('/api/chat/approve', json=decisions).status_code == 200
    message = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert message['citations'][:2] == pending['sources']
    assert message['citations'][2] == {'label': 'S99', 'source_id': None}
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 2
    for ref in pending['sources']:
        inspected = client.get(f'/api/conversations/{conv.id}/sources/{ref["source_id"]}').json()
        assert inspected['available'] and inspected['kind'] == 'web'
    exported = client.get(f'/api/conversations/{conv.id}/export').json()['markdown']
    assert 'https://example.com/policy' in exported and 'https://example.com/other' in exported
