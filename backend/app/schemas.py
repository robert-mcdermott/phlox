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


# -- chat ------------------------------------------------------------------
class ChatRequest(BaseModel):
    conversation_id: str | None = None
    message: str = ""
    profile: str | None = None
    model: str | None = None
    # When true, tools whose policy is "ask" are auto-approved for this turn.
    auto_approve: bool = True
    # When true, advertise web_search to the model for this turn.
    web_search: bool = False
    # When true, re-run the existing history without appending a new user message
    # (used by "regenerate" after the last assistant turn was deleted).
    regenerate: bool = False
    # Base64 data URLs of attached images (data:image/...;base64,...).
    images: list[str] = []


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
