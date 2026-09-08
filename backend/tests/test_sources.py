"""Document evidence identity, visibility, persistence and real scripted chat contracts."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import inspect

from app import sources
from app.agent.registry import ToolRegistry
from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.agent.tools.docs import SearchDocuments
from app.database import SessionLocal
from app.models import Assistant, Conversation, DocChunk, Document, Message, Source, SourceUse
from app.providers.base import StreamDelta, ToolCall


@pytest.fixture
def evidence(db):
    conv = Conversation(user_id='local', title='Citation test')
    db.add(conv)
    db.flush()
    docs, chunks = [], []
    for number in range(2):
        doc = Document(user_id='local', filename=f'Policy {number + 1}.txt', status='ready')
        db.add(doc)
        db.flush()
        chunk = DocChunk(document_id=doc.id, ordinal=number, text=f'Policy {number + 1}: retain the original evidence.')
        db.add(chunk)
        docs.append(doc)
        chunks.append(chunk)
    db.commit()
    item = SimpleNamespace(conv=conv, docs=docs, chunks=chunks, conversations=[conv], assistants=[])
    yield item
    db.rollback()
    for conv in item.conversations:
        current = db.get(Conversation, inspect(conv).identity[0])
        if current:
            db.delete(current)
    for doc in item.docs:
        current = db.get(Document, inspect(doc).identity[0])
        if current:
            db.delete(current)
    for assistant in item.assistants:
        db.delete(assistant)
    db.commit()


def capture(db, evidence, index=0, turn='turn', **kwargs):
    return sources.capture(db, conversation_id=evidence.conv.id, user_id='local', turn_id=turn,
                           document_id=evidence.docs[index].id, chunk_id=evidence.chunks[index].id,
                           query='why retain evidence?', **kwargs)


def frames(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_stable_labels_across_searches_turns_and_changed_content(db, evidence):
    first, block = capture(db, evidence)
    second, _ = capture(db, evidence, 1)
    assert first['label'] == 'S1' and second['label'] == 'S2'
    assert evidence.chunks[0].text in block
    assert capture(db, evidence, turn='next')[0] == first
    assert db.query(Source).filter_by(conversation_id=evidence.conv.id).count() == 2
    evidence.chunks[0].text = 'A revised policy.'
    db.commit()
    changed, _ = capture(db, evidence)
    assert changed['label'] == 'S3' and changed['source_id'] != first['source_id']
    old = sources.inspect_source(db, evidence.conv, first['source_id'])
    assert old['changed'] and old['excerpt'].startswith('Policy 1:')
    assert sources.bind('One [S1], two [S2], invented [S999], repeat [S1].',
                        sources.catalog(db, 'turn', evidence.conv.id)) == [first, second, {'label': 'S999', 'source_id': None}]
    assert sources.bind('[S1]', sources.catalog(db, 'unrelated-turn', evidence.conv.id))[0]['source_id'] is None


def test_concurrent_children_reuse_one_source(db, evidence):
    barrier = threading.Barrier(2)
    conv_id, doc_id, chunk_id = evidence.conv.id, evidence.docs[0].id, evidence.chunks[0].id

    def child(_):
        barrier.wait()
        with SessionLocal() as own:
            return sources.capture(own, conversation_id=conv_id, user_id='local', turn_id='child-turn',
                                   document_id=doc_id, chunk_id=chunk_id)[0]
    with ThreadPoolExecutor(max_workers=2) as pool:
        refs = list(pool.map(child, range(2)))
    assert refs[0] == refs[1]
    assert db.query(SourceUse).filter_by(turn_id='child-turn').count() == 1


def test_limits_truncation_and_expiry_are_explicit(db, evidence, monkeypatch):
    evidence.chunks[0].text = 'long evidence ' * 1000
    db.commit()
    ref, text = capture(db, evidence)
    row = db.get(Source, ref['source_id'])
    assert len(row.excerpt) == sources.MAX_EXCERPT_CHARS
    assert row.location['truncated'] and 'shortened' in text
    monkeypatch.setattr(sources, 'MAX_TURN_SOURCES', 1)
    assert capture(db, evidence, 1) is None
    assert capture(db, evidence)[0] == ref  # Reuse does not spend another slot.
    monkeypatch.setattr(sources, 'MAX_CONVERSATION_SOURCES', 1)
    assert capture(db, evidence, 1, turn='new') is None
    row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert not sources.inspect_source(db, evidence.conv, row.id)['available']
    sources.cleanup(db)
    db.expire_all()
    assert row.excerpt is None and row.title is None
    assert db.get(SourceUse, ('turn', row.id)).query == ''
    # Expired identity can be recaptured only from the currently authorized SQL document.
    assert capture(db, evidence)[0] == ref


def test_foreign_source_ids_and_stale_vector_payloads_do_not_leak(client, db, evidence, monkeypatch, tmp_path):
    ref, _ = capture(db, evidence)
    other = Conversation(user_id='foreign-user', title='private')
    db.add(other)
    db.commit()
    evidence.conversations.append(other)
    assert client.get(f'/api/conversations/{other.id}/sources/{ref["source_id"]}').status_code == 404
    from app.main import app
    from app.auth.deps import get_current_user
    from app.models import User
    app.dependency_overrides[get_current_user] = lambda: User(id='foreign-user', username='admin', role='admin')
    try:
        assert client.get(f'/api/conversations/{evidence.conv.id}/sources/{ref["source_id"]}').status_code == 404
        assert client.get(f'/api/conversations/{evidence.conv.id}/export').status_code == 404
        assert client.get(f'/api/conversations/{other.id}/sources/{ref["source_id"]}').status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user)
    evidence.docs[0].user_id = 'foreign-user'
    db.commit()
    monkeypatch.setattr('app.agent.tools.docs.search_chunks', lambda *a, **k: [{
        'document_id': evidence.docs[0].id, 'chunk_id': evidence.chunks[0].id,
        'text': 'STALE PRIVATE VECTOR PAYLOAD', 'filename': 'secret.txt', 'ordinal': 0,
    }])
    ctx = ToolContext(conversation_id=evidence.conv.id, workspace=tmp_path, db=db, runner=None, user_id='local')
    output = SearchDocuments().run(ctx, query='private').content
    assert 'PRIVATE' not in output and 'secret.txt' not in output and 'omitted' in output
    unavailable = client.get(f'/api/conversations/{evidence.conv.id}/sources/{ref["source_id"]}').json()
    assert not unavailable['available'] and 'title' not in unavailable and 'excerpt' not in unavailable


def test_assistant_visibility_is_rechecked_for_panel_and_export(client, db, evidence):
    assistant = Assistant(name='KB', created_by='other', visibility='public', is_active=True)
    db.add(assistant)
    db.flush()
    evidence.assistants.append(assistant)
    evidence.conv.assistant_id = assistant.id
    evidence.docs[0].user_id = None
    evidence.docs[0].assistant_id = assistant.id
    db.commit()
    ref, _ = capture(db, evidence, assistant_id=assistant.id)
    db.add(Message(conversation_id=evidence.conv.id, role='assistant', content='Supported [S1].', citations=[ref]))
    db.commit()
    endpoint = f'/api/conversations/{evidence.conv.id}/sources/{ref["source_id"]}'
    assert client.get(endpoint).json()['available']
    assistant.visibility = 'private'
    db.commit()
    assert client.get(endpoint).json()['available'] is False
    exported = client.get(f'/api/conversations/{evidence.conv.id}/export').json()['markdown']
    assert 'Source unavailable' in exported and evidence.chunks[0].text not in exported
    assert evidence.docs[0].filename not in exported


def test_document_deletion_purges_snapshot_and_query_without_renumbering(client, db, evidence):
    ref, _ = capture(db, evidence)
    row = db.get(Source, ref['source_id'])
    assert client.delete(f'/api/documents/{evidence.docs[0].id}').status_code == 200
    db.expire_all()
    assert row.excerpt is None and row.document_id is None and row.title is None
    assert db.get(SourceUse, ('turn', row.id)).query == ''
    assert not client.get(f'/api/conversations/{evidence.conv.id}/sources/{row.id}').json()['available']
    assert capture(db, evidence, 1)[0]['label'] == 'S2'


@pytest.mark.parametrize('durable', [False, True])
def test_direct_refs_search_approval_recovery_and_final_citations(client, db, evidence, monkeypatch, durable):
    from app import runs
    from app.routers import chat

    registry = ToolRegistry()
    registry.register(SearchDocuments())

    class Pause(Tool):
        name = 'citation_pause'
        default_permission = 'ask'

        def run(self, ctx):
            return ToolResult(content='Approved')
    registry.register(Pause())

    class Provider:
        supports_tools = True
        model = 'citation-model'

        def stream(self, messages, tools, params):
            count = sum(m['role'] == 'tool' for m in messages)
            yield StreamDelta(type='usage', usage={'input': 4, 'output': 3, 'total': 7})
            if count < 2:
                yield StreamDelta(type='tool_calls', tool_calls=[ToolCall(f'search-{count}', 'search_documents', {'query': str(count)})])
            elif count == 2:
                yield StreamDelta(type='text', text='Found both [S1] [S2].')
                yield StreamDelta(type='tool_calls', tool_calls=[ToolCall('pause', 'citation_pause', {})])
            else:
                yield StreamDelta(type='text', text='Confirmed [S1] and [S2]. Unknown [S99].')
                yield StreamDelta(type='done')

    def search(db, query, **kwargs):
        index = int(query)
        return [{'document_id': evidence.docs[index].id, 'chunk_id': evidence.chunks[index].id,
                 'text': 'vector text is not authoritative', 'ordinal': index, 'filename': 'vector title'}]
    monkeypatch.setattr('app.agent.tools.docs.search_chunks', search)
    monkeypatch.setattr('app.rag.retrieve.search_chunks', lambda *a, **k: [])
    monkeypatch.setattr(chat, 'REGISTRY', registry)
    monkeypatch.setattr(chat, 'build_provider', lambda *a: Provider())
    monkeypatch.setattr(chat, '_build_fallback', lambda *a: None)
    monkeypatch.setattr('app.config.runs_enabled', lambda: durable)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: durable)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    payload = {'conversation_id': evidence.conv.id, 'message': 'Use both documents',
               'document_ids': [evidence.docs[0].id], 'document_search': True}
    if durable:
        run = client.post('/api/runs', headers={'Idempotency-Key': uuid.uuid4().hex}, json=payload).json()
        worker.step()
        initial = frames(client.get(f'/api/runs/{run["id"]}/events'))
    else:
        initial = frames(client.post('/api/chat', json=payload))
    assert [e for e in initial if e['type'] == 'sources']
    pending = client.get(f'/api/chat/approvals/{evidence.conv.id}').json()[0]
    assert [ref['label'] for ref in pending['sources']] == ['S1', 'S2']
    decisions = {'pending_id': pending['pending_id'], 'decisions': {'pause': 'allow'}}
    if durable:
        assert client.post(f'/api/runs/{run["id"]}/approve', json=decisions).status_code == 200
        worker.step()
        resumed = frames(client.get(f'/api/runs/{run["id"]}/events'))
    else:
        resumed = frames(client.post('/api/chat/approve', json=decisions))
    assert any(e['type'] == 'done' for e in resumed)
    message = client.get(f'/api/conversations/{evidence.conv.id}').json()['messages'][-1]
    refs = message['citations']
    assert [ref['label'] for ref in refs] == ['S1', 'S2', 'S99']
    assert refs[2]['source_id'] is None
    assert db.query(Source).filter_by(conversation_id=evidence.conv.id).count() == 2
    for ref in refs[:2]:
        inspected = client.get(f'/api/conversations/{evidence.conv.id}/sources/{ref["source_id"]}').json()
        assert inspected['available'] and 'retain the original evidence' in inspected['excerpt']
    exported = client.get(f'/api/conversations/{evidence.conv.id}/export').json()['markdown']
    assert 'Policy 1.txt' in exported and 'Policy 2.txt' in exported and 'Unverified reference' in exported
    assert 'source_id' not in exported and 'Bearer' not in exported


def test_conversation_deletion_removes_evidence_and_use_records(client, db, evidence):
    ref, _ = capture(db, evidence)
    assert client.delete(f'/api/conversations/{evidence.conv.id}').status_code == 200
    assert db.get(Source, ref['source_id'], populate_existing=True) is None
    assert db.get(SourceUse, ('turn', ref['source_id']), populate_existing=True) is None


@pytest.mark.parametrize('whole_assistant', [False, True])
def test_assistant_deletion_paths_purge_retained_evidence(client, db, evidence, whole_assistant):
    assistant = Assistant(name='Delete KB', created_by='local', visibility='public', is_active=True)
    db.add(assistant)
    db.flush()
    assistant_id = assistant.id
    evidence.conv.assistant_id = assistant_id
    evidence.docs[0].user_id = None
    evidence.docs[0].assistant_id = assistant_id
    db.commit()
    ref, _ = capture(db, evidence, assistant_id=assistant_id)
    path = f'/api/assistants/{assistant_id}'
    if not whole_assistant:
        path += f'/documents/{evidence.docs[0].id}'
    try:
        assert client.delete(path).status_code == 200
        db.expire_all()
        row = db.get(Source, ref['source_id'])
        assert row.excerpt is None and row.title is None and row.document_id is None
        assert db.get(SourceUse, ('turn', ref['source_id'])).query == ''
    finally:
        current = db.get(Assistant, assistant_id, populate_existing=True)
        if current:
            db.delete(current)
            db.commit()


def test_owner_data_purge_removes_sources(db, evidence):
    from app.auth.service import delete_user_data
    owner = 'source-owner-' + uuid.uuid4().hex
    evidence.conv.user_id = owner
    for doc in evidence.docs:
        doc.user_id = owner
    db.commit()
    ref, _ = sources.capture(db, conversation_id=evidence.conv.id, user_id=owner,
                             turn_id='owner-turn', document_id=evidence.docs[0].id,
                             chunk_id=evidence.chunks[0].id)
    delete_user_data(db, owner)
    assert db.get(Source, ref['source_id'], populate_existing=True) is None
    assert db.get(SourceUse, ('owner-turn', ref['source_id']), populate_existing=True) is None


def test_export_marks_unverified_reuse_in_its_answer_and_contains_untrusted_excerpt(db, evidence):
    evidence.chunks[0].text = '```\n# Fake heading\n<script>alert(1)</script>\n```'
    db.commit()
    ref, _ = capture(db, evidence)
    db.add(Message(conversation_id=evidence.conv.id, role='assistant', content='Original [S1].', citations=[ref]))
    db.add(Message(conversation_id=evidence.conv.id, role='assistant', content='Unretrieved reuse [S1].',
                   citations=[{'label': 'S1', 'source_id': None}]))
    db.commit()
    exported = sources.export_markdown(db, evidence.conv)
    assert exported.count('Unverified references in this answer: [S1]') == 1
    assert '````text\n```\n# Fake heading' in exported
    assert 'Unretrieved reuse [S1].' in exported
    assert exported.count('[S1] Policy 1.txt') == 1
