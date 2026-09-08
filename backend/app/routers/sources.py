"""Source reads and portable exports always recheck current ownership and document access."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import sources
from app.auth.deps import get_current_user, require_owned_conversation
from app.database import get_db
from app.models import User

router = APIRouter(prefix='/api/conversations', tags=['sources'])


@router.get('/{conversation_id}/sources/{source_id}')
def inspect_source(conversation_id: str, source_id: str, db: Session = Depends(get_db),
                   user: User = Depends(get_current_user)):
    conv = require_owned_conversation(db, conversation_id, user)
    return sources.inspect_source(db, conv, source_id)


@router.get('/{conversation_id}/export')
def export(conversation_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conv = require_owned_conversation(db, conversation_id, user)
    return {'markdown': sources.export_markdown(db, conv)}


@router.delete('/{conversation_id}/sources/{source_id}')
def forget_source(conversation_id: str, source_id: str, db: Session = Depends(get_db),
                  user: User = Depends(get_current_user)):
    conv = require_owned_conversation(db, conversation_id, user)
    sources.forget_web(db, conv, source_id)
    return {'deleted': source_id}
