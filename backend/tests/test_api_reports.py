"""Exact retained-data reports, access/approval boundaries and real saved downloads."""
from copy import deepcopy
import csv
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from io import StringIO
import json
from pathlib import Path
import threading
from types import SimpleNamespace
import uuid

import pytest

from app import api_dataset, api_reports, dataset_analysis, dataset_report_html, public_api, sources
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.api_reports import AnalyzeApiDataset, CreateApiReport
from app.models import Conversation, PendingApproval, Source, ToolPref
from app.providers.base import ToolCall
from app.research import Research
from app.web_extract_worker import Number
from app.workspace.manager import resolve_in_workspace, workspace_dir
from test_api_dataset import capture, record
from test_clinical_trials import trial_site as clinical_site
from test_pubmed import pubmed_site as bibliography_site
from test_public_api import site as api_site, ARGS
from test_research import ResearchProvider, parse, session
from test_web_formats import ctx as format_context

ctx = format_context
site = api_site
trial_site = clinical_site
pubmed_site = bibliography_site
SUM = {'title': 'Known awards by year', 'group_by': 'fiscal_year', 'metric': 'sum', 'value_field': 'award_amount'}
COUNT = {'title': 'Projects by organization', 'group_by': 'organization.org_name'}


def report(context, labels, **kwargs):
    result = CreateApiReport().run(context, labels=labels, title='Dataset report', sections=[SUM, COUNT], **kwargs)
    assert not result.is_error, result.content
    files = {a['name']: resolve_in_workspace(context.conversation_id, a['path']).read_text() for a in result.artifacts}
    return result, files, json.loads(files['manifest.json'])


def test_exact_tables_missing_values_chart_and_file_integrity(ctx, monkeypatch):
    labels = [capture(ctx, [record(1, '0.1'), record(2, '0.2')], total=5),
              capture(ctx, [record(3, None), record(4, None, 2025)], offset=2, total=5)]
    monkeypatch.setattr(public_api, 'query', lambda *a, **kw: pytest.fail('Reports must not fetch'))
    inspection = AnalyzeApiDataset().run(ctx, labels=labels)
    assert not inspection.is_error and 'organization.org_name' in inspection.content
    assert '"records"' not in inspection.content and 'agency_ic_fundings' not in inspection.content
    result, files, manifest = report(ctx, labels)
    assert len(result.artifacts) == 7 and result.artifacts[0]['name'] == 'report.html'
    analysis = json.loads(files['analysis.json'], parse_float=Decimal)
    groups = analysis['sections'][0]['groups']
    assert groups[0]['value'] == Decimal('0.3') and groups[0]['missing_count'] == 1
    assert groups[1]['value'] is None and groups[1]['known_count'] == 0
    assert 'Partial dataset' in files['report.html']
    assert '<span class="bar-value">0.3</span>' in files['report.html']
    assert '<td class="num">0.3</td>' in files['report.html']
    assert '<span class="bar-value">Not reported</span>' in files['report.html']
    rows = list(csv.DictReader(StringIO(files['analysis.csv'])))
    assert rows[0]['value'] == '0.3' and rows[1]['value'] == ''
    assert not manifest['coverage']['all_reported_records_captured']
    assert manifest['verification']['visual_review'] == 'not performed at generation time'
    for name, digest in manifest['files'].items():
        assert digest['sha256'] == hashlib.sha256(files[name].encode()).hexdigest()
        assert digest['bytes'] == len(files[name].encode())
    assert '[S1]' in files['report.html'] and '#source-S2' in files['report.html']


def test_large_exact_decimals_escaping_and_formula_safe_analysis(ctx):
    label = capture(ctx, [record(1, '9007199254740993.0123400', name='=1+1 EXAMPLE'),
                          record(2, '0.0000001', name='<script>alert(1)</script> EXAMPLE')])
    result = CreateApiReport().run(ctx, labels=[label], title='<img src=x onerror=alert(1)>', sections=[SUM, COUNT])
    assert not result.is_error, result.content
    files = {a['name']: resolve_in_workspace(ctx.conversation_id, a['path']).read_text() for a in result.artifacts}
    assert '9007199254740993.0123401' in files['report.html']
    assert '<script>' not in files['report.html'] and '<img' not in files['report.html']
    assert '&lt;script&gt;' in files['report.html'] and '&lt;img' in files['report.html']
    assert "'=1+1" in files['analysis.csv']
    assert 'default-src' in files['report.html'] and '<script' not in files['report.html']


def test_filters_preserve_original_coverage_and_unfiltered_records(ctx):
    label = capture(ctx, [record(1, '0.1', name='Example A'), record(2, '0.2', name='EXAMPLE B'),
                          record(3, None, name='Elsewhere')], total=10, names=['Example', 'Elsewhere'])
    filters = [{'field': 'organization.org_name', 'op': 'contains', 'value': 'example'},
               {'field': 'award_amount', 'op': 'minimum', 'value': '0.2'}]
    _, files, manifest = report(ctx, [label], filters=filters)
    assert manifest['selected_records'] == 1 and manifest['coverage']['captured_unique_records'] == 3
    analysis = json.loads(files['analysis.json'])
    assert analysis['excluded_records'] == 2 and analysis['sections'][0]['groups'][0]['value'] == 0.2
    assert len(json.loads(files['records.json'])) == 3
    assert manifest['analysis_recipe']['filters'] == filters


def test_multivalued_groups_deduplicate_membership_and_do_not_double_count_sums():
    records = [{'phases': ['P1', 'P1', 'P2'], 'amount': Number('2')},
               {'phases': ['P2'], 'amount': Number('3')}, {'phases': [], 'amount': None}]
    result = dataset_analysis.analyze(records, [{'title': 'Phase', 'group_by': 'phases'}], [])
    section = result['sections'][0]
    assert section['multi_valued_groups'] and section['membership_count'] == 4
    assert {g['group']: g['value'] for g in section['groups']} == {'P1': 1, 'P2': 2, None: 1}
    filtered = dataset_analysis.analyze(records, [], [{'field': 'phases', 'op': 'equals', 'value': 'P1'}])
    assert filtered['selected_records'] == 1
    with pytest.raises(api_dataset.DatasetError, match='double count'):
        dataset_analysis.analyze(records, [{'title': 'Bad', 'group_by': 'phases', 'metric': 'sum', 'value_field': 'amount'}], [])


def test_blank_categories_merge_with_missing_without_hiding_literal_label():
    result = dataset_analysis.analyze([{'journal': ''}, {'journal': None}, {'journal': 'Not reported'}],
                                      [{'title': 'Journals', 'group_by': 'journal'}], [])
    assert result['columns'][0]['missing_records'] == 2
    assert {g['group']: g['value'] for g in result['sections'][0]['groups']} == {None: 2, 'Not reported': 1}
    assert dataset_analysis.display(None) != dataset_analysis.display('Not reported')


@pytest.mark.parametrize('sections,filters', [
    ([{'title': 'Bad', 'group_by': 'not_a_column'}], []),
    ([{'title': 'Bad', 'metric': 'sum', 'value_field': 'appl_id'}], []),
    ([{'title': 'Bad', 'metric': 'sum', 'value_field': 'project_title'}], []),
    ([COUNT], [{'field': 'award_amount', 'op': 'minimum', 'value': 'NaN'}]),
    ([COUNT], [{'field': 'organization.org_name', 'op': 'maximum', 'value': '1'}]),
    ([COUNT], [{'field': 'nonexistent', 'op': 'equals', 'value': 'x'}]),
])
def test_invalid_analysis_never_publishes(ctx, sections, filters):
    label = capture(ctx, [record(1)])
    result = CreateApiReport().run(ctx, labels=[label], title='Invalid', sections=sections, filters=filters)
    assert result.is_error and not result.artifacts
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))


@pytest.mark.parametrize('case', ['owner', 'attempt', 'scope', 'expired', 'removed', 'disabled', 'export_disabled'])
def test_report_rechecks_access_and_policy(ctx, case):
    label = capture(ctx, [record(1)])
    row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).one()
    pref = None
    old = None
    if case == 'owner':
        ctx.user_id = 'foreign'
    elif case == 'attempt':
        ctx.accounting.turn_id = 'other-turn'
    elif case == 'scope':
        ctx.research.state['options']['domains'] = ['example.org']
    elif case == 'expired':
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    elif case == 'removed':
        sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), row.id)
    else:
        name = api_reports.REPORT if case == 'disabled' else api_dataset.NAME
        pref = ctx.db.get(ToolPref, name)
        if pref:
            old = pref.enabled, pref.permission
            pref.enabled = False
        else:
            pref = ToolPref(name=name, enabled=False, permission='ask')
            ctx.db.add(pref)
    ctx.db.commit()
    try:
        result = CreateApiReport().run(ctx, labels=[label], title='Report', sections=[COUNT])
        assert result.is_error and not result.artifacts
        assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))
    finally:
        if pref:
            if old:
                pref.enabled, pref.permission = old
            else:
                ctx.db.delete(pref)
            ctx.db.commit()


@pytest.mark.parametrize('case', ['stop', 'revoke', 'oversize', 'corrupt_staging', 'stop_at_verification'])
def test_failure_before_publication_leaves_no_completed_bundle(ctx, monkeypatch, case):
    label = capture(ctx, [record(1)])
    if case in {'stop', 'revoke'}:
        original = dataset_report_html.render
        def change(*args):
            html = original(*args)
            if case == 'stop':
                ctx.cancel_event = threading.Event()
                ctx.cancel_event.set()
            else:
                row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).one()
                sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), row.id)
            return html
        monkeypatch.setattr(dataset_report_html, 'render', change)
    elif case == 'oversize':
        monkeypatch.setattr(dataset_report_html, 'render', lambda *a: 'x' * (api_dataset.MAX_BYTES + 1))
    else:
        original_read = Path.read_bytes
        def read(path):
            if case == 'stop_at_verification' and path.name == 'manifest.json':
                ctx.cancel_event = threading.Event()
                ctx.cancel_event.set()
            return b'corrupt' if case == 'corrupt_staging' and path.name == 'report.html' else original_read(path)
        monkeypatch.setattr(Path, 'read_bytes', read)
    result = CreateApiReport().run(ctx, labels=[label], title='Report', sections=[COUNT])
    assert result.is_error and not result.artifacts
    assert not list(workspace_dir(ctx.conversation_id).glob('api-dataset-*'))
    assert not list(workspace_dir(ctx.conversation_id).glob('.api-export-*'))


def test_research_tools_survive_read_exhaustion_but_preserve_limits_and_resume(ctx):
    capture(ctx, [record(1)])
    research = ctx.research
    research.state.update(reads=research.limits['reads'], searches=research.limits['searches'], source_capacity=0)
    assert research.available_tools() == {'export_api_dataset', api_reports.INSPECT, api_reports.REPORT}
    research.before_round(2, 8, 0)
    assert research.phase == 'gather'
    assert research.admit(api_reports.INSPECT, {}) is None
    research = Research(state=research.state)
    for _ in range(3):
        assert research.admit(api_reports.INSPECT, {}) is None
    assert research.admit(api_reports.INSPECT, {})
    for _ in range(2):
        assert research.admit(api_reports.REPORT, {}) is None
    assert research.admit(api_reports.REPORT, {})
    assert research.state['reads'] == research.limits['reads']
    research.state['reported_tokens'] = research.limits['tokens']
    assert not research.available_tools()


@pytest.mark.parametrize('revoked', [False, True])
def test_report_approval_resume_publishes_only_with_current_source_access(db, revoked):
    provider = ResearchProvider([[ToolCall('report', api_reports.REPORT, {'labels': ['S1'], 'title': 'Approved report', 'sections': [COUNT]})]])
    agent, conv = session(db, provider)
    capture(agent.ctx, [record(1)])
    agent.gate.auto_approve = False
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Report'}]))
    assert any(e['type'] == 'approval_request' for e in events)
    assert not list(workspace_dir(conv.id).glob('api-dataset-*'))
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    state = deepcopy(pending.state)
    if revoked:
        sources.forget_web(db, conv, db.query(Source).filter_by(conversation_id=conv.id).one().id)
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                           {'max_tool_rounds': 12}, 'test', provider.model, accounting=agent.accounting)
    events = parse(resumed.resume(state, {'report': 'allow'}))
    assert sum(e['type'] == 'artifact' for e in events) == (0 if revoked else 7), json.dumps(events)
    assert resumed.research.state['reads'] == 0


def test_permissions_schema_and_group_cardinality(ctx):
    gate = PermissionGate(ctx.db, REGISTRY)
    assert gate.decide(api_reports.REPORT) == 'ask' and gate.decide(api_reports.INSPECT) == 'allow'
    from app.agent.tools.subagent import READ_ONLY_TOOLS
    assert api_reports.INSPECT in READ_ONLY_TOOLS and api_reports.REPORT not in READ_ONLY_TOOLS
    for tool in (AnalyzeApiDataset(), CreateApiReport()):
        assert not {'allOf', 'anyOf', 'oneOf'} & tool.parameters.keys()
    with pytest.raises(api_dataset.DatasetError, match='50 groups'):
        dataset_analysis.analyze([{'group': str(i)} for i in range(51)], [{'title': 'Groups', 'group_by': 'group'}], [])
    result = CreateApiReport().run(ctx, labels=['S1'], title='Report', sections=[], path='/tmp/untrusted.json')
    assert result.is_error and not result.artifacts


@pytest.mark.parametrize('durable', [False, True])
def test_query_collection_analysis_report_saved_downloads(db, client, monkeypatch, site, durable):
    from app import runs
    provider = ResearchProvider([
        [ToolCall('preview', 'query_public_api', ARGS)],
        [ToolCall('collect', 'collect_api_dataset', {'labels': ['S1'], 'max_pages': 2})],
        [ToolCall('inspect', api_reports.INSPECT, {'labels': ['S1', 'S2', 'S3']})],
        [ToolCall('report', api_reports.REPORT, {'labels': ['S1', 'S2', 'S3'], 'title': 'Project sample', 'sections': [SUM, COUNT]})],
    ])
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='Dataset report', user_id='local')
    db.add(conv)
    db.commit()
    payload = {'conversation_id': conv.id, 'message': 'Inspect, collect, analyze and create an HTML report.',
               'auto_approve': True, 'research': {'scope': 'web', 'depth': 'standard', 'domains': []}}
    if durable:
        response = client.post('/api/runs', json=payload, headers={'Idempotency-Key': uuid.uuid4().hex})
        assert response.status_code == 200, response.text
        worker.step()
        assert 'report.html' in client.get('/api/runs/' + response.json()['id'] + '/events').text
    else:
        response = client.post('/api/chat', json=payload)
        assert response.status_code == 200, response.text
    answer = client.get(f'/api/conversations/{conv.id}').json()['messages'][-1]
    assert answer['usage']['research']['reads'] == 3 and len(site.requests) == 3
    assert len(answer['artifacts']) == 11  # Four collection files plus seven report files.
    for artifact in answer['artifacts']:
        download = client.get(artifact['url'])
        assert download.status_code == 200 and artifact['snapshot_status'] == 'saved'
        if artifact['name'] == 'report.html':
            assert 'Project sample' in download.text and 'Method and scope' in download.text
        assert client.get(artifact['url'].replace(conv.id, 'foreign')).status_code == 404
    assert api_reports.REPORT not in provider.seen[1]['tools']
    assert api_reports.REPORT in provider.seen[2]['tools']
    assert all('Project 5' not in json.dumps(call['messages']) for call in provider.seen)


def test_empty_and_fully_filtered_reports_are_explicit(ctx):
    label = capture(ctx, [], total=0)
    result = CreateApiReport().run(ctx, labels=[label], title='No matches', sections=[{'title': 'Captured records'}])
    assert not result.is_error, result.content
    html = resolve_in_workspace(ctx.conversation_id, result.artifacts[0]['path']).read_text()
    assert 'No records match the selection' in html and 'All API-reported matches captured' in html
    label = capture(ctx, [record(1)])
    _, files, _ = report(ctx, [label], filters=[{'field': 'award_amount', 'op': 'minimum', 'value': '100'}])
    analysis = json.loads(files['analysis.json'])
    assert analysis['selected_records'] == 0 and all(not s['groups'] for s in analysis['sections'])


def test_pubmed_report_uses_bibliography_columns(ctx, pubmed_site):
    from app.agent.tools.public_api import QueryPublicApi
    from test_pubmed import ARGS as pubmed_args
    assert not QueryPublicApi().run(ctx, **pubmed_args).is_error
    result = CreateApiReport().run(ctx, labels=['S1'], title='Article sample',
                                  sections=[{'title': 'Journals', 'group_by': 'journal'},
                                            {'title': 'Authors', 'group_by': 'authors'}])
    assert not result.is_error, result.content
    files = {a['name']: resolve_in_workspace(ctx.conversation_id, a['path']).read_text() for a in result.artifacts}
    analysis = json.loads(files['analysis.json'])
    assert analysis['selected_records'] == 2 and analysis['sections'][1]['multi_valued_groups']
    assert 'Multi-valued categories overlap' in files['report.html']
    assert len(pubmed_site.requests) == 2  # Search and summary only, no report retrieval.
    assert json.loads(files['manifest.json'])['adapter'] == 'pubmed'


def test_trial_report_counts_distinct_phase_memberships_and_filters_booleans(ctx, trial_site):
    from app.agent.tools.public_api import QueryPublicApi
    from test_clinical_trials import ARGS as trial_args
    trial_site.studies[0]['protocolSection']['designModule']['phases'] = ['PHASE1', 'PHASE2']
    assert not QueryPublicApi().run(ctx, **trial_args).is_error
    result = CreateApiReport().run(ctx, labels=['S1'], title='Registry sample',
                                  sections=[{'title': 'Phases', 'group_by': 'phases'}],
                                  filters=[{'field': 'has_results', 'op': 'equals', 'value': 'false'}])
    assert not result.is_error, result.content
    files = {a['name']: resolve_in_workspace(ctx.conversation_id, a['path']).read_text() for a in result.artifacts}
    section = json.loads(files['analysis.json'])['sections'][0]
    assert {g['group']: g['value'] for g in section['groups']} == {'PHASE1': 1, 'PHASE2': 2}
    assert section['membership_count'] == 3 and len(trial_site.requests) == 1
    assert json.loads(files['manifest.json'])['adapter'] == 'clinical_trials'
