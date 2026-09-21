"""Real HTTP + isolated parser process: PDF/JSON evidence and research integration."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from app import research_notebook, sources, web_fetch, web_formats
from app.agent.registry import REGISTRY
from app.agent.tools.base import ToolContext
from app.agent.tools.web import WebFetch
from app.models import Conversation, Message, Source
from app.providers.base import ToolCall
from app.research import Research
from test_research import ResearchProvider, parse, session


def pdf_bytes(texts, encrypted=False, compressed=False):
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
    writer = PdfWriter()
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
                             NameObject('/BaseFont'): NameObject('/Helvetica')})
    for text in texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
        stream = DecodedStreamObject()
        data = 'BT /F1 10 Tf 20 750 Td ' + ' '.join('(' + line + ') Tj 0 -14 Td' for line in text.splitlines()) + ' ET'
        stream.set_data(data.encode('ascii'))
        page[NameObject('/Contents')] = writer._add_object(stream)
        if compressed:
            page.compress_content_streams()
    if encrypted:
        writer.encrypt('test-password')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


@pytest.fixture
def site(monkeypatch):
    records = [{'year': 2020 + i, 'amount': i * 10, 'categories': ['public', 'research']} for i in range(100)]
    data = {
        '/report.pdf': (pdf_bytes(['Earlier activities.\n' * 1200, 'Year       Funding\n2025       45 million\nExcludes private gifts.']), 'application/pdf'),
        '/image.pdf': (pdf_bytes(['']), 'application/pdf'),
        '/locked.pdf': (pdf_bytes(['Secret'], encrypted=True), 'application/pdf'),
        '/broken.pdf': (b'%PDF-1.7\nbroken', 'application/pdf'),
        '/report.json': (json.dumps({'metadata': {'total': 100}, 'results': records}).encode(), 'application/json'),
        '/schema.json': (b'{"type":"object","properties":{"amount":{"type":"number"}},"$ref":"http://127.0.0.1/private"}', 'application/schema+json'),
        '/bad.json': (b'{"amount":3,"amount":4}', 'application/json'),
        '/nan.json': (b'{"amount":NaN}', 'application/json'),
        '/deep.json': (b'[' * 70 + b'0' + b']' * 70, 'application/json'),
        '/escape.json': (b'{"a/b":{"~field":[1,2,3]}}', 'application/json'),
        '/html': (b'<h1>Funding</h1><p>Funding is 45 million in 2025.</p>', 'text/html'),
    }
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            requests.append(self.path)
            if self.path == '/redirect':
                self.send_response(302)
                self.send_header('Location', '/report.pdf')
                self.end_headers()
                return
            if self.path == '/blocked':
                self.send_response(302)
                self.send_header('Location', 'http://169.254.169.254/private.pdf')
                self.end_headers()
                return
            body, kind = data[self.path]
            self.send_response(200)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {'allowlist_hosts': ['127.0.0.1']})
    yield SimpleNamespace(url=f'http://127.0.0.1:{server.server_port}', requests=requests, data=data)
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def ctx(db, tmp_path):
    conv = Conversation(user_id='local', title='Format test')
    db.add(conv)
    db.commit()
    research = Research({'scope': 'web', 'depth': 'standard', 'domains': []})
    research.state['phase'] = 'gather'
    context = ToolContext(conversation_id=conv.id, user_id='local', workspace=tmp_path, db=db, runner=None,
                          research=research, accounting=SimpleNamespace(turn_id='formats-' + conv.id))
    yield context
    db.rollback()
    db.delete(conv)
    db.commit()


def test_pdf_late_page_layout_provenance_identity_and_export(ctx, site):
    result = WebFetch().run(ctx, url=site.url + '/redirect', query='2025 funding', max_chars=1000)
    assert not result.is_error and '45 million' in result.content and 'PDF page 2' in result.content
    rows = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).all()
    row = next(s for s in rows if '45 million' in s.excerpt)
    assert row.url.endswith('/report.pdf') and row.location['page'] == 2
    assert row.location['format'] == 'pdf' and row.location['page_count'] == 2
    direct = WebFetch().run(ctx, url=site.url + '/report.pdf', pdf_page=2)
    assert not direct.is_error
    second = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id, number=len(rows) + 1).first()
    # A focused suffix and a complete page may differ, but a repeated full page is stable.
    before = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).count()
    assert not WebFetch().run(ctx, url=site.url + '/report.pdf', pdf_page=2).is_error
    assert ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).count() == before
    assert second is None or second.location['page'] == 2
    refs = sources.bind(f'Funding [S{row.number}]', sources.catalog(ctx.db, ctx.accounting.turn_id, ctx.conversation_id))
    conv = ctx.db.get(Conversation, ctx.conversation_id)
    ctx.db.add(Message(conversation_id=conv.id, role='assistant', content=f'Funding [S{row.number}]', citations=refs))
    ctx.db.commit()
    exported = sources.export_markdown(ctx.db, conv)
    assert 'PDF page 2' in exported and '45 million' in exported


def test_json_complete_records_pointer_preview_and_retained_notebook(ctx, site):
    preview = WebFetch().run(ctx, url=site.url + '/report.json')
    assert preview.is_error and 'Structure preview only' in preview.content and '/results' in preview.content
    assert ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).count() == 0
    result = WebFetch().run(ctx, url=site.url + '/report.json', json_pointer='/results', json_start=5, json_limit=2)
    assert not result.is_error and 'json_start=7' in result.content
    row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).one()
    assert json.loads(row.excerpt) == json.loads(site.data['/report.json'][0])['results'][5:7]
    assert row.location['json_pointer'] == '/results' and row.location['item_start'] == 5 and row.location['item_end'] == 7
    expiry = row.expires_at
    calls = len(site.requests)
    notes = {'findings': [{'text': 'The data contains funding records.', 'sources': ['S1']}], 'disagreements': [], 'questions': []}
    assert research_notebook.update(ctx, notes) is None
    ctx.research.state['phase'] = 'synthesize'
    projected = research_notebook.prepare(ctx, [{'role': 'user', 'content': 'Report funding'}], [],
                                         {'max_context_tokens': 16000, 'max_tokens': 2000}, None)
    assert row.excerpt in '\n'.join(m['content'] for m in projected)
    assert 'json_pointer' in json.dumps(projected) and len(site.requests) == calls and row.expires_at == expiry
    retained = sources.read_web(ctx.db, conversation_id=ctx.conversation_id, user_id='local', turn_id=ctx.accounting.turn_id,
                                label='S1', research=ctx.research)
    assert 'JSON pointer' in retained and '[5, 7)' in retained
    row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    ctx.db.commit()
    assert not research_notebook.evidence(ctx)


def test_json_pointer_escaping_schema_no_external_refs_and_omitted_records(site):
    page = web_fetch.fetch(site.url + '/escape.json', json_pointer='/a~1b/~0field', json_start=1, json_limit=1)
    assert json.loads(page.text) == [2] and 'json_start=2' in page.notice
    schema = web_fetch.fetch(site.url + '/schema.json')
    assert '$ref' in json.loads(schema.text) and site.requests == ['/escape.json', '/schema.json']
    limited = web_fetch.fetch(site.url + '/report.json', json_pointer='/results', max_chars=300, json_limit=50)
    assert 0 < len(json.loads(limited.text)) < 50 and len(limited.text) <= 300


def test_format_size_page_scan_and_empty_json_boundaries(site):
    site.data['/large.pdf'] = (b'%PDF-' + b'x' * web_fetch.MAX_BYTES, 'application/pdf')
    with pytest.raises(web_fetch.FetchError, match='2 MiB'):
        web_fetch.fetch(site.url + '/large.pdf')
    site.data['/many.pdf'] = (pdf_bytes(['Text'] * 201), 'application/pdf')
    with pytest.raises(web_fetch.FetchError, match='200-page scan limit'):
        web_fetch.fetch(site.url + '/many.pdf')
    selected = web_fetch.fetch(site.url + '/many.pdf', pdf_page=201)
    assert selected.passages[0]['provenance']['page'] == 201
    site.data['/empty.json'] = (b'[]', 'application/json')
    assert json.loads(web_fetch.fetch(site.url + '/empty.json').text) == []
    with pytest.raises(web_fetch.FetchError, match='too small'):
        web_fetch.fetch(site.url + '/empty.json', max_chars=1)


def test_compressed_pdf_expansion_is_bounded_before_text_extraction(site):
    site.data['/expanded.pdf'] = (pdf_bytes(['x' * (9 * 1024 * 1024)], compressed=True), 'application/pdf')
    assert len(site.data['/expanded.pdf'][0]) < web_fetch.MAX_BYTES
    with pytest.raises(web_fetch.FetchError, match='expansion limits'):
        web_fetch.fetch(site.url + '/expanded.pdf')


def test_json_preserves_numeric_spelling_without_float_rounding(site):
    body = b'{"amount":0.1234567890123456789012345,"large":99999999999999999999,"scale":1e999,"flag":true,"missing":null}'
    site.data['/precision.json'] = (body, 'application/json')
    result = web_fetch.fetch(site.url + '/precision.json')
    from decimal import Decimal
    assert json.loads(result.text, parse_float=Decimal) == json.loads(body, parse_float=Decimal)
    assert '0.1234567890123456789012345' in result.text and '1e999' in result.text


def test_json_scope_ownership_and_snapshot_removal(ctx, site):
    assert not WebFetch().run(ctx, url=site.url + '/report.json', json_pointer='/metadata').is_error
    row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).one()
    conv = ctx.db.get(Conversation, ctx.conversation_id)
    assert sources.inspect_source(ctx.db, conv, row.id)['available']
    ctx.user_id = 'another-user'
    assert research_notebook.evidence(ctx) == {}
    ctx.user_id = 'local'
    ctx.research.state['options']['domains'] = ['other.example']
    assert research_notebook.evidence(ctx) == {}
    ctx.research.state['options']['domains'] = []
    row.excerpt = None
    row.deleted_at = datetime.now(timezone.utc)
    ctx.db.commit()
    assert research_notebook.evidence(ctx) == {}


@pytest.mark.parametrize('path,reason', [('/image.pdf', 'OCR'), ('/locked.pdf', 'Encrypted'),
    ('/broken.pdf', 'Malformed'), ('/bad.json', 'Malformed'), ('/nan.json', 'Malformed'), ('/deep.json', '64 levels')])
def test_bad_formats_capture_only_failure_records(ctx, site, path, reason):
    result = WebFetch().run(ctx, url=site.url + path)
    assert result.is_error and reason in result.content
    row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).one()
    assert row.excerpt is None and row.location['status'] != 'fetched'


@pytest.mark.parametrize('options', [{'pdf_page': 0}, {'json_pointer': 'results'}, {'json_pointer': '/a~9'},
    {'json_limit': 0}, {'json_start': -1}])
def test_invalid_selectors_never_access_network(ctx, site, options):
    assert WebFetch().run(ctx, url=site.url + '/report.json', **options).is_error
    assert site.requests == []


def test_format_selection_errors_and_redirect_scope(site):
    for path, options in [('/report.pdf', {'pdf_page': 99}), ('/report.json', {'json_pointer': '/missing'}),
                          ('/report.json', {'query': 'funding'}), ('/html', {'pdf_page': 1})]:
        with pytest.raises(web_fetch.FetchError) as error:
            web_fetch.fetch(site.url + path, **options)
        assert error.value.status in {'invalid_selection', 'selection_empty'}
    with pytest.raises(web_fetch.FetchError):
        web_fetch.fetch(site.url + '/blocked', url_policy=lambda url: url.startswith(site.url))
    assert site.requests[-1] == '/blocked'


@pytest.mark.parametrize('cancel', [False, True])
def test_parser_process_is_killed_on_deadline_or_stop(monkeypatch, cancel):
    original = subprocess.Popen
    children = []
    def stalled(*args, **kwargs):
        process = original([sys.executable, '-c', 'import time; time.sleep(20)'], **kwargs)
        children.append(process)
        return process
    monkeypatch.setattr(web_formats.subprocess, 'Popen', stalled)
    event = threading.Event()
    with web_fetch.Deadline(event) as deadline:
        if cancel:
            timer = threading.Timer(.2, event.set)
            timer.start()
        else:
            deadline.until = time.monotonic() + .2
        started = time.monotonic()
        with pytest.raises(web_fetch.FetchError):
            # The stalled reader cannot drain this payload: Stop must also interrupt
            # a blocked input write and reap the exchange thread/process.
            web_formats.extract(b'x' * (512 * 1024), 'json', deadline, json_pointer='', json_start=0, json_limit=20, max_chars=6000)
        assert time.monotonic() - started < 3
        assert children[0].poll() is not None
        if cancel:
            timer.join()


def test_slow_parser_startup_receives_entire_large_input(monkeypatch):
    original = subprocess.Popen
    children = []

    def delayed_reader(*args, **kwargs):
        process = original([sys.executable, '-c',
            'import time,json,sys,base64; time.sleep(0.3); '
            'v=json.load(sys.stdin); print(json.dumps({"result":{"received":len(base64.b64decode(v["body"]))}}))'], **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(web_formats.subprocess, 'Popen', delayed_reader)
    with web_fetch.Deadline() as deadline:
        deadline.until = time.monotonic() + 5
        result = web_formats.extract(b'x' * (512 * 1024), 'json', deadline)
    assert result['received'] == 512 * 1024
    assert children[0].poll() == 0


def test_scripted_research_combines_html_pdf_json_and_notebook(db, monkeypatch, site):
    provider = ResearchProvider([
        [ToolCall('html', 'web_fetch', {'url': site.url + '/html'})],
        [ToolCall('pdf', 'web_fetch', {'url': site.url + '/report.pdf', 'pdf_page': 2})],
        [ToolCall('json', 'web_fetch', {'url': site.url + '/report.json', 'json_pointer': '/results/5'})],
        [ToolCall('note', 'update_research_notebook', {'findings': [{'text': 'Compare funding definitions.', 'sources': ['S1', 'S2', 'S3']}],
                                                   'disagreements': [], 'questions': []})],
    ])
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    monkeypatch.setattr(REGISTRY.get('run_shell'), 'run', lambda *a, **kw: pytest.fail('No shell downloads'))
    parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Compare the sources'}]))
    assert agent.research.progress()['notebook']['context']['restored_sources'] == ['S1', 'S2', 'S3']
    assert len(site.requests) == 3
    final = json.dumps(provider.seen[-1]['messages'])
    assert 'page_count' in final and 'json_pointer' in final
