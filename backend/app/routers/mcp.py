"""MCP server management: register, connect, list tools, remove."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth.deps import require_admin
from app.database import get_db
from app.mcp.manager import mcp_manager
from app.models import McpServer
from app.schemas import McpServerIn, McpServerOut

# MCP server management is admin-only.
router = APIRouter(prefix="/api/mcp", tags=["mcp"], dependencies=[Depends(require_admin)])


def _masked_headers(headers: dict | None) -> dict[str, str] | None:
    if not headers:
        return None
    return {str(k): "********" for k in headers}


def _to_out(server: McpServer) -> McpServerOut:
    connected = server.name in mcp_manager.connected_servers()
    from app.agent.registry import REGISTRY

    tools = [n for n in REGISTRY.names() if n.startswith(f"mcp__{server.name}__")]
    return McpServerOut(
        id=server.id,
        name=server.name,
        transport=server.transport,
        command=server.command,
        args=server.args,
        env=server.env,
        url=server.url,
        headers=_masked_headers(server.headers),
        auth_token=None,  # never echo bearer/header secrets back to clients
        enabled=server.enabled,
        connected=connected,
        tools=tools,
    )

@router.get("", response_model=list[McpServerOut])
def list_servers(db: Session = Depends(get_db)):
    return [_to_out(s) for s in db.query(McpServer).order_by(McpServer.created_at).all()]


@router.post("", response_model=McpServerOut)
def add_server(body: McpServerIn, db: Session = Depends(get_db)):
    if db.query(McpServer).filter(McpServer.name == body.name).first():
        raise HTTPException(409, f"Server '{body.name}' already exists")
    server = McpServer(**body.model_dump())
    db.add(server)
    db.commit()
    db.refresh(server)
    if server.enabled:
        mcp_manager.connect(_server_dict(server))
    return _to_out(server)


@router.post("/{server_id}/connect", response_model=McpServerOut)
def connect_server(server_id: str, db: Session = Depends(get_db)):
    server = db.get(McpServer, server_id)
    if not server:
        raise HTTPException(404, "Server not found")
    result = mcp_manager.connect(_server_dict(server))
    if not result.get("connected"):
        raise HTTPException(502, f"Connect failed: {result.get('error')}")
    return _to_out(server)


@router.post("/{server_id}/disconnect", response_model=McpServerOut)
def disconnect_server(server_id: str, db: Session = Depends(get_db)):
    server = db.get(McpServer, server_id)
    if not server:
        raise HTTPException(404, "Server not found")
    mcp_manager.disconnect(server.name)
    return _to_out(server)


@router.delete("/{server_id}")
def delete_server(server_id: str, db: Session = Depends(get_db)):
    server = db.get(McpServer, server_id)
    if not server:
        raise HTTPException(404, "Server not found")
    mcp_manager.disconnect(server.name)
    db.delete(server)
    db.commit()
    return {"deleted": server_id}

def _server_dict(server: McpServer) -> dict:
    return {
        "name": server.name,
        "transport": server.transport,
        "command": server.command,
        "args": server.args,
        "env": server.env,
        "url": server.url,
        "headers": server.headers,
        "auth_token": server.auth_token,
    }
