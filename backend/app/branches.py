"""Message ancestry and selection. Workspace state is shared, never implicitly restored."""
from functools import wraps

from fastapi import HTTPException

# Request-bound streams have no Run row. The single-process deployment still needs
# admission exclusion while they execute, including against branch/project changes.
ACTIVE = set()


def serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        from app.runs import LOCK
        with LOCK:
            return fn(*args, **kwargs)
    return call


def path(conv, leaf):
    rows = {m.id: m for m in conv.messages}
    result, seen = [], set()
    while leaf:
        if leaf in seen or leaf not in rows:
            raise HTTPException(409, 'Conversation ancestry is unavailable. Reload the conversation.')
        seen.add(leaf)
        message = rows[leaf]
        result.append(message)
        leaf = message.parent_id
    return list(reversed(result))


def active(conv):
    if hasattr(conv, '_branch_history'):
        return conv._branch_history
    # Pre-migration fixtures/integrations may construct linear messages directly.
    if conv.active_leaf_id is None:
        return list(conv.messages)
    return path(conv, conv.active_leaf_id)


def check_request(conv, req):
    if 'expected_leaf_id' in req.model_fields_set and req.expected_leaf_id != conv.active_leaf_id:
        raise HTTPException(409, 'The selected conversation changed. Reload before sending.')
    if req.edit_message_id and (req.regenerate or req.regenerate_message_id):
        raise HTTPException(400, 'Choose edit or regenerate, not both.')
    target_id = req.edit_message_id or req.regenerate_message_id
    selected = active(conv)
    target = next((m for m in selected if m.id == target_id), None) if target_id else None
    if target_id and target is None:
        raise HTTPException(404, 'Message not found on the selected path')
    if req.edit_message_id and target.role != 'user':
        raise HTTPException(400, 'Only user messages can be edited.')
    if req.regenerate_message_id and target.role != 'assistant':
        raise HTTPException(400, 'Only assistant answers can be regenerated.')
    return target


def select(conv, leaf):
    choices = dict(conv.branch_choices or {})
    for m in path(conv, leaf):
        choices[m.parent_id or 'root'] = m.id
    conv.branch_choices = choices
    conv.active_leaf_id = leaf


def choose(conv, target):
    rows = {m.id: m for m in conv.messages}
    if target not in rows:
        raise HTTPException(404, 'Message not found')
    choices = conv.branch_choices or {}
    seen = set()
    while target not in seen:
        seen.add(target)
        children = [m for m in rows.values() if m.parent_id == target]
        if not children:
            break
        preferred = choices.get(target)
        target = next((m.id for m in children if m.id == preferred), children[-1].id)
    select(conv, target)


def detail(conv):
    from app.schemas import ConversationDetail, MessageOut
    result = ConversationDetail.model_validate(conv).model_dump()
    siblings = {}
    for m in conv.messages:
        siblings.setdefault((m.parent_id, m.role), []).append(m.id)
    result['messages'] = [{**MessageOut.model_validate(m).model_dump(),
                           'alternatives': siblings[(m.parent_id, m.role)]} for m in active(conv)]
    result['has_alternatives'] = any(len(ids) > 1 for ids in siblings.values())
    return result


def initialize(conv):
    # Existing ORM-only linear imports become explicit ancestry on first append.
    if conv.active_leaf_id is None and conv.messages:
        previous = None
        for old in conv.messages:
            old.parent_id = previous
            previous = old.id
        conv.active_leaf_id = previous


def append(db, conv, message, parent_id):
    initialize(conv)
    message.parent_id = parent_id
    db.add(message)
    db.flush()
    db.expire(conv, ['messages'])
    select(conv, message.id)
