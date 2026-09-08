"""Offline run ownership, admission, replay, cancellation and crash contracts."""
import asyncio
import json
import threading
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import runs
from app.agent.registry import ToolRegistry
from app.agent.tools.base import Tool, ToolResult
from app.auth.deps import _dev_admin
from app.database import SessionLocal
from app.models import Conversation, PendingApproval, Run, RunEvent, ToolExecution, UsageLedger, User
from app.providers.base import StreamDelta, ToolCall
from app.schemas import ChatRequest


@pytest.fixture
def runtime(monkeypatch, db):
    from app.routers import chat

    existing = set(db.query(Run.id).all())
    worker = runs.Worker()
    worker.thread = SimpleNamespace(is_alive=lambda: True)
    monkeypatch.setattr(runs, 'worker', worker)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: True)
    monkeypatch.setattr('app.config.runs_enabled', lambda: True)
    registry = ToolRegistry()
    state = SimpleNamespace(mode='text', actions=[], release=threading.Event(), entered=threading.Event())

    class Action(Tool):
        name = 'wave5_action'
        default_permission = 'ask'

        def run(self, ctx, **kwargs):
            with SessionLocal() as check:
                assert check.query(ToolExecution).filter_by(run_id=ctx.accounting.turn_id, status='started').count() == 1
            state.actions.append(kwargs)
            if state.mode == 'overflow':
                ctx.progress('x' * 4096)
                assert ctx.cancel_event.wait(3), 'closing the generator must cancel its tool'
            return ToolResult(content='Action observed')

    class Provider:
        supports_tools = True
        model = 'wave5-model'

        def stream(self, messages, tools, params):
            yield StreamDelta(type='usage', usage={'input': 7, 'output': 3, 'total': 10})
            if state.mode == 'slow':
                yield StreamDelta(type='text', text='Saved prefix' * 50)
                state.entered.set()
                assert state.release.wait(4)
            if state.mode in {'ask', 'overflow'} and not any(m['role'] == 'tool' for m in messages):
                yield StreamDelta(type='text', text='I can do that.')
                yield StreamDelta(type='tool_calls', tool_calls=[ToolCall('action-1', Action.name, {'value': 1})])
            else:
                yield StreamDelta(type='text', text='Finished answer.')
                yield StreamDelta(type='done')

    registry.register(Action())
    monkeypatch.setattr(chat, 'REGISTRY', registry)
    monkeypatch.setattr(chat, 'build_provider', lambda *args: Provider())
    yield worker, state
    state.release.set()
    db.rollback()
    for row in db.query(Run).all():
        if (row.id,) not in existing:
            conv = db.get(Conversation, row.conversation_id)
            db.query(UsageLedger).filter_by(conversation_id=row.conversation_id).delete()
            db.delete(conv)
    db.commit()


def create(client, key=None, **payload):
    response = client.post('/api/runs', headers={'Idempotency-Key': key or uuid.uuid4().hex},
                           json={'message': 'Do the task', **payload})
    assert response.status_code == 200, response.text
    return response.json()


def state(client, row):
    return client.get(f'/api/runs/{row["id"]}').json()


def frames(response):
    assert response.status_code == 200, response.text
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith('data: ')]


def test_deduplicated_admission_and_history_guard(client, db, runtime):
    worker, _ = runtime
    first = create(client, 'same-key')
    assert create(client, 'same-key')['id'] == first['id']
    assert client.post('/api/runs', headers={'Idempotency-Key': 'same-key'}, json={'message': 'different'}).status_code == 409
    assert client.post('/api/runs', headers={'Idempotency-Key': 'other-key'}, json={'conversation_id': first['conversation_id'], 'message': 'second'}).status_code == 409
    assert client.delete(f'/api/conversations/{first["conversation_id"]}').status_code == 409
    assert client.patch(f'/api/conversations/{first["conversation_id"]}', json={'system_prompt': 'changed'}).status_code == 409
    worker.step()
    assert state(client, first)['status'] == 'completed'
    conv = client.get(f'/api/conversations/{first["conversation_id"]}').json()
    assert [m['role'] for m in conv['messages']] == ['user', 'assistant']
    assert db.query(UsageLedger).filter_by(turn_id=first['id']).count() == 1
    assert create(client, 'same-key')['id'] == first['id']


def test_replay_is_ordered_bounded_and_owner_only(client, db, runtime):
    worker, _ = runtime
    row = create(client)
    worker.step()
    replay = frames(client.get(f'/api/runs/{row["id"]}/events'))
    events = [e for e in replay if 'seq' in e]
    assert [e['seq'] for e in events] == list(range(1, len(events) + 1))
    assert all(e['run_id'] == row['id'] for e in events)
    tail = frames(client.get(f'/api/runs/{row["id"]}/events?after=2'))
    assert [e for e in tail if 'seq' in e] == events[2:]
    assert client.get(f'/api/runs/{row["id"]}/events?after=99999').status_code == 400
    from app.main import app
    from app.auth.deps import get_current_user
    app.dependency_overrides[get_current_user] = lambda: User(id='foreign-admin', username='admin', role='admin')
    try:
        for method, path in [('get', ''), ('get', '/events'), ('post', '/cancel'), ('post', '/acknowledge'), ('post', '/approve')]:
            args = {'json': {'pending_id': 'anything', 'decisions': {}}} if path == '/approve' else {}
            assert getattr(client, method)(f'/api/runs/{row["id"]}{path}', **args).status_code == 404
        assert client.get(f'/api/runs?conversation_id={row["conversation_id"]}').status_code == 404
    finally:
        app.dependency_overrides.pop(get_current_user)


def test_disconnect_does_not_stop_worker_but_stop_is_explicit(client, runtime):
    worker, script = runtime
    script.mode = 'slow'
    row = create(client)
    thread = threading.Thread(target=worker.step)
    thread.start()
    assert script.entered.wait(3)

    async def detach():
        response = runs.subscribe(row['id'], 'local')
        iterator = response.body_iterator
        await anext(iterator)
        await iterator.aclose()
    asyncio.run(detach())
    assert not worker.cancel_event.is_set()
    assert state(client, row)['status'] == 'running'
    stopped = client.post(f'/api/runs/{row["id"]}/cancel').json()
    assert stopped['status'] == 'cancel_requested'
    assert worker.cancel_event.is_set()
    assert client.delete(f'/api/conversations/{row["conversation_id"]}').status_code == 409
    script.release.set()
    thread.join(4)
    assert not thread.is_alive()
    assert state(client, row)['status'] == 'cancelled'
    assert client.delete(f'/api/conversations/{row["conversation_id"]}').status_code == 200


def test_approval_reuses_run_and_ledger_and_claims_once(client, db, runtime):
    worker, script = runtime
    script.mode = 'ask'
    row = create(client)
    worker.step()
    paused = state(client, row)
    assert paused['status'] == 'awaiting_approval'
    req = {'pending_id': paused['pending_id'], 'decisions': {'action-1': 'allow'}}
    assert client.post(f'/api/runs/{row["id"]}/approve', json=req).status_code == 200
    assert client.post(f'/api/runs/{row["id"]}/approve', json=req).status_code == 200
    worker.step()
    assert state(client, row)['status'] == 'completed'
    assert script.actions == [{'value': 1}]
    assert db.query(UsageLedger).filter_by(turn_id=row['id']).count() == 2
    assert client.post(f'/api/runs/{row["id"]}/approve', json=req).status_code == 409
    assert db.query(ToolExecution).filter_by(run_id=row['id'], status='completed').count() == 1


def test_approval_current_policy_failure_is_retryable(client, db, runtime, monkeypatch):
    worker, script = runtime
    script.mode = 'ask'
    row = create(client)
    worker.step()
    pending = state(client, row)['pending_id']
    req = {'pending_id': pending, 'decisions': {'action-1': 'allow'}}
    assert client.post(f'/api/runs/{row["id"]}/approve', json=req).status_code == 200
    def blocked(*args, **kwargs):
        raise HTTPException(402, 'Budget changed while queued')
    with monkeypatch.context() as patch:
        patch.setattr('app.budgets.enforce_budget', blocked)
        worker.step()
    assert state(client, row)['status'] == 'awaiting_approval'
    assert not script.actions
    assert db.get(PendingApproval, pending).status == 'pending'
    assert client.post(f'/api/runs/{row["id"]}/approve', json=req).status_code == 200
    worker.step()
    assert len(script.actions) == 1


def test_crash_marks_intents_unknown_and_never_replays(client, db, runtime):
    worker, script = runtime
    row = create(client)
    stored = db.get(Run, row['id'])
    stored.status = 'running'
    db.add(ToolExecution(run_id=row['id'], call_id='uncertain', name='mutating_action'))
    db.commit()
    worker.recover()
    assert state(client, row)['status'] == 'interrupted'
    assert state(client, row)['needs_acknowledgement']
    assert not worker.step()
    assert not script.actions
    db.expire_all()
    assert db.query(ToolExecution).filter_by(run_id=row['id']).one().status == 'outcome_unknown'
    assert client.post(f'/api/runs/{row["id"]}/acknowledge').status_code == 200
    assert not state(client, row)['needs_acknowledgement']


def test_restart_keeps_pause_and_discards_unstarted_approval_decisions(client, runtime):
    worker, script = runtime
    script.mode = 'ask'
    row = create(client)
    worker.step()
    req = {'pending_id': state(client, row)['pending_id'], 'decisions': {'action-1': 'allow'}}
    client.post(f'/api/runs/{row["id"]}/approve', json=req)
    worker.recover()
    assert state(client, row)['status'] == 'awaiting_approval'
    assert not worker.step()
    assert not script.actions


def test_queue_limits_and_queued_cancel(client, runtime, monkeypatch):
    worker, _ = runtime
    monkeypatch.setattr(runs, 'MAX_USER_ACTIVE', 1)
    row = create(client)
    assert client.post('/api/runs', headers={'Idempotency-Key': 'full'}, json={'message': 'extra'}).status_code == 429
    assert client.post(f'/api/runs/{row["id"]}/cancel').json()['status'] == 'cancelled'
    assert not worker.step()
    create(client)


def test_log_overflow_joins_tool_and_retains_unknown_intent(client, runtime, monkeypatch):
    worker, script = runtime
    script.mode = 'overflow'
    monkeypatch.setattr(runs, 'MAX_EVENT_BYTES', 2000)
    row = create(client, auto_approve=True)
    worker.step()
    assert state(client, row)['status'] == 'interrupted'
    assert 'unknown' in state(client, row)['reason']
    assert len(script.actions) == 1


def test_cleanup_expiry_and_private_cascade_leave_accounting(client, db, runtime):
    worker, _ = runtime
    row = create(client)
    worker.step()
    queued = create(client)
    runs.cleanup(db, datetime.now(timezone.utc) + timedelta(days=8))
    assert client.get(f'/api/runs/{row["id"]}/events').status_code == 410
    db.expire_all()
    assert not db.get(Run, queued['id']).events_expired
    assert client.delete(f'/api/conversations/{row["conversation_id"]}').status_code == 200
    assert db.get(Run, row['id'], populate_existing=True) is None
    assert db.query(RunEvent).filter_by(run_id=row['id']).count() == 0
    assert db.query(ToolExecution).filter_by(run_id=row['id']).count() == 0
    assert db.query(UsageLedger).filter_by(turn_id=row['id']).count() == 1


def test_disabled_rollout_and_oversized_payload(db, runtime, monkeypatch):
    monkeypatch.setattr(runs, 'runs_enabled', lambda: False)
    with pytest.raises(HTTPException) as error:
        runs.create(db, _dev_admin(), ChatRequest(message='hello'))
    assert error.value.status_code == 503
    monkeypatch.setattr(runs, 'runs_enabled', lambda: True)
    monkeypatch.setattr(runs, 'MAX_REQUEST_BYTES', 100)
    with pytest.raises(HTTPException) as error:
        runs.create(db, _dev_admin(), ChatRequest(message='x'*101))
    assert error.value.status_code == 413


def test_simultaneous_retries_and_tabs_are_serialized(client, db, runtime):
    from concurrent.futures import ThreadPoolExecutor

    def send(_):
        return client.post('/api/runs', headers={'Idempotency-Key': 'race'}, json={'message': 'one'})
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(send, range(2)))
    assert [r.status_code for r in responses] == [200, 200]
    assert len({r.json()['id'] for r in responses}) == 1
    conv = responses[0].json()['conversation_id']
    assert db.query(Run).filter_by(conversation_id=conv).count() == 1


def test_deleted_account_cannot_recreate_data_from_stale_authentication(db, runtime):
    user = User(id=uuid.uuid4().hex, username=uuid.uuid4().hex, is_active=True, must_change_password=False)
    db.add(user)
    db.commit()
    # The authentication dependency has already returned this user when deletion wins.
    with SessionLocal() as other:
        other.delete(other.get(User, user.id))
        other.commit()
    with pytest.raises(HTTPException) as error:
        runs.create(db, user, ChatRequest(message='must not be saved'))
    assert error.value.status_code == 401
    assert db.query(Run).filter_by(user_id=user.id).count() == 0


def test_unstarted_request_is_reviewable_after_restart_and_flag_disable(client, db, runtime, monkeypatch):
    worker, script = runtime
    row = create(client, message='Keep this unstarted request')
    worker.recover()
    monkeypatch.setattr('app.config.runs_enabled', lambda: False)
    monkeypatch.setattr(runs, 'runs_enabled', lambda: False)
    assert client.get('/api/auth/config').json()['run_history_available']
    recovered = state(client, row)
    assert recovered['queued_message'] == 'Keep this unstarted request'
    assert recovered['status'] == 'interrupted'
    assert not script.actions
    assert client.post(f'/api/runs/{row["id"]}/acknowledge').status_code == 200


def test_expired_linked_approval_can_be_dismissed(client, db, runtime):
    worker, script = runtime
    script.mode = 'ask'
    row = create(client)
    worker.step()
    pending_id = state(client, row)['pending_id']
    pending = db.get(PendingApproval, pending_id)
    pending.created_at = datetime.now(timezone.utc) - timedelta(days=2)
    db.commit()
    assert client.post(f'/api/runs/{row["id"]}/approve', json={
        'pending_id': pending_id, 'decisions': {'action-1': 'allow'},
    }).status_code == 409
    assert client.delete(f'/api/chat/approvals/{pending_id}').status_code == 200
    assert state(client, row)['status'] == 'cancelled'
    assert not script.actions
    create(client, conversation_id=row['conversation_id'])


def test_user_purge_removes_private_run_content_but_keeps_usage(db, client, runtime):
    from app.auth.service import delete_user_data

    worker, _ = runtime
    row = create(client)
    worker.step()
    # Give this fixture a distinct owner to avoid deleting other tests' local chats.
    stored = db.get(Run, row['id'])
    stored.user_id = 'departing-owner'
    db.get(Conversation, row['conversation_id']).user_id = 'departing-owner'
    db.commit()
    delete_user_data(db, 'departing-owner')
    db.commit()
    assert db.get(Run, row['id'], populate_existing=True) is None
    assert not db.query(RunEvent).filter_by(run_id=row['id']).count()
    assert db.query(UsageLedger).filter_by(turn_id=row['id']).count() == 1


def test_worker_lifecycle_runs_independently_and_stops_cleanly(client, runtime):
    import time

    worker, _ = runtime
    row = create(client)
    worker.thread = None
    # start() recovers old queue entries, so first start then admit a fresh request.
    worker.start()
    try:
        assert state(client, row)['status'] == 'interrupted'
        fresh = create(client)
        deadline = time.monotonic() + 4
        while state(client, fresh)['status'] in runs.BUSY and time.monotonic() < deadline:
            time.sleep(.02)
        assert state(client, fresh)['status'] == 'completed'
    finally:
        worker.stop()
    assert not worker.available


def test_progress_flood_is_bounded_without_losing_completion():
    from app.agent.harness import ProgressBuffer

    buffer = ProgressBuffer()
    for _ in range(1000):
        buffer.progress('x' * 10000)
    buffer.finish(None)
    received = []
    while (chunk := buffer.get()) is not None:
        received.append(chunk)
    assert len(received) == 128
    assert max(map(len, received)) == 8192
    assert buffer.omitted.is_set()


def test_child_dispatch_and_result_use_parent_run_journal(client, db, runtime):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.model_calls import CallScope
    from app.routers import chat

    _, script = runtime
    script.mode = 'ask'
    row = create(client)
    conv = db.get(Conversation, row['conversation_id'])
    recorder = runs.Recorder(row['id'])
    session = AgentSession(db, conv, chat.build_provider('test'), chat.REGISTRY,
                           PermissionGate(db, chat.REGISTRY, auto_approve=True),
                           {'max_tool_rounds': 3}, 'test', 'wave5-model', ephemeral=True,
                           accounting=CallScope(row['id'], conv.id, 'local').child('parent-call'),
                           tool_observer=recorder.write)
    list(session.run([{'role': 'user', 'content': 'child work'}]))
    execution = db.query(ToolExecution).filter_by(run_id=row['id']).one()
    assert execution.status == 'completed'
    assert [e.data['type'] for e in db.query(RunEvent).filter_by(run_id=row['id']).order_by(RunEvent.seq)] == ['child_tool_start', 'child_tool_result']
    assert len(script.actions) == 1


def test_pause_stays_linked_when_replay_limit_prevents_approval_event(client, db, runtime, monkeypatch):
    worker, script = runtime
    script.mode = 'ask'
    original = runs.Recorder.write

    def full_before_approval(self, event):
        if event['type'] == 'approval_request':
            raise runs.EventLimit('Replay limit reached')
        original(self, event)

    monkeypatch.setattr(runs.Recorder, 'write', full_before_approval)
    row = create(client)
    worker.step()
    current = state(client, row)
    assert current['status'] == 'limit_reached'
    assert current['pending_id']
    assert db.get(PendingApproval, current['pending_id']).status == 'failed'
    assert client.post('/api/chat/approve', json={
        'pending_id': current['pending_id'], 'decisions': {'action-1': 'allow'},
    }).status_code == 409
    assert client.delete(f'/api/chat/approvals/{current["pending_id"]}').status_code == 200
    assert not script.actions


def test_observed_tool_completion_does_not_request_cancellation(client, db, runtime, monkeypatch):
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate

    class QuickTool(Tool):
        name = 'quick'

        def run(self, ctx, **kwargs):
            return ToolResult(content='done')

    class ExitingThread:
        # Model the interval after the sentinel is delivered but before a thread exits.
        def __init__(self, target, **kwargs):
            self.target = target

        def start(self):
            self.target()

        def is_alive(self):
            return True

        def join(self):
            pass

    row = create(client)
    registry = ToolRegistry()
    registry.register(QuickTool())
    cancel = threading.Event()
    session = AgentSession(db, db.get(Conversation, row['conversation_id']), object(), registry,
                           PermissionGate(db, registry), {}, 'test', 'test', cancel_event=cancel)
    monkeypatch.setattr('app.agent.harness.threading.Thread', ExitingThread)
    list(session._execute_tool_streaming('quick-call', 'quick', {}))
    assert not cancel.is_set()


@pytest.mark.parametrize('stopping', [False, True])
def test_restart_reconciles_prior_claim_after_next_pause_committed(client, db, runtime, stopping):
    worker, script = runtime
    script.mode = 'ask'
    row = create(client)
    worker.step()
    stored = db.get(Run, row['id'])
    previous = db.get(PendingApproval, stored.pending_id)
    previous.status = 'claimed'
    next_pause = PendingApproval(conversation_id=stored.conversation_id, state=dict(previous.state))
    db.add(next_pause)
    db.flush()
    stored.pending_id = next_pause.id
    stored.status = 'cancel_requested' if stopping else 'running'
    db.commit()
    worker.recover()
    db.expire_all()
    assert previous.status == ('interrupted' if stopping else 'paused')
    if stopping:
        assert client.post(f'/api/runs/{row["id"]}/acknowledge').status_code == 200
    else:
        assert state(client, row)['status'] == 'awaiting_approval'
        assert client.delete(f'/api/chat/approvals/{next_pause.id}').status_code == 200
    db.expire_all()
    assert not db.query(PendingApproval).filter(
        PendingApproval.conversation_id == stored.conversation_id,
        PendingApproval.status.in_(runs.approvals.UNRESOLVED),
    ).count()
