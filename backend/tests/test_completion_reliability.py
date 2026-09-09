"""Completion recovery never regathers evidence or executes unfinished tool requests."""
import json
import threading
from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.models import Conversation, Message, UsageLedger
from app.providers.base import StreamDelta, ToolCall
from app.research import Research


def answer(text='', reason='stop'):
    return [StreamDelta(type='text', text=text), StreamDelta(type='done', stop_reason=reason)]


class Script:
    model = 'completion-fixture'
    supports_tools = True
    context_window = 32000

    def __init__(self, batches):
        self.batches = batches
        self.seen = []

    def stream(self, messages, tools, params):
        self.seen.append((deepcopy(messages), [t.name for t in tools], dict(params)))
        yield StreamDelta(type='usage', usage={'input': 100, 'output': 20, 'total': 120})
        yield from self.batches[len(self.seen) - 1]


def run(db, provider, rounds=6, research=None, cancel=None):
    conv = Conversation(title='Synthetic completion', user_id='local')
    db.add(conv)
    db.commit()
    agent = AgentSession(db, conv, provider, REGISTRY,
                         PermissionGate(db, REGISTRY, auto_approve=True),
                         {'max_tokens': 1000, 'max_context_tokens': 8000, 'max_tool_rounds': rounds},
                         'test', provider.model, research=research, cancel_event=cancel)
    events = []
    for frame in agent.run([{'role': 'system', 'content': 'Help complete the task.'},
                            {'role': 'user', 'content': 'Finish the report.'}]):
        event = json.loads(frame[6:])
        events.append(event)
        if cancel and event['type'] == 'status' and 'Continuing the unfinished' in event['content']:
            cancel.set()
    msg = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    return msg, events


@pytest.mark.parametrize('first', [answer('The finding is ', 'length'),
                                   [StreamDelta(type='text', text='The finding is ')]])
def test_partial_completion_recovers_with_retained_text_no_tools_and_accounting(db, first):
    provider = Script([first, answer('supported.')])
    msg, events = run(db, provider)
    assert msg.content == 'The finding is supported.'
    assert msg.usage['outcome'] == 'completed' and msg.usage['completion']['recovered']
    assert len(provider.seen) == 2 and provider.seen[1][1] == []
    assert provider.seen[1][0][-2] == {'role': 'assistant', 'content': 'The finding is '}
    assert msg.usage['total'] == 240
    assert events[-1]['outcome'] == 'completed'
    rows = db.query(UsageLedger).filter_by(turn_id=msg.usage['turn_id']).order_by(UsageLedger.created_at).all()
    assert rows[0].usage_details['call']['finish_reason'] in {'length', 'incomplete_stream'}
    assert rows[1].usage_details['call']['stage'] == 'completion_recovery'


@pytest.mark.parametrize('rounds,expected', [(1, 1), (2, 2), (8, 3)])
def test_empty_and_reasoning_only_answers_cannot_complete_or_retry_forever(db, rounds, expected):
    provider = Script([[StreamDelta(type='reasoning', text='Thinking'), *answer()] for _ in range(8)])
    msg, events = run(db, provider, rounds)
    assert len(provider.seen) == expected
    assert events[-1]['outcome'] == 'failed'
    assert 'Response incomplete' in msg.content
    assert not msg.usage['completion']['recovered']


def test_stop_before_continuation_retains_prefix_and_makes_no_more_calls(db):
    provider = Script([answer('Retained partial report', 'length')])
    msg, events = run(db, provider, cancel=threading.Event())
    assert len(provider.seen) == 1
    assert msg.content == 'Retained partial report'
    assert events[-1]['outcome'] == 'cancelled'


@pytest.mark.parametrize('action', ['redact', 'block'])
def test_output_guardrails_prevent_joining_separately_checked_streams(db, monkeypatch, action):
    from app.guardrails import get_rules

    rules = get_rules('output', {'enabled': True, 'output_action': action})
    assert rules
    monkeypatch.setattr('app.guardrails.get_rules', lambda direction: rules if direction == 'output' else [])
    provider = Script([answer('Contact alice@', 'length'), answer('example.com')])
    msg, events = run(db, provider)
    assert len(provider.seen) == 1
    assert events[-1]['outcome'] == 'limit_reached'
    assert 'Automatic continuation is unavailable while output guardrails are active.' in msg.content
    assert 'alice@example.com' not in msg.content
    assert not msg.usage['completion']['recovered']


def test_duplicate_continuation_is_not_saved_twice(db):
    provider = Script([answer('The retained finding.', 'length'), answer('The retained finding.')])
    msg, events = run(db, provider)
    assert msg.content.count('The retained finding.') == 1
    assert events[-1]['outcome'] == 'failed'


def test_continuation_that_restarts_its_answer_does_not_duplicate_saved_prefix(db):
    provider = Script([answer('The retained finding: ', 'length'), answer('The retained finding: confirmed.')])
    msg, events = run(db, provider)
    assert msg.content == 'The retained finding: confirmed.'
    assert events[-1]['outcome'] == 'completed'


def test_truncated_valid_tool_arguments_never_dispatch(db, monkeypatch):
    monkeypatch.setattr(REGISTRY.get('write_file'), 'run', lambda *a, **kw: pytest.fail('Unfinished call executed'))
    provider = Script([[StreamDelta(type='tool_calls', stop_reason='length', tool_calls=[
        ToolCall('write', 'write_file', {'path': 'report.txt', 'content': 'valid but unfinished'})])]])
    msg, events = run(db, provider)
    assert not msg.tool_calls and len(provider.seen) == 1
    assert events[-1]['outcome'] == 'limit_reached'
    assert 'not executed' in msg.content


def test_last_generic_pass_is_reserved_for_a_final_answer(db, monkeypatch):
    from app.agent.tools.base import ToolResult
    calls = []
    monkeypatch.setattr(REGISTRY.get('web_search'), 'run', lambda *a, **kw: calls.append(kw) or ToolResult('A discovery result.'))
    provider = Script([[StreamDelta(type='tool_calls', tool_calls=[ToolCall('search', 'web_search', {'query': 'fixture'})])],
                       answer('Here is the result of the completed search.')])
    msg, events = run(db, provider, rounds=2)
    assert len(calls) == 1 and provider.seen[-1][1] == []
    assert 'final answer now' in provider.seen[-1][0][-1]['content']
    assert msg.usage['outcome'] == 'completed'


def test_research_report_recovery_reuses_captured_evidence(db, monkeypatch):
    from app import web_fetch
    monkeypatch.setattr(web_fetch, 'fetch', lambda url, cancel, url_policy=None: web_fetch.Page(
        url, 'Synthetic policy', 'The allowance is $45.', 'fixture-hash', False, 200))
    provider = Script([answer('Plan: verify the allowance.'),
                       [StreamDelta(type='tool_calls', tool_calls=[ToolCall('read', 'web_fetch', {'url': 'https://example.org/policy'})])],
                       answer('Ready to report.'), answer('The allowance is ', 'length'), answer('$45 [S1].')])
    research = Research({'scope': 'web', 'depth': 'brief', 'domains': ['example.org']})
    msg, events = run(db, provider, research=research)
    assert msg.content == 'The allowance is $45 [S1].'
    assert len(provider.seen) == 5 and provider.seen[-1][1] == []
    assert sum(t['name'] == 'web_fetch' for t in msg.tool_calls) == 1
    assert 'The allowance is $45.' in json.dumps(provider.seen[-1][0])
    assert msg.citations and msg.usage['research']['phase'] == 'completed'


@pytest.mark.parametrize('adapter', ['openai', 'bedrock'])
@pytest.mark.parametrize('reason', [None, 'length'])
def test_wire_adapters_preserve_truncation_and_unconfirmed_eof(db, monkeypatch, adapter, reason):
    monkeypatch.setattr(REGISTRY.get('write_file'), 'run', lambda *a, **kw: pytest.fail('Unconfirmed tool executed'))
    if adapter == 'openai':
        from app.providers.openai_provider import OpenAIProvider
        provider = object.__new__(OpenAIProvider)
        chunk = SimpleNamespace(usage=None, choices=[SimpleNamespace(finish_reason=reason,
            delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(index=0, id='call',
                function=SimpleNamespace(name='write_file', arguments='{"path":"a","content":"b"}'))]))])
        provider._client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kw: iter([chunk]))))
    else:
        from app.providers.bedrock_provider import BedrockProvider
        provider = object.__new__(BedrockProvider)
        provider.prompt_cache = False
        events = [{'contentBlockStart': {'contentBlockIndex': 0, 'start': {'toolUse': {'toolUseId': 'call', 'name': 'write_file'}}}},
                  {'contentBlockDelta': {'contentBlockIndex': 0, 'delta': {'toolUse': {'input': '{"path":"a","content":"b"}'}}}}]
        if reason:
            events.append({'messageStop': {'stopReason': 'max_tokens'}})
        provider._client = SimpleNamespace(converse_stream=lambda **kw: {'stream': iter(events)})
    provider.model, provider.supports_tools = 'wire-fixture', True
    msg, events = run(db, provider, rounds=1)
    assert events[-1]['outcome'] != 'completed' and not msg.tool_calls


def test_existing_chat_uses_current_settings_and_explicit_overrides_are_distinct(client, monkeypatch):
    provider = Script([answer('One.'), answer('Two.'), answer('Three.'), answer('Four.')])
    settings = {'active_profile': 'test', 'model': provider.model, 'system_prompt': 'Be helpful.',
                'temperature': .3, 'max_tokens': 1000, 'max_context_tokens': 8000, 'max_tool_rounds': 3}
    compact_windows = []
    monkeypatch.setattr('app.routers.chat.get_settings', lambda *a: dict(settings))
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *a: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *a: None)
    monkeypatch.setattr('app.routers.chat.compact_history', lambda p, h, w: compact_windows.append(w) or (h, False))
    def post(payload):
        response = client.post('/api/chat', json=payload)
        assert response.status_code == 200
        return [json.loads(x[6:]) for x in response.text.splitlines() if x.startswith('data: ')]
    first = post({'message': 'One'})
    cid = next(e['id'] for e in first if e['type'] == 'conversation')
    settings.update(max_tokens=4000, max_context_tokens=64000, max_tool_rounds=50)
    post({'conversation_id': cid, 'message': 'Two'})
    assert provider.seen[-1][2]['max_tokens'] == 4000
    assert provider.seen[-1][2]['max_tool_rounds'] == 50
    assert provider.seen[-1][2]['max_context_tokens'] == 32000
    assert compact_windows[-1] == 32000
    assert client.patch(f'/api/conversations/{cid}', json={'params': {'max_tokens': 2000, 'max_tool_rounds': 7}}).status_code == 200
    post({'conversation_id': cid, 'message': 'Three'})
    assert provider.seen[-1][2]['max_tokens'] == 2000 and provider.seen[-1][2]['max_tool_rounds'] == 7
    assert client.patch(f'/api/conversations/{cid}', json={'params': None}).status_code == 200
    post({'conversation_id': cid, 'message': 'Four'})
    assert provider.seen[-1][2]['max_tool_rounds'] == 50
    messages = client.get(f'/api/conversations/{cid}').json()['messages']
    turn = messages[-1]['usage']['turn_id']
    record = client.get(f'/api/conversations/{cid}/context/{turn}').json()
    info = record['calls'][0]['diagnostics']
    assert info['reserved_output_tokens'] == 4000 and info['max_context_tokens'] == 32000
    assert info['finish_reason'] == 'stop' and info['setting_sources']['max_tool_rounds'] == 'runtime'


def test_compaction_does_not_replace_history_with_a_truncated_summary():
    from app.agent.context import compact_history
    history = [{'role': 'system', 'content': 'Preserve instructions.'}]
    for _ in range(6):
        history.extend([{'role': 'user', 'content': 'Important earlier facts. ' * 300},
                        {'role': 'assistant', 'content': 'Acknowledged.'}])
    result, changed = compact_history(Script([answer('Incomplete summary', 'length')]), history, 2500)
    assert not changed and result == history


def test_research_removes_exhausted_tools_and_checks_usage_before_tool_admission():
    r = Research({'scope': 'web', 'depth': 'brief', 'domains': []})
    r.state.update(phase='gather', searches=3)
    assert r.available_tools() == {'web_fetch'}
    r.before_round(2, 5, r.limits['tokens'])
    assert r.phase == 'synthesize'
    assert r.admit('web_fetch', {'url': 'https://example.org'})
    assert r.state['reads'] == 0


def test_reasoning_usage_is_output_subset_and_does_not_add_to_cost():
    from app.model_calls import normalize_usage, call_cost
    raw = normalize_usage({'input': 100, 'output': 80, 'reasoning': 60})
    assert raw['total'] == 180 and raw['reasoning'] == 60
    assert call_cost({'input': 1, 'output': 2}, raw) == .00026


def test_assistant_and_explicit_conversation_settings_have_recorded_precedence():
    from app.runtime_settings import resolve_generation
    settings = {'temperature': .3, 'max_tokens': 1000, 'max_context_tokens': 8000, 'max_tool_rounds': 12}
    params = resolve_generation(settings, {'max_tokens': 2000, 'max_tool_rounds': 20},
                                {'_generation_overrides': {'max_tokens': 3000}})
    assert params['max_tokens'] == 3000 and params['max_tool_rounds'] == 20
    assert params['_setting_sources']['max_tokens'] == 'conversation_override'
    assert params['_setting_sources']['max_tool_rounds'] == 'assistant'


@pytest.mark.parametrize('reason,outcome', [('content_filter', 'blocked'), ('unexpected', 'failed')])
def test_provider_policy_or_unknown_stop_does_not_trigger_recovery(db, reason, outcome):
    provider = Script([answer('Partial', reason)])
    msg, events = run(db, provider)
    assert len(provider.seen) == 1 and events[-1]['outcome'] == outcome
