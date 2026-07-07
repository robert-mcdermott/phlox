"""Unit tests for the permission gate — the harness's security seam.

Covers: policy resolution (auto/ask/deny), the disabled-tool override, and the
``interactive`` flag that keeps an unattended sub-agent from either silently bypassing
"ask" tools or hanging on an approval pause nobody can answer.

Each test builds its own registry with uniquely-named tools and seeds ``ToolPref`` rows
from scratch, since the ``db`` fixture shares one sqlite file for the whole test session
(no per-test rollback) — reusing a tool name across tests would collide on the
``tool_prefs`` primary key or leak state between tests.
"""
import itertools

from app.agent.permissions import PermissionGate, seed_tool_prefs
from app.agent.registry import ToolRegistry
from app.agent.tools.base import Tool

_counter = itertools.count()


def _tool(default_permission: str) -> Tool:
    name = f"test_tool_{default_permission}_{next(_counter)}"

    class _T(Tool):
        pass

    _T.name = name
    _T.default_permission = default_permission
    return _T()


def _seeded_gate(db, tools: list[Tool], **kw) -> tuple[PermissionGate, list[str]]:
    reg = ToolRegistry()
    for t in tools:
        reg.register(t)
    seed_tool_prefs(db, reg)
    return PermissionGate(db, reg, **kw), [t.name for t in tools]


def test_decide_auto_policy_allows(db):
    tool = _tool("auto")
    gate, _ = _seeded_gate(db, [tool])
    assert gate.decide(tool.name) == "allow"


def test_decide_deny_policy_always_denies(db):
    tool = _tool("deny")
    gate, _ = _seeded_gate(db, [tool], auto_approve=True)
    assert gate.decide(tool.name) == "deny"


def test_decide_unregistered_tool_defaults_to_allow(db):
    # No ToolPref row and not in this registry: decide() falls back to policy "auto".
    gate, _ = _seeded_gate(db, [])
    assert gate.decide("nonexistent_tool") == "allow"


def test_decide_ask_policy_pauses_when_interactive_and_not_auto_approved(db):
    tool = _tool("ask")
    gate, _ = _seeded_gate(db, [tool], auto_approve=False, interactive=True)
    assert gate.decide(tool.name) == "ask"


def test_decide_ask_policy_allows_when_auto_approved(db):
    tool = _tool("ask")
    gate, _ = _seeded_gate(db, [tool], auto_approve=True, interactive=True)
    assert gate.decide(tool.name) == "allow"


def test_decide_ask_policy_denies_when_not_interactive_and_not_auto_approved(db):
    # This is the sub-agent case: nobody can answer an approval pause, so "ask" must not
    # silently become "allow" (a permission bypass) or hang (a broken turn) — it must
    # become "deny".
    tool = _tool("ask")
    gate, _ = _seeded_gate(db, [tool], auto_approve=False, interactive=False)
    assert gate.decide(tool.name) == "deny"


def test_decide_ask_policy_still_allows_when_auto_approved_and_not_interactive(db):
    # auto_approve reflects the *parent turn's* real setting, so it still wins even in a
    # non-interactive (sub-agent) gate.
    tool = _tool("ask")
    gate, _ = _seeded_gate(db, [tool], auto_approve=True, interactive=False)
    assert gate.decide(tool.name) == "allow"


def test_disabled_tool_pref_overrides_policy_to_deny(db):
    from app.models import ToolPref

    tool = _tool("auto")
    reg = ToolRegistry()
    reg.register(tool)
    seed_tool_prefs(db, reg)
    db.get(ToolPref, tool.name).enabled = False
    db.commit()

    gate = PermissionGate(db, reg)
    assert gate.decide(tool.name) == "deny"


def test_enabled_names_excludes_denied_and_disabled_tools(db):
    from app.models import ToolPref

    auto_tool, ask_tool, deny_tool = _tool("auto"), _tool("ask"), _tool("deny")
    reg = ToolRegistry()
    for t in (auto_tool, ask_tool, deny_tool):
        reg.register(t)
    seed_tool_prefs(db, reg)
    db.get(ToolPref, auto_tool.name).enabled = False
    db.commit()

    gate = PermissionGate(db, reg)  # constructed after the mutation, so it sees it
    enabled = gate.enabled_names()
    assert auto_tool.name not in enabled  # disabled
    assert deny_tool.name not in enabled  # policy=deny
    assert ask_tool.name in enabled       # ask is still "enabled", just gated at call time
