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
import queue
import threading
from collections.abc import Iterator
from dataclasses import replace
from typing import Any

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
        cancel_event: threading.Event | None = None,
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
        # Set by the caller (e.g. the chat router, watching for a client disconnect) so a
        # user's "Stop" click can actually halt an in-flight turn — kill any running
        # subprocess and stop the loop — instead of just closing the SSE connection while
        # the turn keeps running server-side to completion.
        self.cancel_event = cancel_event
        # Accumulated token usage across this turn (for observability + cost).
        self.turn_usage = {"input": 0, "output": 0, "total": 0}
        self.workspace = workspace_dir(conversation.id)
        self.ctx = ToolContext(
            conversation_id=conversation.id,
            workspace=self.workspace,
            db=db,
            runner=get_runner(),
            user_id=getattr(conversation, "user_id", None),
            auto_approve=gate.auto_approve,
            cancel_event=cancel_event,
        )

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

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
            if self._cancelled():
                yield from self._finalize("", tool_steps, all_artifacts)
                return
            round_text = ""
            pending_calls: list[ToolCall] = []
            stop_reason: str | None = None
            while True:  # retry-this-round-with-fallback loop
                streamed_any = False
                try:
                    for delta in self.provider.stream(messages, tools, self.params):
                        if self._cancelled():
                            # Best-effort: stop consuming further streamed tokens/tool
                            # calls as soon as we notice — the provider call itself may
                            # not be interruptible mid-flight, but we stop paying
                            # attention (and stop acting on it) immediately.
                            break
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
        to_run: list[ToolCall] = []

        for call in calls:
            if self._cancelled():
                break
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
                yield from self._emit_result(call, result, tool_steps, all_artifacts, messages)
            elif decision == "deny":
                result = ToolResult(content=f"Tool '{call.name}' was denied. Not executed.", is_error=True)
                yield from self._emit_result(call, result, tool_steps, all_artifacts, messages)
            else:
                to_run.append(call)

        # spawn_subagent calls requested together in the same round are the model
        # explicitly decomposing a task into independent chunks — run those in parallel.
        # Everything else runs sequentially, as before (most tools mutate the same
        # workspace files directly and aren't safe to run concurrently).
        subagent_calls = [c for c in to_run if c.name == "spawn_subagent"]
        other_calls = [c for c in to_run if c.name != "spawn_subagent"]

        yield from self._run_sequential(other_calls, tool_steps, all_artifacts, messages)

        if len(subagent_calls) >= 2:
            yield events.status(f"Running {len(subagent_calls)} sub-agents…")
            results = yield from self._run_calls_concurrently(subagent_calls)
            for call, result in zip(subagent_calls, results, strict=True):
                if not result.is_error:
                    self._maybe_checkpoint(call.name)
                yield from self._emit_result(call, result, tool_steps, all_artifacts, messages)
        else:
            yield from self._run_sequential(subagent_calls, tool_steps, all_artifacts, messages)

        if ask_batch:
            return (yield from self._pause(messages, ask_batch, tool_steps, all_artifacts))
        return False

    def _run_sequential(
        self,
        calls: list[ToolCall],
        tool_steps: list[dict],
        all_artifacts: list[dict],
        messages: list[dict],
    ) -> Iterator[str]:
        for call in calls:
            if self._cancelled():
                break
            yield events.status(f"Running {call.name}…")
            result = yield from self._execute_tool_streaming(call.id, call.name, call.arguments)
            # Snapshot the workspace after a successful mutating tool, so the change is
            # restorable (undo = restore the previous checkpoint).
            if not result.is_error:
                self._maybe_checkpoint(call.name)
            yield from self._emit_result(call, result, tool_steps, all_artifacts, messages)

    def _run_calls_concurrently(self, calls: list[ToolCall]) -> Iterator[str]:
        """Run independent tool calls in parallel worker threads, interleaving their live
        progress, and return their ``ToolResult``\\ s (same order as ``calls``) as the
        generator's value (consumed via ``yield from``).

        Only used for ``spawn_subagent`` batches: each call gets a *copy* of ``self.ctx``
        with its own ``progress`` callback (concurrent tools can't share the single
        ``self.ctx.progress`` the sequential path uses), but the copy still points at the
        same shared ``db`` session — safe here only because ``spawn_subagent`` opens its
        own isolated session and never touches ``ctx.db`` (see its module docstring). Do
        not route a tool that *does* use ``ctx.db`` through this path without giving it
        the same treatment.
        """
        progress_q: queue.Queue[tuple[str, str | None]] = queue.Queue()
        holders: dict[str, dict[str, Any]] = {c.id: {} for c in calls}
        names_by_id = {c.id: c.name for c in calls}

        def worker(call: ToolCall) -> None:
            tool = self.registry.get(call.name)
            if tool is None:
                holders[call.id]["result"] = ToolResult(content=f"Unknown tool: {call.name}", is_error=True)
                progress_q.put((call.id, None))
                return
            call_ctx = replace(self.ctx, progress=lambda chunk, cid=call.id: progress_q.put((cid, chunk)))
            try:
                holders[call.id]["result"] = tool.run(call_ctx, **call.arguments)
            except Exception as e:  # noqa: BLE001
                logger.exception("Tool %s failed", call.name)
                holders[call.id]["error"] = e
            finally:
                progress_q.put((call.id, None))

        threads = [threading.Thread(target=worker, args=(c,), daemon=True) for c in calls]
        for t in threads:
            t.start()

        remaining = {c.id for c in calls}
        while remaining:
            call_id, chunk = progress_q.get()
            if chunk is None:
                remaining.discard(call_id)
                continue
            yield events.tool_progress(call_id, names_by_id[call_id], chunk)
        for t in threads:
            t.join()

        return [
            ToolResult(content=f"Tool error: {holders[c.id]['error']}", is_error=True)
            if "error" in holders[c.id]
            else holders[c.id]["result"]
            for c in calls
        ]

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

    def _execute_tool_streaming(self, call_id: str, name: str, arguments: dict) -> Iterator[str]:
        """Run a tool in a worker thread, yielding ``tool_progress`` SSE frames for any
        output it streams live (see ``ToolContext.progress``); returns the final
        ``ToolResult`` as the generator's value (consumed via ``yield from``).

        Tools run synchronously and the harness's own loop is a plain generator, so a
        tool that wants to surface partial output *while it's still running* (a long
        shell/code execution) needs somewhere else to run — a worker thread that pushes
        chunks onto a queue, which this generator drains and re-yields as they arrive.
        """
        tool = self.registry.get(name)
        if tool is None:
            return ToolResult(content=f"Unknown tool: {name}", is_error=True)

        progress_queue: queue.Queue[str | None] = queue.Queue()
        holder: dict[str, Any] = {}

        def worker() -> None:
            try:
                holder["result"] = tool.run(self.ctx, **arguments)
            except Exception as e:  # noqa: BLE001
                logger.exception("Tool %s failed", name)
                holder["error"] = e
            finally:
                progress_queue.put(None)  # sentinel: no more progress coming

        self.ctx.progress = progress_queue.put
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        while True:
            chunk = progress_queue.get()
            if chunk is None:
                break
            yield events.tool_progress(call_id, name, chunk)
        t.join()
        self.ctx.progress = None

        if "error" in holder:
            return ToolResult(content=f"Tool error: {holder['error']}", is_error=True)
        return holder["result"]

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
