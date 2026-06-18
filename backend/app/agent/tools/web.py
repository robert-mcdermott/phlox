"""Web fetch/search tools."""
from __future__ import annotations

import json
import re
from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.config import get_web_search_config

MAX_CHARS = 20_000
MAX_SEARCH_RESULTS = 10
DEFAULT_SEARCH_RESULTS = 5
MAX_SEARCH_TITLE_CHARS = 200
MAX_SEARCH_SNIPPET_CHARS = 500


def _html_to_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_text(value: Any, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + " ... [truncated]"


def _result_triplet(raw: dict[str, Any]) -> dict[str, str] | None:
    url = str(raw.get("url") or raw.get("href") or raw.get("link") or "").strip()
    if not url:
        return None
    return {
        "title": _clean_text(raw.get("title") or raw.get("heading") or "(untitled)", MAX_SEARCH_TITLE_CHARS),
        "url": url,
        "snippet": _clean_text(
            raw.get("snippet") or raw.get("body") or raw.get("content") or "",
            MAX_SEARCH_SNIPPET_CHARS,
        ),
    }


def _bounded_max_results(value: Any) -> int:
    try:
        requested = int(value)
    except (TypeError, ValueError):
        requested = DEFAULT_SEARCH_RESULTS
    return max(1, min(requested, MAX_SEARCH_RESULTS))


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


class WebSearch(Tool):
    name = "web_search"
    description = (
        "Search the live web and return ranked results with titles, URLs, and snippets. "
        "Use this to discover current sources, then call web_fetch on promising results."
    )
    category = "web"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {
                "type": "integer",
                "description": f"Maximum results to return (default {DEFAULT_SEARCH_RESULTS}, max {MAX_SEARCH_RESULTS})",
            },
        },
        "required": ["query"],
    }

    def run(
        self,
        ctx: ToolContext,  # noqa: ARG002
        query: str = "",
        max_results: int = DEFAULT_SEARCH_RESULTS,
        **_: Any,
    ) -> ToolResult:
        query = str(query or "").strip()
        if not query:
            return ToolResult(content="Search query is required.", is_error=True)

        limit = _bounded_max_results(max_results)
        cfg = get_web_search_config()
        searxng_url = str(cfg.get("searxng_url") or "").strip()

        try:
            if searxng_url:
                results = self._search_searxng(searxng_url, query, limit)
                backend = "searxng"
            else:
                results = self._search_ddgs(query, limit)
                backend = "ddgs"
        except Exception as e:  # noqa: BLE001
            return ToolResult(content=f"Web search failed: {e}", is_error=True)

        payload = {
            "query": query,
            "backend": backend,
            "results": results,
        }
        return ToolResult(content=json.dumps(payload, ensure_ascii=False, indent=2))

    def _search_ddgs(self, query: str, max_results: int) -> list[dict[str, str]]:
        from ddgs import DDGS

        with DDGS(timeout=20) as ddgs:
            raw_results = ddgs.text(query, max_results=max_results)
        return self._normalize_results(raw_results, max_results)

    def _search_searxng(self, base_url: str, query: str, max_results: int) -> list[dict[str, str]]:
        import httpx

        resp = httpx.get(
            f"{base_url.rstrip('/')}/search",
            params={"q": query, "format": "json"},
            headers={"Accept": "application/json", "User-Agent": "Phlox/0.1"},
            timeout=20.0,
            follow_redirects=True,
        )
        resp.raise_for_status()
        data = resp.json()
        raw_results = data.get("results", []) if isinstance(data, dict) else []
        return self._normalize_results(raw_results, max_results)

    def _normalize_results(self, raw_results: Any, max_results: int) -> list[dict[str, str]]:
        results: list[dict[str, str]] = []
        for raw in raw_results or []:
            if not isinstance(raw, dict):
                continue
            triplet = _result_triplet(raw)
            if triplet is None:
                continue
            results.append(triplet)
            if len(results) >= max_results:
                break
        return results
