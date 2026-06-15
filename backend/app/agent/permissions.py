"""Per-tool permission policy + enabled state.

Policies (persisted in ``ToolPref``):
  - ``auto`` : run without asking
  - ``ask``  : require confirmation (foundation: surfaced to the UI; resolved by the
               conversation's ``auto_approve`` flag or treated as allowed in trusted mode)
  - ``deny`` : never run

This is the security seam. The default policy for a tool comes from
``Tool.default_permission`` until the user overrides it in the Tool Manager.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.agent.registry import ToolRegistry
from app.models import ToolPref


def seed_tool_prefs(db: Session, registry: ToolRegistry) -> None:
    """Ensure a ToolPref row exists for each registered tool (idempotent)."""
    existing = {p.name for p in db.query(ToolPref).all()}
    created = False
    for tool in registry.all():
        if tool.name not in existing:
            db.add(
                ToolPref(
                    name=tool.name,
                    enabled=True,
                    permission=getattr(tool, "default_permission", "auto"),
                )
            )
            created = True
    if created:
        db.commit()


def get_prefs(db: Session) -> dict[str, ToolPref]:
    return {p.name: p for p in db.query(ToolPref).all()}


class PermissionGate:
    """Decides whether a given tool call may run."""

    def __init__(self, db: Session, registry: ToolRegistry, auto_approve: bool = False):
        self.prefs = get_prefs(db)
        self.registry = registry
        self.auto_approve = auto_approve

    def enabled_names(self) -> set[str]:
        names = set()
        for tool in self.registry.all():
            pref = self.prefs.get(tool.name)
            if pref is None or (pref.enabled and pref.permission != "deny"):
                names.add(tool.name)
        return names

    def decide(self, tool_name: str) -> str:
        """Return one of: allow | deny | ask."""
        pref = self.prefs.get(tool_name)
        policy = pref.permission if pref else "auto"
        if pref and not pref.enabled:
            return "deny"
        if policy == "deny":
            return "deny"
        if policy == "ask":
            return "allow" if self.auto_approve else "ask"
        return "allow"
