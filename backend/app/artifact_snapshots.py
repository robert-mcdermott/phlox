"""Bounded saved answer files, distinct from the mutable execution workspace."""
import logging
import shutil

from sqlalchemy import event
from sqlalchemy.orm import Session, object_session

from app.config import ATTACHMENTS_DIR
from app.models import Message
from app.workspace.manager import resolve_in_workspace

MAX_FILE = 32 * 1024 * 1024
MAX_TURN = 64 * 1024 * 1024
logger = logging.getLogger(__name__)


def unique_artifacts(artifacts):
    """One final descriptor per full workspace path; updates retain first-seen order."""
    latest = {}
    for index, artifact in enumerate(artifacts or []):
        key = ('path', artifact['path']) if artifact.get('path') else ('unkeyed', index)
        latest[key] = artifact
    return list(latest.values())


def capture(message):
    remaining = MAX_TURN
    result = []
    for index, artifact in enumerate(unique_artifacts(message.artifacts)):
        item = {**artifact, 'snapshot_status': 'unavailable'}
        try:
            path = resolve_in_workspace(message.conversation_id, artifact['path'])
            limit = min(MAX_FILE, remaining)
            if not path.is_file():
                raise OSError('Artifact no longer exists')
            if path.stat().st_size > limit:
                item['snapshot_status'] = 'size_limit'
            else:
                with path.open('rb') as source:
                    data = source.read(limit + 1)
                if len(data) > limit:
                    item['snapshot_status'] = 'size_limit'
                else:
                    destination = ATTACHMENTS_DIR / message.id / f'artifact-{index}'
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    remaining -= len(data)
                    item.update(snapshot_status='saved', snapshot_index=index, size=len(data),
                                url=f'/api/files/{message.conversation_id}/saved/{message.id}/{index}')
                    db = object_session(message)
                    if db is not None:
                        from app.artifacts import capture as capture_version
                        capture_version(db, message, item, data)
        except (OSError, ValueError, KeyError):
            logger.info('Could not retain an answer artifact snapshot')
        result.append(item)
    message.artifacts = result or None
    by_path = {a.get('path'): a for a in result}
    if message.tool_calls:
        message.tool_calls = [{**step, 'artifacts': [by_path.get(a.get('path'), a)
                             for a in step.get('artifacts') or []]} for step in message.tool_calls]


@event.listens_for(Session, 'after_flush')
def queue_deleted_files(session, _):
    session.info.setdefault('deleted_message_files', set()).update(
        row.id for row in session.deleted if isinstance(row, Message))


@event.listens_for(Session, 'after_commit')
def delete_files(session):
    for message_id in session.info.pop('deleted_message_files', ()):
        shutil.rmtree(ATTACHMENTS_DIR / message_id, ignore_errors=True)


@event.listens_for(Session, 'after_rollback')
def keep_files(session):
    session.info.pop('deleted_message_files', None)
