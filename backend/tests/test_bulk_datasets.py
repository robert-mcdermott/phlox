"""Bulk pages stay outside model context and citation allowances."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading

import pytest

from app import bulk_datasets, sources
from app.agent.tools.api_collection import CollectApiDataset
from app.agent.tools.api_dataset import ExportApiDataset
from app.agent.tools.api_reports import AnalyzeApiDataset, CreateApiReport
from app.agent.tools.public_api import QueryPublicApi
from app.models import ApiDataset, Conversation, ToolPref
from app.workspace.manager import resolve_in_workspace
from test_public_api import ARGS, site as nih_site, rows
from test_pubmed import ARGS as PUBMED, pubmed_site as pubmed_server
from test_clinical_trials import ARGS as TRIALS, trial_site as trial_server
from test_web_formats import ctx as format_context

ctx = format_context
site = nih_site
pubmed_site = pubmed_server
trial_site = trial_server


def collect(ctx, **kw):
    result = CollectApiDataset().run(ctx, **kw)
    assert not result.is_error, result.content
    state = json.loads(result.content.split('\n')[0])
    files = {a['name']: resolve_in_workspace(ctx.conversation_id, a['path']).read_text() for a in result.artifacts}
    return state, files


def large(site, count=4286):
    template = deepcopy(site.records[0])
    site.records[:] = [{**deepcopy(template), 'appl_id': n + 1, 'fiscal_year': 2016 + n % 10,
                        'project_num': f'R01-{n}', 'award_amount': n + 100} for n in range(count)]
    return {**ARGS, 'fiscal_years': list(range(2016, 2026)), 'limit': 5}


def test_ten_year_4286_records_ten_requests_two_citations_one_read(ctx, site):
    assert not QueryPublicApi().run(ctx, **large(site)).is_error
    state, files = collect(ctx, source='S1')
    assert state['complete'] and not state['more_pages'] and state['captured_records'] == 4286
    assert [r['offset'] for r in site.requests] == [0, *range(5, 4286, 500)]
    assert len(site.requests) == 10 and len(rows(ctx)) == 2 and ctx.research.state['reads'] == 1
    assert len(json.loads(files['records.json'])) == 4286
    assert not AnalyzeApiDataset().run(ctx, dataset_id=state['dataset_id']).is_error
    report = CreateApiReport().run(ctx, dataset_id=state['dataset_id'], title='Ten years of funding',
        sections=[{'title': 'Known awards by fiscal year', 'group_by': 'fiscal_year', 'metric': 'sum', 'value_field': 'award_amount'}])
    assert not report.is_error, report.content
    files = {a['name']: resolve_in_workspace(ctx.conversation_id, a['path']).read_text() for a in report.artifacts}
    groups = json.loads(files['analysis.json'])['sections'][0]['groups']
    assert len(groups) == 10
    for group in groups:
        expected = sum(r['award_amount'] for r in site.records if str(r['fiscal_year']) == str(group['group']))
        assert str(group['value']) == str(expected)
    assert 'Ten years of funding' in files['report.html'] and len(rows(ctx)) == 2


@pytest.mark.parametrize('fixture,arguments,total', [('site', ARGS, 6), ('pubmed_site', PUBMED, 6), ('trial_site', TRIALS, 4)])
def test_all_adapters(ctx, request, fixture, arguments, total):
    request.getfixturevalue(fixture)
    assert not QueryPublicApi().run(ctx, **arguments).is_error
    state, files = collect(ctx, source='S1')
    assert state['complete'] and len(json.loads(files['records.json'])) == total
    assert len(rows(ctx)) == 2 and ctx.research.state['reads'] == 1


def test_resume_and_idempotent_start(ctx, site):
    assert not QueryPublicApi().run(ctx, **large(site, 1500)).is_error
    partial, _ = collect(ctx, source='S1', max_pages=1)
    assert partial['captured_records'] == 505 and not partial['complete']
    complete, _ = collect(ctx, dataset_id=partial['dataset_id'])
    assert complete['complete']
    again, _ = collect(ctx, source='S1')
    assert again['dataset_id'] == partial['dataset_id']
    assert [r['offset'] for r in site.requests] == [0, 5, 505, 1005]
    assert ctx.db.query(ApiDataset).count() == 1


def test_stop_new_turn_resume_and_revocation(ctx, site):
    assert not QueryPublicApi().run(ctx, **large(site, 1200)).is_error
    ctx.cancel_event = threading.Event()
    ctx.progress = lambda text: ctx.cancel_event.set() if 'saved 505 ' in text else None
    stopped = CollectApiDataset().run(ctx, source='S1')
    assert stopped.is_error and not stopped.artifacts
    checkpoint = json.loads(stopped.content.split('\n')[0])
    ctx.cancel_event.clear()
    ctx.progress = None
    ctx.research = None
    ctx.accounting.turn_id += '-continue'
    done, _ = collect(ctx, dataset_id=checkpoint['dataset_id'])
    assert done['complete'] and [r['offset'] for r in site.requests] == [0, 5, 505, 1005]
    sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), rows(ctx)[-1].id)
    assert not ctx.db.query(ApiDataset).count()
    assert ExportApiDataset().run(ctx, dataset_id=done['dataset_id']).is_error


def test_expiry_ownership_scope_disabled_query(ctx, site):
    assert not QueryPublicApi().run(ctx, **ARGS).is_error
    state, _ = collect(ctx, source='S1')
    dataset_id = state['dataset_id']
    ctx.user_id = 'another-user'
    assert ExportApiDataset().run(ctx, dataset_id=dataset_id).is_error
    ctx.user_id = 'local'
    ctx.research.state['options']['domains'] = ['example.org']
    assert ExportApiDataset().run(ctx, dataset_id=dataset_id).is_error
    ctx.research.state['options']['domains'] = []
    ctx.db.merge(ToolPref(name='query_public_api', enabled=False, permission='auto'))
    ctx.db.commit()
    assert CollectApiDataset().run(ctx, dataset_id=dataset_id).is_error
    ctx.db.delete(ctx.db.get(ToolPref, 'query_public_api'))
    ctx.db.commit()
    rows(ctx)[0].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    ctx.db.commit()
    assert ExportApiDataset().run(ctx, dataset_id=dataset_id).is_error
    sources.cleanup(ctx.db)
    assert not ctx.db.query(ApiDataset).count()


def test_bad_total_does_not_save_bad_page(ctx, site):
    assert not QueryPublicApi().run(ctx, **ARGS).is_error
    site.mutate = lambda value: value['meta'].update(total=7)
    state, files = collect(ctx, source='S1')
    assert not state['complete'] and len(json.loads(files['records.json'])) == 2
    site.mutate = lambda value: None
    resumed, _ = collect(ctx, dataset_id=state['dataset_id'])
    assert resumed['complete']


def test_storage_limit_saves_checkpoint(ctx, site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **ARGS).is_error
    monkeypatch.setattr(bulk_datasets, 'MAX_BYTES', 2000)
    result = CollectApiDataset().run(ctx, source='S1')
    assert 'allowance' in result.content
    assert len(json.loads(ctx.db.query(ApiDataset).one().pages)) == 1


def test_real_harness_bulk_to_python_custom_chart(ctx, site):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY
    from app.models import Message
    from app.providers.base import ToolCall
    from test_research import ResearchProvider, parse
    args = large(site)

    class Provider(ResearchProvider):
        def stream(self, messages, tools, params):
            if self.batches and self.batches[0][0].name == 'execute_python':
                collection = next(m for m in messages if m.get('name') == 'collect_api_dataset' and m['role'] == 'tool')
                state = json.loads(collection['content'].split('\n')[0])
                path = next(p for p in state['files'] if p.endswith('/records.json'))
                self.batches[0][0].arguments['code'] = f'''
import json
from pathlib import Path
records = json.loads(Path({path!r}).read_text())
assert len(records) == 4286
values = {{year: sum(r['award_amount'] for r in records if r['fiscal_year'] == year) for year in range(2016, 2026)}}
x = list(values)
y = list(values.values())
xmean, ymean = sum(x)/len(x), sum(y)/len(y)
slope = sum((a-xmean)*(b-ymean) for a,b in zip(x,y))/sum((a-xmean)**2 for a in x)
fit = [ymean + slope*(a-xmean) for a in x]
points = ' '.join(f'{{20+i*40}},{{200-v/max(y)*150}}' for i,v in enumerate(fit))
bars = ''.join(f'<rect x="{{20+i*40}}" y="{{200-v/max(y)*150}}" width="20" height="{{v/max(y)*150}}"/>' for i,v in enumerate(y))
Path('chart.html').write_text('<html><h1>Ten-year fixture</h1><svg viewBox="0 0 450 230">'+bars+'<polyline fill="none" stroke="red" points="'+points+'"/><text x="120" y="220">2020: example event annotation</text></svg></html>')
print('Verified 4286 records, ten groups, OLS fit; chart.html written')
'''
            yield from super().stream(messages, tools, params)

    provider = Provider([[ToolCall('preview', 'query_public_api', args)],
        [ToolCall('collect', 'collect_api_dataset', {'source': 'S1'})],
        [ToolCall('analysis', 'begin_research_analysis', {'purpose': 'Create a trend chart', 'output_paths': ['chart.html']})],
        [ToolCall('plot', 'execute_python', {'code': ''})]])
    conv = ctx.db.get(Conversation, ctx.conversation_id)
    agent = AgentSession(ctx.db, conv, provider, REGISTRY, PermissionGate(ctx.db, REGISTRY, auto_approve=True),
        {'max_tool_rounds': 12, 'max_context_tokens': 32000, 'max_tokens': 4096}, 'test', provider.model, research=ctx.research)
    # The fixture context starts at gather; production starts with an extra plan pass.
    parse(agent.run([{'role': 'system', 'content': 'Create the requested funding chart'},
                     {'role': 'user', 'content': 'Collect ten years of awards and create an annotated trend chart.'}]))
    answer = ctx.db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert all(not call['is_error'] for call in answer.tool_calls)
    html = resolve_in_workspace(conv.id, 'chart.html').read_text()
    assert '<polyline' in html and html.count('<rect') == 10 and 'event annotation' in html
    assert any(a['name'] == 'chart.html' for a in answer.artifacts)
    assert agent.research.state['analysis_delivery'][0]['nonempty_file_exists']
    assert len(site.requests) == 10 and len(rows(ctx)) == 2
    assert not any('R01-4285' in json.dumps(call['messages']) for call in provider.seen)


@pytest.mark.parametrize('what', ['preview', 'manifest', 'pages'])
def test_corrupted_private_data_cannot_be_exported(ctx, site, what):
    assert not QueryPublicApi().run(ctx, **ARGS).is_error
    state, _ = collect(ctx, source='S1')
    if what == 'pages':
        row = ctx.db.get(ApiDataset, state['dataset_id'])
        pages = json.loads(row.pages)
        pages[-1]['hash'] = '0' * 64
        row.pages = json.dumps(pages)
    else:
        rows(ctx)[0 if what == 'preview' else -1].excerpt += 'corrupt'
    ctx.db.commit()
    assert ExportApiDataset().run(ctx, dataset_id=state['dataset_id']).is_error


def test_record_ceiling_requires_explicit_increase(ctx, site):
    assert not QueryPublicApi().run(ctx, **ARGS).is_error
    partial, _ = collect(ctx, source='S1', max_records=3)
    assert partial['captured_records'] == 3 and partial['stop_reason'] == 'record_limit'
    assert CollectApiDataset().run(ctx, dataset_id=partial['dataset_id'], max_records=2).is_error
    complete, _ = collect(ctx, dataset_id=partial['dataset_id'], max_records=6)
    assert complete['complete'] and [r['offset'] for r in site.requests] == [0, 2, 3]
