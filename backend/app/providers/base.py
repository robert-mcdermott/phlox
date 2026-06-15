"""Canonical provider types + the ``LLMProvider`` interface.

Canonical message format (a list of plain dicts the harness builds up):

    {"role": "system"|"user"|"assistant"|"tool",
     "content": str,
     "tool_calls": [{"id": str, "name": str, "arguments": dict}],  # assistant only
     "tool_call_id": str,   # tool messages only
     "name": str}           # tool messages: the tool name

Providers translate this list into their own wire format and translate their
streaming responses back into ``StreamDelta`` events.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolSpec:
    """A tool advertised to the model (provider-neutral)."""

    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema object


@dataclass
class ToolCall:
    """A fully-accumulated tool call requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class StreamDelta:
    """One event in a provider's streaming response.

    type:
      - "text"       : incremental assistant text (``text`` set)
      - "reasoning"  : incremental reasoning/thinking text (``text`` set)
      - "tool_calls" : the turn ended requesting tools (``tool_calls`` set)
      - "done"       : the turn ended with a final answer (``stop_reason`` set)
      - "usage"      : token usage info (``usage`` set)
    """

    type: str
    text: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    usage: dict[str, Any] | None = None


class LLMProvider(ABC):
    """Provider interface. One instance is built per active profile."""

    #: human-friendly model id currently selected
    model: str
    #: whether the model/endpoint supports function/tool calling
    supports_tools: bool = True

    @abstractmethod
    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        params: dict[str, Any],
    ) -> Iterator[StreamDelta]:
        """Stream one assistant turn.

        Yields ``text``/``reasoning`` deltas as the model produces them, then exactly
        one terminal delta: ``tool_calls`` if the model wants tools, else ``done``.
        May also yield a ``usage`` delta.
        """
        raise NotImplementedError

    def embed(self, texts: list[str], model: str | None = None) -> list[list[float]]:
        """Return embeddings for ``texts``. Optional; raises if unsupported."""
        raise NotImplementedError("This provider does not support embeddings")
