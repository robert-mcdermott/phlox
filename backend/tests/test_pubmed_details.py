"""Record detail: protected XML, evidence lineage, affiliations and partial exports."""
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest

from app import public_api, sources, web_fetch
from app.agent.tools.api_dataset import ExportApiDataset
from app.agent.tools.public_api import QueryPublicApi
from app.agent.tools.web import ReadWebSource
from app.models import Conversation, PendingApproval, Source
from app.providers.base import ToolCall
from app.workspace.manager import resolve_in_workspace, workspace_dir
from test_pubmed import ARGS, pubmed_site as search_site, ctx as format_context
from test_public_api import rows
from test_research import ResearchProvider, parse, session

ctx = format_context
pubmed_site = search_site

XML = '''<?xml version="1.0"?>
<!DOCTYPE PubmedArticleSet SYSTEM "https://example.invalid/pubmed.dtd">
<PubmedArticleSet><PubmedArticle><MedlineCitation><PMID>{id}</PMID><Article>
<ArticleTitle>Article <i>{id}</i></ArticleTitle>
<Abstract><AbstractText Label="OBJECTIVE">Study ovarian cancer.</AbstractText>
<AbstractText Label="RESULTS" NlmCategory="RESULTS">Reported outcome: H<sub>2</sub>O &amp; results.</AbstractText></Abstract>
<AuthorList CompleteYN="Y">
<Author><ForeName>Alice</ForeName><LastName>Example</LastName><AffiliationInfo><Affiliation>Fred Hutchinson Cancer Center, Seattle.</Affiliation></AffiliationInfo><AffiliationInfo><Affiliation>University of Washington.</Affiliation></AffiliationInfo></Author>
<Author><Initials>B</Initials><LastName>Unknown</LastName></Author>
<Author><CollectiveName>Example Consortium</CollectiveName><AffiliationInfo><Affiliation>Another University.</Affiliation></AffiliationInfo></Author>
</AuthorList><Affiliation>Legacy affiliation without author mapping.</Affiliation>
</Article></MedlineCitation></PubmedArticle></PubmedArticleSet>'''
DETAIL = {'record_from': 'S1', 'record_id': '103'}


@pytest.fixture
def detail_site(monkeypatch):
    state = SimpleNamespace(requests=[], headers=[], statuses=[], retry_after=None, status=200, kind='text/xml',
                            xml=lambda i: XML.format(id=i).encode(), wait=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            params = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
            state.requests.append(params)
            state.headers.append(dict(self.headers))
            if state.wait:
                state.wait.wait(3)
            body = state.xml(params['id'])
            self.send_response(state.statuses.pop(0) if state.statuses else state.status)
            if state.retry_after is not None:
                self.send_header('Retry-After', state.retry_after)
            self.send_header('Content-Type', state.kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Location', 'http://169.254.169.254/private')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(public_api, 'PUBMED_DETAIL_ENDPOINT', f'http://127.0.0.1:{server.server_port}/efetch.fcgi')
    yield state
    if state.wait:
        state.wait.set()
    server.shutdown()
    server.server_close()
    thread.join()


@pytest.fixture
def searched(ctx, pubmed_site, detail_site):
    result = QueryPublicApi().run(ctx, **ARGS)
    assert not result.is_error, result.content
    return ctx


def detail(context, **options):
    result = QueryPublicApi().run(context, **{**DETAIL, **options})
    assert not result.is_error, result.content
    row = rows(context)[-1]
    return row, json.loads(row.excerpt)


def test_abstract_headings_author_mapping_and_retained_reread(searched, detail_site):
    row, data = detail(searched)
    assert data['text'] == 'OBJECTIVE: Study ovarian cancer.\n\nRESULTS: Reported outcome: H2O & results.'
    assert data['abstract_status'] == 'available' and data['full_text_retrieved'] is False
    assert row.location['format'] == 'api_record' and row.location['selected_from_source_id'] == rows(searched)[0].id
    assert detail_site.requests == [{'db': 'pubmed', 'id': '103', 'retmode': 'xml', 'tool': 'phlox'}]
    assert 'xml' in detail_site.headers[0]['Accept']
    assert all('Cookie' not in h and 'Authorization' not in h for h in detail_site.headers)
    row, data = detail(searched, record_from='S2', section='authors', affiliation='fred hutch')
    assert len(data['authors']) == 1 and data['authors'][0]['name'] == 'Alice Example'
    assert len(data['authors'][0]['affiliations']) == 2
    assert data['returned_author_count'] == 3 and data['authors_without_affiliations'] == 1
    assert data['unassigned_affiliations'] == ['Legacy affiliation without author mapping.']
    assert data['author_list_complete'] == 'Y'
    reread = ReadWebSource().run(searched, label='S2')
    assert 'OBJECTIVE' in reread.content and 'API record detail' in reread.content
    assert len(detail_site.requests) == 2
    conv = searched.db.get(Conversation, searched.conversation_id)
    assert not sources.inspect_source(searched.db, conv, row.id)['changed']


def test_long_abstract_and_author_paging_preserve_version_and_offsets(searched, detail_site):
    detail_site.xml = lambda i: XML.format(id=i).replace('Study ovarian cancer.', 'x' * 5200).encode()
    first, data = detail(searched, max_chars=3000)
    assert data['selection']['next_start'] == 3000
    _, second = detail(searched, record_from='S2', start=3000, max_chars=3000)
    assert len(data['text'] + second['text']) == data['selection']['total']
    assert second['selection']['next_start'] is None and first.location['record_hash'] == second['record_hash']
    _, authors = detail(searched, record_from='S2', section='authors', limit=2)
    assert authors['selection']['next_start'] == 2
    _, last = detail(searched, record_from='S4', section='authors', start=2, limit=2)
    assert last['authors'][0]['collective'] and last['authors'][0]['position'] == 3
    assert last['selection']['next_start'] is None
    detail_site.xml = lambda i: XML.format(id=i).replace('Study ovarian cancer.', 'Changed abstract.').encode()
    result = QueryPublicApi().run(searched, **{**DETAIL, 'record_from': 'S2', 'start': 3000})
    assert result.is_error  # Invalid offset or changed version; no new source.
    result = QueryPublicApi().run(searched, **{**DETAIL, 'record_from': 'S2'})
    assert result.is_error and 'changed' in result.content
    assert len(rows(searched)) == 5


def test_missing_abstract_and_unmatched_affiliations_are_explicit(searched, detail_site):
    detail_site.xml = lambda i: XML.format(id=i).replace('<Abstract>', '<Ignored>').replace('</Abstract>', '</Ignored>').encode()
    _, data = detail(searched)
    assert data['abstract_status'] == 'missing' and data['text'] == ''
    _, data = detail(searched, section='authors', affiliation='Not Present')
    assert data['authors'] == [] and data['selection']['total'] == 0
    assert data['authors_without_affiliations'] == 1  # No match does not establish absence.


@pytest.mark.parametrize('arguments', [
    {**DETAIL, 'query': 'asthma'}, {**DETAIL, 'org_names': ['Example']},
    {**DETAIL, 'continue_from': 'S1'}, {**DETAIL, 'section': 'full_text'},
    {**DETAIL, 'section': 'authors', 'max_chars': 1000}, {**DETAIL, 'affiliation': 'Hutch'},
    {**DETAIL, 'record_id': '999'}, {**DETAIL, 'record_id': 'http://localhost/'},
    {**DETAIL, 'adapter': 'nih_projects'}, {**DETAIL, 'start': -1},
])
def test_invalid_or_unselected_detail_requests_do_not_dispatch(searched, detail_site, arguments):
    assert QueryPublicApi().run(searched, **arguments).is_error
    assert not detail_site.requests and len(rows(searched)) == 1


@pytest.mark.parametrize('change', ['owner', 'attempt', 'expired', 'removed', 'scope', 'hash', 'capacity'])
def test_source_authorization_before_detail_request(searched, detail_site, monkeypatch, change):
    row = rows(searched)[0]
    if change == 'owner':
        searched.user_id = 'other'
    elif change == 'attempt':
        searched.accounting.turn_id = 'other-attempt'
    elif change == 'expired':
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif change == 'removed':
        sources.forget_web(searched.db, searched.db.get(Conversation, searched.conversation_id), row.id)
    elif change == 'scope':
        searched.research.state['options']['domains'] = ['example.org']
    elif change == 'hash':
        row.excerpt += ' '
    else:
        monkeypatch.setattr(sources, 'remaining_capacity', lambda *a: 0)
    searched.db.commit()
    assert QueryPublicApi().run(searched, **DETAIL).is_error
    assert not detail_site.requests


@pytest.mark.parametrize('xml', [
    b'<broken', b'<PubmedArticleSet/>',
    XML.format(id='999').encode(),
    XML.format(id='103').replace('<PubmedArticle>', '<PubmedBookArticle>').replace('</PubmedArticle>', '</PubmedBookArticle>').encode(),
    b'<!DOCTYPE PubmedArticleSet [<!ENTITY x "abc">]><PubmedArticleSet>&x;</PubmedArticleSet>',
    b'<!DOCTYPE PubmedArticleSet [<!ENTITY x SYSTEM "file:///etc/passwd">]><PubmedArticleSet>&x;</PubmedArticleSet>',
    b'<PubmedArticleSet>' + b'<x>' * 70 + b'</x>' * 70 + b'</PubmedArticleSet>',
    b'x' * (web_fetch.MAX_BYTES + 1),
], ids=['malformed', 'empty', 'wrong-id', 'book', 'entity', 'external-entity', 'depth', 'oversized'])
def test_malformed_mismatched_unsafe_xml_never_becomes_evidence(searched, detail_site, xml):
    detail_site.xml = lambda i: xml
    assert QueryPublicApi().run(searched, **DETAIL).is_error
    assert len(rows(searched)) == 1


@pytest.mark.parametrize('status,kind', [(302, 'text/xml'), (403, 'text/xml'), (429, 'text/xml'), (200, 'text/html'), (200, 'application/json')])
def test_transport_errors_retry_only_transient_status(searched, detail_site, status, kind):
    detail_site.status, detail_site.kind = status, kind
    assert QueryPublicApi().run(searched, **DETAIL).is_error
    assert len(detail_site.requests) == (3 if status == 429 else 1) and len(rows(searched)) == 1


def test_revocation_during_fetch_prevents_capture(searched, detail_site, monkeypatch):
    original = web_fetch.read_api_query
    def revoke(*args, **kwargs):
        result = original(*args, **kwargs)
        row = rows(searched)[0]
        sources.forget_web(searched.db, searched.db.get(Conversation, searched.conversation_id), row.id)
        return result
    monkeypatch.setattr(web_fetch, 'read_api_query', revoke)
    assert QueryPublicApi().run(searched, **DETAIL).is_error
    assert len(rows(searched)) == 1


def test_stop_during_xml_response(searched, detail_site):
    searched.cancel_event = threading.Event()
    detail_site.wait = threading.Event()
    timer = threading.Timer(0.15, searched.cancel_event.set)
    timer.start()
    start = time.monotonic()
    try:
        assert 'stopped' in QueryPublicApi().run(searched, **DETAIL).content
        assert time.monotonic() - start < 2 and len(rows(searched)) == 1
    finally:
        timer.join()


def test_export_preserves_detail_selections_and_rejects_revoked_or_conflicting_versions(searched, detail_site):
    detail(searched)
    detail(searched, section='authors', affiliation='Fred Hutch')
    result = ExportApiDataset().run(searched, labels=['S1'], detail_labels=['S2', 'S3'])
    assert not result.is_error, result.content
    assert len(result.artifacts) == 5
    files = {a['name']: json.loads(resolve_in_workspace(searched.conversation_id, a['path']).read_text())
             for a in result.artifacts if a['name'].endswith('.json')}
    assert len(files['record_details.json']) == 2
    assert files['record_details.json'][1]['affiliation_filter'] == 'Fred Hutch'
    assert files['manifest.json']['record_detail_sources'][0]['record_id'] == '103'
    assert files['manifest.json']['coverage']['captured_unique_records'] == 2
    detail_site.xml = lambda i: XML.format(id=i).replace('Study ovarian cancer.', 'New version.').encode()
    detail(searched)  # Start explicitly from original query; fresh version is independently cited.
    assert ExportApiDataset().run(searched, labels=['S1'], detail_labels=['S2', 'S4']).is_error
    row = rows(searched)[1]
    sources.forget_web(searched.db, searched.db.get(Conversation, searched.conversation_id), row.id)
    assert ExportApiDataset().run(searched, labels=['S1'], detail_labels=['S2']).is_error
    assert len(list(workspace_dir(searched.conversation_id).glob('api-dataset-*'))) == 1


def test_approval_resume_rechecks_selected_query_source(db, searched, detail_site, monkeypatch):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY
    provider = ResearchProvider([[ToolCall('detail', 'query_public_api', DETAIL)]])
    agent, conv = session(db, provider)
    assert not QueryPublicApi().run(agent.ctx, **ARGS).is_error
    agent.gate.auto_approve = False
    monkeypatch.setattr(agent.gate, 'decide', lambda name: 'ask')
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Read the abstract'}]))
    assert any(e['type'] == 'approval_request' for e in events)
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    row = db.query(Source).filter_by(conversation_id=conv.id).one()
    sources.forget_web(db, conv, row.id)
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                           {'max_tool_rounds': 12}, 'test', provider.model)
    parse(resumed.resume(pending.state, {'detail': 'allow'}))
    assert not detail_site.requests


@pytest.mark.parametrize('durable', [False, True])
def test_search_detail_export_and_saved_citations_in_one_research_turn(db, client, monkeypatch, pubmed_site, detail_site, durable):
    from app import runs
    provider = ResearchProvider([
        [ToolCall('query', 'query_public_api', ARGS)],
        [ToolCall('abstract', 'query_public_api', DETAIL)],
        [ToolCall('authors', 'query_public_api', {**DETAIL, 'record_from': 'S2', 'section': 'authors', 'affiliation': 'Fred Hutch'})],
        [ToolCall('export', 'export_api_dataset', {'labels': ['S1'], 'detail_labels': ['S2', 'S3']})],
    ])
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='Article evidence', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Find publications, read abstracts and affiliations, export evidence',
               'auto_approve': True, 'research': {'scope': 'web', 'depth': 'standard', 'domains': ['127.0.0.1']}}
    if durable:
        result = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        worker.step()
        assert 'record_details.json' in client.get('/api/runs/' + result.json()['id'] + '/events').text
    else:
        assert client.post('/api/chat', json=payload).status_code == 200
    report = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert report['usage']['research']['reads'] == 3
    assert len(report['artifacts']) == 5 and len(detail_site.requests) == 2
    detail_file = next(a for a in report['artifacts'] if a['name'] == 'record_details.json')
    assert len(client.get(detail_file['url']).json()) == 2
    assert client.get(detail_file['url'].replace(conv.id, 'foreign')).status_code == 404
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 3
