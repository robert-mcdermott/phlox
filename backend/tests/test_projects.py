"""Private projects, context exclusions, actual outbound evidence, and durable preparation."""
from copy import deepcopy
import json
import threading
import uuid

import pytest

from app.models import ContextRecord, Conversation, DocChunk, Document, Memory, Project, User
from app.providers.base import StreamDelta, ToolCall


class Provider:
    model = 'test-model'
    profile_name = 'test'
    supports_tools = True

    def __init__(self):
        self.seen = []
        self.calls = []

    def stream(self, messages, tools, params):
        self.seen.append(deepcopy(messages))
        self.tool_names = {t.name for t in tools}
        yield StreamDelta(type='usage', usage={'input': 20, 'output': 5, 'total': 25})
        if self.calls:
            yield StreamDelta(type='tool_calls', tool_calls=self.calls.pop(0))
        else:
            yield StreamDelta(type='text', text='Reviewed the project evidence [S1].')
        yield StreamDelta(type='done', stop_reason='stop')


@pytest.fixture
def setup(db, monkeypatch):
    owner = 'project-' + uuid.uuid4().hex[:12]
    user = User(id=owner, username=owner, role='admin', is_active=True, must_change_password=False, password_hash='test')
    db.add(user)
    docs = []
    for i in range(2):
        doc = Document(user_id=owner, filename=f'Project policy {i}.txt', status='ready')
        db.add(doc)
        db.flush()
        db.add(DocChunk(document_id=doc.id, ordinal=0, text=f'Unique policy {i}: the retention period is {30 + i} days.'))
        docs.append(doc)
    project = Project(user_id=owner, name='Home infrastructure', instructions='Prefer repairable equipment.', document_ids=[docs[0].id])
    db.add(project)
    db.commit()
    from app.main import app
    from app.auth.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: user
    provider = Provider()
    monkeypatch.setattr('app.routers.chat.build_provider', lambda *args: provider)
    monkeypatch.setattr('app.routers.chat._build_fallback', lambda *args: None)
    monkeypatch.setattr('app.memory.retrieve_memories', lambda *args, **kwargs: [])
    yield user, project, docs, provider
    app.dependency_overrides.pop(get_current_user, None)
    db.rollback()
    for conv in db.query(Conversation).filter_by(user_id=owner):
        db.delete(conv)
    for row in db.query(Project).filter_by(user_id=owner):
        db.delete(row)
    for row in db.query(Document).filter_by(user_id=owner):
        db.delete(row)
    for row in db.query(Memory).filter_by(user_id=owner):
        db.delete(row)
    db.delete(user)
    db.commit()


def send(client, **body):
    response = client.post('/api/chat', json={'message': 'What is the retention policy?', **body})
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    assert not [e for e in events if e['type'] == 'error'], events
    return next(e['id'] for e in events if e['type'] == 'conversation'), events


def test_project_crud_owner_isolation_and_optimistic_update(client, db, setup):
    user, project, docs, _ = setup
    foreign = Project(user_id='foreign', name='Private', document_ids=[])
    db.add(foreign)
    db.commit()
    try:
        assert client.get(f'/api/projects/{foreign.id}').status_code == 404
        assert foreign.id not in [p['id'] for p in client.get('/api/projects').json()]
        assert client.post('/api/chat', json={'project_id': foreign.id, 'message': 'read'}).status_code == 404
        row = client.get(f'/api/projects/{project.id}').json()
        assert client.put(f'/api/projects/{project.id}', json={**row, 'revision': 99}).status_code == 409
        saved = client.put(f'/api/projects/{project.id}', json={**row, 'name': 'Revised'}).json()
        assert saved['revision'] == 2 and saved['name'] == 'Revised'
        assert client.post('/api/projects', json={'name': 'Bad', 'document_ids': ['not-owned']}).status_code == 404
        assert client.post('/api/projects', json={'name': '  '}).status_code == 422
    finally:
        db.delete(foreign)
        db.commit()


def test_two_chats_share_only_selected_knowledge_and_record_actual_passages(client, db, setup):
    user, project, docs, provider = setup
    for _ in range(2):
        conv_id, _ = send(client, project_id=project.id)
        text = json.dumps(provider.seen[-1])
        assert 'Prefer repairable equipment' in text and 'Unique policy 0' in text
        assert 'Unique policy 1' not in text and 'save_memory' not in provider.tool_names
        detail = client.get(f'/api/conversations/{conv_id}').json()
        assert detail['project_id'] == project.id
        turn = detail['messages'][-1]['usage']['turn_id']
        record = client.get(f'/api/conversations/{conv_id}/context/{turn}').json()
        assert record['sources'][0]['excerpt'].startswith('Unique policy 0')
        assert record['calls'][0]['project_instructions_present']
        assert record['memories'] == []
    assert len(client.get(f'/api/projects/{project.id}').json()['conversations']) == 2
    ordinary, _ = send(client)
    assert 'Unique policy 0' not in json.dumps(provider.seen[-1])
    assert client.get(f'/api/conversations/{ordinary}/context/{turn}').status_code == 404


def test_exclusions_cannot_reenter_via_history_or_model_search_arguments(client, db, setup):
    _, project, docs, provider = setup
    conv, _ = send(client, project_id=project.id, message='SENSITIVE PREVIOUS QUESTION')
    provider.calls = [[ToolCall('search-excluded', 'search_documents', {'query': 'policy', 'document_ids': [docs[0].id, docs[1].id]})]]
    send(client, conversation_id=conv, document_search=True, context={'project_instructions': False, 'excluded_document_ids': [docs[0].id]})
    text = json.dumps(provider.seen[-1])
    assert 'Unique policy' not in text and 'SENSITIVE PREVIOUS QUESTION' not in text
    assert 'Prefer repairable equipment' not in text
    assert 'No documents are included' in text
    visible = client.get(f'/api/conversations/{conv}').json()['messages']
    assert visible[0]['content'] == 'SENSITIVE PREVIOUS QUESTION'


def test_project_binding_ignores_request_spoof_and_move_starts_fresh_context(client, db, setup):
    user, project, docs, provider = setup
    other = Project(user_id=user.id, name='Other', instructions='Use the other instructions.', document_ids=[docs[1].id])
    db.add(other)
    db.commit()
    conv, _ = send(client, project_id=project.id)
    send(client, conversation_id=conv, project_id=other.id)
    assert 'Unique policy 1' not in json.dumps(provider.seen[-1])
    assert client.patch(f'/api/conversations/{conv}', json={'project_id': other.id}).status_code == 200
    send(client, conversation_id=conv)
    text = json.dumps(provider.seen[-1])
    assert 'Unique policy 1' in text and 'Unique policy 0' not in text


def test_memory_opt_in_exclusion_and_deleted_record_redaction(client, db, setup, monkeypatch):
    user, project, _, provider = setup
    mem = Memory(user_id=user.id, content='PERSONAL PRIVATE PREFERENCE', kind='preference')
    db.add(mem)
    db.commit()
    monkeypatch.setattr('app.memory.retrieve_memories', lambda *a, **kw: [mem])
    conv, _ = send(client, project_id=project.id)
    assert mem.content not in json.dumps(provider.seen[-1])
    send(client, conversation_id=conv, context={'memory': True})
    assert mem.content in json.dumps(provider.seen[-1])
    turn = client.get(f'/api/conversations/{conv}').json()['messages'][-1]['usage']['turn_id']
    record = client.get(f'/api/conversations/{conv}/context/{turn}').json()
    assert record['memories'][0]['supplied']
    send(client, conversation_id=conv, context={'memory': True, 'excluded_memory_ids': [mem.id]})
    assert mem.content not in json.dumps(provider.seen[-1])
    db.delete(mem)
    db.commit()
    record = client.get(f'/api/conversations/{conv}/context/{turn}').json()
    assert record['memories'][0]['content'] is None


def test_archive_blocks_new_turns_but_preserves_overview_and_transcript(client, setup):
    _, project, _, _ = setup
    conv, _ = send(client, project_id=project.id)
    row = client.get(f'/api/projects/{project.id}').json()
    assert client.put(f'/api/projects/{project.id}', json={**row, 'archived': True}).status_code == 200
    assert client.post('/api/chat', json={'conversation_id': conv, 'message': 'more'}).status_code == 409
    assert client.get(f'/api/conversations/{conv}').json()['messages']
    archived = client.get(f'/api/projects/{project.id}').json()
    assert client.put(f'/api/projects/{project.id}', json={**archived, 'archived': False}).status_code == 200
    send(client, conversation_id=conv)


def test_preview_is_read_only_and_shows_destination_options(client, db, setup):
    user, project, docs, _ = setup
    before = db.query(Conversation).filter_by(user_id=user.id).count()
    response = client.post('/api/context/preview', json={'project_id': project.id})
    assert response.status_code == 200
    preview = response.json()
    assert preview['memory_enabled'] is False and preview['documents'][0]['selected']
    assert preview['destination'] == 'localhost' and preview['model'] == 'test-model'
    assert db.query(Conversation).filter_by(user_id=user.id).count() == before


def test_prepared_project_change_blocks_dispatch_and_approval_resume(client, db, setup):
    user, project, _, provider = setup
    from app.routers.chat import prepare_chat
    from app.schemas import ChatRequest
    stream = prepare_chat(ChatRequest(project_id=project.id, message='question'), db, user, threading.Event())
    project.revision += 1
    db.commit()
    events = list(stream)
    assert not provider.seen
    assert any('error' in e for e in events)


def test_context_records_cascade_with_conversation(client, db, setup):
    _, project, _, _ = setup
    conv, _ = send(client, project_id=project.id)
    assert db.query(ContextRecord).filter_by(conversation_id=conv).count() == 1
    assert client.delete(f'/api/conversations/{conv}').status_code == 200
    assert db.query(ContextRecord).filter_by(conversation_id=conv).count() == 0


def test_approval_restores_document_scope_and_rejects_changed_project(client, db, setup):
    _, project, docs, provider = setup
    from app.models import PendingApproval
    provider.calls = [[ToolCall('save', 'write_file', {'path': 'project.txt', 'content': 'Approved result'})]]
    conv, events = send(client, project_id=project.id)
    pending_id = next(e['pending_id'] for e in events if e['type'] == 'approval_request')
    pending = db.get(PendingApproval, pending_id)
    assert pending.state['document_scope'] == [docs[0].id]
    row = client.get(f'/api/projects/{project.id}').json()
    assert client.put(f'/api/projects/{project.id}', json={**row, 'instructions': 'Changed'}).status_code == 409
    project.revision += 1  # Simulate another process changing the project after preparation.
    db.commit()
    assert client.post('/api/chat/approve', json={'pending_id': pending_id, 'decisions': {'save': 'allow'}}).status_code == 409
    project.revision -= 1
    db.commit()
    provider.calls = [[ToolCall('foreign-doc', 'search_documents', {'query': 'policy', 'document_ids': [docs[1].id]})]]
    response = client.post('/api/chat/approve', json={'pending_id': pending_id, 'decisions': {'save': 'deny'}})
    assert response.status_code == 200
    assert 'Unique policy 1' not in json.dumps(provider.seen[-1])
    assert 'No documents are included' in json.dumps(provider.seen[-1])


def test_durable_new_project_chat_and_context_record_replay(client, db, setup, monkeypatch):
    from types import SimpleNamespace
    from app import runs
    _, project, _, provider = setup
    monkeypatch.setattr('app.config.runs_enabled', lambda: True)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: True)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    response = client.post('/api/runs', json={'project_id': project.id, 'message': 'Find the policy'},
                           headers={'Idempotency-Key': uuid.uuid4().hex})
    assert response.status_code == 200, response.text
    run = response.json()
    worker.step()
    conv = client.get(f"/api/conversations/{run['conversation_id']}").json()
    assert conv['project_id'] == project.id and conv['messages'][-1]['role'] == 'assistant'
    assert 'Unique policy 0' in json.dumps(provider.seen[-1])
    record = client.get(f"/api/conversations/{conv['id']}/context/{run['id']}").json()
    assert record['sources'][0]['available']
    assert '"type": "done"' in client.get(f"/api/runs/{run['id']}/events").text


def test_regeneration_preserves_exclusions(client, db, setup):
    _, project, docs, provider = setup
    conv, _ = send(client, project_id=project.id, context={'project_instructions': False, 'excluded_document_ids': [docs[0].id]})
    messages = client.get(f'/api/conversations/{conv}').json()['messages']
    assert client.delete(f"/api/conversations/{conv}/messages/{messages[-1]['id']}").status_code == 200
    send(client, conversation_id=conv, regenerate=True)
    assert 'Unique policy 0' not in json.dumps(provider.seen[-1])
    assert 'Prefer repairable equipment' not in json.dumps(provider.seen[-1])


def test_move_back_to_ordinary_chat_does_not_reintroduce_old_segments(client, db, setup):
    _, project, _, provider = setup
    conv, _ = send(client, message='OLD ORDINARY CONTEXT')
    assert client.patch(f'/api/conversations/{conv}', json={'project_id': project.id}).status_code == 200
    send(client, conversation_id=conv, message='PROJECT-ONLY CONTEXT')
    assert client.patch(f'/api/conversations/{conv}', json={'project_id': None}).status_code == 200
    send(client, conversation_id=conv, message='A fresh ordinary turn')
    text = json.dumps(provider.seen[-1])
    assert 'OLD ORDINARY CONTEXT' not in text and 'PROJECT-ONLY CONTEXT' not in text


def test_regenerate_does_not_turn_unlinked_project_knowledge_into_explicit_attachments(client, db, setup):
    _, project, _, provider = setup
    conv, _ = send(client, project_id=project.id)
    row = client.get(f'/api/projects/{project.id}').json()
    assert client.put(f'/api/projects/{project.id}', json={**row, 'document_ids': []}).status_code == 200
    messages = client.get(f'/api/conversations/{conv}').json()['messages']
    assert client.delete(f"/api/conversations/{conv}/messages/{messages[-1]['id']}").status_code == 200
    send(client, conversation_id=conv, regenerate=True)
    assert 'Unique policy 0' not in json.dumps(provider.seen[-1])


def test_child_document_scope_is_inherited(db, setup, monkeypatch):
    from app.agent.tools.base import ToolContext
    from app.agent.tools.subagent import SpawnSubagent
    from app.agent.harness import AgentSession
    from app.workspace.manager import workspace_dir
    user, project, docs, _ = setup
    conv = Conversation(user_id=user.id, project_id=project.id)
    db.add(conv)
    db.commit()
    seen = []
    monkeypatch.setattr('app.agent.tools.subagent.build_provider', lambda *a: Provider(), raising=False)
    monkeypatch.setattr('app.providers.registry.build_provider', lambda *a: Provider())
    def run(session, messages):
        seen.append(session.ctx.document_scope)
        yield 'data: {"type":"token","content":"done"}\n\n'
    monkeypatch.setattr(AgentSession, 'run', run)
    ctx = ToolContext(conversation_id=conv.id, user_id=user.id, workspace=workspace_dir(conv.id), db=db,
                      runner=None, profile='test', model='test-model', params={},
                      allowed_tools=frozenset({'search_documents'}), document_scope=[docs[0].id])
    result = SpawnSubagent().run(ctx, task='Search project knowledge', read_only=True)
    assert not result.is_error and seen == [[docs[0].id]]
