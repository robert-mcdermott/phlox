"""Research working context: reduction, exact evidence, authorization and persistence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import threading
from types import SimpleNamespace

import pytest

from app import research_notebook as notebook, sources
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.agent.tools.base import ToolContext, ToolResult
from app.models import Conversation, DocChunk, Document, Message, PendingApproval, Source, SourceUse
from app.providers.base import ToolCall
from app.research import Research
from test_research import ResearchProvider, parse, session


@pytest.fixture
def context(db, tmp_path):
    conv = Conversation(user_id='local', title='Notebook fixture')
    db.add(conv)
    db.commit()
    research = Research({'scope': 'web', 'depth': 'standard', 'domains': []})
    research.state['phase'] = 'gather'
    ctx = ToolContext(conversation_id=conv.id, user_id='local', workspace=tmp_path, db=db,
                      runner=None, research=research, cancel_event=threading.Event(),
                      accounting=SimpleNamespace(turn_id='notebook-' + conv.id))
    yield ctx
    db.rollback()
    db.delete(conv)
    db.commit()


def capture(ctx, index=1):
    return '\n'.join(sources.capture_web(ctx.db, conversation_id=ctx.conversation_id,
        user_id=ctx.user_id, turn_id=ctx.accounting.turn_id,
        url=f'https://example.org/{index}', title=f'Report {index}',
        text=(f'Unique evidence {index}: funding {index * 10} million. ' * 160)[:5900]))


def notes(labels=('S1',)):
    return {'findings': [{'text': 'Funding differs across reports.', 'sources': list(labels)}],
            'disagreements': [], 'questions': ['Does the comparison use the same period?']}


def history(ctx):
    messages = [{'role': 'system', 'content': 'Research safely.'},
                {'role': 'user', 'content': 'Compare funding and explain gaps.'}]
    for i in range(1, 7):
        messages.extend([
            {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': f'c{i}', 'name': 'web_fetch', 'arguments': {'url': f'https://example.org/{i}'}}]},
            {'role': 'tool', 'tool_call_id': f'c{i}', 'name': 'web_fetch', 'content': capture(ctx, i)},
        ])
    ctx.research.state['completed_calls'] = [f'c{i}' for i in range(1, 7)]
    ctx.research.state['notebook_eligible_calls'] = list(ctx.research.state['completed_calls'])
    return messages


PARAMS = {'max_context_tokens': 40000, 'max_tokens': 2000}


def test_projection_reduces_input_preserves_raw_and_rehydrates_exact_passages(context):
    ctx = context
    messages = history(ctx)
    original = deepcopy(messages)
    assert notebook.prepare(ctx, messages, [], PARAMS, None) == original
    assert notebook.update(ctx, notes(('S1', 'S2', 'S3'))) is None
    rows = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id).all()
    expiry = {s.id: s.expires_at for s in rows}
    projected = notebook.prepare(ctx, messages, [], PARAMS, None)
    assert messages == original
    assert projected[:2] == original[:2]
    assert [m['tool_call_id'] for m in projected if m['role'] == 'tool'] == ['c5', 'c6']
    metrics = ctx.research.progress()['notebook']['context']
    assert metrics['condensed_exchanges'] == 4
    assert metrics['input_tokens_after'] < metrics['input_tokens_before'] / 2
    ctx.research.state['phase'] = 'synthesize'
    report = notebook.prepare(ctx, messages, [], PARAMS, None)
    text = json.dumps(report)
    for row in rows[:3]:
        assert row.excerpt in text
    assert ctx.research.progress()['notebook']['context']['restored_sources'] == ['S1', 'S2', 'S3']
    assert {s.id: s.expires_at for s in rows} == expiry
    assert ctx.db.query(SourceUse).filter_by(turn_id=ctx.accounting.turn_id).count() == 6
    assert messages == original


def test_evidence_packet_honors_provider_limit_and_reports_omissions(context):
    messages = history(context)
    assert notebook.update(context, notes(('S1', 'S2', 'S3'))) is None
    context.research.state['phase'] = 'synthesize'
    # Three full excerpts exceed this provider's input allowance after output reservation.
    result = notebook.prepare(context, messages, [], PARAMS, SimpleNamespace(context_window=8000))
    metrics = context.research.progress()['notebook']['context']
    assert metrics['omitted_sources']
    assert len(metrics['restored_sources']) < 3
    assert 'Notes alone do not substantiate' in result[-1]['content']


@pytest.mark.parametrize('change', ['expiry', 'deletion', 'domain', 'attempt', 'owner', 'stop'])
def test_stale_sources_withdraw_notes_and_original_results(context, change):
    ctx = context
    messages = history(ctx)
    assert notebook.update(ctx, notes()) is None
    messages.append({'role': 'assistant', 'content': 'Paraphrase of private evidence.'})
    original = deepcopy(messages)
    row = ctx.db.query(Source).filter_by(conversation_id=ctx.conversation_id, number=1).one()
    if change == 'expiry':
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    elif change == 'deletion':
        row.excerpt = None
        row.deleted_at = datetime.now(timezone.utc)
    elif change == 'domain':
        ctx.research.state['options']['domains'] = ['other.example']
    elif change == 'attempt':
        ctx.accounting.turn_id = 'another-attempt'
    elif change == 'owner':
        ctx.user_id = 'someone-else'
    else:
        ctx.cancel_event.set()
    ctx.db.commit()
    projected = notebook.prepare(ctx, messages, [], PARAMS, None)
    text = json.dumps(projected)
    assert 'Unique evidence 1' not in text and 'Paraphrase of private evidence' not in text
    assert 'Funding differs' not in text and 'same period' not in text
    assert ctx.research.progress()['notebook']['unavailable_sources'] == ['S1']
    assert messages == original
    assert notebook.update(ctx, notes())


def test_notebook_requires_valid_current_evidence_and_gathering(context):
    ctx = context
    assert notebook.update(ctx, notes())
    capture(ctx)
    assert notebook.update(ctx, notes(('S99',)))
    invalid = notes()
    invalid['findings'][0]['text'] = 'x' * 501
    assert notebook.update(ctx, invalid)
    assert notebook.update(ctx, notes()) is None
    assert notebook.update(ctx, notes()) is None
    assert ctx.research.progress()['notebook']['revision'] == 2
    counts = (ctx.research.state['reads'], ctx.research.state['searches'])
    assert ctx.research.admit(notebook.NAME, notes()) is None
    assert counts == (ctx.research.state['reads'], ctx.research.state['searches'])
    ctx.research.state['phase'] = 'synthesize'
    assert notebook.update(ctx, notes())


def test_document_selection_reauthorized_for_notes_and_synthesis(context):
    ctx = context
    doc = Document(user_id='local', filename='Funding.txt', status='ready')
    ctx.db.add(doc)
    ctx.db.flush()
    chunk = DocChunk(document_id=doc.id, ordinal=0, text='Document evidence: 45 million.')
    ctx.db.add(chunk)
    ctx.db.commit()
    try:
        sources.capture(ctx.db, conversation_id=ctx.conversation_id, user_id='local',
                        turn_id=ctx.accounting.turn_id, document_id=doc.id, chunk_id=chunk.id)
        ctx.research.state['options']['scope'] = 'documents'
        ctx.research.state['document_ids'] = [doc.id]
        ctx.document_scope = {doc.id}
        assert notebook.update(ctx, notes()) is None
        ctx.document_scope = set()
        assert notebook.evidence(ctx) == {}
        assert notebook.update(ctx, notes())
        ctx.document_scope = {doc.id}
        doc.user_id = 'another-owner'
        ctx.db.commit()
        assert notebook.evidence(ctx) == {}
    finally:
        ctx.db.delete(doc)
        ctx.db.commit()


@pytest.mark.parametrize('action', ['redact', 'block'])
def test_visible_notebook_respects_output_rules(context, monkeypatch, action):
    from app.guardrails import get_rules
    capture(context)
    rules = get_rules('output', {'enabled': True, 'output_action': action,
        'builtin': {'email': True}, 'custom_patterns': []})
    monkeypatch.setattr('app.guardrails.get_rules', lambda direction: rules)
    content = notes()
    content['findings'][0]['text'] = 'Contact person@example.org'
    error = notebook.update(context, content)
    if action == 'block':
        assert error and 'notebook' not in context.research.state
    else:
        assert error is None
        assert 'person@example.org' not in json.dumps(context.research.progress())
        assert '[EMAIL]' in json.dumps(context.research.progress())


def test_real_harness_notebook_survives_approval_and_restores_evidence(db, monkeypatch):
    provider = ResearchProvider([
        [ToolCall('read', 'web_fetch', {'url': 'https://example.org/1'})],
        [ToolCall('note', notebook.NAME, notes())],
        [ToolCall('search', 'web_search', {'query': 'conflicting report'})],
    ])
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    agent.gate.auto_approve = False
    monkeypatch.setattr(agent.gate, 'decide', lambda name: 'ask' if name == 'web_search' else 'auto')
    monkeypatch.setattr(REGISTRY.get('web_fetch'), 'run', lambda ctx, **kw: ToolResult(capture(ctx)))
    events = parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Compare funding'}]))
    pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
    saved = json.loads(json.dumps(pending.state))
    assert saved['research']['notebook']['revision'] == 1
    assert any(e.get('notebook', {}).get('revision') == 1 for e in events if e.get('notebook'))
    assert saved['tool_steps'][0]['content'].count('Unique evidence') > 50
    resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                           {'max_tool_rounds': 12, **PARAMS}, 'test', provider.model,
                           accounting=agent.accounting)
    monkeypatch.setattr(REGISTRY.get('web_search'), 'run', lambda *a, **kw: ToolResult('No other reports.'))
    parse(resumed.resume(saved, {'search': 'allow'}))
    msg = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert msg.usage['research']['notebook']['revision'] == 1
    assert msg.usage['research']['notebook']['context']['restored_sources'] == ['S1']
    assert 'Retained evidence for final writing' in json.dumps(provider.seen[-1]['messages'])
    assert len(msg.tool_calls) == 3
    assert len([s for s in msg.tool_calls if s['name'] == 'web_fetch']) == 1


def test_stop_after_notebook_update_prevents_more_model_calls(db, monkeypatch):
    provider = ResearchProvider([[ToolCall('read', 'web_fetch', {'url': 'https://example.org/1'})],
                                 [ToolCall('note', notebook.NAME, notes())]])
    cancel = threading.Event()
    agent, conv = session(db, provider, cancel_event=cancel)
    monkeypatch.setattr(REGISTRY.get('web_fetch'), 'run', lambda ctx, **kw: ToolResult(capture(ctx)))
    original = REGISTRY.get(notebook.NAME).run

    def stop(ctx, **kwargs):
        result = original(ctx, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(REGISTRY.get(notebook.NAME), 'run', stop)
    parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Compare funding'}]))
    assert len(provider.seen) == 3
    msg = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert msg.usage['research']['phase'] == 'cancelled'
    assert msg.usage['research']['notebook']['revision'] == 1


@pytest.mark.parametrize('pause', [False, True])
def test_notebook_cannot_cover_unseen_sibling_read_results(db, monkeypatch, pause):
    provider = ResearchProvider([
        [ToolCall('read1', 'web_fetch', {'url': 'https://example.org/1'})],
        [ToolCall('read2', 'web_fetch', {'url': 'https://example.org/2'}),
         ToolCall('note', notebook.NAME, notes())],
    ])
    agent, conv = session(db, provider, {'scope': 'web', 'depth': 'standard', 'domains': []})
    monkeypatch.setattr(REGISTRY.get('web_fetch'), 'run',
                        lambda ctx, url: ToolResult(capture(ctx, int(url.rsplit('/', 1)[1]))))
    if pause:
        agent.gate.auto_approve = False
        monkeypatch.setattr(agent.gate, 'decide', lambda name: 'ask' if name == notebook.NAME else 'auto')
    parse(agent.run([{'role': 'system', 'content': 'Research'}, {'role': 'user', 'content': 'Compare funding'}]))
    if pause:
        pending = db.query(PendingApproval).filter_by(conversation_id=conv.id).one()
        # The second read has run, but its result was not available when the model wrote notes.
        assert 'read2' in pending.state['research']['completed_calls']
        resumed = AgentSession(db, conv, provider, REGISTRY, PermissionGate(db, REGISTRY, auto_approve=True),
                               {'max_tool_rounds': 12, **PARAMS}, 'test', provider.model,
                               accounting=agent.accounting)
        parse(resumed.resume(pending.state, {'note': 'allow'}))
        agent = resumed
    assert 'read1' in agent.research.state['notebook']['covered_calls']
    assert 'read2' not in agent.research.state['notebook']['covered_calls']
    for call in provider.seen[3:]:
        assert any(m.get('tool_call_id') == 'read2' and 'Unique evidence 2' in m['content']
                   for m in call['messages'])


def test_notebook_not_advertised_in_normal_chat(db):
    provider = ResearchProvider()
    agent, _ = session(db, provider)
    agent.research = agent.ctx.research = None
    agent.allowed_tools = set(REGISTRY.names())
    parse(agent.run([{'role': 'system', 'content': 'planning step'}, {'role': 'user', 'content': 'Hello'}]))
    assert all(notebook.NAME not in call['tools'] for call in provider.seen)
