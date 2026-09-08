"""RAG tool: semantic search over uploaded documents."""
from __future__ import annotations

from typing import Any

from app.agent.tools.base import Tool, ToolContext, ToolResult
from app.rag.retrieve import search_chunks


class SearchDocuments(Tool):
    name = "search_documents"
    description = (
        "Search the user's uploaded documents (knowledge base) for passages relevant to a "
        "query. Returns the most relevant excerpts with their source filenames. Use this to "
        "answer questions grounded in uploaded files."
    )
    category = "knowledge"
    default_permission = "auto"
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What to search for"},
            "top_k": {"type": "integer", "description": "How many passages to return", "default": 5},
            "document_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional document ids to restrict the search to specific referenced files.",
            },
        },
        "required": ["query"],
    }

    def run(
        self,
        ctx: ToolContext,
        query: str = "",
        top_k: int = 5,
        document_ids: list[str] | None = None,
        **_: Any,
    ) -> ToolResult:
        top_k = max(1, min(int(top_k or 5), 20))
        hits = search_chunks(
            ctx.db, query, top_k=top_k,
            conversation_id=ctx.conversation_id, user_id=ctx.user_id,
            document_ids=document_ids, assistant_id=ctx.assistant_id,
        )
        notice = getattr(hits, "notice", None)
        if not hits:
            return ToolResult(content=(notice + "\n" if notice else "") + "No matching passages found in uploaded documents.")
        from app import sources
        import uuid

        turn_id = ctx.accounting.turn_id if ctx.accounting else uuid.uuid4().hex
        blocks = [sources.INSTRUCTIONS]
        if notice:
            blocks.append(notice)
        omitted = False
        for hit in hits:
            captured = sources.capture(
                ctx.db, conversation_id=ctx.conversation_id, user_id=ctx.user_id,
                turn_id=turn_id, document_id=hit.get('document_id'), chunk_id=hit.get('chunk_id'),
                ordinal=hit.get('ordinal'), assistant_id=ctx.assistant_id, query=query,
            )
            if captured:
                blocks.append(captured[1])
            else:
                omitted = True
        if omitted:
            blocks.append('[Some evidence was omitted because it is unavailable or the source limit was reached.]')
        return ToolResult(content="\n\n".join(blocks))
