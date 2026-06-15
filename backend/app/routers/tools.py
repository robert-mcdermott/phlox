"""List agent tools and manage their enabled/permission state."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.agent.permissions import get_prefs, seed_tool_prefs
from app.agent.registry import REGISTRY
from app.auth.deps import require_admin
from app.database import get_db
from app.models import ToolPref
from app.schemas import ToolOut, ToolUpdate

# Tool enable/permission management is admin-only.
router = APIRouter(prefix="/api/tools", tags=["tools"], dependencies=[Depends(require_admin)])


@router.get("", response_model=list[ToolOut])
def list_tools(db: Session = Depends(get_db)):
    seed_tool_prefs(db, REGISTRY)
    prefs = get_prefs(db)
    out = []
    for tool in REGISTRY.all():
        pref = prefs.get(tool.name)
        out.append(
            ToolOut(
                name=tool.name,
                description=tool.description,
                category=getattr(tool, "category", "general"),
                enabled=pref.enabled if pref else True,
                permission=pref.permission if pref else getattr(tool, "default_permission", "auto"),
                parameters=tool.parameters,
            )
        )
    return sorted(out, key=lambda t: (t.category, t.name))


@router.patch("/{name}", response_model=ToolOut)
def update_tool(name: str, body: ToolUpdate, db: Session = Depends(get_db)):
    tool = REGISTRY.get(name)
    if not tool:
        raise HTTPException(404, "Tool not found")
    pref = db.get(ToolPref, name) or ToolPref(name=name, enabled=True, permission="auto")
    if body.enabled is not None:
        pref.enabled = body.enabled
    if body.permission is not None:
        if body.permission not in ("auto", "ask", "deny"):
            raise HTTPException(400, "permission must be auto|ask|deny")
        pref.permission = body.permission
    db.merge(pref)
    db.commit()
    prefs = get_prefs(db)
    p = prefs[name]
    return ToolOut(
        name=tool.name,
        description=tool.description,
        category=getattr(tool, "category", "general"),
        enabled=p.enabled,
        permission=p.permission,
        parameters=tool.parameters,
    )
