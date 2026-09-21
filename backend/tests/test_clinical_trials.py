"""ClinicalTrials.gov cursor and record evidence over the shared transport/export seams."""
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

from app import public_api, sources, web_fetch
from app.agent.tools.api_dataset import ExportApiDataset
from app.agent.tools.public_api import QueryPublicApi
from app.agent.tools.web import ReadWebSource
from app.agent.validation import argument_error
from app.models import Conversation, Source
from app.providers.base import ToolCall
from app.workspace.manager import resolve_in_workspace
from test_api_dataset import exported
from test_public_api import rows
from test_research import ResearchProvider
from test_web_formats import ctx as format_context

ctx = format_context
ARGS = {'adapter': 'clinical_trials', 'condition': 'ovarian cancer', 'query': 'Fred Hutch',
        'statuses': ['RECRUITING'], 'location': 'Seattle', 'limit': 2}
DETAIL = {'record_from': 'S1', 'record_id': 'NCT00000001'}


def study(index):
    return {'protocolSection': {
        'identificationModule': {'nctId': f'NCT{index:08}', 'briefTitle': f'Study {index}'},
        'statusModule': {'overallStatus': 'RECRUITING', 'lastUpdatePostDateStruct': {'date': '2026-09-01'}},
        'sponsorCollaboratorsModule': {'leadSponsor': {'name': 'Another University'},
                                     'collaborators': [{'name': 'Fred Hutchinson Cancer Center'}]},
        'designModule': {'phases': ['PHASE2']},
        'eligibilityModule': {'sex': 'FEMALE', 'minimumAge': '18 Years', 'eligibilityCriteria': 'Inclusion: ovarian cancer.\nExclusion: example.'},
        'armsInterventionsModule': {'interventions': [{'type': 'DRUG', 'name': 'Example'}]},
        'contactsLocationsModule': {'locations': [{'facility': 'Fred Hutchinson Cancer Center',
                                                  'city': 'Seattle', 'status': 'ACTIVE_NOT_RECRUITING'}]},
    }, 'hasResults': False}


@pytest.fixture
def trial_site(monkeypatch):
    state = SimpleNamespace(requests=[], headers=[], status=200, kind='application/json', raw=None,
                            studies=[study(i) for i in range(1, 5)], mutate=lambda v: None, wait=None)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            path = urlsplit(self.path)
            params = {k: v[0] for k, v in parse_qs(path.query).items()}
            state.requests.append((path.path, params))
            state.headers.append(dict(self.headers))
            if state.wait:
                state.wait.wait(3)
            if path.path == '/studies':
                offset = int(params.get('pageToken', 'cursor:0').split(':')[1])
                limit = int(params['pageSize'])
                value = {'totalCount': len(state.studies), 'studies': deepcopy(state.studies[offset:offset + limit])}
                if 'pageToken' in params:
                    value.pop('totalCount')  # Official v2 API ignores countTotal on later pages.
                if offset + limit < len(state.studies):
                    value['nextPageToken'] = f'cursor:{offset + limit}'
            else:
                value = deepcopy(next(s for s in state.studies if s['protocolSection']['identificationModule']['nctId'] == path.path.split('/')[-1]))
            state.mutate(value)
            body = state.raw if state.raw is not None else json.dumps(value).encode()
            self.send_response(state.status)
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
    monkeypatch.setattr(public_api, 'CLINICAL_TRIALS_ENDPOINT', f'http://127.0.0.1:{server.server_port}/studies')
    monkeypatch.setattr(public_api, 'CLINICAL_TRIALS_INTERVAL', 0)
    monkeypatch.setattr(public_api, '_CLINICAL_TRIALS_NEXT_REQUEST', 0)
    monkeypatch.setattr(web_fetch, 'get_web_fetch_config', lambda: {'allowlist_hosts': ['127.0.0.1']})
    yield state
    if state.wait:
        state.wait.set()
    server.shutdown()
    server.server_close()
    thread.join()


def query(context, **arguments):
    result = QueryPublicApi().run(context, **arguments)
    assert not result.is_error, result.content
    row = rows(context)[-1]
    return row, json.loads(row.excerpt)


@pytest.fixture
def searched(ctx, trial_site):
    query(ctx, **ARGS)
    return ctx


def test_two_pages_cursor_queries_export_and_no_nih_assumptions(searched, trial_site):
    query(searched, continue_from='S1')
    assert trial_site.requests[0][1]['query.cond'] == 'ovarian cancer'
    assert trial_site.requests[0][1]['filter.overallStatus'] == 'RECRUITING'
    assert trial_site.requests[0][1]['query.locn'] == 'Seattle'
    assert trial_site.requests[1][1]['pageToken'] == 'cursor:2'
    assert all(p['countTotal'] == 'true' for _, p in trial_site.requests)
    assert all('Authorization' not in h and 'Cookie' not in h for h in trial_site.headers)
    _, files, manifest = exported(searched, ['S1', 'S2'])
    assert manifest['coverage']['all_reported_records_captured']
    assert [r['nct_id'] for r in json.loads(files['records.json'])] == [f'NCT{i:08}' for i in range(1, 5)]
    assert 'page_token' not in manifest['query'] and manifest['sources'][1]['request']['page_token'] == 'cursor:2'
    assert 'award' not in files['summary.csv'] and 'has_results' in files['records.csv']
    assert 'clinical_trials' in ReadWebSource().run(searched, label='S1').content
    assert QueryPublicApi().run(searched, continue_from='S2').is_error
    assert len(trial_site.requests) == 2


def test_detail_sections_status_missing_results_and_export(searched, trial_site):
    row, overview = query(searched, **DETAIL)
    assert overview['section'] == 'overview' and 'Another University' in overview['text']
    assert 'Fred Hutchinson Cancer Center' in overview['text']
    _, sites = query(searched, **{**DETAIL, 'record_from': 'S2', 'section': 'locations'})
    assert sites['overall_status'] == 'RECRUITING' and 'ACTIVE_NOT_RECRUITING' in sites['text']
    assert sites['has_results'] is False and sites['last_update_posted'] == '2026-09-01'
    _, results = query(searched, **{**DETAIL, 'section': 'results'})
    assert results['section_status'] == 'missing' and results['has_results'] is False and results['text'] == ''
    assert row.url.endswith('/studies/NCT00000001')
    _, eligibility = query(searched, **{**DETAIL, 'section': 'eligibility'})
    assert '18 Years' in eligibility['text']
    result = ExportApiDataset().run(searched, labels=['S1'], detail_labels=['S2', 'S3', 'S4', 'S5'])
    assert not result.is_error, result.content
    files = {a['name']: json.loads(resolve_in_workspace(searched.conversation_id, a['path']).read_text())
             for a in result.artifacts if a['name'].endswith('.json')}
    assert len(files['record_details.json']) == 4
    assert files['manifest.json']['coverage']['all_reported_records_captured'] is False
    assert 'registry' in files['manifest.json']['record_detail_notice']
    assert files['manifest.json']['record_detail_sources'][0]['url'] == row.url
    assert len(trial_site.requests) == 5


def test_detail_passages_versions_and_posted_results(searched, trial_site):
    trial_site.studies[0]['hasResults'] = True
    trial_site.studies[0]['resultsSection'] = {'outcomeMeasuresModule': {'outcomeMeasures': [{'title': 'Outcome',
        'description': 'Long context. ' * 150, 'units': 'participants', 'classes': [{'denoms': [{'value': '12'}]}]}]}}
    _, first = query(searched, **{**DETAIL, 'section': 'results', 'max_chars': 500})
    _, second = query(searched, **{**DETAIL, 'record_from': 'S2', 'section': 'results', 'start': 500, 'max_chars': 500})
    assert first['has_results'] is True and first['selection']['next_start'] == 500
    assert second['selection']['start'] == 500 and first['record_hash'] == second['record_hash']
    trial_site.studies[0]['protocolSection']['eligibilityModule']['minimumAge'] = '21 Years'
    assert 'changed' in QueryPublicApi().run(searched, **{**DETAIL, 'record_from': 'S2', 'section': 'eligibility'}).content
    query(searched, **{**DETAIL, 'section': 'eligibility'})
    assert ExportApiDataset().run(searched, labels=['S1'], detail_labels=['S2', 'S4']).is_error


@pytest.mark.parametrize('args', [
    {'adapter': 'clinical_trials'}, {**ARGS, 'condition': ' '}, {**ARGS, 'query': 'NCT00000001'},
    {**ARGS, 'statuses': ['BOGUS']}, {**ARGS, 'fiscal_years': [2024]}, {**ARGS, 'page_token': 'forged'},
    {**ARGS, 'section': 'results'}, {'continue_from': 'S1', 'condition': 'cancer'},
    {'adapter': 'pubmed', 'query': 'cancer', 'statuses': ['RECRUITING']},
])
def test_invalid_queries_do_not_dispatch(ctx, trial_site, args):
    assert QueryPublicApi().run(ctx, **args).is_error
    assert not trial_site.requests and not rows(ctx)


@pytest.mark.parametrize('args', [
    {**DETAIL, 'record_id': 'NCT99999999'}, {**DETAIL, 'section': 'authors'},
    {**DETAIL, 'section': 'abstract'}, {**DETAIL, 'adapter': 'pubmed'},
    {**DETAIL, 'section': 'locations', 'affiliation': 'Fred Hutch'},
    {**DETAIL, 'section': 'eligibility', 'limit': 2},
])
def test_invalid_details_do_not_dispatch(searched, trial_site, args):
    assert QueryPublicApi().run(searched, **args).is_error
    assert len(trial_site.requests) == 1


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(totalCount=1), lambda v: v.update(studies=[study(1), study(1)]),
    lambda v: v.update(nextPageToken=''),
    lambda v: v['studies'][0]['protocolSection']['statusModule'].update(overallStatus='COMPLETED'),
    lambda v: v['studies'][0].update(hasResults='false'),
    lambda v: v['studies'][0]['protocolSection']['identificationModule'].update(nctId='../private'),
    lambda v: v['studies'][0]['protocolSection']['identificationModule'].update(briefTitle='x' * 6000),
])
def test_invalid_responses_do_not_capture(ctx, trial_site, mutation):
    trial_site.mutate = mutation
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert not rows(ctx)


def test_empty_pages_missing_fields_and_total_drift(ctx, trial_site):
    trial_site.mutate = lambda v: v.update(studies=[])
    _, first = query(ctx, **ARGS)
    assert first['results'] == [] and rows(ctx)[0].location['next_offset'] == 0
    trial_site.mutate = lambda v: None
    query(ctx, continue_from='S1')  # Token, not offset, determines the wire page.
    _, _, manifest = exported(ctx, ['S1', 'S2'])
    assert manifest['coverage']['captured_unique_records'] == 2
    assert manifest['coverage']['all_reported_records_captured'] is False
    trial_site.studies = []
    row, empty = query(ctx, **ARGS)
    assert empty['results'] == [] and row.location['next_offset'] is None
    trial_site.studies = [study(1)]
    trial_site.studies[0].pop('hasResults')
    trial_site.studies[0]['protocolSection'].pop('designModule')
    _, page = query(ctx, **ARGS)
    assert page['results'][0]['has_results'] is None and page['results'][0]['phases'] is None


@pytest.mark.parametrize('mutation', [
    lambda v: v.update(totalCount=5),
    lambda v: v.update(nextPageToken='cursor:2'),
    lambda v: v.update(studies=[study(1), study(4)]),
])
def test_continuation_rejects_total_drift_cursor_loops_and_duplicates(searched, trial_site, mutation):
    trial_site.mutate = mutation
    assert QueryPublicApi().run(searched, continue_from='S1').is_error
    assert len(rows(searched)) == 1


def test_mismatched_record_response_and_inconsistent_token_provenance(searched, trial_site):
    trial_site.mutate = lambda v: v['protocolSection']['identificationModule'].update(nctId='NCT99999999')
    assert QueryPublicApi().run(searched, **DETAIL).is_error
    row = rows(searched)[0]
    row.location = {**row.location, 'next_page_token': 'forged'}
    searched.db.commit()
    assert QueryPublicApi().run(searched, continue_from='S1').is_error
    assert len(trial_site.requests) == 2 and len(rows(searched)) == 1


@pytest.mark.parametrize('status', [302, 403, 429, 503])
def test_transport_failure_no_retry_or_evidence(ctx, trial_site, status):
    trial_site.status = status
    assert QueryPublicApi().run(ctx, **ARGS).is_error
    assert len(trial_site.requests) == 1 and not rows(ctx)


@pytest.mark.parametrize('revocation', ['owner', 'expiry', 'removed', 'scope', 'attempt'])
def test_detail_reauthorizes_saved_source(searched, trial_site, revocation):
    row = rows(searched)[0]
    if revocation == 'owner':
        searched.user_id = 'foreign'
    elif revocation == 'expiry':
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        searched.db.commit()
    elif revocation == 'removed':
        sources.forget_web(searched.db, searched.db.get(Conversation, searched.conversation_id), row.id)
    else:
        searched.research = SimpleNamespace(state={'options': {'scope': 'documents' if revocation == 'scope' else 'web'}}, url_allowed=lambda url: True)
        if revocation == 'attempt':
            searched.accounting.turn_id = 'other-attempt'
    assert QueryPublicApi().run(searched, **DETAIL).is_error
    assert len(trial_site.requests) == 1


def test_stop_while_waiting_for_record_response(searched, trial_site):
    searched.cancel_event = threading.Event()
    trial_site.wait = threading.Event()
    timer = threading.Timer(0.15, searched.cancel_event.set)
    timer.start()
    started = time.monotonic()
    try:
        assert 'stopped' in QueryPublicApi().run(searched, **DETAIL).content
        assert time.monotonic() - started < 2 and len(rows(searched)) == 1
    finally:
        timer.join()


@pytest.mark.parametrize('durable', [False, True])
def test_search_inspect_export_in_one_research_turn(db, client, monkeypatch, trial_site, durable):
    from app import runs
    provider = ResearchProvider([
        [ToolCall('query', 'query_public_api', ARGS)],
        [ToolCall('overview', 'query_public_api', DETAIL)],
        [ToolCall('sites', 'query_public_api', {**DETAIL, 'record_from': 'S2', 'section': 'locations'})],
        [ToolCall('export', 'export_api_dataset', {'labels': ['S1'], 'detail_labels': ['S2', 'S3']})],
    ])
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='Study evidence', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Find trials, inspect sponsors/sites, export evidence',
               'auto_approve': True, 'research': {'scope': 'web', 'depth': 'standard', 'domains': ['127.0.0.1']}}
    if durable:
        response = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert response.status_code == 200, response.text
        worker.step()
        assert 'record_details.json' in client.get('/api/runs/' + response.json()['id'] + '/events').text
    else:
        assert client.post('/api/chat', json=payload).status_code == 200
    report = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert report['usage']['research']['reads'] == 3
    assert len(report['artifacts']) == 5 and len(trial_site.requests) == 3
    detail_file = next(a for a in report['artifacts'] if a['name'] == 'record_details.json')
    assert len(client.get(detail_file['url']).json()) == 2
    assert db.query(Source).filter_by(conversation_id=conv.id).count() == 3


def test_trial_schema_remains_provider_compatible():
    tool = QueryPublicApi()
    assert not {'oneOf', 'allOf', 'anyOf'} & tool.advertised_parameters.keys()
    for args in [ARGS, DETAIL, {**DETAIL, 'section': 'results', 'max_chars': 500}]:
        assert argument_error(tool, args) is None
