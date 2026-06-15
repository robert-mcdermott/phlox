"""AWS Bedrock provider (Converse / ConverseStream API).

Streams text and accumulates ``toolUse`` blocks (whose input arrives as partial JSON)
into complete ``ToolCall`` objects.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Iterator
from typing import Any

from app.providers.base import LLMProvider, StreamDelta, ToolCall, ToolSpec

logger = logging.getLogger(__name__)


def _bedrock_image_block(data_url: str) -> dict | None:
    """Convert a data URL into a Bedrock Converse image block."""
    m = re.match(r"data:image/(?P<fmt>\w+);base64,(?P<b64>.+)", data_url, re.DOTALL)
    if not m:
        return None
    fmt = m.group("fmt").lower()
    fmt = {"jpg": "jpeg"}.get(fmt, fmt)
    try:
        raw = base64.b64decode(m.group("b64"))
    except Exception:  # noqa: BLE001
        return None
    return {"image": {"format": fmt, "source": {"bytes": raw}}}


class BedrockProvider(LLMProvider):
    def __init__(self, config: dict[str, Any]):
        import boto3

        self.model = config["model"]
        self.supports_tools = config.get("supports_tools", True)
        # Anthropic Claude models on Bedrock support prompt caching; opt in per profile.
        self.prompt_cache = config.get("prompt_cache", False)

        from botocore.config import Config as BotoConfig

        from app.config import get_resilience_config

        res = get_resilience_config()
        boto_cfg = BotoConfig(
            read_timeout=res["timeout"],
            connect_timeout=min(res["timeout"], 20),
            retries={"max_attempts": res["max_retries"] + 1, "mode": "adaptive"},
        )

        # Newer single **Amazon Bedrock API key** (a bearer token) — an alternative to IAM
        # SigV4 credentials. When provided, authenticate with it instead of the IAM chain;
        # we set the token on a low-level botocore session so it stays scoped to this client
        # (not a process-global ``AWS_BEARER_TOKEN_BEDROCK`` env var).
        bedrock_api_key = config.get("aws_bedrock_api_key")
        if bedrock_api_key:
            import botocore.session
            from botocore.tokens import FrozenAuthToken

            bsession = botocore.session.get_session()
            if config.get("aws_region"):
                bsession.set_config_variable("region", config["aws_region"])
            bsession._auth_token = FrozenAuthToken(bedrock_api_key)
            self._client = bsession.create_client(
                "bedrock-runtime", config=boto_cfg.merge(BotoConfig(signature_version="bearer"))
            )
            return

        session_kwargs: dict[str, Any] = {}
        if config.get("aws_region"):
            session_kwargs["region_name"] = config["aws_region"]
        if config.get("aws_profile"):
            session_kwargs["profile_name"] = config["aws_profile"]
        if config.get("aws_access_key_id"):
            session_kwargs["aws_access_key_id"] = config["aws_access_key_id"]
            session_kwargs["aws_secret_access_key"] = config.get("aws_secret_access_key", "")
            # Temporary/STS credentials (ASIA… keys) also require a session token.
            if config.get("aws_session_token"):
                session_kwargs["aws_session_token"] = config["aws_session_token"]
        session = boto3.Session(**session_kwargs)
        self._client = session.client("bedrock-runtime", config=boto_cfg)

    # -- message + tool translation ----------------------------------------
    @staticmethod
    def _split(messages: list[dict[str, Any]]) -> tuple[list[dict], list[dict]]:
        """Return (system_blocks, converse_messages)."""
        system: list[dict] = []
        out: list[dict] = []
        for m in messages:
            role = m["role"]
            if role == "system":
                if m.get("content"):
                    system.append({"text": m["content"]})
                continue
            if role == "assistant" and m.get("tool_calls"):
                content: list[dict] = []
                if m.get("content"):
                    content.append({"text": m["content"]})
                for tc in m["tool_calls"]:
                    content.append(
                        {"toolUse": {"toolUseId": tc["id"], "name": tc["name"], "input": tc["arguments"]}}
                    )
                out.append({"role": "assistant", "content": content})
            elif role == "tool":
                raw = m.get("content", "")
                try:
                    parsed = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    parsed = raw
                if not isinstance(parsed, dict):
                    parsed = {"result": parsed}
                out.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "toolResult": {
                                    "toolUseId": m.get("tool_call_id", ""),
                                    "content": [{"json": parsed}],
                                }
                            }
                        ],
                    }
                )
            elif m.get("images"):
                content: list[dict] = []
                if m.get("content"):
                    content.append({"text": m["content"]})
                for url in m["images"]:
                    blk = _bedrock_image_block(url)
                    if blk:
                        content.append(blk)
                out.append({"role": role, "content": content})
            else:
                out.append({"role": role, "content": [{"text": m.get("content", "")}]})
        return system, out

    @staticmethod
    def _tools_to_wire(tools: list[ToolSpec]) -> dict[str, Any]:
        return {
            "tools": [
                {
                    "toolSpec": {
                        "name": t.name,
                        "description": t.description,
                        "inputSchema": {"json": t.parameters},
                    }
                }
                for t in tools
            ]
        }

    # -- streaming ----------------------------------------------------------
    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[ToolSpec],
        params: dict[str, Any],
    ) -> Iterator[StreamDelta]:
        system, conv = self._split(messages)
        kwargs: dict[str, Any] = {
            "modelId": self.model,
            "messages": conv,
            "inferenceConfig": {
                "temperature": params.get("temperature", 0.3),
                "maxTokens": params.get("max_tokens", 4096),
            },
        }
        if system:
            # A cache point after the (stable) system prompt lets Bedrock reuse it across
            # turns — lower latency + cost for Claude models that support caching.
            if self.prompt_cache:
                system = [*system, {"cachePoint": {"type": "default"}}]
            kwargs["system"] = system
        if tools and self.supports_tools:
            tool_cfg = self._tools_to_wire(tools)
            if self.prompt_cache:
                tool_cfg["tools"] = [*tool_cfg["tools"], {"cachePoint": {"type": "default"}}]
            kwargs["toolConfig"] = tool_cfg

        resp = self._client.converse_stream(**kwargs)

        # Track tool-use blocks by content-block index.
        tool_blocks: dict[int, dict[str, str]] = {}
        stop_reason: str | None = None

        for event in resp.get("stream", []):
            if "contentBlockStart" in event:
                start = event["contentBlockStart"]
                idx = start.get("contentBlockIndex", 0)
                tu = start.get("start", {}).get("toolUse")
                if tu:
                    tool_blocks[idx] = {"id": tu.get("toolUseId", ""), "name": tu.get("name", ""), "args": ""}
            elif "contentBlockDelta" in event:
                cbd = event["contentBlockDelta"]
                idx = cbd.get("contentBlockIndex", 0)
                d = cbd.get("delta", {})
                if "text" in d:
                    yield StreamDelta(type="text", text=d["text"])
                elif "reasoningContent" in d:
                    rc = d["reasoningContent"]
                    if rc.get("text"):
                        yield StreamDelta(type="reasoning", text=rc["text"])
                elif "toolUse" in d and idx in tool_blocks:
                    tool_blocks[idx]["args"] += d["toolUse"].get("input", "")
            elif "messageStop" in event:
                stop_reason = event["messageStop"].get("stopReason")
            elif "metadata" in event:
                usage = event["metadata"].get("usage")
                if usage:
                    yield StreamDelta(
                        type="usage",
                        usage={
                            "input": usage.get("inputTokens", 0),
                            "output": usage.get("outputTokens", 0),
                            "total": usage.get("totalTokens", 0),
                        },
                    )

        if tool_blocks:
            calls: list[ToolCall] = []
            for _idx, blk in sorted(tool_blocks.items()):
                try:
                    args = json.loads(blk["args"]) if blk["args"] else {}
                except json.JSONDecodeError:
                    args = {}
                calls.append(ToolCall(id=blk["id"], name=blk["name"], arguments=args))
            yield StreamDelta(type="tool_calls", tool_calls=calls)
        else:
            yield StreamDelta(type="done", stop_reason=stop_reason or "stop")
