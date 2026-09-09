"""Private project management and context inspection; admins have no read bypass."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app import projects
from app.auth.deps import get_current_user, require_owned_conversation
from app.database import get_db
from app.models import ContextRecord, Conversation, Document, Project, User
from app.schemas import ChatRequest, ConversationOut

router = APIRouter(prefix='/api', tags=['projects'])


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default='', max_length=2000)
    instructions: str = Field(default='', max_length=8000)
    document_ids: list[str] = Field(default_factory=list, max_length=32)
    archived: bool = False
    revision: int | None = None

    @field_validator('name')
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError('Project name is required')
        return value.strip()


def public(db, row):
    return {'id': row.id, 'name': row.name, 'description': row.description,
            'instructions': row.instructions, 'document_ids': [d.id for d in projects.linked_documents(db, row)],
            'archived': row.archived, 'revision': row.revision,
            'created_at': row.created_at, 'updated_at': row.updated_at}


def validate_documents(db, ids, user):
    docs = db.query(Document).filter(Document.id.in_(ids), Document.user_id == user.id,
        Document.assistant_id.is_(None), Document.conversation_id.is_(None)).all()
    if {d.id for d in docs} != set(ids):
        raise HTTPException(404, 'Library document not found')


@router.get('/projects')
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(Project).filter_by(user_id=user.id).order_by(Project.updated_at.desc()).all()
    return [public(db, row) for row in rows]


@router.post('/projects')
def create(body: ProjectInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    validate_documents(db, body.document_ids, user)
    row = Project(user_id=user.id, **body.model_dump(exclude={'revision'}))
    db.add(row)
    db.commit()
    return public(db, row)


@router.get('/projects/{project_id}')
def detail(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = projects.owned(db, project_id, user.id)
    chats = db.query(Conversation).filter_by(project_id=row.id, user_id=user.id).order_by(Conversation.updated_at.desc()).all()
    return {**public(db, row), 'conversations': [ConversationOut.model_validate(c) for c in chats],
            'documents': [{'id': d.id, 'filename': d.filename, 'status': d.status} for d in projects.linked_documents(db, row)]}


@router.put('/projects/{project_id}')
def update(project_id: str, body: ProjectInput, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.runs import LOCK, require_idle
    from app.approvals import require_no_approval
    with LOCK:
        row = projects.owned(db, project_id, user.id)
        if body.revision != row.revision:
            raise HTTPException(409, 'Project changed. Reload it before saving.')
        for conv in db.query(Conversation).filter_by(project_id=row.id, user_id=user.id):
            require_idle(db, conv.id)
            require_no_approval(db, conv.id)
        validate_documents(db, body.document_ids, user)
        for key, value in body.model_dump(exclude={'revision'}).items():
            setattr(row, key, value)
        row.revision += 1
        db.commit()
        return public(db, row)


@router.post('/context/preview')
def preview(req: ChatRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app.routers.chat import _resolve_assistant, _resolve_document_refs
    from app.runtime_settings import get_settings
    from app.config import get_profile
    from urllib.parse import urlsplit
    conv = require_owned_conversation(db, req.conversation_id, user) if req.conversation_id else None
    assistant = _resolve_assistant(db, conv.assistant_id if conv else req.assistant_id, user)
    data = projects.selection(db, req, conv, user.id, assistant)
    refs = _resolve_document_refs(db, user, conv or Conversation(id='__new__'), req.document_ids)
    shared = db.query(Document).filter_by(assistant_id=assistant.id, status='ready').all() if assistant else []
    known = {d['id'] for d in data['documents']}
    for doc in [*refs, *shared]:
        if doc.id not in known:
            data['documents'].append({'id': doc.id, 'filename': doc.filename, 'status': doc.status,
                                      'selected': doc.id not in req.context.excluded_document_ids})
            known.add(doc.id)
    settings = get_settings(db, user.id)
    profile = req.profile or (assistant.profile if assistant else None) or settings['active_profile']
    model = req.model or (assistant.model if assistant else None) or settings.get('model')
    cfg = get_profile(profile) or {}
    data.update(profile=profile, model=model, destination=urlsplit(cfg.get('endpoint') or '').hostname or cfg.get('aws_region') or cfg.get('type'),
                base_instructions=(assistant.system_prompt if assistant else None) or (conv.system_prompt if conv else None) or settings['system_prompt'],
                assistant=assistant.name if assistant else None,
                history_messages=len(projects.history_messages(conv, data)) if conv and not req.research else 0)
    from app import memory
    data['memories'] = [{'id': m.id, 'content': m.content[:4000], 'selected': m.id not in req.context.excluded_memory_ids}
                        for m in memory.list_memories(db, user.id)[:100]] if data['memory_enabled'] else []
    return data


@router.get('/conversations/{conversation_id}/context/{turn_id}')
def context_record(conversation_id: str, turn_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conv = require_owned_conversation(db, conversation_id, user)
    row = db.get(ContextRecord, turn_id)
    if not row or row.conversation_id != conv.id:
        raise HTTPException(404, 'Context record not found')
    return projects.public_record(db, conv, row)
