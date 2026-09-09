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
import uuid
from collections.abc import Iterator
from copy import deepcopy
from contextlib import closing
from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app import sources
from app.agent import events
from app.agent.permissions import PermissionGate
from app.agent.registry import ToolRegistry
from app.agent.tools.base import ToolContext, ToolResult
from app.models import Conversation, Message, PendingApproval
from app.providers.base import LLMProvider, ToolCall
from app.sandbox.runner import get_runner
from app.workspace.manager import workspace_dir

logger = logging.getLogger(__name__)

MAX_CONCURRENT_SUBAGENTS = 3
MAX_SUBAGENTS_PER_ROUND = 8


class ProgressBuffer:
    """Bound live previews without blocking tools during cancellation/slow persistence.

    Completion notices bypass the 128 preview slots (at most eight per child batch),
    so overflow can never discard a completion or deadlock a generator's final join.
    ToolResult remains the authoritative output; omission is surfaced in the preview.
    """

    def __init__(self):
        self.queue = queue.Queue()
        self.slots = threading.BoundedSemaphore(128)
        self.omitted = threading.Event()

    def progress(self, value):
        if not self.slots.acquire(blocking=False):
            self.omitted.set()
            return
        if isinstance(value, tuple):
            call_id, content = value
        else:
            call_id, content = None, value
        if len(content) > 8192:
            content = content[:8192]
            self.omitted.set()
        self.queue.put((True, (call_id, content) if call_id is not None else content))

    def finish(self, value):
        self.queue.put((False, value))

    def get(self):
        preview, value = self.queue.get()
        if preview:
            self.slots.release()
        return value


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
        assistant_id: str | None = None,
        accounting=None,
        tool_observer=None,
        research=None,
        document_scope=None,
        branch_parent_id=None,
    ):
        from app.model_calls import CallScope

        self.accounting = accounting or CallScope.new(conversation.id, conversation.user_id)
        self.research = research
        self.tool_observer = tool_observer
        self.journal_prefix = uuid.uuid4().hex
        self.db = db
        self.conversation = conversation
        from app.branches import active
        selected = active(conversation)
        self.branch_parent_id = branch_parent_id or (selected[-1].id if selected else None)
        self.provider = provider
        self.fallback_provider = fallback_provider
        self.registry = registry
        self.gate = gate
        self.params = deepcopy(params)
        self.profile = getattr(provider, "profile_name", None) or profile
        self.model = getattr(provider, "model", model)
        # ephemeral sessions (sub-agents) don't persist messages; they expose final_text.
        self.ephemeral = ephemeral
        self.allowed_tools = set(gate.enabled_names())
        if allowed_tools is not None:
            self.allowed_tools &= allowed_tools
        if research:
            self.allowed_tools &= research.allowed_tools()
        self.final_text = ""
        # Set by the caller (e.g. the chat router, watching for a client disconnect) so a
        # user's "Stop" click can actually halt an in-flight turn — kill any running
        # subprocess and stop the loop — instead of just closing the SSE connection while
        # the turn keeps running server-side to completion.
        self.cancel_event = cancel_event
        # Accumulated token usage across this turn (for observability + cost).
        self.rounds_used = 0
        self.outcome = "running"
        self.turn_usage = {"input": 0, "output": 0, "total": 0}
        self.workspace = workspace_dir(conversation.id)
        self.ctx = ToolContext(
            conversation_id=conversation.id,
            workspace=self.workspace,
            db=db,
            runner=get_runner(),
            user_id=getattr(conversation, "user_id", None),
            assistant_id=assistant_id,
            auto_approve=gate.auto_approve,
            cancel_event=cancel_event,
            profile=self.profile,
            model=self.model,
            params=deepcopy(self.params),
            allowed_tools=frozenset(self.allowed_tools),
            accounting=self.accounting,
            tool_observer=tool_observer,
            research=research,
            document_scope=deepcopy(document_scope),
        )

    def _observe_child_tool(self, kind, call, **data):
        if self.ephemeral and self.tool_observer:
            self.tool_observer({"type": kind, "id": f"{self.journal_prefix}:{call.id}",
                                "name": call.name, **data})

    def _research_event(self):
        from app.model_calls import turn_usage
        return events.sse("research", **self.research.progress(
            turn_usage(self.accounting), len(self._source_refs())))

    def _has_research_evidence(self):
        from app.models import Source, SourceUse
        return self.db.query(Source.id).join(SourceUse).filter(
            SourceUse.turn_id == self.accounting.turn_id,
            Source.conversation_id == self.conversation.id, Source.excerpt.isnot(None),
        ).first() is not None

    def _source_refs(self):
        return sources.catalog(self.db, self.accounting.turn_id, self.conversation.id)

    def _source_events(self):
        refs = self._source_refs()
        if refs and not self.ephemeral:
            yield events.sse("sources", sources=refs)

    def _cancelled(self) -> bool:
        return self.cancel_event is not None and self.cancel_event.is_set()

    # -- public entry points ------------------------------------------------
    def run(self, messages: list[dict]) -> Iterator[str]:
        """Start a fresh turn. ``messages`` is the canonical history incl. the new user msg."""
        yield from self._source_events()
        yield from self._loop(messages, tool_steps=[], all_artifacts=[])

    def resume(self, state: dict, decisions: dict[str, str]) -> Iterator[str]:
        """Continue a paused turn with the user's approval decisions (call_id -> allow|deny)."""
        state = deepcopy(state)
        self.ctx.document_scope = state.get('document_scope')
        self.branch_parent_id = state.get('branch_parent_id', self.branch_parent_id)
        if state.get('research'):
            from app.research import Research
            self.research = self.ctx.research = Research(state=state['research'])
            self.allowed_tools &= self.research.allowed_tools()
        self.rounds_used = int(state["rounds_used"])
        self.turn_usage = dict(state["turn_usage"])
        messages = deepcopy(state["messages"])
        tool_steps = state.get("tool_steps", [])
        all_artifacts = state.get("all_artifacts", [])
        pending = [ToolCall(c["id"], c["name"], c["arguments"]) for c in state.get("pending_calls", [])]
        yield from self._source_events()
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
        research_rounds = min(max_rounds, self.research.limits['rounds']) if self.research else max_rounds
        enabled = self.allowed_tools if self.allowed_tools is not None else self.gate.enabled_names()
        tools = self.registry.specs(enabled_names=enabled)

        # Guardrails (see app/guardrails.py): input rules scrub the outbound message
        # copy before *every* provider round — covering user turns, tool results, and
        # sub-agent traffic, since they all pass through here. Output rules wrap the
        # streamed completion in a holdback redactor so a PII match can't slip through
        # split across two SSE tokens.
        from app.guardrails import StreamRedactor, get_rules, scrub_messages

        guardrails_in = get_rules("input")
        guardrails_out = get_rules("output")

        # A lowered current limit cannot authorize pending actions from excess rounds.
        if self.rounds_used > max_rounds:
            self.outcome = "limit_reached"
            yield from self._finalize(
                "The current tool-call limit no longer permits this approval. Start a new turn.",
                tool_steps, all_artifacts,
            )
            return

        # If resuming, finish the previously-pending calls first.
        if initial_calls:
            paused = yield from self._process_calls(
                messages, initial_calls, tool_steps, all_artifacts, decisions=initial_decisions
            )
            if paused:
                return

        used_fallback = False
        completion_text = ''
        recovery_attempts = 0
        completion_only = False
        for _round in range(self.rounds_used, max_rounds):
            if self._cancelled():
                yield from self._finalize(completion_text, tool_steps, all_artifacts)
                return
            if self.research:
                from app.model_calls import turn_usage
                instruction = self.research.before_round(self.rounds_used, research_rounds,
                                                        turn_usage(self.accounting)['total'])
                # Transient stage instructions are not persisted as user messages. End with
                # a user turn for providers that disallow assistant-prefill continuations.
                round_messages = deepcopy(messages)
                round_messages[0]['content'] += '\n\nCurrent research stage: ' + instruction
                round_messages.append({'role': 'user', 'content': 'Proceed with the current research stage.'})
                round_tools = [t for t in tools if t.name in self.research.available_tools()] if self.research.phase == 'gather' else []
                yield self._research_event()
            else:
                round_messages, round_tools = messages, tools
            final_reserve = not self.research and max_rounds > 1 and self.rounds_used >= max_rounds - 1
            if completion_only or final_reserve:
                round_messages, round_tools = deepcopy(round_messages), []
                if completion_text:
                    round_messages.append({'role': 'assistant', 'content': completion_text})
                round_messages.append({'role': 'user', 'content': (
                    'Continue the unfinished answer exactly where it stopped, without repeating its text. '
                    if completion_only and completion_text else
                    'Write the final answer now from the work already performed. '
                ) + 'Use the existing evidence; no more tools or new actions. Do not claim files or actions '
                    'were completed unless the saved tool results confirm them. Explain any remaining gaps.'})
            call_params = {**self.params, '_stage': (
                'completion_recovery' if completion_only else
                self.research.phase if self.research else 'finalize' if final_reserve else 'generation'
            )}
            self.rounds_used += 1
            round_text = ""
            pending_calls: list[ToolCall] = []
            stop_reason: str | None = None
            blocked = False
            redactor = thinking_redactor = None
            while True:  # retry-this-round-with-fallback loop
                streamed_any = False
                if guardrails_out:
                    # Fresh redactors per attempt so a fallback retry starts clean.
                    # Text and reasoning stream independently, so each needs its own.
                    redactor = StreamRedactor(guardrails_out)
                    thinking_redactor = StreamRedactor(guardrails_out)
                provider_messages = (
                    scrub_messages(round_messages, guardrails_in)[0] if guardrails_in else round_messages
                )
                try:
                    from app.model_calls import stream_model

                    model_stream = stream_model(
                        self.provider, provider_messages, round_tools, call_params,
                        replace(self.accounting, kind="fallback") if used_fallback else self.accounting,
                        cancel_event=self.cancel_event,
                    )
                    with closing(model_stream):
                        for delta in model_stream:
                            self.ctx.parent_call_id = delta.call_id
                            if self._cancelled():
                                # Best-effort: stop consuming further streamed tokens/tool
                                # calls as soon as we notice — the provider call itself may
                                # not be interruptible mid-flight, but we stop paying
                                # attention (and stop acting on it) immediately.
                                break
                            if delta.type == "status":
                                yield events.status(delta.text or "")
                            elif delta.type == "text":
                                streamed_any = True
                                chunk = delta.text or ""
                                if redactor is not None:
                                    chunk = redactor.feed(chunk)
                                    if redactor.blocked:
                                        blocked = True
                                        break
                                if chunk:
                                    round_text += chunk
                                    if not self.research or self.research.phase == "synthesize":
                                        yield events.token(chunk)
                            elif delta.type == "reasoning":
                                streamed_any = True
                                chunk = delta.text or ""
                                if thinking_redactor is not None:
                                    chunk = thinking_redactor.feed(chunk)
                                    if thinking_redactor.blocked:
                                        blocked = True
                                        break
                                if chunk and not self.research:
                                    yield events.thinking(chunk)
                            elif delta.type == "usage":
                                u = delta.usage or {}
                                from app.model_calls import turn_usage

                                totals = turn_usage(self.accounting)
                                self.turn_usage = {k: totals[k] for k in ("input", "output", "total")}
                                if self.research:
                                    self.research.state['reported_tokens'] = totals['total']
                                yield events.usage(u)
                            elif delta.type == "tool_calls":
                                pending_calls = delta.tool_calls
                                stop_reason = delta.stop_reason
                            elif delta.type == "done":
                                stop_reason = delta.stop_reason
                    break  # round streamed successfully
                except Exception as e:  # noqa: BLE001
                    # Fall back to a secondary provider if one is configured and we failed
                    # before producing any output this round (so we don't duplicate tokens).
                    from app.agent.context import ContextLimitError
                    from app.model_calls import IncompleteStreamError

                    if isinstance(e, IncompleteStreamError):
                        stop_reason = 'incomplete_stream'
                        pending_calls = []  # No action from an unconfirmed stream may run.
                        break

                    if (self.fallback_provider and not used_fallback and not streamed_any
                            and not round_text and not isinstance(e, ContextLimitError)):
                        logger.warning("Provider failed (%s); switching to fallback %s",
                                       e, self.fallback_provider.model)
                        yield events.status(
                            f"Primary model unavailable — switching to fallback ({self.fallback_provider.model})…"
                        )
                        self.provider = self.fallback_provider
                        self.profile = getattr(self.provider, "profile_name", None)
                        self.model = self.provider.model
                        self.ctx.profile = self.profile
                        self.ctx.model = self.model
                        used_fallback = True
                        round_text, pending_calls, stop_reason = "", [], None
                        continue  # retry the round with the fallback
                    logger.exception("Provider stream error")
                    self.outcome = "failed"
                    yield events.error(f"Model error: {e}")
                    yield from self._finalize(completion_text + round_text, tool_steps, all_artifacts)
                    return

            # Drain the guardrails redactors: emit the held-back tail (or discover a
            # block match sitting in it). On block, the withheld text is discarded, a
            # visible notice is persisted in its place, and the turn ends here.
            if redactor is not None and not blocked:
                think_tail = thinking_redactor.flush()
                tail = redactor.flush()
                if redactor.blocked or thinking_redactor.blocked:
                    blocked = True
                else:
                    if think_tail and not self.research:
                        yield events.thinking(think_tail)
                    if tail:
                        round_text += tail
                        if not self.research or self.research.phase == "synthesize":
                            yield events.token(tail)
            if blocked:
                self.outcome = "blocked"
                matched = sorted(redactor.matched | thinking_redactor.matched)
                note = (
                    "\n\n> 🛡️ **Response blocked by guardrails policy"
                    + (f" ({', '.join(matched)})" if matched else "")
                    + ".**"
                )
                round_text += note
                yield events.token(note)
                yield from self._finalize(completion_text + round_text, tool_steps, all_artifacts)
                return

            if self._cancelled():
                yield from self._finalize(completion_text + round_text, tool_steps, all_artifacts)
                return

            if stop_reason in {'content_filter', 'guardrail_intervened'}:
                self.outcome = 'blocked'
                note = '\n\nResponse stopped by the provider’s content policy. No further actions were executed.'
                yield events.token(note)
                yield from self._finalize(completion_text + round_text + note, tool_steps, all_artifacts)
                return

            if stop_reason not in {None, 'stop', 'end_turn', 'stop_sequence', 'tool_use', 'tool_calls',
                                   'length', 'max_tokens', 'incomplete_stream'}:
                self.outcome = 'failed'
                note = '\n\nResponse incomplete: the provider ended with an unsupported completion reason. No further actions were executed.'
                yield events.token(note)
                yield from self._finalize(completion_text + round_text + note, tool_steps, all_artifacts)
                return

            # Complete text can continue safely with no tools. Never execute a tool
            # batch whose provider finish reason indicates truncation/interruption.
            incomplete = stop_reason in {'length', 'max_tokens', 'incomplete_stream'}
            if (completion_only and completion_text and round_text.startswith(completion_text)
                    and len(round_text) > len(completion_text)):
                # Some models restart their answer despite the continuation instruction.
                # Keep an exact repeated prefix only once in the canonical saved answer.
                round_text = round_text[len(completion_text):]
            empty = not round_text.strip() and not pending_calls
            no_progress = bool(completion_only and completion_text and round_text.strip()
                               and round_text.strip() in completion_text)
            if incomplete or empty or no_progress:
                reason = stop_reason if incomplete else 'no_progress' if no_progress else 'empty_response'
                if not no_progress:
                    completion_text += round_text
                # Each call flushes its output redactor. Joining two separately
                # checked streams could reconstruct a sensitive match at the seam.
                can_recover = (not guardrails_out and not pending_calls
                               and (not self.research or self.research.phase == 'synthesize')
                               and recovery_attempts < 2 and self.rounds_used < max_rounds and not no_progress)
                if can_recover:
                    recovery_attempts += 1
                    if self.research:
                        self.research.state['recovery_calls'] = recovery_attempts
                    self.completion = {'reason': reason, 'recovery_calls': recovery_attempts, 'recovered': False}
                    completion_only = True
                    yield events.status('Continuing the unfinished answer from saved work; no tools will be repeated…')
                    continue
                self.outcome = 'limit_reached' if reason in {'length', 'max_tokens'} else 'failed'
                self.completion = {'reason': reason, 'recovery_calls': recovery_attempts, 'recovered': False}
                note = ('\n\nResponse incomplete: ' + (
                    'the model reached its output-token limit. Increase Max output tokens in Settings → Model.'
                    if reason in {'length', 'max_tokens'} else
                    'the model returned no usable continuation or ended without confirming completion.'
                ) + ' Saved output and tool results are retained. Continue in Chat to reuse the saved work.'
                    + (' The unfinished tool request was not executed.' if pending_calls else '')
                    + (' Automatic continuation is unavailable while output guardrails are active.'
                       if guardrails_out else ''))
                yield events.token(note)
                yield from self._finalize(completion_text + note, tool_steps, all_artifacts)
                return

            if completion_only or final_reserve:
                if pending_calls:
                    self.outcome = 'limit_reached'
                    note = '\n\nResponse incomplete: the model requested additional tools during final completion. No further actions were executed. Continue from the saved work after adjusting the round limit if needed.'
                    yield events.token(note)
                    yield from self._finalize(completion_text + round_text + note, tool_steps, all_artifacts)
                    return
                round_text = completion_text + round_text
                if completion_only:
                    self.completion['recovered'] = True

            if self.research:
                if self._cancelled():
                    yield from self._finalize(round_text, tool_steps, all_artifacts)
                    return
                if self.research.phase == 'synthesize':
                    if pending_calls:
                        self.outcome = 'limit_reached'
                        round_text += '\n\nResearch ended: the model requested more tools instead of completing the report. No further actions were executed.'
                    if not self._has_research_evidence():
                        round_text += '\n\nNo supporting passages were retained in this research run. Treat the report as unverified.'
                    yield from self._finalize(round_text, tool_steps, all_artifacts)
                    return
                if self.research.phase == 'plan' or not pending_calls:
                    self.research.advance(round_text)
                    messages.append({'role': 'assistant', 'content': round_text})
                    yield self._research_event()
                    continue
                # Bound oversized provider tool-call batches as well as model passes.
                pending_calls = pending_calls[:16]

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

        self.outcome = "limit_reached"
        yield from self._finalize(
            "I reached the configured model-pass limit before finishing. Saved tool results and output are retained. Increase Max tool rounds in Settings → Model, then continue from the saved work.",
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
        child_count = 0

        for call in calls:
            if self._cancelled():
                break
            tool = self.registry.get(call.name)
            if tool is not None:
                from app.agent.validation import argument_error
                invalid = argument_error(tool, call.arguments)
                if invalid is not None:
                    yield events.tool_call(call.id, call.name, call.arguments)
                    yield from self._emit_result(call, ToolResult(
                        content=invalid,
                        is_error=True), tool_steps, all_artifacts, messages)
                    continue
            if call.name == "spawn_subagent":
                child_count += 1
                if child_count > MAX_SUBAGENTS_PER_ROUND:
                    yield events.tool_call(call.id, call.name, call.arguments)
                    yield from self._emit_result(
                        call, ToolResult(
                            content=f"Sub-agent limit ({MAX_SUBAGENTS_PER_ROUND} per round) "
                            "reached. Not executed.", is_error=True,
                        ), tool_steps, all_artifacts, messages,
                    )
                    continue
            if self.allowed_tools is not None and call.name not in self.allowed_tools:
                decision = "not_enabled"
            elif self.gate.decide(call.name) == "deny":
                decision = "deny"
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

        # Only explicitly read-only children can run concurrently. Mutation-capable
        # children run one at a time so they cannot race on this turn's shared files.
        subagent_calls = [c for c in to_run if c.name == "spawn_subagent"]
        other_calls = [c for c in to_run if c.name != "spawn_subagent"]
        readonly = [c for c in subagent_calls if c.arguments.get("read_only") is True]
        mutating = [c for c in subagent_calls if c.arguments.get("read_only") is not True]

        yield from self._run_sequential(other_calls, tool_steps, all_artifacts, messages)
        yield from self._run_sequential(mutating, tool_steps, all_artifacts, messages)

        if len(readonly) >= 2:
            yield events.status(
                f"Running {len(readonly)} read-only sub-agents "
                f"(up to {MAX_CONCURRENT_SUBAGENTS} at once)…"
            )
            results = yield from self._run_calls_concurrently(readonly)
            for call, result in zip(readonly, results, strict=True):
                if not result.is_error:
                    self._maybe_checkpoint(call.name)
                yield from self._emit_result(call, result, tool_steps, all_artifacts, messages)
        else:
            yield from self._run_sequential(readonly, tool_steps, all_artifacts, messages)

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
            if self.research:
                denied = self.research.admit(call.name, call.arguments)
                if denied:
                    yield from self._emit_result(call, ToolResult(content=denied, is_error=True),
                                                 tool_steps, all_artifacts, messages)
                    continue
                yield self._research_event()
            self._observe_child_tool("child_tool_start", call, arguments=call.arguments)
            yield events.sse("tool_start", id=call.id, name=call.name)
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
        progress_q = ProgressBuffer()
        holders: dict[str, dict[str, Any]] = {c.id: {} for c in calls}
        names_by_id = {c.id: c.name for c in calls}

        def execute(call: ToolCall) -> None:
            if self._cancelled():
                holders[call.id]["result"] = ToolResult(
                    content="Sub-agent cancelled before dispatch. Not executed.", is_error=True
                )
                progress_q.finish((call.id, None))
                return
            tool = self.registry.get(call.name)
            if tool is None:
                holders[call.id]["result"] = ToolResult(content=f"Unknown tool: {call.name}", is_error=True)
                progress_q.finish((call.id, None))
                return
            call_ctx = replace(
                self.ctx, params=deepcopy(self.ctx.params),
                progress=lambda chunk, cid=call.id: progress_q.progress((cid, chunk)),
            )
            try:
                holders[call.id]["result"] = tool.run(call_ctx, **call.arguments)
            except Exception as e:  # noqa: BLE001
                logger.exception("Tool %s failed", call.name)
                holders[call.id]["error"] = e
            finally:
                progress_q.finish((call.id, None))

        work: queue.Queue[ToolCall] = queue.Queue()
        for call in calls:
            work.put(call)

        def worker() -> None:
            while True:
                try:
                    call = work.get_nowait()
                except queue.Empty:
                    return
                execute(call)

        threads = [threading.Thread(target=worker, daemon=True)
                   for _ in range(min(MAX_CONCURRENT_SUBAGENTS, len(calls)))]
        for call in calls:
            self._observe_child_tool("child_tool_start", call, arguments=call.arguments)
            yield events.sse("tool_start", id=call.id, name=call.name)
        for t in threads:
            t.start()

        remaining = {c.id for c in calls}
        try:
            while remaining:
                call_id, chunk = progress_q.get()
                if chunk is None:
                    remaining.discard(call_id)
                    continue
                yield events.tool_progress(call_id, names_by_id[call_id], chunk)
        finally:
            if remaining and self.cancel_event is not None:
                self.cancel_event.set()
            for t in threads:
                t.join()

        if progress_q.omitted.is_set():
            yield events.status("Some live child progress was omitted; saved tool results follow.")
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
        self.outcome = "paused"
        if self.research:
            yield events.sse('research', **{**self.research.progress(), 'phase': 'paused'})
        from app.model_calls import turn_usage

        totals = turn_usage(self.accounting)
        self.turn_usage = {k: totals[k] for k in ("input", "output", "total")}
        state = {
            "branch_parent_id": self.branch_parent_id,
            "document_scope": self.ctx.document_scope,
            "research": deepcopy(self.research.state) if self.research else None,
            "sources": self._source_refs(),
            "version": 3,
            "turn_id": self.accounting.turn_id,
            "usage_summary": turn_usage(self.accounting),
            "rounds_used": self.rounds_used,
            "turn_usage": dict(self.turn_usage),
            "assistant_id": self.ctx.assistant_id,
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
        self.db.flush()
        from app.models import Run

        linked_run = self.db.get(Run, self.accounting.turn_id)
        if linked_run and not self.ephemeral:
            linked_run.pending_id = pending.id
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
        self._observe_child_tool("child_tool_result", call, content=result.content, is_error=result.is_error)
        for art in result.artifacts:
            url = f"/api/files/{self.conversation.id}?path={art['path']}"
            all_artifacts.append({**art, "url": url})
            yield events.artifact(art["name"], art["path"], art.get("ext", ""), url)

        yield events.tool_result(call.id, call.name, result.content, result.is_error, result.artifacts)
        yield from self._source_events()

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

        progress_queue = ProgressBuffer()
        holder: dict[str, Any] = {}

        def worker() -> None:
            try:
                holder["result"] = tool.run(self.ctx, **arguments)
            except Exception as e:  # noqa: BLE001
                logger.exception("Tool %s failed", name)
                holder["error"] = e
            finally:
                progress_queue.finish(None)  # sentinel: no more progress coming

        self.ctx.progress = progress_queue.progress
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        completed = False
        try:
            while True:
                chunk = progress_queue.get()
                if chunk is None:
                    completed = True
                    break
                yield events.tool_progress(call_id, name, chunk)
        finally:
            if not completed and self.cancel_event is not None:
                self.cancel_event.set()
            t.join()
            self.ctx.progress = None

        if progress_queue.omitted.is_set():
            yield events.tool_progress(call_id, name, "\n[Some live progress omitted; saved tool result follows.]\n")
        if "error" in holder:
            return ToolResult(content=f"Tool error: {holder['error']}", is_error=True)
        return holder["result"]

    def _finalize(self, final_text: str, tool_steps: list[dict], all_artifacts: list[dict]) -> Iterator[str]:
        if self._cancelled():
            self.outcome = "cancelled"
        elif self.outcome == "running":
            self.outcome = "completed"
        if self.ephemeral:
            self.final_text = final_text
            yield events.done("", outcome=self.outcome)
            return
        if self.outcome != 'completed' and not final_text.strip():
            final_text = 'The response did not complete. Review the saved progress and tool results before continuing.'

        from app.model_calls import turn_usage

        usage = turn_usage(self.accounting)
        usage['outcome'] = self.outcome
        if hasattr(self, 'completion'):
            usage['completion'] = self.completion
        from app.models import ContextRecord
        record = self.db.get(ContextRecord, self.accounting.turn_id)
        if record:
            usage.update(context_key=record.data.get('key'),
                         context_attachments=record.data.get('user_attachments', []))
        if self.research:
            import time
            if self.outcome in {'cancelled', 'failed', 'limit_reached'} and self.research.phase != 'synthesize':
                refs = self._source_refs()
                final_text = ('Research ended before a complete report was written. '
                              'The collected passages remain available for inspection: '
                              + (', '.join('[' + r['label'] + ']' for r in refs) if refs else 'none were retained.')
                              + ('\n\nSaved partial output:\n' + final_text if final_text.strip() else '')
                              + '\n\nNo complete conclusion was reached. Continue in Chat to reuse the saved work.')
            self.research.state['phase'] = self.outcome
            self.research.state['finished_at'] = time.time()
            usage['research'] = self.research.progress(dict(usage), len(self._source_refs()))
            yield self._research_event()

        msg = Message(
            conversation_id=self.conversation.id,
            role="assistant",
            content=final_text,
            tool_calls=tool_steps or None,
            artifacts=all_artifacts or None,
            usage=usage,
            model=self.provider.model,
            citations=sources.bind(final_text, self._source_refs()),
        )
        from app import branches
        branches.append(self.db, self.conversation, msg, self.branch_parent_id)
        from app.artifact_snapshots import capture
        capture(msg)
        self.db.commit()
        self.db.refresh(msg)

        yield events.done(msg.id, outcome=self.outcome)


# Note: ``_process_calls`` / ``_pause`` return a bool via the generator's StopIteration
# value, consumed by ``yield from``. ``_finalize`` yields the final ``done`` event.
