"""SQLAlchemy ORM models (SQLite).

Tables
------
- ``Conversation``  : a chat thread; owns a per-conversation workspace directory.
- ``Message``       : one turn. ``tool_calls`` / ``artifacts`` hold structured JSON
                      so the UI can re-render tool cards and artifacts after reload.
- ``Document`` /
  ``DocChunk``      : uploaded files and their embedded chunks for RAG.
- ``Setting``       : key/value runtime settings (active profile, params, theme...).
- ``McpServer``     : configured MCP servers to connect on demand.
- ``ToolPref``      : per-tool enabled flag + permission policy.
- ``AppConfig``     : admin-edited overrides for deployment config (seeded from config.yml).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    """An account. Local users have a password_hash; SSO users (EntraID) have an
    external_id + auth_provider and no password. Role gates admin features."""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(150), unique=True, index=True)
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(20), default="user")  # user | admin
    auth_provider: Mapped[str] = mapped_column(String(20), default="local")  # local | entra
    external_id: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    # Cost-center / department for chargeback accounting (editable; mappable from SSO).
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(300), default="New chat")
    profile: Mapped[str | None] = mapped_column(String(100), nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.created_at",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    # role: user | assistant | system | tool
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text, default="")
    # Structured tool-call steps the assistant took this turn (list of dicts).
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Artifacts produced this turn (images/files): [{name, mime, path, kind}].
    artifacts: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # User-attached inputs (images): [{type:'image', idx, mime, url}].
    attachments: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # Token usage + cost for an assistant turn: {input, output, total, cost}.
    usage: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    filename: Mapped[str] = mapped_column(String(500))
    # null = global knowledge base; otherwise scoped to one conversation.
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    mime: Mapped[str | None] = mapped_column(String(200), nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    n_chunks: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending|ready|error
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    chunks: Mapped[list["DocChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )


class DocChunk(Base):
    __tablename__ = "doc_chunks"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    document_id: Mapped[str] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)
    # Embedding vector stored as JSON array of floats (small scale; fine for SQLite).
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)

    document: Mapped[Document] = relationship(back_populates="chunks")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict | list | str | int | float | bool | None] = mapped_column(JSON)


class McpServer(Base):
    __tablename__ = "mcp_servers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    transport: Mapped[str] = mapped_column(String(20), default="stdio")  # stdio | sse
    # stdio: command + args + env ; sse: url
    command: Mapped[str | None] = mapped_column(String(500), nullable=True)
    args: Mapped[list | None] = mapped_column(JSON, nullable=True)
    env: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ToolPref(Base):
    __tablename__ = "tool_prefs"

    name: Mapped[str] = mapped_column(String(120), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # permission: auto | ask | deny
    permission: Mapped[str] = mapped_column(String(10), default="auto")


class Memory(Base):
    """A durable fact/preference remembered across conversations.

    Retrieved by semantic similarity and injected into the system prompt so the assistant
    "remembers" the user across chats (ChatGPT-style memory). Small scale → cosine in
    Python over the stored embedding is fine.
    """

    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    content: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(30), default="fact")  # fact | preference | project
    source_conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class UsageLedger(Base):
    """Durable, append-only record of token usage + cost per assistant turn, for
    chargeback accounting.

    **Deliberately decoupled from the live data model:** it has *no foreign keys* to
    ``users``/``conversations`` and is excluded from ``delete_user_data``, so it survives
    user/conversation deletion. The owner's identity (username/email/department) is
    *snapshotted* at write time, freezing who to bill even after the account is gone. It
    holds usage **metadata only** — never message content — so retaining it does not expose
    private chats. This is the one intentional exception to the "deletion purges all user
    data" guarantee in docs/AUTH.md, justified by departmental chargeback.
    """

    __tablename__ = "usage_ledger"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    # The assistant Message this row was derived from; unique so backfill is idempotent.
    # Not a ForeignKey: the source message may be deleted while this row must persist.
    message_id: Mapped[str | None] = mapped_column(String(32), unique=True, nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Identity snapshot (frozen at write time; survives user deletion).
    user_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    username: Mapped[str | None] = mapped_column(String(150), nullable=True)
    email: Mapped[str | None] = mapped_column(String(300), nullable=True)
    department: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # Usage metadata.
    model: Mapped[str | None] = mapped_column(String(200), nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class PendingApproval(Base):
    """A paused agent turn awaiting user approval of one or more tool calls.

    ``state`` holds everything needed to resume statelessly: the in-flight canonical
    messages, accumulated tool steps/artifacts, the calls awaiting approval, params, and
    the profile/model. Resuming applies the user's decisions and continues the loop.
    """

    __tablename__ = "pending_approvals"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AppConfig(Base):
    """Admin-edited overrides for deployment config, applied live (no restart).

    ``config.yml`` provides the seed/bootstrap defaults; rows here override a whole config
    *section* (e.g. ``profiles``, ``pricing``, ``resilience``, ``generation``, ``sandbox``).
    The getters in ``app/config.py`` merge these over the file values. **Global** (no
    ``user_id``) — this is deployment-wide configuration, not per-user state, so it is
    untouched by ``delete_user_data``. Secrets (provider API keys) are stored here with the
    same plaintext-at-rest posture as ``config.yml`` and are never returned to the client.
    """

    __tablename__ = "app_config"

    section: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[dict | list | None] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    updated_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
