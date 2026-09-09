"""Private artifact editing, history, explicit restore and workspace publication."""
import difflib
import asyncio
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from starlette.background import BackgroundTask
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import artifacts, branches
from app.approvals import require_no_approval
from app.auth.deps import get_current_user, require_owned_conversation
from app.database import get_db
from app.models import Artifact, ArtifactVersion, Message, User
from app.runs import require_idle

router = APIRouter(prefix='/api/artifacts/{conversation_id}', tags=['artifacts'])


class OpenInput(BaseModel):
    path: str = Field(max_length=1000)
    message_id: str | None = None
    snapshot_index: int | None = Field(default=None, ge=0)


class SaveInput(BaseModel):
    expected_head: str
    base_version_id: str
    content: str = Field(max_length=artifacts.MAX_TEXT)


class RestoreInput(BaseModel):
    expected_head: str
    version_id: str


class PublishInput(RestoreInput):
    expected_workspace_sha256: str | None


class ReviseInput(RestoreInput):
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    instruction: str = Field(min_length=1, max_length=2000)


def owned(db, conversation_id, artifact_id, user, *, writing=False):
    conv = require_owned_conversation(db, conversation_id, user)
    if writing:
        require_idle(db, conv.id)
        require_no_approval(db, conv.id)
        if conv.project_id:
            from app.projects import owned as owned_project
            owned_project(db, conv.project_id, user.id, active=True)
    row = db.get(Artifact, artifact_id)
    if not row or row.conversation_id != conversation_id:
        raise HTTPException(404, 'Artifact not found.')
    return row


@router.post('/open')
@branches.serialized
def open_artifact(conversation_id: str, body: OpenInput,
                  db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    conv = require_owned_conversation(db, conversation_id, user)
    require_idle(db, conversation_id)
    require_no_approval(db, conversation_id)
    if conv.project_id:
        from app.projects import owned as owned_project
        owned_project(db, conv.project_id, user.id, active=True)
    path, relative = artifacts.location(conversation_id, body.path)
    message = None
    item = None
    if body.message_id:
        from app.config import ATTACHMENTS_DIR
        message = db.get(Message, body.message_id)
        if not message or message.conversation_id != conversation_id:
            raise HTTPException(404, 'Saved artifact not found.')
        item = next((a for a in message.artifacts or [] if a.get('snapshot_status') == 'saved'
                     and a.get('snapshot_index') == body.snapshot_index), None)
        if item is None or artifacts.location(conversation_id, item['path'])[1] != relative:
            raise HTTPException(404, 'Saved artifact not found.')
        path = ATTACHMENTS_DIR / message.id / f'artifact-{body.snapshot_index}'
    content = artifacts.read_file(path)
    row = artifacts.get_or_create(db, conversation_id, relative)
    if item and item.get('version_id'):
        selected = artifacts.version(db, row, item['version_id'])
    elif message:
        selected = db.query(ArtifactVersion).filter_by(artifact_id=row.id, source_message_id=message.id,
            origin='saved_answer').first()
        if selected is None:
            selected = artifacts.add_version(db, row, content, 'saved_answer', message_id=message.id, promote=False)
    else:
        selected = db.query(ArtifactVersion).filter_by(artifact_id=row.id,
            sha256=artifacts.digest(content.encode('utf-8'))).order_by(ArtifactVersion.number.desc()).first()
        if selected is None:
            selected = artifacts.add_version(db, row, content, 'workspace')
    db.commit()
    return artifacts.detail(db, row, selected)


@router.get('/{artifact_id}')
def get_artifact(conversation_id: str, artifact_id: str, version_id: str | None = None,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, conversation_id, artifact_id, user)
    return artifacts.detail(db, row, artifacts.version(db, row, version_id) if version_id else None)


@router.post('/{artifact_id}/versions')
@branches.serialized
def save(conversation_id: str, artifact_id: str, body: SaveInput,
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, conversation_id, artifact_id, user, writing=True)
    artifacts.require_head(row, body.expected_head)
    base = artifacts.version(db, row, body.base_version_id)
    selected = artifacts.add_version(db, row, body.content, 'edit', parent=base)
    db.commit()
    return artifacts.detail(db, row, selected)


@router.post('/{artifact_id}/restore')
@branches.serialized
def restore(conversation_id: str, artifact_id: str, body: RestoreInput,
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, conversation_id, artifact_id, user, writing=True)
    artifacts.require_head(row, body.expected_head)
    base = artifacts.version(db, row, body.version_id)
    selected = artifacts.add_version(db, row, base.content, 'restore', message_id=base.source_message_id,
        details={'restored_version_id': base.id, 'restored_number': base.number})
    db.commit()
    return artifacts.detail(db, row, selected)


@router.post('/{artifact_id}/publish')
@branches.serialized
def publish(conversation_id: str, artifact_id: str, body: PublishInput,
            db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, conversation_id, artifact_id, user, writing=True)
    artifacts.require_head(row, body.expected_head)
    if body.version_id != row.head_version_id:
        raise HTTPException(409, 'Restore this version before using it in the workspace.')
    selected = artifacts.version(db, row, body.version_id)
    return artifacts.publish(row, selected, body.expected_workspace_sha256)


@router.get('/{artifact_id}/download/{version_id}')
def download(conversation_id: str, artifact_id: str, version_id: str,
             db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, conversation_id, artifact_id, user)
    selected = artifacts.version(db, row, version_id)
    filename = Path(row.path)
    name = f'{filename.stem}-v{selected.number}{filename.suffix}'
    return Response(selected.content.encode('utf-8'), media_type='application/octet-stream',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(name)}",
                 'X-Content-Type-Options': 'nosniff'})


@router.get('/{artifact_id}/diff')
def diff(conversation_id: str, artifact_id: str, before: str, after: str,
         db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = owned(db, conversation_id, artifact_id, user)
    left, right = artifacts.version(db, row, before), artifacts.version(db, row, after)
    a, b = left.content.splitlines(keepends=True), right.content.splitlines(keepends=True)
    if len(a) > 2000 or len(b) > 2000:
        raise HTTPException(413, 'Inline comparison supports up to 2,000 lines per version. Download versions to compare larger files.')
    result, size, truncated = [], 0, False
    for line in difflib.unified_diff(a, b, fromfile=f'v{left.number}', tofile=f'v{right.number}', lineterm=''):
        # Make line-ending changes visible, including a missing final newline.
        if line[:1] in {'+', '-', ' '} and not line.startswith(('+++', '---')):
            marker = '\n\\ No newline at end of file' if not line.endswith(('\n', '\r')) else ''
            line = line.removesuffix('\n').replace('\r', '␍') + marker
        if size + len(line) > 128_000:
            truncated = True
            break
        result.append(line)
        size += len(line) + 1
    return {'diff': '\n'.join(result), 'truncated': truncated}


@router.post('/{artifact_id}/revise')
async def revise(conversation_id: str, artifact_id: str, body: ReviseInput, request: Request,
                 db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    from app import artifact_revisions
    from app.routers.chat import _watch_disconnect
    from app.runs import LOCK
    cancel_event = threading.Event()
    with LOCK:
        row = owned(db, conversation_id, artifact_id, user, writing=True)
        artifacts.require_head(row, body.expected_head)
        selected = artifacts.version(db, row, body.version_id)
        stream, lifecycle = artifact_revisions.prepare(db, user, row, selected, body, cancel_event)
    watcher = asyncio.create_task(_watch_disconnect(request, cancel_event))

    async def finish():
        cancel_event.set()
        watcher.cancel()
        # Handles responses disconnected before the generator's first iteration.
        if not lifecycle['started']:
            branches.ACTIVE.discard(conversation_id)

    return StreamingResponse(stream, media_type='text/event-stream', background=BackgroundTask(finish))
