"""Retained-page exports: exact calculations, coverage, permissions and saved files."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import StringIO
import json
import threading
from types import SimpleNamespace
import uuid

import pytest

from app import api_dataset, artifact_snapshots, public_api, sources
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.api_dataset import ExportApiDataset
from app.models import Conversation, Message, PendingApproval, Source
from app.providers.base import ToolCall
from app.web_extract_worker import Number, encode_json, nih_projects
from app.workspace.manager import resolve_in_workspace, workspace_dir
from test_research import ResearchProvider, parse, session
from test_public_api import site as api_site
from test_web_formats import ctx as format_context

ctx = format_context
site = api_site


def record(identifier, amount='0.1', year=2024, name='EXAMPLE UNIVERSITY'):
    return {'appl_id': identifier, 'subproject_id': None, 'fiscal_year': year,
            'organization': {'org_name': name, 'org_ipf_code': '123', 'primary_uei': 'ABC'},
            'project_num': f'R01-{identifier}', 'project_title': f'Project {identifier}',
            'award_amount': Number(amount) if amount is not None else None,
            'agency_ic_admin': {'code': 'CA'}, 'agency_ic_fundings': [{'code': 'CA'}]}


def capture(context, data, offset=0, total=None, names=None):
    request = public_api.recipe({'org_names': names or ['Example'], 'fiscal_years': [2024, 2025],
                                 'limit': max(1, len(data))})
    request['offset'] = offset
    body = ''.join(encode_json({'meta': {'offset': offset, 'total': total if total is not None else offset + len(data),
                                        'limit': request['limit']}, 'results': data})).encode()
    page = nih_projects(body, request)
    location = {'format': 'api', 'adapter': 'nih_projects', 'method': 'POST', 'request': request,
                'request_hash': hashlib.sha256(json.dumps(request, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest(),
                'offset': offset, 'item_end': page['end'], 'total_records': page['total'],
                'next_offset': page['next_offset'], 'record_ids': page['ids']}
    blocks = sources.capture_web(context.db, conversation_id=context.conversation_id, user_id=context.user_id,
                turn_id=context.accounting.turn_id, url=public_api.ENDPOINT, title='API fixture', text=page['text'],
                content_hash=hashlib.sha256(page['text'].encode()).hexdigest(), provenance=location)
    if context.research:
        context.research.state['api_data_available'] = True
    return blocks[0].split(']')[0][1:]


def exported(context, labels):
    result = ExportApiDataset().run(context, labels=labels)
    assert not result.is_error, result.content
    files = {a['name']: resolve_in_workspace(context.conversation_id, a['path']).read_text() for a in result.artifacts}
    return result, files, json.loads(files['manifest.json'])


def test_exact_sums_nulls_and_file_hashes_without_network(ctx, monkeypatch):
    labels = [capture(ctx, [record(1, '0.1'), record(2, '0.2')], total=4),
              capture(ctx, [record(3, None), record(4, None, 2025)], offset=2, total=4)]
    monkeypatch.setattr(public_api, 'query', lambda *a, **kw: pytest.fail('Export must not fetch'))
    expiries = [s.expires_at for s in ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id)]
    result, files, manifest = exported(ctx, labels)
    assert len(result.artifacts) == 4 and manifest['coverage']['all_reported_records_captured']
    summary = list(csv.DictReader(StringIO(files['summary.csv'])))
    assert summary[0]['dataset_coverage'] == 'all_api_reported_matches'
    assert summary[0]['known_award_amount_sum'] == '0.3'
    assert summary[0]['missing_amount_count'] == '1'
    assert summary[1]['known_award_amount_sum'] == '' and summary[1]['missing_amount_count'] == '1'
    assert json.loads(files['records.json'], parse_float=Decimal)[0]['award_amount'] == Decimal('0.1')
    for name, expected in manifest['files'].items():
        assert hashlib.sha256(files[name].encode()).hexdigest() == expected['sha256']
    assert [s.expires_at for s in ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id)] == expiries
    assert len(manifest['sources']) == 2 and manifest['sources'][1]['request']['offset'] == 2
    assert 'not verified NIH-only annual funding' in manifest['notice']


def test_partial_gaps_overlap_deduplication_and_empty_query(ctx):
    labels = [capture(ctx, [record(1), record(2)], total=6),
              capture(ctx, [record(5), record(6)], offset=4, total=6)]
    _, files, manifest = exported(ctx, labels)
    assert all(r['dataset_coverage'] == 'partial' for r in csv.DictReader(StringIO(files['summary.csv'])))
    assert manifest['coverage']['missing_record_ranges'] == [[2, 4]]
    assert not manifest['coverage']['all_reported_records_captured']
    overlap = capture(ctx, [record(1), record(2), record(3)], total=6)
    _, _, manifest = exported(ctx, [labels[0], overlap])
    assert manifest['coverage']['duplicate_records_removed'] == 2
    assert manifest['coverage']['captured_unique_records'] == 3
    empty = capture(ctx, [], total=0)
    _, files, manifest = exported(ctx, [empty])
    assert json.loads(files['records.json']) == []
    assert manifest['coverage']['all_reported_records_captured']


@pytest.mark.parametrize('case', ['conflict', 'different_query', 'total_drift', 'different_position'])
def test_inconsistent_pages_do_not_publish(ctx, case):
    first = capture(ctx, [record(1)], total=3)
    if case == 'conflict':
        second = capture(ctx, [record(1, '999')], total=3)
    elif case == 'different_query':
        second = capture(ctx, [record(2)], offset=1, total=3, names=['EXAMPLE UNIVERSITY'])
    elif case == 'total_drift':
        second = capture(ctx, [record(2)], offset=1, total=4)
    else:
        second = capture(ctx, [record(1)], offset=1, total=3)
    result = ExportApiDataset().run(ctx, labels=[first, second])
    assert result.is_error and not result.artifacts
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))


def test_csv_formula_defense_and_json_precision(ctx):
    data = record(1, '9007199254740993.0123400', name='=EXAMPLE UNIVERSITY')
    data['project_title'] = '\t=HYPERLINK("https://invalid")'
    label = capture(ctx, [data])
    _, files, _ = exported(ctx, [label])
    rows = list(csv.DictReader(StringIO(files['records.csv'])))
    assert rows[0]['org_name'].startswith("'=") and rows[0]['project_title'].startswith("'\t=")
    assert rows[0]['award_amount'] == '9007199254740993.0123400'
    assert '9007199254740993.0123400' in files['summary.csv']
    assert json.loads(files['records.json'])[0]['organization']['org_name'] == '=EXAMPLE UNIVERSITY'


@pytest.mark.parametrize('case', ['owner', 'attempt', 'scope', 'expired', 'removed', 'hash'])
def test_access_rechecked_at_export(ctx, case):
    label = capture(ctx, [record(1)])
    row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).one()
    if case == 'owner':
        ctx.user_id = 'another-user'
    elif case == 'attempt':
        ctx.accounting.turn_id = 'another-turn'
    elif case == 'scope':
        ctx.research.state['options']['domains'] = ['example.org']
    elif case == 'expired':
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif case == 'removed':
        sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), row.id)
    else:
        row.excerpt += ' '
    ctx.db.commit()
    result = ExportApiDataset().run(ctx, labels=[label])
    assert result.is_error and not result.artifacts
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))


def test_stop_and_write_failure_leave_no_partial_bundle(ctx, monkeypatch):
    label = capture(ctx, [record(1)])
    ctx.cancel_event = threading.Event()
    ctx.cancel_event.set()
    assert ExportApiDataset().run(ctx, labels=[label]).is_error
    ctx.cancel_event.clear()
    original = api_dataset.Path.write_text
    calls = []
    def fail(path, *args, **kwargs):
        calls.append(path)
        if len(calls) == 2:
            raise OSError('Synthetic disk full')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(api_dataset.Path, 'write_text', fail)
    assert ExportApiDataset().run(ctx, labels=[label]).is_error
    assert not list(workspace_dir(ctx.conversation_id).iterdir())


def test_publication_is_new_and_saved_answer_snapshot_survives_workspace_edit(ctx):
    label = capture(ctx, [record(1)])
    result, _, _ = exported(ctx, [label])
    second, _, _ = exported(ctx, [label])
    assert {a['path'] for a in result.artifacts}.isdisjoint({a['path'] for a in second.artifacts})
    message = Message(conversation_id=ctx.conversation_id, role='assistant', content='Exported', artifacts=result.artifacts)
    ctx.db.add(message)
    ctx.db.flush()
    artifact_snapshots.capture(message)
    ctx.db.commit()
    assert all(a['snapshot_status'] == 'saved' for a in message.artifacts)
    from app.config import ATTACHMENTS_DIR
    saved = ATTACHMENTS_DIR / message.id / 'artifact-0'
    original = saved.read_bytes()
    resolve_in_workspace(ctx.conversation_id, result.artifacts[0]['path']).write_text('Changed')
    assert saved.read_bytes() == original


def test_export_remains_available_after_read_or_source_capacity_exhaustion(ctx):
    label = capture(ctx, [record(1)])
    research = ctx.research
    research.state.update(reads=research.limits['reads'], searches=research.limits['searches'], source_capacity=0)
    assert research.available_tools() == {'export_api_dataset'}
    research.before_round(2, 6, 0)
    assert research.phase == 'gather'
    assert research.admit('export_api_dataset', {'labels': [label]}) is None
    assert research.admit('export_api_dataset', {'labels': [label]}) is None
    assert research.admit('export_api_dataset', {'labels': [label]}) is not None
    assert not research.available_tools()


def test_approval_resume_revalidates_sources_before_export(db, monkeypatch):
    provider = ResearchProvider([[ToolCall('export', 'export_api_dataset', {'labels': ['S1']})]])
    agent, conv = session(db, provider)
    capture(agent.ctx, [record(1)])
    agent.gate.auto_approve = False
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Export the dataset'}]))
    assert any(e['type'] == 'approval_request' for e in events)
    assert not list(workspace_dir(conv.id).glob('api-dataset-*'))
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    state = deepcopy(pending.state)
    row = db.query(Source).filter_by(conversation_id=conv.id).one()
    sources.forget_web(db, conv, row.id)
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                           {'max_tool_rounds': 12}, 'test', provider.model)
    parse(resumed.resume(state, {'export': 'allow'}))
    assert not list(workspace_dir(conv.id).glob('api-dataset-*'))


def test_research_export_delivers_real_saved_artifacts_in_same_turn(db):
    provider = ResearchProvider([[ToolCall('export', 'export_api_dataset', {'labels': ['S1']})]])
    agent, conv = session(db, provider)
    capture(agent.ctx, [record(1)])
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Export the dataset'}]))
    assert sum(e['type'] == 'artifact' for e in events) == 4
    message = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert len(message.artifacts) == 4 and all(a['snapshot_status'] == 'saved' for a in message.artifacts)
    assert agent.research.state['reads'] == 0 and agent.research.state['export_attempts'] == 1
    assert all(resolve_in_workspace(conv.id, a['path']).is_file() for a in message.artifacts)


def test_permission_defaults_and_hard_research_limits(ctx):
    gate = PermissionGate(ctx.db, REGISTRY, auto_approve=False)
    assert gate.decide('export_api_dataset') == 'ask'
    from app.agent.tools.subagent import READ_ONLY_TOOLS
    assert 'export_api_dataset' not in READ_ONLY_TOOLS
    capture(ctx, [record(1)])
    ctx.research.state['reported_tokens'] = ctx.research.limits['tokens']
    assert 'export_api_dataset' not in ctx.research.available_tools()
    assert ctx.research.admit('export_api_dataset', {'labels': ['S1']}) is not None


@pytest.mark.parametrize('durable', [False, True])
def test_query_to_export_and_saved_downloads_survive_replay(db, client, monkeypatch, site, durable):
    from app import runs
    from test_public_api import ARGS
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
    conv = Conversation(title='Query and export', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Inspect two API pages and export data files',
               'auto_approve': True, 'research': {'scope': 'web', 'depth': 'standard', 'domains': []}}
    if durable:
        result = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert result.status_code == 200, result.text
        worker.step()
        replay = client.get('/api/runs/' + result.json()['id'] + '/events').text
        assert 'manifest.json' in replay
    else:
        assert client.post('/api/chat', json=payload).status_code == 200
    report = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert len(report['artifacts']) == 4
    assert report['usage']['research']['reads'] == 2
    assert len(site.requests) == 2  # Export does not refetch any page.
    assert 'export_api_dataset' not in provider.seen[1]['tools']
    assert 'export_api_dataset' in provider.seen[2]['tools']
    for artifact in report['artifacts']:
        response = client.get(artifact['url'])
        assert response.status_code == 200
        if artifact['name'] == 'manifest.json':
            assert not response.json()['coverage']['all_reported_records_captured']
            assert response.json()['coverage']['captured_unique_records'] == 4
        assert client.get(artifact['url'].replace(conv.id, 'foreign')).status_code == 404
