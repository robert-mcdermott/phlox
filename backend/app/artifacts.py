"""Bounded, immutable text versions and optimistic workspace publication.

Saving a version is a database-only transaction. Publishing is a separate explicit
filesystem action, serialized with Phlox runs and guarded by the observed file hash.
"""
import hashlib
import os
from pathlib import Path
import tempfile

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import defer

from app.models import Artifact, ArtifactVersion
from app.workspace.manager import resolve_in_workspace, workspace_dir

MAX_TEXT = 1024 * 1024
HIDDEN = {'.git', '.phlox', '.hutchchat', '.venv', '__pycache__', 'node_modules'}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def text_content(data):
    if len(data) > MAX_TEXT:
        raise HTTPException(413, 'Editing supports UTF-8 text files up to 1 MiB.')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        raise HTTPException(415, 'This file is not UTF-8 text.') from None
    if any(ord(c) < 32 and c not in '\t\r\n' for c in text):
        raise HTTPException(415, 'This file contains binary control characters.')
    return text


def encode_text(content):
    try:
        return content.encode('utf-8')
    except UnicodeEncodeError:
        raise HTTPException(415, 'This text contains invalid Unicode characters.') from None


def location(conversation_id, name):
    try:
        path = resolve_in_workspace(conversation_id, name)
        relative = path.relative_to(workspace_dir(conversation_id).resolve()).as_posix()
        if relative == '.' or len(relative) > 1000 or HIDDEN.intersection(Path(relative).parts):
            raise ValueError()
        return path, relative
    except ValueError:
        raise HTTPException(400, 'Choose a file inside the conversation workspace.') from None


def read_file(path):
    try:
        with path.open('rb') as source:
            data = source.read(MAX_TEXT + 1)
        return text_content(data)
    except OSError:
        raise HTTPException(404, 'Workspace file is unavailable.') from None


def workspace_state(artifact):
    """Only bounded text files can be replaced through the editor."""
    try:
        path, _ = location(artifact.conversation_id, artifact.path)
        if not path.exists():
            return {'sha256': None, 'available': True, 'exists': False}
        content = read_file(path)
        return {'sha256': digest(content.encode('utf-8')), 'available': True, 'exists': True}
    except HTTPException:
        return {'sha256': None, 'available': False, 'exists': True}


def get_or_create(db, conversation_id, path):
    _, relative = location(conversation_id, path)
    key = digest(relative.encode('utf-8'))
    row = db.query(Artifact).filter_by(conversation_id=conversation_id, path_key=key).first()
    if row is None:
        row = Artifact(conversation_id=conversation_id, path=relative, path_key=key)
        db.add(row)
        db.flush()
    return row


def add_version(db, artifact, content, origin, *, parent=None, message_id=None, details=None, promote=True):
    text_content(encode_text(content))
    number = (db.query(func.max(ArtifactVersion.number)).filter_by(artifact_id=artifact.id).scalar() or 0) + 1
    row = ArtifactVersion(artifact_id=artifact.id, number=number, content=content,
        sha256=digest(content.encode('utf-8')), origin=origin,
        parent_version_id=parent.id if parent else artifact.head_version_id,
        source_message_id=message_id or (parent.source_message_id if parent else None), details=details)
    db.add(row)
    db.flush()
    if promote or artifact.head_version_id is None:
        artifact.head_version_id = row.id
    return row


def capture(db, message, item, data):
    """Register supported agent output without changing the original answer snapshot."""
    try:
        content = text_content(data)
        location(message.conversation_id, item['path'])
    except HTTPException:
        return
    artifact = get_or_create(db, message.conversation_id, item['path'])
    version = add_version(db, artifact, content, 'agent', message_id=message.id,
        details={'turn_id': (message.usage or {}).get('turn_id'), 'model': message.model})
    item.update(artifact_id=artifact.id, version_id=version.id)


def version(db, artifact, version_id):
    row = db.get(ArtifactVersion, version_id)
    if not row or row.artifact_id != artifact.id:
        raise HTTPException(404, 'Artifact version not found.')
    return row


def require_head(artifact, expected):
    if artifact.head_version_id != expected:
        raise HTTPException(409, 'A newer version was saved. Reload versions before saving; your draft is unchanged.')


def metadata(row):
    return {'id': row.id, 'number': row.number, 'sha256': row.sha256,
        'parent_version_id': row.parent_version_id, 'origin': row.origin,
        'source_message_id': row.source_message_id, 'details': row.details,
        'created_at': row.created_at}


def detail(db, artifact, selected=None):
    selected = selected or version(db, artifact, artifact.head_version_id)
    rows = db.query(ArtifactVersion).options(defer(ArtifactVersion.content)).filter_by(
        artifact_id=artifact.id).order_by(ArtifactVersion.number.desc()).all()
    return {'id': artifact.id, 'path': artifact.path, 'head_version_id': artifact.head_version_id,
        'version': {**metadata(selected), 'content': selected.content},
        'versions': [metadata(row) for row in rows], 'workspace': workspace_state(artifact)}


def publish(artifact, selected, expected_hash):
    state = workspace_state(artifact)
    if not state['available'] or state['sha256'] != expected_hash:
        raise HTTPException(409, 'The workspace file changed or is unavailable. Reload and review it before replacing it.')
    path, _ = location(artifact.conversation_id, artifact.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.phlox-edit-', delete=False) as output:
            temporary = Path(output.name)
            output.write(selected.content.encode('utf-8'))
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            temporary.chmod(path.stat().st_mode & 0o777)
        # Recheck after writing the temporary file. External host writers are outside
        # Phlox's process lock; filesystem publication is not a database transaction.
        latest_path, _ = location(artifact.conversation_id, artifact.path)
        if latest_path != path or workspace_state(artifact) != state:
            raise HTTPException(409, 'The workspace changed. Reload before replacing it.')
        os.replace(temporary, path)
    except OSError:
        raise HTTPException(409, 'Could not update the workspace. The saved version is still available.') from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)
    return workspace_state(artifact)
