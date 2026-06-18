"""Phlox FastAPI application.

Startup wiring:
  1. create tables
  2. register built-in tools into the shared REGISTRY
  3. seed per-tool preferences
  4. auto-connect enabled MCP servers (best-effort)

Serves the built frontend (frontend/dist) in production; in dev the Vite server
proxies /api to this app (see frontend/vite.config.js).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.permissions import seed_tool_prefs
from app.agent.registry import REGISTRY
from app.agent.tools import register_builtin_tools
from app.config import BACKEND_DIR
from app.database import SessionLocal, init_db
from app.observability import setup_observability
from app.routers import (
    admin_config,
    api_keys,
    attachments,
    auth,
    chat,
    checkpoints,
    conversations,
    documents,
    files,
    gateway,
    mcp,
    memories,
    providers,
    settings,
    tools,
    usage,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("phlox")


def _bootstrap() -> None:
    init_db()
    register_builtin_tools(REGISTRY)
    db = SessionLocal()
    try:
        seed_tool_prefs(db, REGISTRY)
        # Seed the bootstrap admin account (no-op if users already exist), then claim any
        # pre-auth data so per-user isolation is clean.
        from app.auth.service import claim_orphans, seed_default_admin
        from app.models import User

        seed_default_admin(db)
        first_admin = (
            db.query(User).filter(User.role == "admin").order_by(User.created_at).first()
        )
        if first_admin:
            claim_orphans(db, first_admin.id)

        # Backfill the durable usage ledger from any pre-existing Message.usage history so
        # historical cost isn't lost when this feature ships. Idempotent (safe each boot).
        try:
            from app.usage_ledger import backfill_usage_ledger

            backfill_usage_ledger(db)
        except Exception as e:  # noqa: BLE001
            logger.warning("Usage ledger backfill skipped: %s", e)

        # Auto-connect enabled MCP servers.
        from app.mcp.manager import mcp_manager
        from app.models import McpServer
        from app.routers.mcp import _server_dict

        for server in db.query(McpServer).filter(McpServer.enabled.is_(True)).all():
            try:
                mcp_manager.connect(_server_dict(server))
            except Exception as e:  # noqa: BLE001
                logger.warning("MCP auto-connect failed for %s: %s", server.name, e)

        # Keep stored embeddings + the vector index consistent with the configured
        # embedder (re-embeds automatically if the embedding model/dimension changed).
        try:
            from app.rag.maintenance import sync_index

            result = sync_index(db)
            if result.get("indexed"):
                logger.info(
                    "Vector index synced: %d indexed (%d re-embedded, dim=%s)",
                    result["indexed"], result["reembedded"], result["dim"],
                )
        except Exception as e:  # noqa: BLE001
            logger.warning("Vector index sync on startup skipped: %s", e)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _bootstrap()
    logger.info("Phlox ready — %d tools registered", len(REGISTRY.names()))
    yield


app = FastAPI(title="Phlox", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

for r in (auth, chat, conversations, providers, settings, documents, mcp, tools, files,
          memories, checkpoints, attachments, usage, admin_config, api_keys, gateway):
    app.include_router(r.router)

# Structured request logging (always) + OpenTelemetry tracing (if configured).
setup_observability(app)


@app.get("/api/health")
def health():
    return {"status": "ok", "tools": len(REGISTRY.names())}


# Serve the built SPA in production (no-op in dev).
_DIST = BACKEND_DIR.parent / "frontend" / "dist"
if _DIST.exists():
    app.mount("/", StaticFiles(directory=str(_DIST), html=True), name="spa")
