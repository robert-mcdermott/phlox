"""Single-process durable queue. HTTP connections only observe; the worker owns execution.

All admission/state/event writes serialize through LOCK. The deployment maintenance lock
excludes a second process. Worker sessions and subscription sessions are never shared.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import update

from app import approvals
from app.auth.deps import LOCAL_USER_ID, _dev_admin, require_owned_conversation
from app.config import get_auth_config, runs_enabled
from app.database import SessionLocal
from app.models import Conversation, PendingApproval, Run, RunEvent, ToolExecution, User
from app.schemas import ApproveRequest, ChatRequest

logger = logging.getLogger(__name__)
LOCK = threading.RLock()
BUSY = {'queued', 'running', 'cancel_requested'}
MAX_ACTIVE = 32
MAX_USER_ACTIVE = 4
MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_EVENT_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 128 * 1024
RETENTION_DAYS = 7


def owned(db, run_id, user_id):
    row = db.get(Run, run_id, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, 'Run not found')
    return row


def public(row):
    return dict(id=row.id, conversation_id=row.conversation_id, status=row.status,
                reason=row.reason, pending_id=row.pending_id, message_id=row.message_id,
                last_seq=row.last_seq, events_expired=row.events_expired,
                needs_acknowledgement=row.status == 'interrupted' and bool(row.active_conversation_id),
                queued_message=row.payload.get('message') if row.status in {'queued', 'interrupted'} else None)


def require_idle(db, conversation_id, except_run=None):
    query = db.query(Run).filter(Run.active_conversation_id == conversation_id)
    if except_run:
        query = query.filter(Run.id != except_run)
    if query.first():
        raise HTTPException(409, 'This conversation has unresolved work. Stop it or review its run status first.')


def require_deletable(db, conversation_id):
    if db.query(Run.id).filter(Run.conversation_id == conversation_id, Run.status.in_(BUSY)).first():
        raise HTTPException(409, 'Stop active work and wait for confirmation before deleting this conversation.')


def current_user(db, user_id):
    if not get_auth_config()['enabled'] and user_id == LOCAL_USER_ID:
        return _dev_admin()
    user = db.get(User, user_id, populate_existing=True)
    if not user or not user.is_active or user.must_change_password:
        raise HTTPException(401, 'Run owner is unavailable or needs password setup.')
    return user


def create(db, user, req, key=None):
    if not runs_enabled() or not worker.available:
        raise HTTPException(503, 'Reconnectable runs are not enabled or the worker is unavailable.')
    key = key or uuid.uuid4().hex
    if not 1 <= len(key) <= 100:
        raise HTTPException(400, 'Idempotency-Key must contain 1–100 characters.')
    payload = req.model_dump(mode='json')
    raw = json.dumps(payload, sort_keys=True).encode()
    if len(raw) > MAX_REQUEST_BYTES:
        raise HTTPException(413, 'Run request exceeds the 8 MiB limit.')
    digest = hashlib.sha256(raw).hexdigest()
    with LOCK:
        current_user(db, user.id)  # A request authenticated before account deletion cannot recreate private data.
        previous = db.query(Run).filter_by(user_id=user.id, request_key=key).first()
        if previous:
            if previous.request_hash != digest:
                raise HTTPException(409, 'Idempotency-Key was already used for a different request.')
            return previous
        conv = require_owned_conversation(db, req.conversation_id, user) if req.conversation_id else None
        if conv:
            require_idle(db, conv.id)
            approvals.require_no_approval(db, conv.id)
        active = db.query(Run).filter(Run.active_conversation_id.is_not(None))
        if active.count() >= MAX_ACTIVE or active.filter(Run.user_id == user.id).count() >= MAX_USER_ACTIVE:
            raise HTTPException(429, 'Run queue is full. Finish or dismiss unresolved work before retrying.')
        from app.routers.chat import _resolve_assistant
        from app.runtime_settings import generation_params, get_settings
        from app.budgets import enforce_budget
        from app.guardrails import apply_rules, get_rules

        settings = get_settings(db, user.id)
        assistant = _resolve_assistant(db, conv.assistant_id if conv else req.assistant_id, user)
        profile = req.profile or (assistant.profile if assistant else None) or settings['active_profile']
        model = req.model or (assistant.model if assistant else None) or settings.get('model')
        enforce_budget(db, user, model)
        if not req.regenerate and apply_rules(req.message, get_rules('input')).blocked:
            raise HTTPException(400, 'Message blocked by current guardrails policy.')
        if not conv:
            conv = Conversation(id=uuid.uuid4().hex, user_id=user.id, title=req.message[:60] or 'New chat',
                                profile=profile, model=model, assistant_id=assistant.id if assistant else None,
                                system_prompt=(assistant.system_prompt if assistant else None) or settings['system_prompt'],
                                params={**generation_params(settings), **((assistant.params if assistant else None) or {})})
            db.add(conv)
        payload.update(conversation_id=conv.id, profile=profile, model=model)
        row = Run(id=uuid.uuid4().hex, user_id=user.id, conversation_id=conv.id,
                  active_conversation_id=conv.id, request_key=key, request_hash=digest, payload=payload)
        db.add(row)
        db.commit()
        worker.wake.set()
        return row


def for_approval(db, pending_id, user):
    # Ownership remains 404 even when an approval ID is linked to someone else's run.
    row = db.query(Run).filter_by(pending_id=pending_id).first()
    if row is None:
        pending = db.get(PendingApproval, pending_id)
        if pending and pending.state.get('turn_id'):
            candidate = db.get(Run, pending.state['turn_id'])
            if candidate and candidate.conversation_id == pending.conversation_id:
                row = candidate
    if row:
        approvals.owned_approval(db, pending_id, user)
    return row


def resume(db, user, run_id, req):
    with LOCK:
        row = owned(db, run_id, user.id)
        if not runs_enabled() or not worker.available:
            raise HTTPException(503, 'Enable reconnectable runs and restart to resume this approval.')
        payload = req.model_dump(mode='json')
        if row.status in BUSY and row.payload == payload:
            return row  # Retry after a lost acceptance response; never claim twice.
        if row.status != 'awaiting_approval' or row.pending_id != req.pending_id:
            raise HTTPException(409, 'Run is no longer waiting for this approval. Refresh its status.')
        from app.routers.chat import prepare_approval
        prepare_approval(req, db, user, threading.Event(), validate_only=True)
        row.payload = payload
        row.status = 'queued'
        row.reason = None
        db.commit()
        worker.wake.set()
        return row


def linked_approvals(db, row):
    return [pending for pending in db.query(PendingApproval).filter_by(conversation_id=row.conversation_id)
            .filter(PendingApproval.status.in_(approvals.UNRESOLVED)).all()
            if pending.id == row.pending_id or pending.state.get('turn_id') == row.id]


def dismiss_linked(db, row):
    for pending in linked_approvals(db, row):
        if pending.status != 'claimed':
            pending.status = 'dismissed'


def _close(row, status, reason=None):
    row.status, row.reason = status, reason
    if status != 'interrupted':
        row.payload = {}
        row.active_conversation_id = None


def cancel(db, user, run_id):
    with LOCK:
        row = owned(db, run_id, user.id)
        if row.status in {'queued', 'awaiting_approval', 'failed', 'limit_reached', 'blocked'}:
            dismiss_linked(db, row)
            _close(row, 'cancelled', 'Stopped before further execution.')
            db.commit()
        elif row.status == 'interrupted':
            return acknowledge(db, user, run_id)
        elif row.status in {'running', 'cancel_requested'}:
            row.status = 'cancel_requested'
            row.reason = 'Stop requested; waiting for execution to return. Remote actions may already have happened.'
            db.commit()
            if worker.run_id == row.id:
                worker.cancel_event.set()
        return public(row)


def acknowledge(db, user, run_id):
    with LOCK:
        row = owned(db, run_id, user.id)
        if row.status != 'interrupted':
            raise HTTPException(409, 'Only interrupted runs need acknowledgement.')
        dismiss_linked(db, row)
        row.active_conversation_id = None
        db.commit()
        return public(row)


def cleanup(db, now=None):
    """Expire terminal replay content after seven days; preserve unresolved evidence."""
    cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=RETENTION_DAYS)
    rows = db.query(Run).filter(Run.active_conversation_id.is_(None), Run.updated_at < cutoff,
                               Run.events_expired.is_(False)).all()
    for row in rows:
        db.query(RunEvent).filter_by(run_id=row.id).delete(synchronize_session=False)
        row.events_expired = True
        row.payload = {}
    db.commit()
    from app.sources import cleanup as cleanup_sources
    cleanup_sources(db, now)


class EventLimit(RuntimeError):
    pass


class Recorder:
    """Batch consecutive text deltas; flush before dispatch/results/terminal events.

    A full replay log stops further work, rather than silently losing action evidence.
    Terminal status is stored on Run even when the replay budget has been exhausted.
    """

    def __init__(self, run_id, factory=SessionLocal):
        self.run_id, self.factory = run_id, factory
        self.pending = None
        self.since = time.monotonic()

    def accept(self, event):
        if event['type'] in {'token', 'thinking', 'tool_progress'}:
            if self.pending and (self.pending['type'], self.pending.get('id')) != (event['type'], event.get('id')):
                self.flush()
            if self.pending:
                self.pending['content'] += event.get('content', '')
            else:
                self.pending = dict(event)
            if len(self.pending.get('content', '')) >= 512 or time.monotonic() - self.since >= .1:
                self.flush()
        else:
            self.flush()
            self.write(event)

    def flush(self):
        if self.pending:
            event, self.pending = self.pending, None
            self.write(event)
        self.since = time.monotonic()

    def write(self, event):
        size = len(json.dumps(event).encode())
        with LOCK, self.factory() as db:
            row = db.get(Run, self.run_id)
            if not row:
                raise RuntimeError('Run removed during execution')
            if size > MAX_FRAME_BYTES or row.event_bytes + size > MAX_EVENT_BYTES:
                raise EventLimit('Saved progress limit reached; further execution stopped. Inspect the transcript and tool results.')
            row.last_seq += 1
            row.event_bytes += size
            db.add(RunEvent(run_id=row.id, seq=row.last_seq, data=event))
            if event['type'] in {'tool_start', 'child_tool_start'}:
                db.add(ToolExecution(run_id=row.id, call_id=hashlib.sha256(event['id'].encode()).hexdigest(), name=event['name'][:200]))
            elif event['type'] in {'tool_result', 'child_tool_result'}:
                db.execute(update(ToolExecution).where(ToolExecution.run_id == row.id,
                           ToolExecution.call_id == hashlib.sha256(event['id'].encode()).hexdigest(), ToolExecution.status == 'started')
                           .values(status='failed' if event.get('is_error') else 'completed'))
            elif event['type'] == 'approval_request':
                row.pending_id = event['pending_id']
            elif event['type'] == 'done':
                row.message_id = event.get('message_id') or None
            db.commit()  # The generator cannot dispatch its next action until this succeeds.


class Worker:
    def __init__(self, factory=SessionLocal):
        self.factory = factory
        self.wake, self.stopping, self.cancel_event = threading.Event(), threading.Event(), threading.Event()
        self.thread = None
        self.run_id = None

    @property
    def available(self):
        return bool(self.thread and self.thread.is_alive() and not self.stopping.is_set())

    def recover(self):
        with LOCK, self.factory() as db:
            for row in db.query(Run).filter(Run.status.in_(BUSY | {'awaiting_approval'})).all():
                # A crash after saving the pause but before its event can still recover it.
                pending = db.query(PendingApproval).filter_by(conversation_id=row.conversation_id, status='pending').all()
                pause = next((p for p in pending if p.state.get('turn_id') == row.id), None)
                if pause and row.status != 'cancel_requested':
                    row.status, row.pending_id, row.payload = 'awaiting_approval', pause.id, {}
                    row.reason = 'Server restarted. Review the pending decision before continuing.'
                else:
                    _close(row, 'interrupted', 'Server stopped before completion was confirmed. Inspect results before continuing; nothing was replayed.')
                # A new saved pause also proves the previous claim reached that pause.
                # Reconcile the whole turn, including a crash before finish_claim(old_id).
                for claim in linked_approvals(db, row):
                    if claim.status == 'claimed':
                        claim.status = 'paused' if row.status == 'awaiting_approval' else 'interrupted'
                db.execute(update(ToolExecution).where(ToolExecution.run_id == row.id,
                           ToolExecution.status == 'started').values(status='outcome_unknown'))
            db.commit()
            cleanup(db)

    def start(self):
        if self.available:
            return
        self.recover()  # Also recover when rollout is off; never orphan an old claim.
        if not runs_enabled():
            return
        self.stopping.clear()
        self.thread = threading.Thread(target=self.loop, name='phlox-run-worker', daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        self.cancel_event.set()
        self.wake.set()
        if self.thread:
            # Keep the deployment maintenance lock until all writers have actually left.
            self.thread.join()
        self.recover()

    def loop(self):
        last_cleanup = time.monotonic()
        while not self.stopping.is_set():
            try:
                if not self.step():
                    self.wake.wait(.25)
                    self.wake.clear()
                if time.monotonic() - last_cleanup > 3600:
                    with LOCK, self.factory() as db:
                        cleanup(db)
                    last_cleanup = time.monotonic()
            except Exception:
                logger.exception('Run worker stopped after a persistence failure; restart to recover safely')
                self.stopping.set()  # Do not keep dispatching after an uncertain persistence failure.

    def step(self):
        with LOCK, self.factory() as db:
            row = db.query(Run).filter_by(status='queued').order_by(Run.created_at, Run.id).first()
            if not row:
                return False
            row.status = 'running'
            self.run_id = row.id
            self.cancel_event = threading.Event()
            if self.stopping.is_set():
                self.cancel_event.set()
            db.commit()
            run_id, payload, user_id = row.id, dict(row.payload), row.user_id
            context_version = row.context_version
        recorder = Recorder(run_id, self.factory)
        outcome, reason, generator = 'failed', None, None
        try:
            with self.factory() as db:
                if context_version != 1:
                    raise HTTPException(409, 'Unsupported saved run context. Start a new turn with this release.')
                user = current_user(db, user_id)
                if self.cancel_event.is_set():
                    outcome = 'cancelled'
                else:
                    from app.routers.chat import prepare_approval, prepare_chat
                    if 'pending_id' in payload:
                        generator = prepare_approval(ApproveRequest(**payload), db, user, self.cancel_event, tool_observer=recorder.write)
                    else:
                        generator = prepare_chat(ChatRequest(**payload), db, user, self.cancel_event, run_id=run_id, tool_observer=recorder.write)
                    try:
                        for frame in generator:
                            event = json.loads(frame.removeprefix('data: ').strip())
                            recorder.accept(event)
                            if event['type'] == 'paused':
                                outcome = 'awaiting_approval'
                            elif event['type'] == 'done':
                                outcome = event.get('outcome', 'completed')
                            elif event['type'] == 'error':
                                reason = event.get('content')
                        recorder.flush()
                    except BaseException:
                        self.cancel_event.set()
                        raise
                    finally:
                        generator.close()
        except HTTPException as exc:
            reason = str(exc.detail)
        except EventLimit as exc:
            outcome, reason = 'limit_reached', str(exc)
            self.cancel_event.set()
        except Exception:
            logger.exception('Run execution failed')
            reason = 'Execution failed. Inspect saved progress before starting another turn.'
            self.cancel_event.set()
        with LOCK, self.factory() as db:
            row = db.get(Run, run_id)
            unknown = db.query(ToolExecution).filter_by(run_id=run_id, status='started').all()
            for execution in unknown:
                execution.status = 'outcome_unknown'
            pending = db.get(PendingApproval, row.pending_id) if row.pending_id else None
            # Policy/preflight failure must leave an unclaimed approval retryable.
            if 'pending_id' in payload and pending and pending.status == 'pending' and row.status != 'cancel_requested':
                outcome = 'awaiting_approval'
            if row.status == 'cancel_requested' and outcome == 'awaiting_approval':
                if pending and pending.status == 'pending':
                    pending.status = 'dismissed'
                outcome = 'cancelled'
            if unknown:
                outcome = 'interrupted'
                reason = 'A tool started but its result was not saved. Its outcome is unknown; inspect external results before acknowledging.'
            for claim in linked_approvals(db, row):
                if claim.status == 'claimed':
                    claim.status = 'paused' if outcome == 'awaiting_approval' else 'interrupted'
            if outcome == 'awaiting_approval':
                row.status, row.reason, row.payload = outcome, reason, {}
            else:
                if pending and pending.status == 'pending':
                    pending.status = 'dismissed' if outcome == 'cancelled' else 'failed'
                if reason and outcome == 'completed':
                    outcome = 'failed'
                _close(row, outcome, reason)
            db.commit()
            self.run_id = None
        return True


worker = Worker()


def subscribe(run_id, user_id, after=0):
    with SessionLocal() as db:
        row = owned(db, run_id, user_id)
        if row.events_expired:
            raise HTTPException(410, 'Saved progress expired. Reload the conversation for its final transcript.')
        if after < 0 or after > row.last_seq:
            raise HTTPException(400, 'Invalid event cursor.')

    async def stream():
        cursor = after
        while True:
            with SessionLocal() as db:
                row = owned(db, run_id, user_id)
                current_user(db, user_id)
                page = db.query(RunEvent).filter(RunEvent.run_id == run_id, RunEvent.seq > cursor).order_by(RunEvent.seq).limit(128).all()
                state = public(row)
                frames = [(ev.seq, dict(ev.data)) for ev in page]
            for seq, data in frames:
                cursor = seq
                yield f'id: {seq}\ndata: {json.dumps({**data, "run_id": run_id, "seq": seq})}\n\n'
            if len(frames) == 128:
                continue
            yield f'data: {json.dumps({"type": "run_state", **state})}\n\n'
            if state['status'] not in BUSY:
                return
            await asyncio.sleep(.25)

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
