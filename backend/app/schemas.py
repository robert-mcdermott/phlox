"""Pydantic request/response models."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator


# -- conversations / messages ---------------------------------------------
class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    tool_calls: list[dict] | None = None
    artifacts: list[dict] | None = None
    attachments: list[dict] | None = None
    usage: dict | None = None
    model: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True


class ConversationOut(BaseModel):
    id: str
    title: str
    profile: str | None = None
    model: str | None = None
    assistant_id: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class ConversationDetail(ConversationOut):
    system_prompt: str | None = None
    params: dict | None = None
    messages: list[MessageOut] = []


class ConversationCreate(BaseModel):
    title: str | None = None
    profile: str | None = None
    model: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = None
    profile: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    params: dict | None = None


# -- assistants --------------------------------------------------------------
_AVATAR_MAX_CHARS = 200_000  # ~150 KB base64 image; client resizes before upload


def _validate_visibility(v: str | None) -> str | None:
    if v is not None and v not in ("public", "private"):
        raise ValueError("visibility must be one of: public, private")
    return v


def _validate_avatar(v: str | None) -> str | None:
    if not v:
        return None
    if len(v) > _AVATAR_MAX_CHARS:
        raise ValueError("avatar image too large; resize before upload")
    if not v.startswith("data:image/") and len(v) > 16:
        raise ValueError("avatar must be a data:image/ URL or a short emoji")
    return v


class AssistantBase(BaseModel):
    name: str
    description: str | None = None
    # Either a "data:image/..." data URL or a short emoji string.
    avatar: str | None = None
    # Base model; null falls back to the chatting user's settings.
    profile: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    # Generation-param overrides (no editor UI yet).
    params: dict | None = None
    prompt_suggestions: list[str] = []
    # Hard capability limits: {"web_search", "document_search", "tools"} -> bool.
    capabilities: dict[str, bool] = {}
    visibility: str = "public"  # public | private

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, v: str) -> str:
        return _validate_visibility(v)

    @field_validator("avatar")
    @classmethod
    def _check_avatar(cls, v: str | None) -> str | None:
        return _validate_avatar(v)

    @field_validator("name")
    @classmethod
    def _check_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("name is required")
        return v

    # JSON columns come back as NULL from the ORM; coerce to the typed defaults.
    @field_validator("prompt_suggestions", mode="before")
    @classmethod
    def _default_suggestions(cls, v):
        return v if v is not None else []

    @field_validator("capabilities", mode="before")
    @classmethod
    def _default_capabilities(cls, v):
        return v if v is not None else {}


class AssistantCreate(AssistantBase):
    pass


class AssistantUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    avatar: str | None = None
    profile: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    params: dict | None = None
    prompt_suggestions: list[str] | None = None
    capabilities: dict[str, bool] | None = None
    visibility: str | None = None
    is_active: bool | None = None

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, v: str | None) -> str | None:
        return _validate_visibility(v)

    @field_validator("avatar")
    @classmethod
    def _check_avatar(cls, v: str | None) -> str | None:
        return _validate_avatar(v)


class AssistantOut(AssistantBase):
    id: str
    created_by: str | None = None
    is_active: bool = True
    # Ready knowledge-base document count, computed in the router.
    n_documents: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# -- skills ------------------------------------------------------------------
class SkillBase(BaseModel):
    # Slug handle; the router normalizes via app.skills.slugify, this just rejects empties.
    name: str
    # What the skill does AND when to use it — the model-facing activation trigger.
    description: str
    # Full markdown instructions (the SKILL.md body).
    instructions: str
    # Advertise to the model for self-serve activation via the use_skill tool.
    auto_activate: bool = True
    visibility: str = "private"  # public | private (public requires admin)

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, v: str) -> str:
        return _validate_visibility(v)

    @field_validator("name", "description")
    @classmethod
    def _check_required_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("field is required")
        return v


class SkillCreate(SkillBase):
    pass


class SkillUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    instructions: str | None = None
    auto_activate: bool | None = None
    visibility: str | None = None
    is_active: bool | None = None

    @field_validator("visibility")
    @classmethod
    def _check_visibility(cls, v: str | None) -> str | None:
        return _validate_visibility(v)


class SkillOut(SkillBase):
    id: str
    created_by: str | None = None
    is_active: bool = True
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# -- chat ------------------------------------------------------------------
class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = ""
    profile: str | None = None
    model: str | None = None
    # Assistant persona for a NEW conversation; ignored on existing conversations
    # (the conversation's pinned assistant wins).
    assistant_id: str | None = None
    # When true, tools whose policy is "ask" are auto-approved for this turn.
    auto_approve: bool = True
    # When true, advertise web_search to the model for this turn.
    web_search: bool = False
    # When true, advertise search_documents and instruct the model to use it this turn.
    document_search: bool = False
    # Uploaded/library documents directly referenced by this user message.
    document_ids: list[str] = []
    # When true, re-run the existing history without appending a new user message
    # (used by "regenerate" after the last assistant turn was deleted).
    regenerate: bool = False
    # Base64 data URLs of attached images (data:image/...;base64,...).
    images: list[str] = []
    # Skill slugs the user explicitly invoked ("/name" in the composer) for this message;
    # their full instructions are injected into the system prompt for this turn.
    skills: list[str] = []
    # When true, advertise registered skills (name + description) and the use_skill tool
    # so the model can load relevant skills on its own (progressive disclosure).
    skills_enabled: bool = True


class ApproveRequest(BaseModel):
    pending_id: str
    # call_id -> "allow" | "deny"
    decisions: dict[str, str]


# -- settings --------------------------------------------------------------
class SettingsOut(BaseModel):
    active_profile: str
    model: str | None = None
    theme: str = "phlox-dark"
    system_prompt: str
    temperature: float
    max_tokens: int
    max_tool_rounds: int
    max_context_tokens: int


class SettingsUpdate(BaseModel):
    active_profile: str | None = None
    model: str | None = None
    theme: str | None = None
    system_prompt: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    max_tool_rounds: int | None = None
    max_context_tokens: int | None = None


# -- mcp -------------------------------------------------------------------
class McpServerIn(BaseModel):
    name: str
    transport: str = "stdio"  # stdio | sse | http
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    # Optional auth for network transports (sse/http).
    headers: dict[str, str] | None = None
    auth_token: str | None = None  # sent as "Authorization: Bearer <token>"
    enabled: bool = True

    @field_validator("transport")
    @classmethod
    def _check_transport(cls, v: str) -> str:
        if v not in ("stdio", "sse", "http"):
            raise ValueError("transport must be one of: stdio, sse, http")
        return v


class McpServerOut(McpServerIn):
    id: str
    connected: bool = False
    tools: list[str] = []


# -- tools -----------------------------------------------------------------
class ToolOut(BaseModel):
    name: str
    description: str
    category: str
    enabled: bool
    permission: str
    parameters: dict[str, Any] = {}


class ToolUpdate(BaseModel):
    enabled: bool | None = None
    permission: str | None = None


# -- admin deployment config (config.yml overlay) -------------------------
# These are intentionally permissive: profiles are heterogeneous (openai vs bedrock have
# different fields), so each section validates the few invariants that matter and lets the
# router/getters handle the rest. Secrets are write-only — see routers/admin_config.py.
class ProfileIn(BaseModel):
    name: str
    type: str = "openai"            # "openai" | "bedrock"
    label: str | None = None
    model: str | None = None
    models: list[str] | None = None
    supports_tools: bool = True
    # openai
    endpoint: str | None = None
    api_key: str | None = None      # write-only; omitted/empty => keep existing
    # bedrock
    aws_region: str | None = None
    aws_profile: str | None = None
    aws_access_key_id: str | None = None
    aws_secret_access_key: str | None = None  # write-only
    aws_session_token: str | None = None      # write-only
    aws_bedrock_api_key: str | None = None    # write-only; single Bedrock API key (bearer)
    prompt_cache: bool | None = None


class ProfilesUpdate(BaseModel):
    profiles: list[ProfileIn]


class PriceRate(BaseModel):
    input: float = 0.0             # USD per 1,000,000 input tokens
    output: float = 0.0            # USD per 1,000,000 output tokens


class PricingUpdate(BaseModel):
    pricing: dict[str, PriceRate]  # model id -> rate


class ResilienceUpdate(BaseModel):
    timeout: int | None = None
    max_retries: int | None = None
    fallback_profile: str | None = None


class GenerationUpdate(BaseModel):
    temperature: float | None = None
    max_tokens: int | None = None
    max_tool_rounds: int | None = None
    max_context_tokens: int | None = None
    system_prompt: str | None = None


class SandboxLimits(BaseModel):
    memory: str | None = None
    cpus: str | None = None
    pids_limit: int | None = None
    network: str | None = None     # "none" | "bridge"
    python_image: str | None = None
    node_image: str | None = None
    engine: str | None = None      # auto | podman | docker | <path>


class SandboxUpdate(BaseModel):
    container: SandboxLimits


class BudgetCreate(BaseModel):
    scope_type: str                  # "user" | "department"
    scope_value: str                 # user id, or department name
    limit_usd: float
    warn_pct: int = 90
    is_active: bool = True


class BudgetUpdate(BaseModel):
    limit_usd: float | None = None
    warn_pct: int | None = None
    is_active: bool | None = None
