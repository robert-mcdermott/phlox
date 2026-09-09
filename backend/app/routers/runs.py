"""Owner-only run actions and replay subscriptions."""
from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy.orm import Session

from app import runs
from app.auth.deps import get_current_user, require_owned_conversation
from app.database import get_db
from app.models import Run, User
from app.schemas import ApproveRequest, ChatRequest

router = APIRouter(prefix='/api/runs', tags=['runs'])


@router.post('')
def create(body: ChatRequest, idempotency_key: str = Header(min_length=1, max_length=100),
           db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return runs.public(runs.create(db, user, body, idempotency_key))


@router.get('')
def latest(conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conv = require_owned_conversation(db, conversation_id, user)
    from app.branches import active
    selected = {m.id for m in active(conv)}
    rows = db.query(Run).filter_by(conversation_id=conversation_id, user_id=user.id).order_by(Run.created_at.desc(), Run.id.desc())
    # Unresolved work always wins; completed/failed receipts belong to their selected
    # path. A failed attempt without a saved answer belongs to its admitted leaf.
    row = rows.filter(Run.active_conversation_id.is_not(None)).first()
    if row is None:
        row = next((r for r in rows if r.message_id in selected or
                    (not r.message_id and (r.payload.get('branch_parent_id', r.payload.get('expected_leaf_id')) == conv.active_leaf_id))), None)
    return runs.public(row) if row else None


@router.get('/{run_id}')
def status(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return runs.public(runs.owned(db, run_id, user.id))


@router.get('/{run_id}/events')
def events(run_id: str, after: int = Query(0, ge=0), user: User = Depends(get_current_user)):
    return runs.subscribe(run_id, user.id, after)


@router.post('/{run_id}/cancel')
def cancel(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return runs.cancel(db, user, run_id)


@router.post('/{run_id}/acknowledge')
def acknowledge(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return runs.acknowledge(db, user, run_id)


@router.post('/{run_id}/approve')
def approve(run_id: str, body: ApproveRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return runs.public(runs.resume(db, user, run_id, body))
