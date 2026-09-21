"""Research allowances through real configuration, approval and evidence seams."""
import hashlib
import json
import re
from copy import deepcopy

import pytest

from app import app_config, config, sources, web_fetch
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.models import Conversation, Message, PendingApproval
from app.providers.base import StreamDelta, ToolCall
from app.research import Research
from app.research_config import DEFAULT_PRESETS, LEGACY_PRESETS


@pytest.fixture
def presets():
    previous = app_config.get_section('research')
    app_config.set_section('research', None)
    yield deepcopy(DEFAULT_PRESETS)
    app_config.set_section('research', previous)


def test_admin_presets_roundtrip_public_read_and_access_control(client, presets):
    from app.auth.deps import get_current_user
    from app.main import app
    from app.models import User

    presets['thorough']['tokens'] = 1500000
    response = client.put('/api/admin/config/research', json=presets)
    assert response.status_code == 200 and response.json()['research'] == presets
    app_config.invalidate()
    assert config.get_research_config() == presets
    app.dependency_overrides[get_current_user] = lambda: User(id='ordinary-reader', role='user')
    try:
        response = client.get('/api/settings/research')
        assert response.status_code == 200
        assert response.json() == {'presets': presets, 'source_limit': 64, 'conversation_source_limit': 512}
        assert client.put('/api/admin/config/research', json=presets).status_code == 403
    finally:
        app.dependency_overrides.pop(get_current_user, None)


@pytest.mark.parametrize('key,value', [
    ('rounds', 2), ('rounds', 101), ('reads', 0), ('searches', -1), ('seconds', 7201),
    ('tokens', 5000001), ('tokens', 0), ('tokens', True), ('reads', 4.5), ('rounds', '24'),
    ('unlimited', True),
])
def test_invalid_presets_never_change_saved_policy(client, presets, key, value):
    bad = deepcopy(presets)
    bad['standard'][key] = value
    assert client.put('/api/admin/config/research', json=bad).status_code == 422
    assert config.get_research_config() == presets


def test_run_snapshots_and_legacy_resumes_never_gain_allowances(presets):
    options = {'scope': 'web', 'depth': 'thorough', 'domains': []}
    run = Research(options)
    run.state.update(searches=7, reads=9, reported_tokens=230000, phase='gather')
    saved = deepcopy(run.state)
    presets['thorough'].update(rounds=30, reads=60, searches=30, seconds=2400, tokens=2000000)
    app_config.set_section('research', presets)
    assert run.limits == saved['limits']
    assert Research(state=saved).limits == saved['limits']
    legacy = deepcopy(saved)
    legacy.pop('limits')
    assert Research(state=legacy).limits == LEGACY_PRESETS['thorough']
    presets['thorough'].update(searches=3, tokens=100000)
    app_config.set_section('research', presets)
    resumed = Research(state=saved)
    assert resumed.limits['searches'] == 3 and resumed.limits['tokens'] == 100000
    assert resumed.limits['reads'] == saved['limits']['reads']
    assert resumed.state['started_at'] == saved['started_at']
    assert resumed.state['searches'] == 7 and resumed.state['reads'] == 9
    assert resumed.state['limits_restricted']
    assert 'Reported-token' in resumed.admit('web_fetch', {'url': 'https://example.org/2020'})
    assert run.limits == saved['limits']  # An in-flight turn keeps its snapshot.


class AnnualProvider:
    """Synthetic repeated-input load with one unavailable year and cited reference values."""
    model = 'annual-budget-fixture'
    supports_tools = True
    context_window = 32000

    def __init__(self, batches=None, tokens=18000):
        self.seen = []
        self.tokens = tokens
        self.batches = batches if batches is not None else [
            [ToolCall(str(year), 'web_fetch', {'url': f'https://example.org/{year}'})]
            for year in range(2020, 2030)
        ]

    def stream(self, messages, tools, params):
        self.seen.append((deepcopy(messages), [tool.name for tool in tools], dict(params)))
        yield StreamDelta(type='usage', usage={'input': self.tokens - 100, 'output': 100, 'total': self.tokens})
        if 'planning step' in messages[0]['content']:
            yield StreamDelta(type='text', text='Read annual totals and explain unavailable years.')
        elif tools and self.batches:
            yield StreamDelta(type='tool_calls', tool_calls=self.batches.pop(0))
        else:
            rows = []
            for message in messages:
                if message['role'] != 'tool':
                    continue
                match = re.search(r'Year (\d{4}): funding (\d+) units', message.get('content', ''))
                if match:
                    label = re.search(r'\[(S\d+)\]', message['content'])[1]
                    rows.append(f'{match[1]} | {match[2]} [{label}]')
            yield StreamDelta(type='text', text='Year | Funding\n' + '\n'.join(rows) + '\nUnavailable years are gaps, not zero.')
        yield StreamDelta(type='done', stop_reason='stop')


def agent(db, provider, rounds=24, state=None, conv=None):
    if conv is None:
        conv = Conversation(title='Synthetic annual research', user_id='local')
        db.add(conv)
        db.commit()
    research = Research({'scope': 'web', 'depth': 'thorough', 'domains': ['example.org']}, state=state)
    return AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                        {'max_tokens': 4096, 'max_context_tokens': 64000, 'max_tool_rounds': rounds},
                        'test', provider.model, research=research)


def run(session):
    events = [json.loads(frame[6:]) for frame in session.run([
        {'role': 'system', 'content': 'Research annual data.'},
        {'role': 'user', 'content': 'Compare annual funding in 2020–2029; explain gaps.'},
    ])]
    msg = session.db.query(Message).filter_by(conversation_id=session.conversation.id, role='assistant').one()
    return msg, events


@pytest.fixture
def annual_pages(monkeypatch):
    fetched = []

    def fetch(url, cancel=None, url_policy=None):
        assert url_policy(url)
        fetched.append(url)
        year = int(url.rsplit('/', 1)[-1])
        if year == 2024:
            raise web_fetch.FetchError('blocked', 'Unavailable fixture', http_status=403)
        text = f'Year {year}: funding {(year - 2019) * 100} units.'
        return web_fetch.Page(url, f'Annual {year}', text, hashlib.sha256(text.encode()).hexdigest(), False, 200)

    monkeypatch.setattr(web_fetch, 'fetch', fetch)
    return fetched


def test_larger_allowance_retains_nine_years_and_a_gap_without_restarts(db, presets, annual_pages):
    results = []
    for limits in [LEGACY_PRESETS['thorough'], DEFAULT_PRESETS['thorough']]:
        provider = AnnualProvider()
        session = agent(db, provider)
        session.research.state['limits'] = deepcopy(limits)
        msg, events = run(session)
        results.append(msg)
        assert events[-1]['outcome'] == 'completed'
        assert msg.usage['research']['rounds_used'] == len(provider.seen)
        assert all(params['max_tokens'] == 4096 and params['max_context_tokens'] == 32000
                   for _, _, params in provider.seen)
    old, new = results
    assert old.usage['research']['reads'] == 3
    assert new.usage['research']['reads'] == 10
    assert old.usage['total'] == 108000 and new.usage['total'] == 234000
    assert len(new.citations) == 9 and '2029 | 1000 [S10]' in new.content
    assert '2024 |' not in new.content and 'gaps, not zero' in new.content
    assert len(annual_pages[-10:]) == len(set(annual_pages[-10:])) == 10


def test_lower_generic_round_limit_reserves_report_and_remains_visible(db, presets, annual_pages):
    provider = AnnualProvider(tokens=40)
    msg, _ = run(agent(db, provider, rounds=4))
    assert len(provider.seen) == 4 and provider.seen[-1][1] == []
    assert len(annual_pages) == 2
    assert msg.usage['research']['effective_rounds'] == 4
    assert msg.usage['research']['limits']['rounds'] == 24


def test_durable_queue_resolves_at_start_then_keeps_snapshot(client, db, presets, annual_pages, monkeypatch):
    import uuid
    from types import SimpleNamespace
    from app import runs

    class ChangePolicyDuringCall(AnnualProvider):
        def stream(self, messages, tools, params):
            if not self.seen:
                latest = deepcopy(presets)
                latest['thorough']['reads'] = 1
                app_config.set_section('research', latest)
            yield from super().stream(messages, tools, params)

    provider = ChangePolicyDuringCall(tokens=40)
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr(config, 'runs_enabled', lambda: True)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: True)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    conv = Conversation(title='Queued research policy', user_id='local')
    db.add(conv)
    db.commit()
    response = client.post('/api/runs', json={
        'conversation_id': conv.id, 'message': 'Compare annual funding',
        'research': {'scope': 'web', 'depth': 'thorough', 'domains': ['example.org']},
    }, headers={'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    # Saved after enqueue but before execution: this is the snapshot the run must use.
    presets['thorough']['reads'] = 2
    app_config.set_section('research', presets)
    worker.step()
    replay = client.get('/api/runs/' + response.json()['id'] + '/events').text
    events = [json.loads(line[6:]) for line in replay.splitlines() if line.startswith('data: ')]
    progress = [event for event in events if event['type'] == 'research']
    assert progress and all(event['limits']['reads'] == 2 for event in progress)
    assert progress[-1]['reads'] == 2 and len(annual_pages) == 2
    assert config.get_research_config()['thorough']['reads'] == 1
    msg = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert msg.usage['research']['limits']['reads'] == 2


@pytest.mark.parametrize('scope', ['turn', 'conversation'])
def test_source_capacity_stops_additional_fetches_inside_a_batch(db, presets, annual_pages, monkeypatch, scope):
    monkeypatch.setattr(sources, 'MAX_TURN_SOURCES' if scope == 'turn' else 'MAX_CONVERSATION_SOURCES', 2)
    provider = AnnualProvider(batches=[[ToolCall(str(year), 'web_fetch', {'url': f'https://example.org/{year}'})
                                       for year in range(2020, 2025)]], tokens=40)
    msg, events = run(agent(db, provider))
    assert len(annual_pages) == 2 and msg.usage['research']['reads'] == 2
    assert msg.usage['research']['source_capacity'] == 0
    assert 'Source storage allowance reached' in msg.usage['research']['reason']
    assert len(msg.citations) == 2 and provider.seen[-1][1] == []
    assert len([e for e in events if e['type'] == 'tool_result' and e['is_error']]) == 3


def test_lower_admin_round_limit_rechecks_pending_tools_before_resume(db, presets, annual_pages, monkeypatch):
    provider = AnnualProvider(tokens=40)
    session = agent(db, provider)
    session.gate.auto_approve = False
    monkeypatch.setattr(session.gate, 'decide', lambda name: 'ask')
    list(session.run([{'role': 'system', 'content': ''}, {'role': 'user', 'content': 'Research'}]))
    pending = db.query(PendingApproval).filter_by(conversation_id=session.conversation.id).one()
    state = deepcopy(pending.state)
    state['rounds_used'] = 3  # A later paused gathering pass under the original allowance.
    presets['thorough']['rounds'] = 3
    app_config.set_section('research', presets)
    resumed = agent(db, provider, conv=session.conversation)
    list(resumed.resume(state, {'2020': 'allow'}))
    assert not annual_pages
    msg = db.query(Message).filter_by(conversation_id=session.conversation.id, role='assistant').one()
    assert msg.usage['research']['limits_restricted']
    assert msg.usage['research']['limits']['rounds'] == 3
    assert provider.seen[-1][1] == []
