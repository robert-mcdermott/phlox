"""Shutdown admission, cancellation identity and completed-answer crash reconciliation."""
import threading
from copy import deepcopy

import pytest

from app import shutdown
from app.server import shutdown_seconds


@pytest.fixture
def clean_shutdown():
    shutdown.reset()
    yield
    shutdown.reset()


def test_nested_tracking_and_user_stop_are_distinct(clean_shutdown):
    stopped, running = threading.Event(), threading.Event()
    stopped.set()
    with shutdown.active(stopped), shutdown.active(running):
        with shutdown.active(running):
            shutdown.begin()
            shutdown.begin()
            assert running.is_set() and shutdown.interrupted(running)
            assert not shutdown.interrupted(stopped)
        assert shutdown.interrupted(running)
        with pytest.raises(RuntimeError):
            shutdown.reset()
        with pytest.raises(Exception) as error:
            shutdown.reject_new_work()
        assert error.value.status_code == 503
    with shutdown.active() as late:
        assert late.is_set() and shutdown.interrupted(late)


@pytest.mark.parametrize('value', ['0', '301', 'nan', 'inf', 'nonsense'])
def test_invalid_deadline_rejected(monkeypatch, value):
    monkeypatch.setenv('PHLOX_SHUTDOWN_SECONDS', value)
    with pytest.raises(ValueError):
        shutdown_seconds()


def test_shutdown_admission_is_503_not_logout(client, clean_shutdown):
    shutdown.begin()
    response = client.get('/api/health')
    assert response.status_code == 503 and response.headers['retry-after'] == '5'


@pytest.mark.parametrize('unknown_status', [None, 'started', 'outcome_unknown'])
def test_completed_answer_survives_crash_before_worker_close(db, unknown_status):
    import uuid
    from app.models import Conversation, Message, Run, ToolExecution
    from app.runs import Worker
    unknown = bool(unknown_status)
    conv = Conversation(user_id='local', title='Shutdown recovery fixture')
    db.add(conv)
    db.flush()
    row = Run(id=uuid.uuid4().hex, user_id='local', conversation_id=conv.id,
              active_conversation_id=conv.id, request_key=uuid.uuid4().hex, request_hash='fixture',
              status='running', payload={})
    db.add(row)
    db.flush()
    answer = Message(conversation_id=conv.id, role='assistant', content='Confirmed report',
                     usage={'turn_id': row.id, 'outcome': 'completed'})
    db.add(answer)
    if unknown:
        db.add(ToolExecution(run_id=row.id, call_id=uuid.uuid4().hex, name='fixture', status=unknown_status))
    db.commit()
    Worker().recover()
    db.refresh(row)
    assert row.status == ('interrupted' if unknown else 'completed')
    if not unknown:
        assert row.message_id == answer.id and row.active_conversation_id is None
    else:
        assert db.query(ToolExecution).filter_by(run_id=row.id).one().status == 'outcome_unknown'


def test_request_bound_shutdown_saves_interrupted_not_cancelled(db, clean_shutdown):
    from app.models import Message
    from app.providers.base import StreamDelta
    from test_research import session, ResearchProvider, parse
    from test_research_analysis import MESSAGES

    class Provider(ResearchProvider):
        def stream(self, *args):
            yield StreamDelta(type='text', text='Partial answer')
            shutdown.begin()
            yield StreamDelta(type='done', stop_reason='stop')

    cancel = threading.Event()
    agent, conv = session(db, Provider(), cancel_event=cancel)
    with shutdown.active(cancel):
        parse(agent.run(deepcopy(MESSAGES)))
    answer = db.query(Message).filter_by(conversation_id=conv.id, role='assistant').one()
    assert answer.usage['outcome'] == 'interrupted'


@pytest.mark.parametrize('streaming', [False, True])
def test_gateway_shutdown_does_not_report_success(client, monkeypatch, clean_shutdown, streaming):
    import json
    from app.providers.base import StreamDelta
    from test_gateway import _make_key

    class Provider:
        model = 'test-model'
        def stream(self, *args):
            yield StreamDelta(type='text', text='Partial progress')
            shutdown.begin()
            yield StreamDelta(type='done', stop_reason='stop')

    monkeypatch.setattr('app.routers.gateway.build_provider', lambda *args: Provider())
    key = _make_key(client)
    result = client.post('/v1/chat/completions',
                         json={'model': 'test-model', 'messages': [{'role': 'user', 'content': 'hi'}],
                               'stream': streaming},
                         headers={'Authorization': 'Bearer ' + key['key']})
    if streaming:
        frames = [json.loads(line[6:]) for line in result.text.splitlines()
                  if line.startswith('data: ') and line != 'data: [DONE]']
        assert frames[-1]['error']['type'] == 'server_error'
        assert not any(choice.get('finish_reason') == 'stop'
                       for frame in frames for choice in frame.get('choices', []))
    else:
        assert result.status_code == 503
        assert result.json()['error']['type'] == 'server_error'


def test_server_reports_failed_startup(monkeypatch):
    import uvicorn
    from app.server import Server
    monkeypatch.setattr(uvicorn.Server, 'run', lambda *args: None)
    with pytest.raises(SystemExit) as exc:
        Server(uvicorn.Config('app.main:app')).run()
    assert exc.value.code == 3
