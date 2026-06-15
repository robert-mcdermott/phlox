"""User CRUD + authentication logic (provider-agnostic)."""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.auth.security import hash_password, verify_password
from app.config import get_auth_config
from app.models import User

logger = logging.getLogger(__name__)


def get_by_username(db: Session, username: str) -> User | None:
    return db.query(User).filter(User.username == username).first()


def get_by_external_id(db: Session, provider: str, external_id: str) -> User | None:
    return (
        db.query(User)
        .filter(User.auth_provider == provider, User.external_id == external_id)
        .first()
    )


def create_user(
    db: Session,
    username: str,
    password: str | None = None,
    role: str = "user",
    email: str | None = None,
    display_name: str | None = None,
    auth_provider: str = "local",
    external_id: str | None = None,
    department: str | None = None,
) -> User:
    user = User(
        username=username,
        email=email,
        display_name=display_name or username,
        password_hash=hash_password(password) if password else None,
        role=role,
        auth_provider=auth_provider,
        external_id=external_id,
        department=department,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def authenticate_local(db: Session, username: str, password: str) -> User | None:
    user = get_by_username(db, username)
    if not user or not user.is_active or user.auth_provider != "local":
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user


def set_password(db: Session, user: User, password: str) -> None:
    user.password_hash = hash_password(password)
    db.commit()


def delete_user_data(db: Session, user_id: str) -> dict:
    """Permanently remove all of a user's private data: conversations (+ workspaces),
    documents (+ uploaded files + vectors), memories, and per-user settings."""
    import shutil

    from app.config import UPLOADS_DIR, WORKSPACES_DIR
    from app.models import Conversation, Document, Memory, Setting

    counts = {"conversations": 0, "documents": 0, "memories": 0}

    for conv in db.query(Conversation).filter(Conversation.user_id == user_id).all():
        ws = WORKSPACES_DIR / conv.id
        if ws.exists():
            shutil.rmtree(ws, ignore_errors=True)
        db.delete(conv)  # cascades messages
        counts["conversations"] += 1

    for doc in db.query(Document).filter(Document.user_id == user_id).all():
        for p in UPLOADS_DIR.glob(f"{doc.id}_*"):
            p.unlink(missing_ok=True)
        try:
            from app.rag.store import get_vector_store

            get_vector_store().delete_by_document(doc.id)
        except Exception:  # noqa: BLE001
            pass
        db.delete(doc)  # cascades chunks
        counts["documents"] += 1

    for mem in db.query(Memory).filter(Memory.user_id == user_id).all():
        db.delete(mem)
        counts["memories"] += 1

    # Per-user settings are keyed "<user_id>:<key>".
    for s in db.query(Setting).filter(Setting.key.like(f"{user_id}:%")).all():
        db.delete(s)

    # NOTE: UsageLedger rows are intentionally NOT deleted here. They are the durable
    # chargeback record (usage metadata only, identity snapshotted at write time) and must
    # survive account deletion so a departing user's department can still be billed. This is
    # the one deliberate exception to "deletion purges all user data" — see docs/AUTH.md.
    db.commit()
    return counts


def claim_orphans(db: Session, admin_id: str) -> int:
    """Assign pre-auth (user_id NULL) data to the admin so multi-user isolation is clean."""
    from app.models import Conversation, Document, Memory

    n = 0
    for model in (Conversation, Document, Memory):
        for row in db.query(model).filter(model.user_id.is_(None)).all():
            row.user_id = admin_id
            n += 1
    if n:
        db.commit()
        logger.info("Claimed %d orphaned (pre-auth) records for admin", n)
    return n


def seed_default_admin(db: Session) -> None:
    """Create the bootstrap admin from config if no users exist yet."""
    if db.query(User).count() > 0:
        return
    cfg = get_auth_config()
    admin = cfg.get("default_admin") or {}
    username = admin.get("username", "admin")
    password = admin.get("password", "admin")
    create_user(db, username=username, password=password, role="admin", display_name="Administrator")
    logger.warning(
        "Seeded default admin account '%s' (change the password in config.yml / via the UI).",
        username,
    )
