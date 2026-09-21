"""Batched reads preserve evidence, limits, permission gates and compact model context."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace
import uuid

import pytest

from app import api_collection, api_dataset, public_api, public_api_transport, sources
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.api_collection import CollectApiDataset
from app.agent.tools.public_api import QueryPublicApi
from app.agent.tools.subagent import READ_ONLY_TOOLS
from app.models import Conversation, Message, PendingApproval, ToolPref
from app.providers.base import ToolCall
from app.workspace.manager import resolve_in_workspace, workspace_dir
from test_clinical_trials import ARGS as TRIALS, trial_site as trial_server
from test_public_api import ARGS as NIH, site as nih_server, rows
from test_pubmed import ARGS as PUBMED, pubmed_site as pubmed_server
from test_research import ResearchProvider, parse, session
from test_web_formats import ctx as format_context

ctx = format_context
site = nih_server
pubmed_site = pubmed_server
trial_site = trial_server


@pytest.fixture(autouse=True)
def fast_retries(monkeypatch):
    monkeypatch.setattr(public_api_transport, 'BACKOFF_SECONDS', 0.001)
    monkeypatch.setattr(public_api_transport, 'JITTER_SECONDS', 0)


def collect(ctx, **kw):
    result = CollectApiDataset().run(ctx, **{'labels': ['S1'], **kw})
    assert not result.is_error, result.content
    files = {a['name']: resolve_in_workspace(ctx.conversation_id, a['path']).read_text() for a in result.artifacts}
    return result, json.loads(files['manifest.json']), json.loads(files['records.json'])


@pytest.mark.parametrize('fixture,arguments,total,http', [('site', NIH, 6, 3),
    ('pubmed_site', PUBMED, 6, 6), ('trial_site', TRIALS, 4, 2)])
def test_collect_all_adapters_without_raw_records_in_result(ctx, request, fixture, arguments, total, http):
    server = request.getfixturevalue(fixture)
    assert not QueryPublicApi().run(ctx, **arguments).is_error
    progress = []
    ctx.progress = progress.append
    result, manifest, records = collect(ctx)
    assert len(records) == total and len(server.requests) == http
    assert manifest['coverage']['all_reported_records_captured']
    assert manifest['collection']['complete'] and manifest['collection']['stop_reason'] == 'api_end'
    assert ctx.research.state['reads'] == total // 2 - 1
    assert 'project_title' not in result.content and 'brief_title' not in result.content and 'Article 103' not in result.content
    assert len(result.content) < 3000 and len(result.artifacts) == 4
    assert len(progress) == total // 2 and 'Continuation labels:' in progress[-1]
    assert len(rows(ctx)) == total // 2
    assert len(manifest['sources']) == total // 2


def test_page_limit_then_explicit_continuation_never_refetches(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    _, first, _ = collect(ctx, max_pages=1)
    assert not first['collection']['complete'] and first['collection']['stop_reason'] == 'page_limit'
    labels = first['collection']['labels']
    result, second, records = collect(ctx, labels=labels)
    assert second['collection']['complete'] and len(records) == 6
    assert [r['offset'] for r in site.requests] == [0, 2, 4]
    assert len(rows(ctx)) == 3
    assert 'Continuation labels' not in result.content  # Progress is separate.


def test_failure_exports_partial_prefix_then_resumes(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    site.statuses = [200, 503, 503, 503]
    _, partial, records = collect(ctx)
    assert len(records) == 4 and partial['collection']['stop_reason'] == 'api_failure'
    assert not partial['collection']['complete'] and len(rows(ctx)) == 2
    _, complete, records = collect(ctx, labels=partial['collection']['labels'])
    assert complete['collection']['complete'] and len(records) == 6
    assert [r['offset'] for r in site.requests] == [0, 2, 4, 4, 4, 4]
    assert ctx.research.state['reads'] == 3  # HTTP retries stay inside one logical read.


def test_stop_retains_new_page_and_allows_explicit_normal_chat_resume(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    ctx.cancel_event = threading.Event()
    progress = []
    def stop_after_page(text):
        progress.append(text)
        if len(progress) == 2:
            ctx.cancel_event.set()
    ctx.progress = stop_after_page
    result = CollectApiDataset().run(ctx, labels=['S1'])
    assert result.is_error and not result.artifacts and 'stopped' in result.content
    assert len(rows(ctx)) == 2 and len(site.requests) == 2
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))
    ctx.cancel_event.clear()
    ctx.progress = None
    ctx.research = None  # New Research attempts cannot silently import older evidence.
    ctx.accounting.turn_id = 'explicit-followup'
    _, completed, records = collect(ctx, labels=['S1', 'S2'])
    assert completed['collection']['complete'] and len(records) == 6
    assert [r['offset'] for r in site.requests] == [0, 2, 4]


def test_record_ceiling_keeps_page_size_and_never_overfetches(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    _, manifest, records = collect(ctx, max_records=5)
    assert len(records) == 4 and len(site.requests) == 2
    assert manifest['collection']['stop_reason'] == 'record_limit'
    assert not manifest['collection']['complete']
    assert CollectApiDataset().run(ctx, labels=['S1', 'S2'], max_records=3).is_error
    assert len(site.requests) == 2


def test_research_read_ceiling_charges_each_attempt_and_still_exports(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    ctx.research.state['reads'] = ctx.research.limits['reads'] - 1
    _, manifest, records = collect(ctx)
    assert len(records) == 4 and len(site.requests) == 2
    assert ctx.research.state['reads'] == ctx.research.limits['reads']
    assert manifest['collection']['stop_reason'] == 'research_limit'
    assert api_collection.NAME not in ctx.research.available_tools()
    assert api_dataset.NAME in ctx.research.available_tools()


def test_collection_deadline_caps_network_and_exports_progress(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    site.wait = threading.Event()
    _, manifest, records = collect(ctx, max_seconds=1)
    assert len(records) == 2 and manifest['collection']['stop_reason'] == 'time_limit'
    assert len(site.requests) == 2 and len(rows(ctx)) == 1


def test_source_and_bundle_limits_stop_before_new_evidence(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    monkeypatch.setattr(sources, 'remaining_capacity', lambda *args: 0)
    _, manifest, records = collect(ctx)
    assert len(records) == 2 and len(site.requests) == 1
    assert manifest['collection']['stop_reason'] == 'source_limit'


def test_cross_page_validation_happens_before_capture(ctx, trial_site):
    from test_clinical_trials import study
    trial_site.studies = [study(i) for i in [1, 2, 3, 4, 1, 6]]
    assert not QueryPublicApi().run(ctx, **TRIALS).is_error
    _, manifest, records = collect(ctx)
    assert len(records) == 4 and len(rows(ctx)) == 2
    assert manifest['collection']['stop_reason'] == 'validation_or_storage_limit'
    assert 'different offsets' in manifest['collection']['detail']


@pytest.mark.parametrize('failure', ['owner', 'expired', 'removed', 'hash', 'attempt', 'scope'])
def test_invalid_start_never_fetches_or_writes(ctx, site, failure):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    row = rows(ctx)[0]
    if failure == 'owner':
        ctx.user_id = 'foreign'
    elif failure == 'expired':
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        ctx.db.commit()
    elif failure == 'removed':
        sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), row.id)
    elif failure == 'hash':
        row.content_hash = 'invalid'
        ctx.db.commit()
    elif failure == 'attempt':
        ctx.accounting.turn_id = 'other-attempt'
    else:
        ctx.research.state['options']['scope'] = 'documents'
    result = CollectApiDataset().run(ctx, labels=['S1'])
    assert result.is_error and not result.artifacts and len(site.requests) == 1
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))


@pytest.mark.parametrize('tool', [api_collection.NAME, public_api.NAME, api_dataset.NAME])
def test_disabled_capabilities_cannot_be_bypassed(ctx, site, tool):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    pref = ctx.db.get(ToolPref, tool)
    old = pref.permission if pref else None
    if pref is None:
        pref = ToolPref(name=tool, enabled=True, permission='deny')
        ctx.db.add(pref)
    else:
        pref.permission = 'deny'
    ctx.db.commit()
    try:
        assert CollectApiDataset().run(ctx, labels=['S1']).is_error
        assert len(site.requests) == 1
    finally:
        if old is None:
            ctx.db.delete(pref)
        else:
            pref.permission = old
        ctx.db.commit()


def test_revocation_between_pages_prevents_more_reads_and_publication(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    def revoke(text):
        if '2 pages' in text:
            sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), rows(ctx)[0].id)
    ctx.progress = revoke
    result = CollectApiDataset().run(ctx, labels=['S1'])
    assert result.is_error and not result.artifacts
    assert len(site.requests) == 2 and len(rows(ctx)) == 2


@pytest.mark.parametrize('labels', [['S2'], ['S2', 'S1'], ['S1', 'S3']])
def test_missing_reordered_or_noncontiguous_prefix_fails(ctx, site, labels):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    assert not QueryPublicApi().run(ctx, continue_from='S1').is_error
    assert not QueryPublicApi().run(ctx, continue_from='S2').is_error
    assert CollectApiDataset().run(ctx, labels=labels).is_error
    assert len(site.requests) == 3


def test_permission_and_research_advertisement(ctx, site):
    assert PermissionGate(ctx.db, REGISTRY).decide(api_collection.NAME) == 'ask'
    assert api_collection.NAME not in READ_ONLY_TOOLS
    assert api_collection.NAME not in ctx.research.available_tools()
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    assert api_collection.NAME in ctx.research.available_tools()
    before = ctx.research.state['reads']
    assert ctx.research.admit(api_collection.NAME, {'labels': ['S1']}) is None
    assert ctx.research.state['reads'] == before


@pytest.mark.parametrize('durable', [False, True])
def test_one_prompt_collection_saved_files_and_compact_provider_input(db, client, monkeypatch, site, durable):
    from app import runs
    provider = ResearchProvider([
        [ToolCall('preview', 'query_public_api', NIH)],
        [ToolCall('collect', api_collection.NAME, {'labels': ['S1'], 'max_pages': 2})],
    ])
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='Multi-page collection', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Inspect a preview, collect all six projects, export files.',
               'auto_approve': True, 'research': {'scope': 'web', 'depth': 'standard', 'domains': []}}
    if durable:
        result = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        worker.step()
        replay = client.get('/api/runs/' + result.json()['id'] + '/events').text
        assert 'Continuation labels:' in replay and 'manifest.json' in replay
    else:
        response = client.post('/api/chat', json=payload)
        assert response.status_code == 200 and 'Continuation labels:' in response.text
    report = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert report['usage']['research']['reads'] == 3 and len(site.requests) == 3
    assert len(report['artifacts']) == 4
    for artifact in report['artifacts']:
        response = client.get(artifact['url'])
        assert response.status_code == 200
        if artifact['name'] == 'manifest.json':
            assert response.json()['collection']['complete']
        assert client.get(artifact['url'].replace(conv.id, 'foreign')).status_code == 404
    assert all('Project 5' not in json.dumps(call['messages']) for call in provider.seen)
    assert api_collection.NAME not in provider.seen[1]['tools']
    assert api_collection.NAME in provider.seen[2]['tools']


def test_approval_resume_rechecks_pages_and_does_not_fetch_on_denial(db, site):
    provider = ResearchProvider([[ToolCall('collect', api_collection.NAME, {'labels': ['S1']})]])
    agent, conv = session(db, provider)
    assert not QueryPublicApi().run(agent.ctx, **NIH).is_error
    agent.gate.auto_approve = False
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Collect files'}]))
    assert any(e['type'] == 'approval_request' for e in events) and len(site.requests) == 1
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    state = deepcopy(pending.state)
    sources.forget_web(db, conv, rows(agent.ctx)[0].id)
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY),
                           {'max_tool_rounds': 12}, 'test', provider.model)
    parse(resumed.resume(state, {'collect': 'allow'}))
    assert len(site.requests) == 1
    assert not list(workspace_dir(conv.id).glob('api-dataset-*'))
    assert db.query(Message).filter_by(conversation_id=conv.id).count() > 0


def test_bundle_allowance_preserves_prior_pages(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    # Fit a first-page export but leave too little room for safe batch publication.
    monkeypatch.setattr(api_dataset, 'MAX_BYTES', 18000)
    _, manifest, records = collect(ctx)
    assert len(records) == 2 and len(rows(ctx)) == 1
    assert manifest['collection']['stop_reason'] == 'validation_or_storage_limit'


def test_retry_after_keeps_partial_export_and_no_early_resume_request(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    site.status, site.retry_after = 429, '3600'
    _, manifest, _ = collect(ctx)
    assert manifest['collection']['stop_reason'] == 'api_failure' and len(site.requests) == 2
    _, resumed, _ = collect(ctx, labels=manifest['collection']['labels'])
    assert resumed['collection']['stop_reason'] == 'api_failure' and len(site.requests) == 2
    assert len(rows(ctx)) == 1


def test_administrative_disable_during_wait_blocks_further_requests(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    site.statuses = [503, 200]
    pref = ctx.db.get(ToolPref, api_collection.NAME)
    old = (pref.enabled, pref.permission) if pref else None
    if pref is None:
        pref = ToolPref(name=api_collection.NAME, enabled=True, permission='ask')
        ctx.db.add(pref)
    ctx.db.commit()
    def disable(*args):
        pref.enabled = False
        ctx.db.commit()
    monkeypatch.setattr(public_api_transport, 'wait', disable)
    try:
        result = CollectApiDataset().run(ctx, labels=['S1'])
        assert result.is_error and not result.artifacts and len(site.requests) == 2
        assert len(rows(ctx)) == 1
    finally:
        if old is None:
            ctx.db.delete(pref)
        else:
            pref.enabled, pref.permission = old
        ctx.db.commit()


@pytest.mark.parametrize('args', [{'labels': []}, {'labels': ['S1', 'S1']}, {'labels': ['../private']},
    {'labels': ['S1'], 'max_pages': 21}, {'labels': ['S1'], 'max_records': 1001},
    {'labels': ['S1'], 'max_seconds': 121}, {'labels': ['S1'], 'url': 'https://example.com'}])
def test_schema_rejects_unbounded_or_arbitrary_requests(ctx, args):
    assert CollectApiDataset().run(ctx, **args).is_error


def test_stop_during_query_keeps_prefix_and_does_not_publish(ctx, site):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    ctx.cancel_event = threading.Event()
    site.wait = threading.Event()
    timer = threading.Timer(0.15, ctx.cancel_event.set)
    timer.start()
    try:
        result = CollectApiDataset().run(ctx, labels=['S1'])
    finally:
        timer.cancel()
        timer.join()
    assert result.is_error and not result.artifacts and 'stopped' in result.content
    assert len(rows(ctx)) == 1 and len(site.requests) == 2


def test_empty_preview_exports_without_another_request(ctx, site):
    site.records.clear()
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    _, manifest, records = collect(ctx)
    assert not records and manifest['collection']['complete']
    assert len(site.requests) == 1 and ctx.research.state['reads'] == 0


def test_file_failure_returns_actionable_saved_prefix(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    def fail(*args):
        raise OSError('Fixture publication failed')
    monkeypatch.setattr(api_dataset, 'publish', fail)
    result = CollectApiDataset().run(ctx, labels=['S1'])
    assert result.is_error and not result.artifacts and 'export failed' in result.content
    state = json.loads(result.content.splitlines()[1])
    assert state['labels'] == ['S1', 'S2', 'S3'] and state['coverage']['captured_unique_records'] == 6
    assert not state['files_created'] and not state['complete']
    assert len(site.requests) == 3 and len(rows(ctx)) == 3


def test_followup_reserves_existing_source_capacity_before_fetch(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    assert not QueryPublicApi().run(ctx, continue_from='S1').is_error
    ctx.research = None
    ctx.accounting.turn_id = 'followup-with-two-source-slots'
    monkeypatch.setattr(sources, 'MAX_TURN_SOURCES', 2)
    _, manifest, records = collect(ctx, labels=['S1', 'S2'])
    assert manifest['collection']['stop_reason'] == 'source_limit'
    assert len(records) == 4 and len(site.requests) == 2
    assert len(sources.catalog(ctx.db, ctx.accounting.turn_id, ctx.conversation_id)) == 2


def test_stop_during_file_publication_remains_stopped(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    ctx.cancel_event = threading.Event()
    original = api_dataset.publish
    def stop(context, files):
        context.cancel_event.set()
        return original(context, files)
    monkeypatch.setattr(api_dataset, 'publish', stop)
    result = CollectApiDataset().run(ctx, labels=['S1'])
    assert result.is_error and not result.artifacts
    assert json.loads(result.content.splitlines()[1])['stop_reason'] == 'stopped'
    assert len(rows(ctx)) == 3
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))
