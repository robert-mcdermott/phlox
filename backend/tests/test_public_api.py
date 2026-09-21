"""Controlled POST reads: real HTTP, parser isolation, evidence and harness policy."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from app import public_api, sources, web_fetch
from app.agent.tools.public_api import QueryPublicApi
from app.agent.tools.web import ReadWebSource
from app.models import Conversation, Message, PendingApproval, Source
from app.providers.base import ToolCall
from test_research import ResearchProvider, parse, session
from test_web_formats import ctx as format_context

ctx = format_context

ARGS = {'org_names': ['Example'], 'fiscal_years': [2024], 'limit': 2}


@pytest.fixture
def site(monkeypatch):
    state = SimpleNamespace(requests=[], headers=[], statuses=[], retry_after=None, status=200, kind='application/json', raw=None,
                            mutate=lambda value: None, wait=None)
    records = [{'appl_id': i + 1, 'subproject_id': None, 'fiscal_year': 2024,
                'organization': {'org_name': 'EXAMPLE UNIVERSITY', 'org_ipf_code': '123', 'primary_uei': 'ABC'},
                'project_num': f'R01-{i}', 'project_title': f'Project {i}', 'award_amount': 100 + i,
                'agency_ic_admin': {'code': 'CA'}, 'agency_ic_fundings': [{'fy': 2024, 'total_cost': 100 + i}]}
               for i in range(6)]
    state.records = records

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            state.requests.append(request)
            state.headers.append(dict(self.headers))
            if state.wait:
                state.wait.wait(3)
            start, count = request['offset'], request['limit']
            value = {'meta': {'total': len(records), 'offset': start, 'limit': count},
                     'results': deepcopy(records[start:start + count])}
            state.mutate(value)
            body = state.raw if state.raw is not None else json.dumps(value).encode()
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
    state.url = f'http://127.0.0.1:{server.server_port}/v2/projects/search'
    monkeypatch.setattr(public_api, 'ENDPOINT', state.url)
    monkeypatch.setattr(public_api, 'MIN_INTERVAL', 0)
    monkeypatch.setattr(public_api, '_NEXT_REQUEST', 0)
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {'allowlist_hosts': ['127.0.0.1']})
    yield state
    if state.wait:
        state.wait.set()
    server.shutdown()
    server.server_close()
    thread.join()


def rows(ctx):
    return ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).order_by(Source.number).all()


def test_pagination_reuses_saved_recipe_and_preserves_citable_provenance(ctx, site):
    tool = QueryPublicApi()
    assert not tool.run(ctx, **ARGS).is_error
    first = rows(ctx)[0]
    assert first.location['request']['criteria']['exclude_subprojects'] is True
    for label in ('S1', 'S2'):
        result = tool.run(ctx, continue_from=label)
        assert not result.is_error, result.content
    assert [r['offset'] for r in site.requests] == [0, 2, 4]
    assert all(r['criteria'] == site.requests[0]['criteria'] for r in site.requests)
    assert all('Authorization' not in h and 'Cookie' not in h for h in site.headers)
    assert site.headers[0]['User-Agent'] == web_fetch.USER_AGENT
    assert rows(ctx)[-1].location['next_offset'] is None
    assert 'End of this API query' in result.content
    assert tool.run(ctx, continue_from='S3').is_error and len(site.requests) == 3
    conv = ctx.db.get(Conversation, ctx.conversation_id)
    assert not sources.inspect_source(ctx.db, conv, first.id)['changed']
    reread = ReadWebSource().run(ctx, label='S1')
    assert 'Request:' in reread.content and 'fiscal_years' in reread.content and len(site.requests) == 3
    ctx.db.add(Message(conversation_id=conv.id, role='assistant', content='Finding [S1]',
                       citations=[{'label': 'S1', 'source_id': first.id}]))
    ctx.db.commit()
    assert 'fiscal_years' in sources.export_markdown(ctx.db, conv)


@pytest.mark.parametrize('arguments', [
    {}, {**ARGS, 'url': 'https://other.test'}, {**ARGS, 'criteria': {'anything': 1}},
    {**ARGS, 'fiscal_years': []}, {**ARGS, 'org_names': ['  ']}, {**ARGS, 'org_names': ['*']},
    {**ARGS, 'limit': 21}, {**ARGS, 'continue_from': 'S1'}, {**ARGS, 'adapter': 'arbitrary'},
])
def test_unsupported_queries_never_dispatch(ctx, site, arguments):
    assert QueryPublicApi().run(ctx, **arguments).is_error
    assert not site.requests and not rows(ctx)


@pytest.mark.parametrize('change', [
    lambda v: v['results'][0].update(fiscal_year=2023),
    lambda v: v['results'][0]['organization'].update(org_name='OTHER'),
    lambda v: v['results'][0].update(subproject_id=12),
    lambda v: v['results'][0].update(award_amount='100'),
    lambda v: v['results'][0].update(appl_id=True),
    lambda v: v['results'][0].update(fiscal_year='2024'),
    lambda v: v['results'][1].update(appl_id=1),
    lambda v: v['meta'].update(offset=1),
    lambda v: v['meta'].update(limit=500),
    lambda v: v['results'].pop(),
])
def test_http_success_cannot_capture_unvalidated_data_or_cursor(ctx, site, change):
    site.mutate = change
    result = QueryPublicApi().run(ctx, **ARGS)
    assert result.is_error and 'No evidence' in result.content
    assert not rows(ctx)


def test_repeated_pages_and_total_drift_block_continuation(ctx, site):
    tool = QueryPublicApi()
    assert not tool.run(ctx, **ARGS).is_error
    site.mutate = lambda v: v['results'].__setitem__(0, deepcopy(site.records[0]))
    assert 'Duplicate' in tool.run(ctx, continue_from='S1').content
    site.mutate = lambda v: v['meta'].update(total=7)
    assert 'total changed' in tool.run(ctx, continue_from='S1').content
    assert len(rows(ctx)) == 1


def test_empty_and_null_results_are_explicit(ctx, site):
    site.records[0]['award_amount'] = None
    result = QueryPublicApi().run(ctx, **ARGS)
    assert not result.is_error and 'null award amounts are unknown' in result.content
    assert json.loads(rows(ctx)[0].excerpt)['results'][0]['award_amount'] is None
    site.records.clear()
    result = QueryPublicApi().run(ctx, **ARGS)
    assert not result.is_error and 'End of this API query' in result.content
    assert json.loads(rows(ctx)[-1].excerpt)['results'] == []


def test_numeric_spelling_preserved(ctx, site):
    site.raw = json.dumps({'meta': {'total': 1, 'offset': 0, 'limit': 1},
                           'results': [site.records[0]]}).encode().replace(
                               b'"award_amount": 100', b'"award_amount": 9007199254740993.0123400')
    result = QueryPublicApi().run(ctx, **{**ARGS, 'limit': 1})
    assert not result.is_error and '9007199254740993.0123400' in rows(ctx)[0].excerpt


@pytest.mark.parametrize('status', [301, 302, 303, 307, 308, 401, 403, 429, 500])
def test_http_errors_retry_only_transient_failures_without_capture(ctx, site, status):
    site.status = status
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert len(site.requests) == (3 if status in (429, 500) else 1) and not rows(ctx)


@pytest.mark.parametrize('body', [b'{broken', b'{"meta":1,"meta":2}', b'{"value":NaN}',
                                b'[' * 70 + b'0' + b']' * 70, b'x' * (web_fetch.MAX_BYTES + 1)])
def test_malformed_or_oversized_body(ctx, site, body):
    site.raw = body
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert not rows(ctx)


def test_large_records_require_smaller_query_without_partial_evidence(ctx, site):
    site.records[0]['project_title'] = 'x' * 6000
    result = QueryPublicApi().run(ctx, **ARGS)
    assert result.is_error and 'smaller limit' in result.content and not rows(ctx)


def test_scope_capacity_and_dns_protection_prevent_dispatch(ctx, site, monkeypatch):
    ctx.research.state['options']['domains'] = ['example.org']
    assert QueryPublicApi().run(ctx, **ARGS).is_error and not site.requests
    ctx.research.state['options']['domains'] = []
    ctx.research.state['options']['scope'] = 'documents'
    assert QueryPublicApi().run(ctx, **ARGS).is_error and not site.requests
    ctx.research = None
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {})
    assert QueryPublicApi().run(ctx, **ARGS).is_error and not site.requests
    monkeypatch.setattr(sources, 'remaining_capacity', lambda *a: 0)
    assert QueryPublicApi().run(ctx, **ARGS).is_error and not site.requests


def test_continuation_requires_owner_attempt_and_available_snapshot(ctx, site):
    tool = QueryPublicApi()
    tool.run(ctx, **ARGS)
    ctx.user_id = 'different-owner'
    assert tool.run(ctx, continue_from='S1').is_error
    ctx.user_id = 'local'
    ctx.accounting.turn_id = 'another-attempt'
    assert tool.run(ctx, continue_from='S1').is_error
    ctx.research = None
    first = rows(ctx)[0]
    first.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    ctx.db.commit()
    assert tool.run(ctx, continue_from='S1').is_error
    assert len(site.requests) == 1


def test_stop_during_response_and_rate_wait(ctx, site, monkeypatch):
    ctx.cancel_event = threading.Event()
    site.wait = threading.Event()
    timer = threading.Timer(0.15, ctx.cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        assert 'stopped' in QueryPublicApi().run(ctx, **ARGS).content
        assert time.monotonic() - started < 2 and not rows(ctx)
    finally:
        timer.join()
    ctx.cancel_event.clear()
    monkeypatch.setattr(public_api, '_NEXT_REQUEST', time.monotonic() + 30)
    timer = threading.Timer(0.1, ctx.cancel_event.set)
    timer.start()
    try:
        assert 'stopped' in QueryPublicApi().run(ctx, **ARGS).content
        assert len(site.requests) == 1
    finally:
        timer.join()


def test_scripted_research_queries_and_continues_under_read_allowance(db, site):
    provider = ResearchProvider([
        [ToolCall('first', 'query_public_api', ARGS)],
        [ToolCall('second', 'query_public_api', {'continue_from': 'S1'})],
    ])
    agent, conv = session(db, provider)
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Find project data'}]))
    assert len(site.requests) == 2 and agent.research.state['reads'] == 2
    assert any(e['type'] == 'sources' for e in events)
    assert 'query_public_api' in provider.seen[1]['tools'] and not provider.seen[-1]['tools']
    assert 'Request:' in json.dumps(provider.seen[-1]['messages'])


def test_approval_resume_preserves_cursor_and_read_count(db, site, monkeypatch):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY

    provider = ResearchProvider([[ToolCall('first', 'query_public_api', ARGS)],
                                 [ToolCall('second', 'query_public_api', {'continue_from': 'S1'})]])
    agent, conv = session(db, provider)
    agent.gate.auto_approve = False
    monkeypatch.setattr(agent.gate, 'decide', lambda name: 'ask')
    parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Find projects'}]))
    assert not site.requests
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                           {'max_tool_rounds': 12}, 'test', provider.model)
    parse(resumed.resume(pending.state, {'first': 'allow'}))
    assert [r['offset'] for r in site.requests] == [0, 2]
    assert resumed.research.state['reads'] == 2


@pytest.mark.parametrize('durable', [False, True])
def test_chat_and_durable_replay_keep_api_recipe_and_notebook(db, client, monkeypatch, site, durable):
    from app import runs

    provider = ResearchProvider([
        [ToolCall('first', 'query_public_api', ARGS)],
        [ToolCall('second', 'query_public_api', {'continue_from': 'S1'})],
        [ToolCall('notes', 'update_research_notebook', {
            'findings': [{'text': 'The queried records have unknown completeness beyond these pages.', 'sources': ['S1', 'S2']}],
            'disagreements': [], 'questions': []})],
    ])
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='API research', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Find projects',
               'research': {'scope': 'web', 'depth': 'standard', 'domains': []}}
    if durable:
        result = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        worker.step()
        assert 'nih_projects' in client.get('/api/runs/' + result.json()['id'] + '/events').text
    else:
        assert client.post('/api/chat', json=payload).status_code == 200
    report = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert report['usage']['research']['reads'] == 2
    assert report['usage']['research']['notebook']['context']['restored_sources'] == ['S1', 'S2']
    assert report['citations'][0]['source_id']
    assert 'fiscal_years' in client.get(f'/api/conversations/{conv.id}/export').json()['markdown']
    assert len(site.requests) == 2
