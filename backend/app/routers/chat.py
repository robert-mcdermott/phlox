"""POST /api/chat — run the agent and stream the result as SSE.
POST /api/chat/approve — resume a paused turn with the user's tool-approval decisions.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from fastapi import HTTPException

from app.agent import events
from app.agent.context import compact_history
from app.agent.harness import AgentSession
from app.agent.permissions import PermissionGate
from app.agent.registry import REGISTRY
from app.auth.deps import get_current_user
from app.database import get_db
from app.models import Conversation, Message, PendingApproval, User
from app.providers.registry import build_provider
from app.runtime_settings import generation_params, get_settings
from app.schemas import ApproveRequest, ChatRequest

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["chat"])


def _build_history(conversation: Conversation, system_prompt: str) -> list[dict]:
    """Reconstruct canonical message history (incl. tool steps) for the provider."""
    history: list[dict] = [{"role": "system", "content": system_prompt}]
    for m in conversation.messages:
        if m.role == "user":
            entry = {"role": "user", "content": m.content}
            if m.attachments:
                from app.attachments import load_image_data_urls

                imgs = load_image_data_urls(m.id, m.attachments)
                if imgs:
                    entry["images"] = imgs
            history.append(entry)
        elif m.role == "assistant":
            for step in m.tool_calls or []:
                history.append(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": step["id"], "name": step["name"], "arguments": step["arguments"]}
                        ],
                    }
                )
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": step["id"],
                        "name": step["name"],
                        "content": step["content"],
                    }
                )
            if m.content:
                history.append({"role": "assistant", "content": m.content})
    return history


def _build_fallback(active_profile: str):
    """Build the configured fallback provider (if any and distinct from the active one)."""
    from app.config import get_resilience_config

    fb = get_resilience_config().get("fallback_profile")
    if not fb or fb == active_profile:
        return None
    try:
        return build_provider(fb)
    except Exception as e:  # noqa: BLE001
        logger.warning("Fallback provider %s unavailable: %s", fb, e)
        return None


@router.post("/chat")
def chat(req: ChatRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    settings = get_settings(db, user.id)
    profile = req.profile or settings["active_profile"]
    model = req.model or settings.get("model")

    # Budget gate: refuse a priced model once this user (or their department) is over their
    # monthly cap. Runs before any message is persisted so a blocked turn writes nothing.
    from app.budgets import enforce_budget

    enforce_budget(db, user, model)

    conversation: Conversation | None = None
    if req.conversation_id:
        conversation = db.get(Conversation, req.conversation_id)
        # Conversations are private to their creator (admins included).
        if conversation and conversation.user_id != user.id:
            raise HTTPException(404, "Conversation not found")
    if conversation is None:
        conversation = Conversation(
            title=req.message[:60] or "New chat",
            profile=profile,
            model=model,
            system_prompt=settings["system_prompt"],
            params=generation_params(settings),
            user_id=user.id,
        )
        db.add(conversation)
        db.commit()
        db.refresh(conversation)

    system_prompt = conversation.system_prompt or settings["system_prompt"]
    # Inject relevant long-term memories (this user's, cross-conversation) into the prompt.
    from app.memory import memory_preamble

    system_prompt += memory_preamble(db, req.message, user.id)

    # Regenerate re-runs existing history (the client already removed the prior assistant
    # turn); otherwise append the new user message (+ any image attachments).
    if not req.regenerate:
        user_msg = Message(conversation_id=conversation.id, role="user", content=req.message)
        db.add(user_msg)
        db.commit()
        if req.images:
            from app.attachments import save_message_images

            user_msg.attachments = save_message_images(user_msg.id, req.images)
            db.commit()
        db.refresh(conversation)

    history = _build_history(conversation, system_prompt)
    params = {
        **generation_params(settings),
        "max_tool_rounds": (conversation.params or {}).get(
            "max_tool_rounds", settings["max_tool_rounds"]
        ),
    }

    def stream():
        yield events.sse("conversation", id=conversation.id, title=conversation.title)
        try:
            provider = build_provider(profile, model)
        except Exception as e:  # noqa: BLE001
            logger.exception("Provider build failed")
            yield events.error(f"Provider error: {e}")
            yield events.done("")
            return

        # Build an optional fallback provider (used if the primary errors mid-stream).
        fallback = _build_fallback(profile)

        # Compact long histories to stay within the per-user context budget.
        compacted, did = compact_history(provider, history, int(settings["max_context_tokens"]))
        if did:
            yield events.status("Summarizing earlier context…")

        gate = PermissionGate(db, REGISTRY, auto_approve=req.auto_approve)
        enabled_tools = gate.enabled_names()
        if not req.web_search:
            enabled_tools.discard("web_search")
        session = AgentSession(
            db, conversation, provider, REGISTRY, gate, params, profile, model,
            allowed_tools=enabled_tools,
            fallback_provider=fallback,
        )
        yield from session.run(compacted)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.post("/chat/approve")
def approve(req: ApproveRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Resume a paused turn. ``decisions`` maps tool-call id -> 'allow' | 'deny'."""
    pending = db.get(PendingApproval, req.pending_id)

    def stream():
        if pending is None:
            yield events.error("This approval request is no longer valid.")
            yield events.done("")
            return

        state = pending.state
        conversation = db.get(Conversation, pending.conversation_id)
        if conversation is None:
            yield events.error("Conversation not found.")
            yield events.done("")
            return
        if conversation.user_id != user.id:
            yield events.error("Not authorized.")
            yield events.done("")
            return

        yield events.sse("conversation", id=conversation.id, title=conversation.title)
        try:
            provider = build_provider(state["profile"], state.get("model"))
        except Exception as e:  # noqa: BLE001
            yield events.error(f"Provider error: {e}")
            yield events.done("")
            return

        gate = PermissionGate(db, REGISTRY, auto_approve=False)
        allowed_tools = state.get("allowed_tools")
        session = AgentSession(
            db, conversation, provider, REGISTRY, gate,
            state.get("params", {}), state["profile"], state.get("model"),
            allowed_tools=set(allowed_tools) if allowed_tools is not None else None,
        )
        # Consume the pending row before resuming (it's superseded once we continue).
        db.delete(pending)
        db.commit()
        yield from session.resume(state, req.decisions)

    return StreamingResponse(stream(), media_type="text/event-stream")
