"""Analysis requires a handoff plus ordinary per-tool approvals; no invented sandbox restrictions."""
from copy import deepcopy

import pytest

from app import research_analysis
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.research import BeginResearchAnalysis
from app.models import Message, PendingApproval, ToolPref, UsageLedger
from app.providers.base import ToolCall
from app.workspace.manager import workspace_dir
from test_research import ResearchProvider, parse, session

HANDOFF = {'purpose': 'Create the requested chart from retained data', 'output_paths': ['chart.html']}
MESSAGES = [{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Create chart.html'}]


def test_handoff_approval_and_execution_approval_are_separate_and_resume(db):
    provider = ResearchProvider([[ToolCall('handoff', research_analysis.NAME, HANDOFF)],
        [ToolCall('write', 'write_file', {'path': 'chart.html', 'content': '<h1>Fixture chart</h1>'})]])
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    agent.gate.auto_approve = False
    events = parse(agent.run(deepcopy(MESSAGES)))
    assert any(e['type'] == 'approval_request' for e in events)
    assert not agent.research.state.get('analysis_enabled')
    assert all('write_file' not in call['tools'] for call in provider.seen)
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id, status='pending').one()
    state = deepcopy(pending.state)
    pending.status = 'resolved'
    db.commit()
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY),
                           {'max_tool_rounds': 12}, 'test', provider.model)
    parse(resumed.resume(state, {'handoff': 'allow'}))
    assert resumed.research.state['analysis_enabled']
    assert not (workspace_dir(conv.id) / 'chart.html').exists()
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id, status='pending').one()
    state = deepcopy(pending.state)
    pending.status = 'resolved'
    db.commit()
    final = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY),
                         {'max_tool_rounds': 12}, 'test', provider.model)
    parse(final.resume(state, {'write': 'allow'}))
    assert (workspace_dir(conv.id) / 'chart.html').read_text() == '<h1>Fixture chart</h1>'
    assert final.research.state['analysis_delivery'] == [{'path': 'chart.html', 'nonempty_file_exists': True}]
    calls = db.query(UsageLedger).filter_by(conversation_id=conv.id).all()
    assert any('write_file' in row.usage_details['call']['advertised_tools'] for row in calls)
    assert all('Execution tools now:' in call['messages'][0]['content'] for call in provider.seen)


def test_execution_not_dispatched_without_handoff_and_missing_files_reported(db, monkeypatch):
    provider = ResearchProvider([[ToolCall('bad', 'execute_python', {'code': 'raise Exception()'})],
                                 [ToolCall('begin', research_analysis.NAME, HANDOFF)]])
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    monkeypatch.setattr(REGISTRY.get('execute_python'), 'run', lambda *a, **kw: pytest.fail('Unapproved execution'))
    parse(agent.run(deepcopy(MESSAGES)))
    answer = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert 'missing or empty: chart.html' in answer.content
    assert answer.usage['outcome'] == 'failed'
    assert answer.tool_calls[0]['is_error']


def test_disabled_exec_stays_disabled_after_handoff(db):
    db.merge(ToolPref(name='execute_python', enabled=False, permission='ask'))
    db.commit()
    try:
        provider = ResearchProvider([[ToolCall('begin', research_analysis.NAME, HANDOFF)]])
        agent, _ = session(db, provider)
        parse(agent.run(deepcopy(MESSAGES)))
        assert all('execute_python' not in call['tools'] for call in provider.seen)
    finally:
        db.delete(db.get(ToolPref, 'execute_python'))
        db.commit()


@pytest.mark.parametrize('runner,network,expected', [('container', 'bridge', 'external access is configured'),
    ('container', 'none', 'networking is disabled'), ('local', 'none', 'host networking')])
def test_capability_facts_match_configuration(monkeypatch, runner, network, expected):
    monkeypatch.setattr('app.config.get_sandbox_config', lambda: {'runner': runner, 'container': {'network': network}})
    assert expected in research_analysis.capabilities()


def test_handoff_rejects_escaping_paths_and_does_not_autocreate_files(db):
    agent, conv = session(db, ResearchProvider())
    agent.research.state['phase'] = 'gather'
    for path in ('../bad.html', '/tmp/bad.html', '..\\bad.html'):
        assert BeginResearchAnalysis().run(agent.ctx, **{**HANDOFF, 'output_paths': [path]}).is_error
    assert not agent.research.state.get('analysis_enabled')
    assert not list(workspace_dir(conv.id).iterdir())


@pytest.mark.parametrize('auto_approve', [True, False])
def test_late_analysis_uses_remaining_model_rounds_and_existing_paths(db, monkeypatch, auto_approve):
    from app.agent.tools.base import ToolResult
    from app.agent.tools.fs import WriteFile
    # Eight gathering passes, then a report + approved handoff on pass ten.
    batches = [[ToolCall(f'search{i}', 'web_search', {'query': str(i)})] for i in range(8)]
    batches += [[ToolCall('report', 'create_api_report', {'title': 'Saved report', 'labels': ['S1'], 'sections': [{'title': 'Count'}]}),
                 ToolCall('begin', research_analysis.NAME, {**HANDOFF, 'output_paths': ['api-dataset-test/report.html', 'chart.html']})],
                [ToolCall('glob', 'glob_search', {'pattern': '**/api-dataset-*/records.csv'})],
                [ToolCall('read', 'read_file', {'path': 'api-dataset-test/records.csv'})],
                [ToolCall('plot', 'execute_python', {'code': "from pathlib import Path\nimport csv\nrows = list(csv.DictReader(Path('api-dataset-test/records.csv').open()))\nassert rows[0]['amount'] == '100'\nPath('chart.html').write_text('<svg><title>Fixture plot</title><rect width=\"100\" height=\"20\"/></svg>')"})]]
    provider = ResearchProvider(batches)
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    agent.gate.auto_approve = auto_approve
    agent.params['max_tool_rounds'] = 20
    agent.research.state['api_data_available'] = True
    monkeypatch.setattr(REGISTRY.get('web_search'), 'run', lambda *a, **kw: ToolResult('Discovery lead'))

    def report(ctx, **kw):
        html = WriteFile().run(ctx, path='api-dataset-test/report.html', content='<h1>Saved report</h1>')
        csv = WriteFile().run(ctx, path='api-dataset-test/records.csv', content='year,amount\n2020,100\n')
        return ToolResult('Complete fixture data and report', artifacts=html.artifacts + csv.artifacts)

    monkeypatch.setattr(REGISTRY.get('create_api_report'), 'run', report)
    parse(agent.run(deepcopy(MESSAGES)))
    for _ in range(3):
        pending = db.query(PendingApproval).filter_by(conversation_id=conv.id, status='pending').first()
        if not pending:
            break
        state = deepcopy(pending.state)
        pending.status = 'resolved'
        db.commit()
        agent = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY),
                             {'max_tool_rounds': 20}, 'test', provider.model)
        parse(agent.resume(state, {call['id']: 'allow' for call in state['pending_calls']}))
    answer = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert answer.usage['outcome'] == 'completed', [(c['name'], c['content']) for c in answer.tool_calls if c['is_error']]
    assert answer.usage['research']['rounds_used'] > 12
    assert answer.usage['research']['effective_rounds'] == 20
    assert (workspace_dir(conv.id) / 'chart.html').is_file()
    handoff = next(c for c in answer.tool_calls if c['name'] == research_analysis.NAME)
    assert 'api-dataset-test/records.csv' in handoff['content']
    assert not next(c for c in answer.tool_calls if c['name'] == 'glob_search')['is_error']
    assert next(c for c in answer.tool_calls if c['name'] == 'glob_search')['content'] == 'api-dataset-test/records.csv'
    late_calls = provider.seen[11:-1]
    assert late_calls and all('write_file' in c['tools'] and 'web_search' not in c['tools'] for c in late_calls)
    assert len(provider.seen) <= 20


def test_delivery_preserves_existing_report_without_claiming_missing_chart(db):
    provider = ResearchProvider([[ToolCall('write', research_analysis.NAME,
        {**HANDOFF, 'output_paths': ['renamed.html', 'chart.png']})],
        [ToolCall('report', 'write_file', {'path': 'report.html', 'content': '<h1>Report</h1>'})]])
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    parse(agent.run(deepcopy(MESSAGES)))
    answer = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    delivery = answer.usage['research']['delivery']
    assert delivery == {'status': 'partial', 'available_files': ['report.html'], 'missing_files': ['renamed.html', 'chart.png']}
    assert answer.usage['outcome'] != 'completed'
    assert 'Available files retained: report.html' in answer.content
    assert 'chart.png' in answer.content


def test_analysis_ceiling_time_tokens_and_legacy_policy(monkeypatch):
    from app.research import Research
    options = {'scope': 'web', 'depth': 'standard', 'domains': []}
    research = Research(options)
    research.state.update(phase='gather', analysis_enabled=True)
    assert research.effective_rounds(9) == 9
    assert research.effective_rounds(50) == 50
    research.before_round(11, 50, 100)
    assert 'execute_python' in research.available_tools()
    assert 'web_search' not in research.available_tools()
    assert research.admit('web_search', {'query': 'extra'})
    research.before_round(49, 50, 100)
    assert research.phase == 'synthesize'
    legacy = deepcopy(research.state)
    legacy.pop('analysis_uses_model_rounds')
    assert Research(state=legacy).effective_rounds(50) == 12
    for exhausted in ('tokens', 'seconds'):
        limited = Research(options)
        limited.state.update(phase='gather', analysis_enabled=True)
        tokens = limited.limits['tokens'] if exhausted == 'tokens' else 0
        if exhausted == 'seconds':
            limited.state['started_at'] -= limited.limits['seconds'] + 1
        limited.before_round(11, 50, tokens)
        assert limited.phase == 'synthesize'
        assert not limited.available_tools()


def test_stop_keeps_files_even_when_the_creating_tool_failed(db, monkeypatch):
    import threading
    from app.agent.tools.base import ToolResult
    from app.agent.tools.fs import WriteFile
    original = WriteFile.run
    stop = threading.Event()
    provider = ResearchProvider([[ToolCall('begin', research_analysis.NAME, HANDOFF)],
        [ToolCall('write', 'write_file', {'path': 'partial.csv', 'content': 'x,y\n1,2'})]])
    agent, conv = session(db, provider, cancel_event=stop)

    def write_then_stop(ctx, **args):
        result = original(WriteFile(), ctx, **args)
        stop.set()
        return ToolResult('Stopped after partial output.', is_error=True, artifacts=result.artifacts)

    monkeypatch.setattr(REGISTRY.get('write_file'), 'run', write_then_stop)
    parse(agent.run(deepcopy(MESSAGES)))
    answer = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert answer.usage['outcome'] == 'cancelled'
    assert answer.usage['research']['delivery'] == {
        'status': 'partial', 'available_files': ['partial.csv'], 'missing_files': ['chart.html']}
