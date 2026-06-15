"""OpenAI-compatible provider.

Works with OpenAI itself and any compatible endpoint (Ollama, vLLM, LiteLLM,
LM Studio, etc.) by varying ``base_url``. Streams text and accumulates streamed
tool-call deltas into complete ``ToolCall`` objects.
"""
from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

from app.providers.base import LLMProvider, StreamDelta, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


def _usage_dict(usage) -> dict[str, int]:
    """Normalize an OpenAI usage object to {input, output, total}."""
    return {
        "input": int(getattr(usage, "prompt_tokens", 0) or 0),
        "output": int(getattr(usage, "completion_tokens", 0) or 0),
        "total": int(getattr(usage, "total_tokens", 0) or 0),
    }


class OpenAIProvider(LLMProvider):
    def __init__(self, config: dict[str, Any]):
        from openai import OpenAI

        from app.config import get_resilience_config

        self.model = config["model"]
        self.supports_tools = config.get("supports_tools", True)
        res = get_resilience_config()
        self._client = OpenAI(
            base_url=config.get("endpoint", "https://api.openai.com/v1"),
            api_key=config.get("api_key") or "not-needed",
            timeout=res["timeout"],
            max_retries=res["max_retries"],
        )

    # -- message + tool translation ----------------------------------------
    @staticmethod
    def _to_wire(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        wire: list[dict[str, Any]] = []
        for m in messages:
            role = m["role"]
            if role == "assistant" and m.get("tool_calls"):
                wire.append(
                    {
                        "role": "assistant",
                        "content": m.get("content") or "",
                        "tool_calls": [
                            {
                                "id": tc["id"],
                                "type": "function",
                                "function": {
                                    "name": tc["name"],
                                    "arguments": json.dumps(tc["arguments"]),
                                },
                            }
                            for tc in m["tool_calls"]
                        ],
                    }
                )
            elif role == "tool":
                wire.append(
                    {
                        "role": "tool",
                        "tool_call_id": m.get("tool_call_id", ""),
                        "content": m.get("content", ""),
                    }
                )
            elif m.get("images"):
                # Multimodal content: text + image parts (OpenAI vision format).
                parts: list[dict] = []
                if m.get("content"):
                    parts.append({"type": "text", "text": m["content"]})
                for url in m["images"]:
                    parts.append({"type": "image_url", "image_url": {"url": url}})
                wire.append({"role": role, "content": parts})
            else:
                wire.append({"role": role, "content": m.get("content", "")})
        return wire

    @staticmethod
    def _tools_to_wire(tools: list[ToolSpec]) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                },
            }
            for t in tools
        ]

    # -- streaming ----------------------------------------------------------
    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        params: dict[str, Any],
    ) -> Iterator[StreamDelta]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self._to_wire(messages),
            "temperature": params.get("temperature", 0.3),
            "max_tokens": params.get("max_tokens", 4096),
            "stream": True,
            # Ask for a final usage chunk (OpenAI + Ollama support this).
            "stream_options": {"include_usage": True},
        }
        if tools and self.supports_tools:
            kwargs["tools"] = self._tools_to_wire(tools)
            kwargs["tool_choice"] = "auto"

        # Accumulate streamed tool-call fragments keyed by index.
        acc: dict[int, dict[str, str]] = {}
        finish_reason: str | None = None
        usage = None

        stream = self._open_stream(kwargs)
        for chunk in stream:
            # The usage chunk arrives at the end with empty choices.
            if getattr(chunk, "usage", None):
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            if choice.finish_reason:
                finish_reason = choice.finish_reason

            if delta and delta.content:
                yield StreamDelta(type="text", text=delta.content)

            # Reasoning/thinking models (e.g. qwen3) stream their chain-of-thought in a
            # separate `reasoning` field — surface it as a reasoning delta.
            if delta:
                reasoning = getattr(delta, "reasoning", None)
                if reasoning is None and getattr(delta, "model_extra", None):
                    reasoning = delta.model_extra.get("reasoning")
                if reasoning:
                    yield StreamDelta(type="reasoning", text=reasoning)

            if delta and getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    slot = acc.setdefault(tc.index, {"id": "", "name": "", "args": ""})
                    if tc.id:
                        slot["id"] = tc.id
                    if tc.function and tc.function.name:
                        slot["name"] = tc.function.name
                    if tc.function and tc.function.arguments:
                        slot["args"] += tc.function.arguments

        if acc:
            calls: list[ToolCall] = []
            for _idx, slot in sorted(acc.items()):
                try:
                    args = json.loads(slot["args"]) if slot["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=slot["id"] or slot["name"], name=slot["name"], arguments=args))
            if usage is not None:
                yield StreamDelta(type="usage", usage=_usage_dict(usage))
            yield StreamDelta(type="tool_calls", tool_calls=calls)
        else:
            if usage is not None:
                yield StreamDelta(type="usage", usage=_usage_dict(usage))
            yield StreamDelta(type="done", stop_reason=finish_reason or "stop")

    def _open_stream(self, kwargs: dict):
        """Create the streaming response, retrying without tools if the model rejects
        tools+vision together (some local vision models can't combine them)."""
        try:
            return self._client.chat.completions.create(**kwargs)
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "tools" in kwargs and ("multimodal" in msg or "does not support tools" in msg):
                logger.info("Model rejected tools+vision; retrying this turn without tools")
                kwargs.pop("tools", None)
                kwargs.pop("tool_choice", None)
                return self._client.chat.completions.create(**kwargs)
            raise

    # -- embeddings ---------------------------------------------------------
    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        resp = self._client.embeddings.create(
            model=model or "text-embedding-3-small", input=texts
        )
        return [d.embedding for d in resp.data]
