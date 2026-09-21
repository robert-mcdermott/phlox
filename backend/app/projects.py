"""Private projects and explicit, bounded context selection shared by both run modes."""
from copy import deepcopy
import hashlib
import json

from fastapi import HTTPException

from app.models import ContextRecord, Conversation, Document, Memory, Project, Source


def owned(db, project_id, user_id, *, active=False):
    row = db.get(Project, project_id, populate_existing=True)
    if row is None or row.user_id != user_id:
        raise HTTPException(404, 'Project not found')
    if active and row.archived:
        raise HTTPException(409, 'This project is archived. Restore it before continuing.')
    return row


def resolve(db, req, conversation, user_id):
    project_id = conversation.project_id if conversation else req.project_id
    return owned(db, project_id, user_id, active=True) if project_id else None


def linked_documents(db, project):
    if not project or not project.document_ids:
        return []
    return db.query(Document).filter(Document.id.in_(project.document_ids),
        Document.user_id == project.user_id, Document.assistant_id.is_(None),
        Document.conversation_id.is_(None)).order_by(Document.filename).all()


def selection(db, req, conversation, user_id, assistant=None):
    project = resolve(db, req, conversation, user_id)
    options = req.context
    # A project never implicitly opts into cross-conversation personal memory.
    memory = bool(options.memory if options.memory is not None else not project) and not req.research
    docs = linked_documents(db, project)
    excluded = set(options.excluded_document_ids)
    selected = [d.id for d in docs if d.status == 'ready' and d.id not in excluded]
    if req.research and req.research.scope == 'web':
        selected = []
    instructions = project.instructions if project and options.project_instructions else ''
    # Scope changes start a fresh context segment; old transcript stays visible but cannot
    # silently reintroduce excluded sources or instructions via previous tool results.
    signature = {'project': project.id if project else None, 'instructions': instructions,
                 'membership': (conversation.params or {}).get('project_membership') if conversation else None,
                 'selected': sorted(selected), 'options': options.model_dump(), 'research': bool(req.research),
                 'assistant': assistant.id if assistant else None}
    scoped = bool(project or signature['membership'] or excluded or options.excluded_memory_ids or options.memory is False or not options.history)
    if scoped and memory:
        inventory = db.query(Memory.id, Memory.content).filter_by(user_id=user_id).order_by(Memory.id).all()
        signature['memory_version'] = hashlib.sha256(json.dumps([list(m) for m in inventory]).encode()).hexdigest()
    key = hashlib.sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest() if scoped else None
    return {'project_id': project.id if project else None, 'project_name': project.name if project else None,
            'membership': signature['membership'],
            'project_revision': project.revision if project else None, 'instructions': instructions,
            'memory_enabled': memory, 'options': options.model_dump(), 'key': key,
            'documents': [{'id': d.id, 'filename': d.filename, 'status': d.status,
                           'selected': d.id in selected} for d in docs],
            'project_document_ids': selected}


def history_messages(conversation, data):
    from app.branches import active
    messages = active(conversation)
    if not data['options']['history']:
        return [m for m in messages if m.role == 'user'][-1:]
    if data['key'] is None:
        # Legacy chats keep their original behavior, but never replay a scoped segment
        # after a chat is removed from a project.
        wanted = None
    else:
        wanted = data['key']
    from types import SimpleNamespace
    selected = []
    for index, message in enumerate(messages):
        if message.role != 'user':
            continue
        end = next((i for i in range(index + 1, len(messages)) if messages[i].role == 'user'), len(messages))
        segment = messages[index:end]
        answer = next((m for m in segment if m.role == 'assistant' and 'context_key' in (m.usage or {})), None)
        marker = next((a for a in message.attachments or [] if a.get('type') == 'context'), {})
        key = answer.usage['context_key'] if answer else marker.get('key')
        if key == wanted:
            if answer:
                # Regeneration may use updated project context without mutating the
                # original question's attachments or another answer's history.
                segment[0] = SimpleNamespace(id=message.id, role=message.role, content=message.content,
                                             attachments=answer.usage.get('context_attachments', message.attachments))
            selected.extend(segment)
    return selected


def validate_record(db, conversation, data):
    if 'branch_leaf_id' in data and conversation.active_leaf_id != data['branch_leaf_id']:
        raise HTTPException(409, 'Conversation selection changed. Start a new turn.')
    if (conversation.params or {}).get('project_membership') != data.get('membership'):
        raise HTTPException(409, 'Conversation project changed. Start a new turn.')
    if data.get('project_id'):
        project = owned(db, data['project_id'], conversation.user_id, active=True)
        if conversation.project_id != project.id or project.revision != data['project_revision']:
            raise HTTPException(409, 'Project context changed. Start a new turn with the updated context.')
    elif conversation.project_id:
        raise HTTPException(409, 'Conversation project changed. Start a new turn.')
    for mid in data.get('memory_ids', []):
        mem = db.get(Memory, mid, populate_existing=True)
        if not mem or mem.user_id != conversation.user_id:
            raise HTTPException(409, 'Selected memory was removed. Start a new turn.')


def record_call(scope, provider, messages, *, call_id=None):
    """Record evidence present in the fitted outbound input, never an entire raw prompt.

    Dispatch is an attempt, not proof of provider processing. Guardrail-redacted excerpts
    are matched against their redacted form. Shortened/omitted excerpts aren't claimed full.
    """
    from app.database import SessionLocal
    from app.guardrails import apply_rules, get_rules
    from app.runs import LOCK

    with LOCK, SessionLocal() as db:
        row = db.get(ContextRecord, scope.turn_id)
        if not row:
            return  # Gateway and older turns don't have context records.
        conv = db.get(Conversation, row.conversation_id, populate_existing=True)
        if not conv or conv.user_id != scope.user_id:
            raise PermissionError('Context owner is unavailable')
        data = deepcopy(row.data)
        validate_record(db, conv, data)
        text = '\n'.join(m.get('content', '') for m in messages if isinstance(m.get('content'), str))
        rules = get_rules('input')
        def present(value):
            if not value:
                return False
            clean = apply_rules(value, rules)
            return not clean.blocked and bool(clean.text.strip()) and clean.text in text
        sources = db.query(Source).filter_by(conversation_id=conv.id).filter(Source.excerpt.isnot(None)).all()
        seen = [s.id for s in sources if present(s.excerpt)]
        memory_seen = [mid for mid, content in data.get('memories', {}).items() if present(content)]
        call = {'call_id': call_id, 'profile': getattr(provider, 'profile_name', None), 'model': provider.model,
                'kind': scope.kind, 'source_ids': seen, 'memory_ids': memory_seen,
                'project_instructions_present': present(data.get('instructions'))}
        calls = data.get('calls', [])
        if len(calls) < 128:
            calls.append(call)
        else:
            data['calls_truncated'] = True
        data['calls'] = calls
        row.data = data
        db.commit()


def public_record(db, conv, row):
    from app.sources import inspect_source
    from app.models import UsageLedger
    data = deepcopy(row.data)
    call_ids = [call['call_id'] for call in data.get('calls', []) if call.get('call_id')]
    ledgers = {call.message_id: call for call in db.query(UsageLedger).filter(
        UsageLedger.message_id.in_(call_ids), UsageLedger.turn_id == row.turn_id,
        UsageLedger.conversation_id == conv.id, UsageLedger.user_id == conv.user_id,
    ).all()}
    for call in data.get('calls', []):
        ledger = ledgers.get(call.get('call_id'))
        if ledger:
            details = ledger.usage_details or {}
            call['diagnostics'] = {**details.get('call', {}), 'status': ledger.status,
                                   'usage_status': ledger.usage_status,
                                   'reasoning_tokens': details.get('reasoning')}
    memory_ids = {mid for call in data.get('calls', []) for mid in call['memory_ids']}
    memory = []
    for mid, content in data.pop('memories', {}).items():
        current = db.get(Memory, mid, populate_existing=True)
        available = bool(current and current.user_id == conv.user_id)
        memory.append({'id': mid, 'content': content if available else None,
                       'available': available, 'supplied': mid in memory_ids})
    ids = {sid for call in data.get('calls', []) for sid in call['source_ids']}
    data['sources'] = [inspect_source(db, conv, sid) for sid in sorted(ids)]
    data['memories'] = memory
    data['turn_id'] = row.turn_id
    return data
