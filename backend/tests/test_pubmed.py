"""PubMed validates search and summaries before shared citation, continuation and export."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import uuid

import pytest

from app import public_api, web_fetch
from app.agent.tools.api_dataset import ExportApiDataset
from app.agent.tools.public_api import QueryPublicApi
from app.agent.tools.web import ReadWebSource
from app.models import Conversation, Source
from app.providers.base import ToolCall
from app.web_extract_worker import ExtractionError, pubmed_search
from app.workspace.manager import workspace_dir
from test_api_dataset import capture, exported, record
from test_public_api import rows
from test_research import ResearchProvider
from test_web_formats import ctx as format_context

ctx = format_context
ARGS = {'adapter': 'pubmed', 'query': 'asthma[Title] AND 2024[pdat]', 'limit': 2}


@pytest.fixture
def pubmed_site(monkeypatch):
    state = SimpleNamespace(requests=[], headers=[], status=200, summary_status=200,
                            mutate_search=lambda v: None, mutate_summary=lambda v: None,
                            kind='application/json', raw=None, wait=None,
                            ids=['103', '101', '104', '102', '106', '105'])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            parts = urlsplit(self.path)
            params = {k: v[0] for k, v in parse_qs(parts.query).items()}
            state.requests.append((parts.path, params))
            state.headers.append(dict(self.headers))
            if state.wait:
                state.wait.wait(3)
            status = state.status
            if parts.path == '/esearch.fcgi':
                start, count = int(params['retstart']), int(params['retmax'])
                value = {'esearchresult': {'count': str(len(state.ids)), 'retstart': str(start),
                    'retmax': str(count), 'idlist': state.ids[start:start + count],
                    'querytranslation': '"asthma"[Title] AND 2024/01/01:2024/12/31[Date - Publication]'}}
                state.mutate_search(value)
            else:
                status = state.summary_status
                ids = params['id'].split(',')
                value = {'result': {'uids': ids, **{i: {'uid': i, 'title': f'Article {i}', 'pubdate': '2024 Sep',
                    'fulljournalname': 'Example Journal', 'authors': [{'name': 'Example A'}],
                    'articleids': [{'idtype': 'doi', 'value': '10.123/example.' + i}],
                    'volume': '12', 'issue': '2', 'pages': '1-5'} for i in ids}}}
                state.mutate_summary(value)
            body = state.raw if state.raw is not None else json.dumps(value).encode()
            self.send_response(status)
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
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(public_api, 'PUBMED_ENDPOINT', base + '/esearch.fcgi')
    monkeypatch.setattr(public_api, 'PUBMED_SUMMARY_ENDPOINT', base + '/esummary.fcgi')
    monkeypatch.setattr(public_api, 'PUBMED_INTERVAL', 0)
    monkeypatch.setattr(public_api, '_PUBMED_NEXT_REQUEST', 0)
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {'allowlist_hosts': ['127.0.0.1']})
    yield state
    if state.wait:
        state.wait.set()
    server.shutdown()
    server.server_close()
    thread.join()


def test_two_pages_and_shared_export_preserve_metadata_not_nih_assumptions(ctx, pubmed_site):
    tool = QueryPublicApi()
    result = tool.run(ctx, **ARGS)
    assert not result.is_error, result.content
    assert 'abstracts and full article text were not retrieved' in result.content
    result = tool.run(ctx, continue_from='S1')
    assert not result.is_error, result.content
    assert [p['retstart'] for path, p in pubmed_site.requests if path == '/esearch.fcgi'] == ['0', '2']
    assert all(p['db'] == 'pubmed' and p['tool'] == 'phlox' for _, p in pubmed_site.requests)
    assert all('Authorization' not in h and 'Cookie' not in h for h in pubmed_site.headers)
    assert '(GET)' in ReadWebSource().run(ctx, label='S1').content
    assert rows(ctx)[0].location['query_translation'].startswith('"asthma"')
    _, files, manifest = exported(ctx, ['S1', 'S2'])
    assert [r['pmid'] for r in json.loads(files['records.json'])] == ['103', '101', '104', '102']
    assert manifest['adapter'] == 'pubmed' and manifest['query']['query'] == ARGS['query']
    assert manifest['coverage']['missing_record_ranges'] == [[4, 6]]
    assert manifest['query_translation'] == rows(ctx)[0].location['query_translation']
    assert 'award' not in files['summary.csv'] and 'partial' in files['summary.csv']
    assert len(pubmed_site.requests) == 4  # No refetches during reread/export.
    exported(ctx, ['S1', 'S2'])  # New folder, same data.
    assert len(pubmed_site.requests) == 4


@pytest.mark.parametrize('args', [
    {'query': 'asthma'}, {'adapter': 'pubmed', 'query': '  '},
    {**ARGS, 'org_names': ['Example']}, {**ARGS, 'fiscal_years': [2024]},
    {**ARGS, 'url': 'https://example.org'}, {**ARGS, 'continue_from': 'S1'},
    {**ARGS, 'query': 'asthma\n'}, {**ARGS, 'limit': 21},
])
def test_invalid_arguments_never_dispatch(ctx, pubmed_site, args):
    assert QueryPublicApi().run(ctx, **args).is_error
    assert not pubmed_site.requests and not rows(ctx)


@pytest.mark.parametrize('change', [
    lambda v: v['esearchresult'].update(errorlist={'fieldnotfound': ['badfield']}),
    lambda v: v['esearchresult'].update(warninglist={'phrasesignored': ['bad term']}),
    lambda v: v['esearchresult'].update(warninglist={'outputmessages': ['Query was changed']}),
    lambda v: v['esearchresult'].update(idlist=['103', '103']),
    lambda v: v['esearchresult'].update(idlist=['103']),
    lambda v: v['esearchresult'].update(idlist=['103', 'not-an-id']),
    lambda v: v['esearchresult'].update(retstart='1'),
    lambda v: v['esearchresult'].update(retmax='500'),
    lambda v: v['esearchresult'].update(count='-1'),
    lambda v: v['esearchresult'].update(querytranslation=None),
])
def test_invalid_search_never_fetches_summaries_or_captures(ctx, pubmed_site, change):
    pubmed_site.mutate_search = change
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert len(pubmed_site.requests) == 1 and not rows(ctx)


@pytest.mark.parametrize('change', [
    lambda v: v['result'].update(uids=['101', '103']),
    lambda v: v['result'].pop('103'),
    lambda v: v['result']['103'].update(uid='999'),
    lambda v: v['result']['103'].update(error='Record not found'),
    lambda v: v['result']['103'].update(title=None),
    lambda v: v['result']['103'].update(authors=[{'name': 1}]),
    lambda v: v['result']['103'].update(articleids=[{'idtype': 'doi', 'value': 1}]),
    lambda v: v['result']['103'].update(title='a' * 6000),
])
def test_incomplete_or_invalid_summaries_never_capture_partial_page(ctx, pubmed_site, change):
    pubmed_site.mutate_summary = change
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert len(pubmed_site.requests) == 2 and not rows(ctx)


@pytest.mark.parametrize('change', [
    lambda v: v['esearchresult'].update(count='7'),
    lambda v: v['esearchresult'].update(querytranslation='different'),
    lambda v: v['esearchresult'].update(idlist=['103', '102']),
])
def test_continuation_rejects_drift_and_repeated_records(ctx, pubmed_site, change):
    tool = QueryPublicApi()
    assert not tool.run(ctx, **ARGS).is_error
    pubmed_site.mutate_search = change
    assert tool.run(ctx, continue_from='S1').is_error
    assert len(rows(ctx)) == 1 and len(pubmed_site.requests) == 3


def test_empty_result_and_api_window_are_explicit(ctx, pubmed_site):
    pubmed_site.ids.clear()
    pubmed_site.mutate_search = lambda v: v['esearchresult'].update(warninglist={'outputmessages': ['No items found.']})
    result = QueryPublicApi().run(ctx, **ARGS)
    assert not result.is_error, result.content
    assert len(pubmed_site.requests) == 1  # No pointless summary request.
    _, files, manifest = exported(ctx, ['S1'])
    assert json.loads(files['records.json']) == [] and manifest['coverage']['all_reported_records_captured']
    assert QueryPublicApi().run(ctx, continue_from='S1').is_error
    request = {**public_api.recipe(ARGS), 'offset': 9999}
    value = {'esearchresult': {'count': '12000', 'retstart': '9999', 'retmax': '1',
                              'idlist': ['101'], 'querytranslation': 'asthma'}}
    search = pubmed_search(json.dumps(value).encode(), request)
    from app.web_extract_worker import pubmed_summary
    page = pubmed_summary(json.dumps({'result': {'uids': ['101'], '101': {'uid': '101', 'title': 'Example',
                         'pubdate': '2024', 'authors': [], 'articleids': []}}}).encode(), request, search)
    assert page['window_exhausted'] and page['next_offset'] is None and page['end'] == 10000
    value['esearchresult']['idlist'].append('102')
    with pytest.raises(ExtractionError):
        pubmed_search(json.dumps(value).encode(), request)


@pytest.mark.parametrize('summary,status', [(False, 302), (False, 429), (False, 500), (True, 403), (True, 302)])
def test_http_failures_no_retries_or_partial_evidence(ctx, pubmed_site, summary, status):
    setattr(pubmed_site, 'summary_status' if summary else 'status', status)
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert not rows(ctx) and len(pubmed_site.requests) == (2 if summary else 1)


def test_ownership_scope_retention_and_cursor_integrity(ctx, pubmed_site):
    tool = QueryPublicApi()
    ctx.research.state['options']['domains'] = ['127.0.0.1']
    assert 'query_public_api' in ctx.research.allowed_tools()  # NIH host is excluded.
    assert not tool.run(ctx, **ARGS).is_error
    for change in ('adapter', 'scope', 'owner', 'attempt', 'expired', 'cursor'):
        row = rows(ctx)[0]
        ctx.user_id = 'local'
        original_turn = ctx.accounting.turn_id
        original_location = deepcopy(row.location)
        original_expiry = row.expires_at
        ctx.research.state['options']['domains'] = ['127.0.0.1']
        args = {'continue_from': 'S1'}
        if change == 'adapter':
            args['adapter'] = 'nih_projects'
        elif change == 'scope':
            ctx.research.state['options']['domains'] = ['api.reporter.nih.gov']
        elif change == 'owner':
            ctx.user_id = 'other'
        elif change == 'attempt':
            ctx.accounting.turn_id = 'other'
        elif change == 'expired':
            row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        else:
            row.location = {**row.location, 'next_offset': 999}
        ctx.db.commit()
        assert tool.run(ctx, **args).is_error, change
        assert len(pubmed_site.requests) == 2
        ctx.accounting.turn_id = original_turn
        row.location, row.expires_at = original_location, original_expiry
        ctx.db.commit()


def test_mixed_adapters_and_csv_formula_text(ctx, pubmed_site):
    pubmed_site.mutate_summary = lambda v: v['result']['103'].update(title='=EXAMPLE')
    assert not QueryPublicApi().run(ctx, **ARGS).is_error
    _, files, _ = exported(ctx, ['S1'])
    assert "'=EXAMPLE" in files['records.csv']
    assert json.loads(files['records.json'])[0]['title'] == '=EXAMPLE'
    nih = capture(ctx, [record(1)])
    result = ExportApiDataset().run(ctx, labels=['S1', nih])
    assert result.is_error and 'different queries' in result.content


def test_stop_interrupts_shared_rate_wait_and_slow_response(ctx, pubmed_site, monkeypatch):
    ctx.cancel_event = threading.Event()
    for waiting in (True, False):
        ctx.cancel_event.clear()
        monkeypatch.setattr(public_api, '_PUBMED_NEXT_REQUEST', time.monotonic() + 30 if waiting else 0)
        if not waiting:
            pubmed_site.wait = threading.Event()
        timer = threading.Timer(0.15, ctx.cancel_event.set)
        timer.start()
        started = time.monotonic()
        try:
            assert 'stopped' in QueryPublicApi().run(ctx, **ARGS).content
            assert time.monotonic() - started < 2 and not rows(ctx)
        finally:
            timer.join()


@pytest.mark.parametrize('durable', [False, True])
def test_research_pubmed_to_export_saved_files_and_citations(db, client, monkeypatch, pubmed_site, durable):
    from app import runs
    provider = ResearchProvider([
        [ToolCall('page1', 'query_public_api', ARGS)],
        [ToolCall('page2', 'query_public_api', {'continue_from': 'S1'})],
        [ToolCall('export', 'export_api_dataset', {'labels': ['S1', 'S2']})],
    ])
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='PubMed query and export', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Find publications and export files',
               'auto_approve': True, 'research': {'scope': 'web', 'depth': 'standard', 'domains': ['127.0.0.1']}}
    if durable:
        result = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        worker.step()
        assert 'manifest.json' in client.get('/api/runs/' + result.json()['id'] + '/events').text
    else:
        assert client.post('/api/chat', json=payload).status_code == 200
    report = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert report['usage']['research']['reads'] == 2
    assert len(report['artifacts']) == 4 and len(pubmed_site.requests) == 4
    for artifact in report['artifacts']:
        response = client.get(artifact['url'])
        assert response.status_code == 200
        if artifact['name'] == 'manifest.json':
            assert response.json()['coverage']['captured_unique_records'] == 4
            assert response.json()['adapter'] == 'pubmed'
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 2
    assert len(list(workspace_dir(conv.id).glob('api-dataset-*'))) == 1
