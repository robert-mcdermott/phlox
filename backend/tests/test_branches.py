"""Saved alternatives must preserve evidence and isolate the provider's selected history."""
import json
import threading

from app import branches
from app.models import Conversation, Message, ContextRecord
from test_projects import setup as setup, send


def detail(client, cid):
    return client.get(f'/api/conversations/{cid}').json()


def choose(client, cid, message_id):
    row = detail(client, cid)
    response = client.post(f'/api/conversations/{cid}/alternatives/{message_id}',
                           json={'expected_leaf_id': row['active_leaf_id']})
    assert response.status_code == 200, response.text
    return response.json()


def test_regenerate_preserves_answer_evidence_usage_and_continuations(client, db, setup):
    _, project, _, provider = setup
    cid, _ = send(client, project_id=project.id, message='Original question')
    original = detail(client, cid)['messages'][-1]
    cid, _ = send(client, conversation_id=cid, message='Original follow-up')
    old_leaf = detail(client, cid)['active_leaf_id']
    send(client, conversation_id=cid, regenerate=True, regenerate_message_id=original['id'], expected_leaf_id=old_leaf)
    new = detail(client, cid)
    assert len(new['messages']) == 2
    assert new['messages'][-1]['alternatives'] == [original['id'], new['active_leaf_id']]
    assert 'Original follow-up' not in json.dumps(provider.seen[-1])
    assert db.get(Message, original['id']).usage == original['usage']
    assert db.get(ContextRecord, original['usage']['turn_id'])
    record = client.get(f"/api/conversations/{cid}/context/{original['usage']['turn_id']}")
    assert record.status_code == 200 and record.json()['sources'][0]['available']
    restored = choose(client, cid, original['id'])
    assert restored['active_leaf_id'] == old_leaf
    assert restored['messages'][1]['citations'] == original['citations']
    assert 'Original follow-up' in client.get(f'/api/conversations/{cid}/export').json()['markdown']
    choose(client, cid, new['active_leaf_id'])
    assert 'Original follow-up' not in client.get(f'/api/conversations/{cid}/export').json()['markdown']
    send(client, conversation_id=cid, message='Continue the new alternative')
    assert 'Original follow-up' not in json.dumps(provider.seen[-1])


def test_edit_forks_prompt_and_retains_original_images_context_and_sources(client, db, setup):
    _, project, _, provider = setup
    image = 'data:image/png;base64,aW1hZ2U='
    cid, _ = send(client, project_id=project.id, message='Original', images=[image], context={'project_instructions': False})
    old = detail(client, cid)
    send(client, conversation_id=cid, message='Revised', edit_message_id=old['messages'][0]['id'], expected_leaf_id=old['active_leaf_id'])
    new = detail(client, cid)
    assert new['messages'][0]['alternatives'] == [old['messages'][0]['id'], new['messages'][0]['id']]
    assert db.get(Message, old['messages'][0]['id']).content == 'Original'
    assert new['messages'][0]['attachments'][0]['type'] == 'image'
    assert client.get(new['messages'][0]['attachments'][0]['url']).content == b'image'
    assert 'Prefer repairable equipment' not in json.dumps(provider.seen[-1])
    assert 'Original' not in json.dumps(provider.seen[-1])
    assert choose(client, cid, old['messages'][0]['id'])['active_leaf_id'] == old['active_leaf_id']
    assert choose(client, cid, new['messages'][0]['id'])['active_leaf_id'] == new['active_leaf_id']


def test_failure_keeps_old_answer_selected(client, db, setup, monkeypatch):
    cid, _ = send(client)
    old = detail(client, cid)
    def unavailable(*args):
        raise RuntimeError('Synthetic unavailable provider')
    monkeypatch.setattr('app.routers.chat.build_provider', unavailable)
    response = client.post('/api/chat', json={'conversation_id': cid, 'regenerate': True})
    assert response.status_code == 200 and 'Provider error' in response.text
    assert detail(client, cid)['active_leaf_id'] == old['active_leaf_id']
    assert detail(client, cid)['messages'] == old['messages']
    assert cid not in branches.ACTIVE


def test_private_stale_and_active_path_validation(client, db, setup):
    cid, _ = send(client)
    old = detail(client, cid)
    assert client.post('/api/chat', json={'conversation_id': cid, 'message': 'stale', 'expected_leaf_id': None}).status_code == 409
    foreign = Conversation(user_id='another-user')
    db.add(foreign)
    db.flush()
    message = Message(conversation_id=foreign.id, role='user', content='private')
    db.add(message)
    db.commit()
    assert client.post(f'/api/conversations/{cid}/alternatives/{message.id}', json={'expected_leaf_id': old['active_leaf_id']}).status_code == 404
    assert client.post('/api/chat', json={'conversation_id': cid, 'edit_message_id': message.id, 'message': 'spoof'}).status_code == 404
    assert client.post(f'/api/conversations/{foreign.id}/alternatives/{message.id}', json={'expected_leaf_id': None}).status_code == 404
    assert client.post(f'/api/conversations/{cid}/alternatives/{old["active_leaf_id"]}', json={'expected_leaf_id': 'stale'}).status_code == 409
    db.delete(foreign)
    db.commit()


def test_request_bound_stream_blocks_navigation_and_releases_after_stop(client, db, setup):
    from app.routers.chat import prepare_chat
    from app.schemas import ChatRequest
    user, _, _, _ = setup
    cid, _ = send(client)
    old = detail(client, cid)
    stream = prepare_chat(ChatRequest(conversation_id=cid, regenerate=True), db, user, threading.Event())
    assert next(stream).startswith('data: ')
    assert client.post(f'/api/conversations/{cid}/alternatives/{old["active_leaf_id"]}', json={'expected_leaf_id': old['active_leaf_id']}).status_code == 409
    assert client.patch(f'/api/conversations/{cid}', json={'project_id': None}).status_code == 409
    stream.close()
    assert cid not in branches.ACTIVE
    assert choose(client, cid, old['active_leaf_id'])['active_leaf_id'] == old['active_leaf_id']


def test_saved_artifact_bytes_survive_workspace_overwrite_and_cleanup(client, db, setup):
    from app.artifact_snapshots import capture
    from app.config import ATTACHMENTS_DIR
    from app.workspace.manager import workspace_dir
    cid, _ = send(client)
    message = db.get(Message, detail(client, cid)['active_leaf_id'])
    path = workspace_dir(cid) / 'report.md'
    path.write_text('Original report')
    message.artifacts = [{'path': 'report.md', 'name': 'report.md', 'ext': '.md'}]
    capture(message)
    db.commit()
    artifact = message.artifacts[0]
    path.write_text('Revised report')
    assert client.get(artifact['url']).content == b'Original report'
    assert client.get(f'/api/files/{cid}?path=report.md').content == b'Revised report'
    assert client.get(artifact['url'].replace(cid, 'foreign')).status_code == 404
    assert client.delete(f'/api/conversations/{cid}').status_code == 200
    assert not (ATTACHMENTS_DIR / message.id).exists()


def test_oversized_artifacts_are_explicitly_unsaved(client, db, setup, monkeypatch):
    from app.artifact_snapshots import capture
    from app.workspace.manager import workspace_dir
    cid, _ = send(client)
    message = db.get(Message, detail(client, cid)['active_leaf_id'])
    (workspace_dir(cid) / 'large.txt').write_text('too big')
    monkeypatch.setattr('app.artifact_snapshots.MAX_FILE', 2)
    message.artifacts = [{'path': 'large.txt', 'name': 'large.txt'}]
    capture(message)
    assert message.artifacts[0]['snapshot_status'] == 'size_limit'
    assert 'url' not in message.artifacts[0]


def test_new_branch_retains_previous_nested_selection(client, db, setup):
    cid, _ = send(client, message='Root')
    first = detail(client, cid)['active_leaf_id']
    send(client, conversation_id=cid, regenerate=True)
    second = detail(client, cid)['active_leaf_id']
    send(client, conversation_id=cid, message='Child')
    child = detail(client, cid)['active_leaf_id']
    send(client, conversation_id=cid, regenerate=True)
    choose(client, cid, child)
    choose(client, cid, first)
    assert choose(client, cid, second)['active_leaf_id'] == child


def test_regeneration_after_project_change_keeps_new_context_continuity(client, db, setup):
    _, project, docs, provider = setup
    cid, _ = send(client, project_id=project.id, message='Original question')
    old = detail(client, cid)
    row = client.get(f'/api/projects/{project.id}').json()
    assert client.put(f'/api/projects/{project.id}', json={**row, 'document_ids': []}).status_code == 200
    send(client, conversation_id=cid, regenerate=True)
    db.expire_all()
    assert db.get(Message, old['messages'][0]['id']).attachments == old['messages'][0]['attachments']
    send(client, conversation_id=cid, message='Continue this revised context')
    text = json.dumps(provider.seen[-1])
    assert 'Original question' in text
    assert docs[0].filename not in text and 'Unique policy 0' not in text


def test_approval_resume_binds_new_answer_to_original_parent(client, db, setup):
    from app.models import PendingApproval
    from app.providers.base import ToolCall
    _, _, _, provider = setup
    cid, _ = send(client)
    original = detail(client, cid)
    provider.calls = [[ToolCall('save-alternative', 'write_file', {'path': 'choice.txt', 'content': 'Alternative'})]]
    _, events = send(client, conversation_id=cid, regenerate=True)
    pending_id = next(e['pending_id'] for e in events if e['type'] == 'approval_request')
    assert db.get(PendingApproval, pending_id).state['branch_parent_id'] == original['messages'][0]['id']
    assert client.post(f'/api/conversations/{cid}/alternatives/{original["active_leaf_id"]}',
                       json={'expected_leaf_id': original['active_leaf_id']}).status_code == 409
    response = client.post('/api/chat/approve', json={'pending_id': pending_id, 'decisions': {'save-alternative': 'allow'}})
    assert response.status_code == 200, response.text
    row = detail(client, cid)
    assert len(row['messages'][-1]['alternatives']) == 2
    assert row['messages'][-1]['parent_id'] == original['messages'][0]['id']
    assert row['messages'][-1]['artifacts'][0]['snapshot_status'] == 'saved'
    assert choose(client, cid, original['active_leaf_id'])['messages'] == original['messages'][:1] + [
        {**original['messages'][-1], 'alternatives': row['messages'][-1]['alternatives']}]


def test_durable_regeneration_admission_replay_and_navigation_lock(client, db, setup, monkeypatch):
    from types import SimpleNamespace
    from app import runs
    cid, _ = send(client)
    original = detail(client, cid)
    monkeypatch.setattr('app.config.runs_enabled', lambda: True)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: True)
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    payload = {'conversation_id': cid, 'regenerate': True, 'expected_leaf_id': original['active_leaf_id']}
    response = client.post('/api/runs', json=payload, headers={'Idempotency-Key': 'branch-retry'})
    assert response.status_code == 200, response.text
    assert client.post('/api/runs', json=payload, headers={'Idempotency-Key': 'branch-retry'}).json()['id'] == response.json()['id']
    assert client.post(f'/api/conversations/{cid}/alternatives/{original["active_leaf_id"]}',
                       json={'expected_leaf_id': original['active_leaf_id']}).status_code == 409
    worker.step()
    assert '"type": "done"' in client.get(f'/api/runs/{response.json()["id"]}/events').text
    assert len(detail(client, cid)['messages'][-1]['alternatives']) == 2
    assert db.get(Message, original['active_leaf_id'])
    assert client.get(f'/api/runs?conversation_id={cid}').json()['id'] == response.json()['id']
    choose(client, cid, original['active_leaf_id'])
    assert client.get(f'/api/runs?conversation_id={cid}').json() is None
    assert client.get(f'/api/runs/{response.json()["id"]}').status_code == 200


def test_research_regeneration_preserves_options_and_original_report(client, db, setup):
    _, project, _, provider = setup
    cid, _ = send(client, project_id=project.id, research={'scope': 'documents', 'depth': 'brief'}, message='Research retention')
    old = detail(client, cid)
    send(client, conversation_id=cid, regenerate=True)
    row = detail(client, cid)
    assert row['messages'][-1]['usage']['research']['scope'] == 'documents'
    assert len(row['messages'][-1]['alternatives']) == 2
    assert db.get(Message, old['active_leaf_id'])
    assert 'Research retention' in json.dumps(provider.seen[-1])
