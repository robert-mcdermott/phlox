"""Web fetch tool (best-effort HTML→text)."""
from __future__ import annotations

import re
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult

MAX_CHARS = 20_000


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class WebFetch(Tool):
    name = "web_fetch"
    description = "Fetch a URL over HTTP(S) and return its text content (HTML converted to text)."
    category = "web"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {"url": {"type": "string", "description": "The http(s) URL to fetch"}},
        "required": ["url"],
    }

    def run(self, ctx: ToolContext, url: str = "", **_: Any) -> ToolResult:  # noqa: ARG002
        if not url.startswith(("http://", "https://")):
            return ToolResult(content="URL must start with http:// or https://", is_error=True)
        try:
            import httpx

            resp = httpx.get(url, follow_redirects=True, timeout=20.0, headers={"User-Agent": "Phlox/0.1"})
            ctype = resp.headers.get("content-type", "")
            body = resp.text
            text = _html_to_text(body) if "html" in ctype else body
            if len(text) > MAX_CHARS:
                text = text[:MAX_CHARS] + "\n... [truncated]"
            return ToolResult(content=f"HTTP {resp.status_code} {url}\n\n{text}")
        except Exception as e:  # noqa: BLE001
            return ToolResult(content=f"Fetch failed: {e}", is_error=True)
