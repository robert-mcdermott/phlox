"""Artifact history, workspace races, isolation, and read-only model revisions."""
import json
import re
import threading

import pytest
from fastapi import HTTPException

from app import artifacts, artifact_snapshots, branches
from app.models import Artifact, ArtifactVersion, Conversation, Message, UsageLedger
from app.providers.base import StreamDelta
from app.workspace.manager import workspace_dir
from test_projects import setup as setup, send


def output(client, db, content='Original paragraph.\n\nKeep this section.', name='report.md'):
    cid, _ = send(client)
    message = db.get(Message, client.get(f'/api/conversations/{cid}').json()['active_leaf_id'])
    path = workspace_dir(cid) / name
    path.write_text(content, encoding='utf-8')
    message.artifacts = [{'name': name, 'path': name, 'ext': '.md'}]
    artifact_snapshots.capture(message)
    db.commit()
    item = message.artifacts[0]
    url = f'/api/artifacts/{cid}/{item["artifact_id"]}'
    result = client.get(url)
    assert result.status_code == 200, result.text
    return cid, url, result.json(), path, item


def save(client, url, row, content):
    return client.post(url + '/versions', json={'expected_head': row['head_version_id'],
        'base_version_id': row['version']['id'], 'content': content})


def publish(client, url, row, sha=None):
    return client.post(url + '/publish', json={'expected_head': row['head_version_id'],
        'version_id': row['version']['id'], 'expected_workspace_sha256': sha or row['workspace']['sha256']})


def test_edit_restore_diff_and_download_preserve_originals(client, db, setup):
    cid, url, original, path, item = output(client, db)
    edited = save(client, url, original, 'Revised paragraph.\n\nKeep this section.').json()
    assert edited['version']['number'] == 2
    assert edited['version']['parent_version_id'] == original['version']['id']
    assert edited['version']['source_message_id'] == original['version']['source_message_id']
    assert path.read_text() == original['version']['content']
    assert client.get(item['url']).text == original['version']['content']
    difference = client.get(url + '/diff', params={'before': original['version']['id'], 'after': edited['version']['id']}).json()
    assert '-Original paragraph.' in difference['diff'] and '+Revised paragraph.' in difference['diff']
    assert publish(client, url, edited).status_code == 200
    assert path.read_text() == edited['version']['content']
    restored = client.post(url + '/restore', json={'expected_head': edited['head_version_id'],
        'version_id': original['version']['id']}).json()
    assert restored['version']['number'] == 3 and restored['version']['origin'] == 'restore'
    assert restored['version']['content'] == original['version']['content']
    assert path.read_text() == edited['version']['content']  # Restore is a DB-only action.
    downloaded = client.get(url + '/download/' + restored['version']['id'])
    assert downloaded.content == original['version']['content'].encode()
    assert 'v3.md' in downloaded.headers['content-disposition']
    assert len(client.get(url).json()['versions']) == 3
    assert client.delete(f'/api/conversations/{cid}').status_code == 200
    db.expire_all()
    assert db.get(Artifact, original['id']) is None
    assert db.get(ArtifactVersion, original['version']['id']) is None


def test_stale_saves_and_workspace_overwrites_are_rejected(client, db, setup):
    _, url, original, path, _ = output(client, db)
    edited = save(client, url, original, 'User edit').json()
    assert save(client, url, original, 'Stale tab').status_code == 409
    assert publish(client, url, original).status_code == 409
    path.write_text('Agent or external writer changed this')
    assert publish(client, url, edited, original['workspace']['sha256']).status_code == 409
    assert path.read_text() == 'Agent or external writer changed this'
    assert client.get(url).json()['version']['content'] == 'User edit'
    assert client.get(url + '/download/' + original['version']['id']).status_code == 200


def test_workspace_import_and_legacy_snapshots_are_idempotent(client, db, setup):
    cid, url, original, path, item = output(client, db)
    edited = save(client, url, original, 'New saved version').json()
    opened = client.post(f'/api/artifacts/{cid}/open', json={'path': path.name}).json()
    assert opened['head_version_id'] == edited['head_version_id']
    assert opened['version']['id'] == original['version']['id']
    message = db.get(Message, original['version']['source_message_id'])
    message.artifacts = [{k: v for k, v in item.items() if k not in {'artifact_id', 'version_id'}}]
    db.commit()
    body = {'path': path.name, 'message_id': message.id, 'snapshot_index': 0}
    old = client.post(f'/api/artifacts/{cid}/open', json=body).json()
    again = client.post(f'/api/artifacts/{cid}/open', json=body).json()
    assert old['version']['id'] == again['version']['id']
    assert old['head_version_id'] == edited['head_version_id']
    assert old['version']['content'] == original['version']['content']
    path.write_text('New tool output')
    imported = client.post(f'/api/artifacts/{cid}/open', json={'path': path.name}).json()
    assert imported['version']['origin'] == 'workspace'
    assert imported['version']['content'] == 'New tool output'


def test_all_routes_enforce_private_ownership_and_version_membership(client, db, setup):
    cid, url, row, _, _ = output(client, db)
    other_cid, other_url, other, _, _ = output(client, db, 'Another artifact')
    foreign = Conversation(user_id='someone-else')
    db.add(foreign)
    db.commit()
    bad = url.replace(cid, foreign.id)
    try:
        assert client.get(bad).status_code == 404
        assert client.get(url, params={'version_id': other['version']['id']}).status_code == 404
        assert client.get(bad + '/download/' + row['version']['id']).status_code == 404
        assert save(client, bad, row, 'spoof').status_code == 404
        assert publish(client, bad, row).status_code == 404
        assert client.post(bad + '/restore', json={'expected_head': row['head_version_id'], 'version_id': row['version']['id']}).status_code == 404
        assert client.get(url + '/diff', params={'before': row['version']['id'], 'after': other['version']['id']}).status_code == 404
        assert client.post(f'/api/artifacts/{foreign.id}/open', json={'path': 'report.md'}).status_code == 404
        assert client.post(other_url + '/versions', json={'expected_head': other['head_version_id'],
            'base_version_id': row['version']['id'], 'content': 'spoof'}).status_code == 404
    finally:
        db.delete(foreign)
        db.commit()


@pytest.mark.parametrize('busy_kind', ['stream', 'approval', 'run', 'archived'])
def test_mutations_are_blocked_while_busy_or_archived(client, db, setup, busy_kind):
    from app.models import PendingApproval, Run
    cid, url, row, _, _ = output(client, db)
    blocker = None
    if busy_kind == 'stream':
        branches.ACTIVE.add(cid)
    elif busy_kind == 'approval':
        blocker = PendingApproval(conversation_id=cid, status='pending', state={})
        db.add(blocker)
        db.commit()
    elif busy_kind == 'run':
        blocker = Run(conversation_id=cid, user_id=setup[0].id, active_conversation_id=cid,
                      status='running', payload={}, request_key=cid, request_hash='0' * 64)
        db.add(blocker)
        db.commit()
    else:
        db.get(Conversation, cid).project_id = setup[1].id
        setup[1].archived = True
        db.commit()
    try:
        assert save(client, url, row, 'Busy edit').status_code == 409
        assert publish(client, url, row).status_code == 409
        assert client.get(url).status_code == 200
        if busy_kind != 'archived':
            assert client.post(f'/api/checkpoints/{cid}/restore', json={'sha': 'abc'}).status_code == 409
    finally:
        branches.ACTIVE.discard(cid)
        if blocker:
            db.delete(blocker)
            db.commit()


def test_binary_large_escaped_and_symlinked_files_are_not_editable(client, db, setup, tmp_path):
    cid, url, row, path, _ = output(client, db)
    assert save(client, url, row, '\x00binary').status_code == 415
    assert save(client, url, row, 'é' * artifacts.MAX_TEXT).status_code == 413
    for name in ['../outside.txt', '.git/config']:
        assert client.post(f'/api/artifacts/{cid}/open', json={'path': name}).status_code == 400
    path.write_bytes(b'\xff\x00')
    assert client.post(f'/api/artifacts/{cid}/open', json={'path': path.name}).status_code == 415
    path.unlink()
    target = tmp_path / 'outside.txt'
    target.write_text('Private outside file')
    path.symlink_to(target)
    assert publish(client, url, row).status_code == 409
    assert target.read_text() == 'Private outside file'
    assert client.get(url + '/download/' + row['version']['id']).status_code == 200


def test_workspace_write_failure_keeps_version_and_file(client, db, setup, monkeypatch):
    _, url, row, path, _ = output(client, db)
    edited = save(client, url, row, 'Saved even if filesystem is read-only').json()
    def denied(*args):
        raise OSError('permission denied')
    monkeypatch.setattr('app.artifacts.os.replace', denied)
    assert publish(client, url, edited).status_code == 409
    assert path.read_text() == row['version']['content']
    assert client.get(url).json()['version']['content'] == edited['version']['content']
    assert not list(path.parent.glob('.phlox-edit-*'))


def test_diffs_expose_line_endings_and_bound_large_comparisons(client, db, setup):
    _, url, row, _, _ = output(client, db, 'First\r\nLast')
    edited = save(client, url, row, 'First\nLast\n').json()
    diff = client.get(url + '/diff', params={'before': row['version']['id'], 'after': edited['version']['id']}).json()
    assert '␍' in diff['diff'] and 'No newline at end of file' in diff['diff']
    large = save(client, url, edited, 'line\n' * 2001).json()
    assert client.get(url + '/diff', params={'before': row['version']['id'], 'after': large['version']['id']}).status_code == 413


def revision_body(row):
    return {'expected_head': row['head_version_id'], 'version_id': row['version']['id'],
            'start': 0, 'end': 19, 'instruction': 'Make this concise.'}


def test_ai_revision_is_tool_free_scoped_accounted_and_never_writes(client, db, setup, monkeypatch):
    cid, url, row, path, _ = output(client, db)
    provider = setup[3]
    monkeypatch.setattr('app.artifact_revisions.build_provider', lambda *args: provider)
    response = client.post(url + '/revise', json=revision_body(row))
    assert response.status_code == 200, response.text
    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]
    proposal = next(e for e in events if e['type'] == 'artifact_proposal')
    assert proposal['replacement'] == 'Reviewed the project evidence [S1].'
    sent = json.dumps(provider.seen[-1])
    assert 'Original paragraph.' in sent and 'Keep this section.' not in sent
    assert 'retention policy' not in sent and provider.tool_names == set()
    assert path.read_text() == row['version']['content']
    assert len(client.get(url).json()['versions']) == 1
    usage = db.query(UsageLedger).filter_by(turn_id=proposal['usage']['turn_id']).one()
    assert usage.call_kind == 'artifact_edit' and usage.status == 'completed' and usage.total_tokens == 25
    assert cid not in branches.ACTIVE


@pytest.mark.parametrize('failure', ['tool', 'truncated', 'exception', 'output_block', 'input_block', 'budget'])
def test_ai_failures_never_apply_partial_output_or_leak_content(client, db, setup, monkeypatch, failure):
    from app.guardrails import Rule
    _, url, row, path, _ = output(client, db)
    provider = setup[3]
    def stream(*args):
        yield StreamDelta(type='text', text='blocked-secret')
        if failure == 'exception':
            raise RuntimeError('provider secret diagnostics')
        if failure == 'tool':
            yield StreamDelta(type='tool_calls')
        yield StreamDelta(type='done', stop_reason='length' if failure == 'truncated' else 'stop')
    monkeypatch.setattr(provider, 'stream', stream)
    monkeypatch.setattr('app.artifact_revisions.build_provider', lambda *args: provider)
    if failure in {'input_block', 'output_block'}:
        direction = failure.split('_')[0]
        monkeypatch.setattr('app.artifact_revisions.get_rules', lambda d: [Rule('test', re.compile('blocked-secret|Original'), 'block', '[BLOCKED]')] if d == direction else [])
    if failure == 'budget':
        def blocked(*args):
            raise HTTPException(402, 'Monthly budget exceeded')
        monkeypatch.setattr('app.budgets.enforce_budget', blocked)
    response = client.post(url + '/revise', json=revision_body(row))
    assert response.status_code == (400 if failure == 'input_block' else 200)
    assert 'artifact_proposal' not in response.text and 'blocked-secret' not in response.text
    assert 'provider secret diagnostics' not in response.text
    assert path.read_text() == row['version']['content']
    assert len(client.get(url).json()['versions']) == 1


def test_ai_cancel_closes_provider_and_releases_busy_lease(client, db, setup, monkeypatch):
    from app.artifact_revisions import prepare
    from app.routers.artifacts import ReviseInput
    cid, url, row, _, _ = output(client, db)
    provider = setup[3]
    cancel, closed = threading.Event(), []
    def stream(*args):
        try:
            cancel.set()
            yield StreamDelta(type='text', text='Do not apply')
        finally:
            closed.append(True)
    monkeypatch.setattr(provider, 'stream', stream)
    monkeypatch.setattr('app.artifact_revisions.build_provider', lambda *args: provider)
    result, lifecycle = prepare(db, setup[0], db.get(Artifact, row['id']),
        db.get(ArtifactVersion, row['version']['id']), ReviseInput(**revision_body(row)), cancel)
    assert cid in branches.ACTIVE
    assert 'status' in next(result)
    assert save(client, url, row, 'while revising').status_code == 409
    assert list(result) == [] and closed
    assert lifecycle['started'] and cid not in branches.ACTIVE
