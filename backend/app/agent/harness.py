"""AgentSession — the resumable agentic loop.

Drives provider tool-call rounds and emits a normalized SSE event stream. Mirrors PI
Coder's minimal model: call the model, run any requested tools (through the permission
gate + registry), feed results back, repeat until the model answers or we hit
``max_tool_rounds``.

**Human-in-the-loop:** when a requested tool's policy is ``ask`` (and the turn is not
auto-approved), the loop executes the auto-allowed calls, then **pauses** — it persists a
``PendingApproval`` capturing the full in-flight state, emits ``approval_request`` +
``paused``, and stops. ``resume`` re-hydrates that state, applies the user's decisions,
and continues — so approvals survive disconnects and don't hold server resources.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.agent import events
from app.agent.permissions import PermissionGate
from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext, ToolResult
from app.models import Conversation, Message, PendingApproval
from app.providers.base import LLMProvider, ToolCall
from app.sandbox.runner import get_runner
from app.workspace.manager import workspace_dir

logger = logging.getLogger(__name__)


class AgentSession:
    def __init__(
        self,
        db: Session,
        conversation: Conversation,
        provider: LLMProvider,
        registry: ToolRegistry,
        gate: PermissionGate,
        params: dict,
        profile: str,
        model: str | None,
        ephemeral: bool = False,
        allowed_tools: set[str] | None = None,
        fallback_provider: LLMProvider | None = None,
    ):
        self.db = db
        self.conversation = conversation
        self.provider = provider
        self.fallback_provider = fallback_provider
        self.registry = registry
        self.gate = gate
        self.params = params
        self.profile = profile
        self.model = model
        # ephemeral sessions (sub-agents) don't persist messages; they expose final_text.
        self.ephemeral = ephemeral
        self.allowed_tools = allowed_tools
        self.final_text = ""
        # Accumulated token usage across this turn (for observability + cost).
        self.turn_usage = {"input": 0, "output": 0, "total": 0}
        self.workspace = workspace_dir(conversation.id)
        self.ctx = ToolContext(
            conversation_id=conversation.id,
            workspace=self.workspace,
            db=db,
            runner=get_runner(),
            user_id=getattr(conversation, "user_id", None),
        )

    # -- public entry points ------------------------------------------------
    def run(self, messages: list[dict]) -> Iterator[str]:
        """Start a fresh turn. ``messages`` is the canonical history incl. the new user msg."""
        yield from self._loop(messages, tool_steps=[], all_artifacts=[])

    def resume(self, state: dict, decisions: dict[str, str]) -> Iterator[str]:
        """Continue a paused turn with the user's approval decisions (call_id -> allow|deny)."""
        messages = state["messages"]
        tool_steps = state.get("tool_steps", [])
        all_artifacts = state.get("all_artifacts", [])
        pending = [ToolCall(c["id"], c["name"], c["arguments"]) for c in state.get("pending_calls", [])]
        yield from self._loop(
            messages, tool_steps, all_artifacts, initial_calls=pending, initial_decisions=decisions
        )

    # -- core loop ----------------------------------------------------------
    def _loop(
        self,
        messages: list[dict],
        tool_steps: list[dict],
        all_artifacts: list[dict],
        initial_calls: list[ToolCall] | None = None,
        initial_decisions: dict[str, str] | None = None,
    ) -> Iterator[str]:
        max_rounds = int(self.params.get("max_tool_rounds", 12))
        enabled = self.allowed_tools if self.allowed_tools is not None else self.gate.enabled_names()
        tools = self.registry.specs(enabled_names=enabled)

        # If resuming, finish the previously-pending calls first.
        if initial_calls:
            paused = yield from self._process_calls(
                messages, initial_calls, tool_steps, all_artifacts, decisions=initial_decisions
            )
            if paused:
                return

        used_fallback = False
        for _round in range(max_rounds):
            round_text = ""
            pending_calls: list[ToolCall] = []
            stop_reason: str | None = None
            while True:  # retry-this-round-with-fallback loop
                streamed_any = False
                try:
                    for delta in self.provider.stream(messages, tools, self.params):
                        if delta.type == "text":
                            streamed_any = True
                            round_text += delta.text or ""
                            yield events.token(delta.text or "")
                        elif delta.type == "reasoning":
                            streamed_any = True
                            yield events.thinking(delta.text or "")
                        elif delta.type == "usage":
                            u = delta.usage or {}
                            for k in ("input", "output", "total"):
                                self.turn_usage[k] += int(u.get(k, 0) or 0)
                            yield events.usage(u)
                        elif delta.type == "tool_calls":
                            pending_calls = delta.tool_calls
                        elif delta.type == "done":
                            stop_reason = delta.stop_reason
                    break  # round streamed successfully
                except Exception as e:  # noqa: BLE001
                    # Fall back to a secondary provider if one is configured and we failed
                    # before producing any output this round (so we don't duplicate tokens).
                    if self.fallback_provider and not used_fallback and not streamed_any and not round_text:
                        logger.warning("Provider failed (%s); switching to fallback %s",
                                       e, self.fallback_provider.model)
                        yield events.status(
                            f"Primary model unavailable — switching to fallback ({self.fallback_provider.model})…"
                        )
                        self.provider = self.fallback_provider
                        used_fallback = True
                        round_text, pending_calls, stop_reason = "", [], None
                        continue  # retry the round with the fallback
                    logger.exception("Provider stream error")
                    yield events.error(f"Model error: {e}")
                    yield from self._finalize(round_text, tool_steps, all_artifacts)
                    return

            # The turn hit the output-token limit before finishing (common with heavy
            # "thinking" models or large file output) — make it visible, not silent.
            if stop_reason in ("length", "max_tokens"):
                note = (
                    "\n\n> ⚠️ **Response was cut off at the max-tokens limit** before completing"
                    " (the model may have spent the budget on reasoning and/or large output)."
                    " Increase **Max tokens** in Settings → Model and try again."
                )
                round_text += note
                yield events.token(note)

            if not pending_calls:
                yield from self._finalize(round_text, tool_steps, all_artifacts)
                return

            messages.append(
                {
                    "role": "assistant",
                    "content": round_text,
                    "tool_calls": [
                        {"id": c.id, "name": c.name, "arguments": c.arguments} for c in pending_calls
                    ],
                }
            )
            paused = yield from self._process_calls(
                messages, pending_calls, tool_steps, all_artifacts, decisions=None
            )
            if paused:
                return

        yield from self._finalize(
            "I reached the tool-call limit before finishing. Please refine the request.",
            tool_steps,
            all_artifacts,
        )

    def _process_calls(
        self,
        messages: list[dict],
        calls: list[ToolCall],
        tool_steps: list[dict],
        all_artifacts: list[dict],
        decisions: dict[str, str] | None,
    ) -> Iterator[str]:
        """Execute auto/approved calls; defer 'ask' calls. Returns True (via StopIteration
        value) if the turn paused awaiting approval."""
        ask_batch: list[ToolCall] = []

        for call in calls:
            if self.allowed_tools is not None and call.name not in self.allowed_tools:
                decision = "not_enabled"
            elif decisions is not None and call.id in decisions:
                decision = "allow" if decisions[call.id] == "allow" else "deny"
            else:
                decision = self.gate.decide(call.name)

            if decision == "ask":
                ask_batch.append(call)
                continue

            yield events.tool_call(call.id, call.name, call.arguments)
            if decision == "not_enabled":
                result = ToolResult(
                    content=f"Tool '{call.name}' is not enabled for this turn. Not executed.",
                    is_error=True,
                )
            elif decision == "deny":
                result = ToolResult(content=f"Tool '{call.name}' was denied. Not executed.", is_error=True)
            else:
                yield events.status(f"Running {call.name}…")
                result = self._execute_tool(call.name, call.arguments)
                # Snapshot the workspace after a successful mutating tool, so the change
                # is restorable (undo = restore the previous checkpoint).
                if not result.is_error:
                    self._maybe_checkpoint(call.name)

            yield from self._emit_result(call, result, tool_steps, all_artifacts, messages)

        if ask_batch:
            return (yield from self._pause(messages, ask_batch, tool_steps, all_artifacts))
        return False

    def _pause(
        self,
        messages: list[dict],
        ask_batch: list[ToolCall],
        tool_steps: list[dict],
        all_artifacts: list[dict],
    ) -> Iterator[str]:
        state = {
            "messages": messages,
            "tool_steps": tool_steps,
            "all_artifacts": all_artifacts,
            "pending_calls": [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in ask_batch],
            "params": self.params,
            "profile": self.profile,
            "model": self.model,
            "allowed_tools": sorted(self.allowed_tools) if self.allowed_tools is not None else None,
        }
        pending = PendingApproval(conversation_id=self.conversation.id, state=state)
        self.db.add(pending)
        self.db.commit()
        self.db.refresh(pending)

        # The UI renders the pending tool cards from this approval request; the actual
        # tool_call/tool_result events are emitted on resume when they run.
        yield events.approval_request(
            pending.id,
            [{"id": c.id, "name": c.name, "arguments": c.arguments} for c in ask_batch],
        )
        yield events.paused(pending.id)
        return True

    def _emit_result(
        self,
        call: ToolCall,
        result: ToolResult,
        tool_steps: list[dict],
        all_artifacts: list[dict],
        messages: list[dict],
    ) -> Iterator[str]:
        for art in result.artifacts:
            url = f"/api/files/{self.conversation.id}?path={art['path']}"
            all_artifacts.append({**art, "url": url})
            yield events.artifact(art["name"], art["path"], art.get("ext", ""), url)

        yield events.tool_result(call.id, call.name, result.content, result.is_error, result.artifacts)

        tool_steps.append(
            {
                "id": call.id,
                "name": call.name,
                "arguments": call.arguments,
                "content": result.content,
                "is_error": result.is_error,
                "artifacts": result.artifacts,
            }
        )
        messages.append(
            {
                "role": "tool",
                "tool_call_id": call.id,
                "name": call.name,
                "content": result.content if isinstance(result.content, str) else json.dumps(result.content),
            }
        )

    #: tools that change workspace files — snapshot before they run (for undo)
    MUTATING_TOOLS = {"write_file", "edit_file", "run_shell", "execute_python", "execute_node"}

    def _maybe_checkpoint(self, tool_name: str) -> None:
        if tool_name not in self.MUTATING_TOOLS:
            return
        try:
            from app.workspace.checkpoints import create_checkpoint

            create_checkpoint(self.workspace, f"after {tool_name}")
        except Exception:  # noqa: BLE001
            pass

    def _execute_tool(self, name: str, arguments: dict) -> ToolResult:
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)
        try:
            return tool.run(self.ctx, **arguments)
        except Exception as e:  # noqa: BLE001
            logger.exception("Tool %s failed", name)
            return ToolResult(content=f"Tool error: {e}", is_error=True)

    def _finalize(self, final_text: str, tool_steps: list[dict], all_artifacts: list[dict]) -> Iterator[str]:
        if self.ephemeral:
            self.final_text = final_text
            yield events.done("")
            return

        usage = None
        if self.turn_usage.get("total"):
            from app.observability import compute_cost

            usage = dict(self.turn_usage)
            usage["cost"] = compute_cost(self.provider.model, usage)

        msg = Message(
            conversation_id=self.conversation.id,
            role="assistant",
            content=final_text,
            tool_calls=tool_steps or None,
            artifacts=all_artifacts or None,
            usage=usage,
            model=self.provider.model,
        )
        self.db.add(msg)
        self.db.commit()
        self.db.refresh(msg)

        # Append to the durable usage ledger (chargeback accounting that survives user
        # deletion). Best-effort: never let a ledger failure break the turn.
        if usage:
            try:
                from app.usage_ledger import record_usage

                record_usage(
                    self.db,
                    message_id=msg.id,
                    conversation_id=self.conversation.id,
                    user_id=self.conversation.user_id,
                    model=self.provider.model,
                    usage=usage,
                )
            except Exception:  # noqa: BLE001
                logger.exception("Usage ledger write failed (continuing)")

        yield events.done(msg.id)


# Note: ``_process_calls`` / ``_pause`` return a bool via the generator's StopIteration
# value, consumed by ``yield from``. ``_finalize`` yields the final ``done`` event.
