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


def _summarize(provider: LLMProvider, old: list[dict[str, Any]]) -> str:
    prompt = [
        {
            "role": "system",
            "content": (
                "You compress conversation history. Summarize the excerpt below concisely "
                "but preserve key facts, user goals, decisions, file/code changes, tool "
                "results, and any open threads. Use terse bullet points."
            ),
        },
        {"role": "user", "content": _transcript(old)},
    ]
    text = ""
    for delta in provider.stream(prompt, [], {"temperature": 0.2, "max_tokens": SUMMARY_MAX_TOKENS}):
        if delta.type == "text":
            text += delta.text or ""
    return text.strip()


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
        summary = _summarize(provider, old)
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
