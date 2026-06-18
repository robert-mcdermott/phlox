"""OpenAI-compatible gateway: turn Phlox into an authenticated LLM endpoint.

Phase 1 (raw passthrough) of issue #5: exactly one model call per request, so cost is
predictable and existing OpenAI SDKs/tools work unmodified against ``<phlox>/v1``.

    POST /v1/chat/completions   — streaming (SSE) or buffered, OpenAI request/response shape
    GET  /v1/models             — the catalog of callable models across all profiles

Auth is an API key (``Authorization: Bearer phlox-sk-...``) resolved to the owning user by
:func:`app.auth.deps.require_api_key`. Usage is captured at the **model-call layer** and
written to the same durable :class:`~app.models.UsageLedger` as interactive chat, so the
admin chargeback view ("usage by month × user × department × model") already includes
gateway traffic with no extra work. Tools/RAG/agentic behavior are intentionally **not**
exposed here — that is Phase 2's ``/v1/agent/completions``.

The handler is provider-agnostic: it reuses ``build_provider`` and the canonical
``StreamDelta`` interface, so OpenAI-compatible backends, Bedrock, and local models all work.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Iterator

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth.deps import require_api_key
from app.database import get_db
from app.models import User
from app.observability import compute_cost
from app.providers.registry import build_provider, gateway_models, resolve_model

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["gateway"])


# --- Request schema (OpenAI-compatible subset) -------------------------------
class ChatMessage(BaseModel):
    role: str
    content: str | None = None


class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    # Accepted and ignored for compatibility (Phase 1 is single-call passthrough).
    top_p: float | None = None
    stop: list[str] | str | None = None
    user: str | None = None


def _err(status: int, message: str, etype: str = "invalid_request_error") -> JSONResponse:
    """OpenAI-style error envelope so client SDKs parse failures cleanly."""
    return JSONResponse(status_code=status, content={"error": {"message": message, "type": etype}})


def _canonical(messages: list[ChatMessage]) -> list[dict]:
    return [{"role": m.role, "content": m.content or ""} for m in messages]


def _record(db: Session, *, request_id: str, user_id: str, model: str, usage: dict) -> None:
    """Best-effort per-call ledger write (never breaks the API response)."""
    if not usage.get("total"):
        return
    usage = dict(usage)
    usage["cost"] = compute_cost(model, usage)
    try:
        from app.usage_ledger import record_gateway_usage

        record_gateway_usage(
            db, request_id=request_id, user_id=user_id, model=model, usage=usage
        )
    except Exception:  # noqa: BLE001
        logger.exception("Gateway usage ledger write failed (continuing)")


@router.get("/models")
def list_v1_models(_user: User = Depends(require_api_key)):
    """OpenAI-compatible model list. ``id`` is the fully-qualified ``profile/model``."""
    created = int(time.time())
    return {
        "object": "list",
        "data": [
            {"id": m["id"], "object": "model", "created": created, "owned_by": m["profile"]}
            for m in gateway_models()
        ],
    }


@router.post("/chat/completions")
def chat_completions(
    req: ChatCompletionRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_api_key),
):
    if not req.messages:
        return _err(400, "'messages' must not be empty.")
    try:
        profile, model = resolve_model(req.model)
    except ValueError as e:
        return _err(404, str(e), etype="model_not_found")
    try:
        provider = build_provider(profile, model)
    except Exception as e:  # noqa: BLE001
        logger.exception("Gateway provider build failed")
        return _err(502, f"Provider error: {e}", etype="api_error")

    messages = _canonical(req.messages)
    params = {
        "temperature": req.temperature if req.temperature is not None else 0.3,
        "max_tokens": req.max_tokens or 4096,
    }
    request_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    # The id clients see uses the model string they sent; ledger uses our resolved model.
    advertised_model = req.model

    if req.stream:
        return StreamingResponse(
            _stream(db, provider, messages, params, request_id, created, advertised_model, user.id),
            media_type="text/event-stream",
        )
    return _buffered(db, provider, messages, params, request_id, created, advertised_model, user.id)


def _buffered(
    db, provider, messages, params, request_id, created, advertised_model, user_id
) -> JSONResponse:
    """Run one non-streaming model call and return a full OpenAI ``chat.completion``."""
    text = ""
    usage = {"input": 0, "output": 0, "total": 0}
    finish_reason = "stop"
    try:
        for delta in provider.stream(messages, [], params):
            if delta.type == "text":
                text += delta.text or ""
            elif delta.type == "usage":
                usage = delta.usage or usage
            elif delta.type == "done":
                finish_reason = _finish(delta.stop_reason)
    except Exception as e:  # noqa: BLE001
        logger.exception("Gateway model call failed")
        return _err(502, f"Upstream model error: {e}", etype="api_error")

    _record(db, request_id=request_id, user_id=user_id, model=provider.model, usage=usage)
    return JSONResponse(
        content={
            "id": request_id,
            "object": "chat.completion",
            "created": created,
            "model": advertised_model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": finish_reason,
                }
            ],
            "usage": {
                "prompt_tokens": usage.get("input", 0),
                "completion_tokens": usage.get("output", 0),
                "total_tokens": usage.get("total", 0),
            },
        }
    )


def _stream(
    db, provider, messages, params, request_id, created, advertised_model, user_id
) -> Iterator[str]:
    """Emit OpenAI-compatible ``chat.completion.chunk`` SSE frames for one model call."""
    base = {"id": request_id, "object": "chat.completion.chunk", "created": created,
            "model": advertised_model}

    def frame(delta: dict, finish_reason=None) -> str:
        payload = {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}]}
        return f"data: {json.dumps(payload)}\n\n"

    # First chunk carries the assistant role, per the OpenAI streaming contract.
    yield frame({"role": "assistant"})
    usage = {"input": 0, "output": 0, "total": 0}
    finish_reason = "stop"
    try:
        for delta in provider.stream(messages, [], params):
            if delta.type == "text" and delta.text:
                yield frame({"content": delta.text})
            elif delta.type == "usage":
                usage = delta.usage or usage
            elif delta.type == "done":
                finish_reason = _finish(delta.stop_reason)
    except Exception as e:  # noqa: BLE001
        logger.exception("Gateway streaming model call failed")
        # Surface a terminal error frame, then close the stream.
        yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'api_error'}})}\n\n"
        yield "data: [DONE]\n\n"
        return

    yield frame({}, finish_reason=finish_reason)
    yield "data: [DONE]\n\n"
    _record(db, request_id=request_id, user_id=user_id, model=provider.model, usage=usage)


def _finish(stop_reason: str | None) -> str:
    """Map provider stop reasons to OpenAI ``finish_reason`` values."""
    if stop_reason in ("length", "max_tokens"):
        return "length"
    if stop_reason == "tool_calls":
        return "tool_calls"
    return "stop"
