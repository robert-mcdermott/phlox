"""Context management: keep the prompt within a token budget for long conversations.

The chat router replays the full transcript each turn (so the model has complete context).
For long sessions that exceeds the model's context window and wastes tokens. ``compact_history``
summarizes the *older* turns into a single synthetic system message while keeping the most
recent turns verbatim. It splits only on user-message boundaries so it never breaks an
assistant→tool call/result pairing.

Token counting uses a chars/4 heuristic (no tokenizer dependency); it's an estimate, which
is fine for deciding when to compact.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.providers.base import LLMProvider

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONTEXT_TOKENS = 12_000
KEEP_LAST_TURNS = 4
SUMMARY_MAX_TOKENS = 1024


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    chars = 0
    for m in messages:
        chars += len(m.get("content") or "")
        for tc in m.get("tool_calls") or []:
            chars += len(json.dumps(tc.get("arguments", {})))
    return chars // 4


def _transcript(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for m in messages:
        role = m["role"]
        if role == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                lines.append(f"assistant→tool {tc['name']}({json.dumps(tc['arguments'])[:300]})")
        elif role == "tool":
            lines.append(f"tool[{m.get('name', '')}]: {(m.get('content') or '')[:500]}")
        elif m.get("content"):
            lines.append(f"{role}: {m['content'][:1500]}")
    return "\n".join(lines)


def _summarize(provider: LLMProvider, old: list[dict[str, Any]], max_context: int) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "You compress conversation history. Summarize the excerpt below concisely "
                "but preserve key facts, user goals, decisions, file/code changes, tool "
                "results, and any open threads. Use terse bullet points."
            ),
        },
        {"role": "user", "content": _transcript(old).encode("utf-8")[:max(0, (max_context - SUMMARY_MAX_TOKENS - 512) * 3)].decode("utf-8", errors="ignore")},
    ]
    text = ""
    complete = False
    from contextlib import closing

    params = {"temperature": 0.2, "max_tokens": SUMMARY_MAX_TOKENS, "max_context_tokens": max_context}
    prompt, _ = fit_context(prompt, [], params)
    with closing(provider.stream(prompt, [], params)) as stream:
        for delta in stream:
            if delta.type == "text":
                text += delta.text or ""
            elif delta.type == 'done':
                complete = delta.stop_reason in {None, 'stop', 'end_turn', 'stop_sequence'}
    return text.strip() if complete else ''


def compact_history(
    provider: LLMProvider,
    messages: list[dict[str, Any]],
    max_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
    keep_last_turns: int = KEEP_LAST_TURNS,
) -> tuple[list[dict[str, Any]], bool]:
    """Return (possibly-compacted messages, did_compact)."""
    if estimate_tokens(messages) <= max_tokens:
        return messages, False

    system = messages[:1] if messages and messages[0]["role"] == "system" else []
    rest = messages[len(system):]

    # Split only on user-message boundaries to keep turns intact.
    user_positions = [i for i, m in enumerate(rest) if m["role"] == "user"]
    if len(user_positions) <= keep_last_turns:
        return messages, False  # too short to compact safely

    split = user_positions[-keep_last_turns]
    old, tail = rest[:split], rest[split:]
    if not old:
        return messages, False

    try:
        summary = _summarize(provider, old, max_tokens)
    except Exception as e:  # noqa: BLE001
        logger.warning("Compaction summary failed: %s", e)
        return messages, False
    if not summary:
        return messages, False

    summary_msg = {
        "role": "system",
        "content": "Summary of earlier conversation (compacted to save context):\n" + summary,
    }
    return system + [summary_msg] + tail, True


class ContextLimitError(ValueError):
    """A provider-bound prompt cannot fit without dropping user/system instructions."""


def request_tokens(messages, tools=()) -> int:
    """Conservative heuristic, not a provider tokenizer: UTF-8 bytes/3 + framing.

    Images reserve 4096 tokens each rather than treating base64 as natural-language text.
    Operators should leave headroom for the selected model's tokenizer/vision encoding.
    """
    from dataclasses import asdict
    from math import ceil

    size = 0
    for message in messages:
        payload = {k: v for k, v in message.items() if k != "images"}
        size += ceil(len(json.dumps(payload, ensure_ascii=False).encode("utf-8")) / 3) + 32
        size += len(message.get("images") or []) * 4096
    size += sum(ceil(len(json.dumps(asdict(t), ensure_ascii=False).encode("utf-8")) / 3) + 32
                for t in tools)
    return size


def fit_context(messages, tools, params):
    """Fit only a provider-bound copy; never truncate canonical user text or tool IDs.

    Oversized tool results are shortened with a visible marker. If that is insufficient,
    reject before dispatch rather than silently deleting instructions, images or schemas.
    """
    from copy import deepcopy

    limit = int(params.get("max_context_tokens", 16000))
    reserve = int(params.get("max_tokens", 4096))
    if limit <= 0 or reserve <= 0 or reserve >= limit:
        raise ContextLimitError("Output token reservation must be positive and smaller than the context limit. Reduce Max tokens or increase the context budget.")
    budget = limit - reserve
    fitted = deepcopy(messages)
    original = request_tokens(fitted, tools)
    changed = False
    # Shrink the largest tool output first. Pairing/order and call arguments stay intact.
    while request_tokens(fitted, tools) > budget:
        candidates = [m for m in fitted if m.get("role") == "tool"
                      and isinstance(m.get("content"), str) and len(m["content"]) > 256]
        if not candidates:
            raise ContextLimitError(
                f"Context needs approximately {request_tokens(fitted, tools) + reserve:,} tokens "
                f"including reserved output; the configured limit is {limit:,}. "
                "Shorten the message, reduce attachments/tools or Max tokens, or increase the context budget."
            )
        largest = max(candidates, key=lambda m: len(m["content"]))
        keep = max(128, len(largest["content"]) // 2)
        largest["content"] = largest["content"][:keep] + "\n[Tool output truncated to fit context.]"
        changed = True
    return fitted, {"trimmed": changed, "original_input_tokens": original,
                    "input_tokens": request_tokens(fitted, tools), "reserved_output_tokens": reserve}
