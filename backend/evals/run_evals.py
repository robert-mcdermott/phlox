"""Agent eval harness — run NL scenarios against the *real* configured model and score them.

Unlike the unit tests (which use scripted fake providers and run in CI), this exercises the
live model end-to-end: it builds an ``AgentSession`` with the active profile, runs each
prompt, and checks the outcome (a tool was used, a file was produced, etc.).

Usage (from backend/, with a configured config.yml):
    uv run python -m evals.run_evals
    uv run python -m evals.run_evals --profile bedrock-sonnet

Exits non-zero if any scenario fails, so it can gate a release with a real model.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import dataclass
from collections.abc import Callable


@dataclass
class Scenario:
    name: str
    prompt: str
    # check(events_text, workspace_path) -> (passed, detail)
    check: Callable[[str, "Path"], tuple[bool, str]]


def _tools_used(events_text: str) -> set[str]:
    used = set()
    for line in events_text.splitlines():
        if line.startswith("data: "):
            try:
                ev = json.loads(line[6:])
            except ValueError:
                continue
            if ev.get("type") == "tool_call":
                used.add(ev.get("name"))
    return used


SCENARIOS = [
    Scenario(
        name="write_html_file",
        prompt="Create a minimal index.html file that says 'IT Department' in an <h1>.",
        check=lambda ev, ws: (
            (ws / "index.html").exists() and "IT Department" in (ws / "index.html").read_text(errors="ignore"),
            "index.html exists with expected content",
        ),
    ),
    Scenario(
        name="run_python_math",
        prompt="Use Python to compute the sum of integers 1..100 and print only the number.",
        check=lambda ev, ws: ("execute_python" in _tools_used(ev), "execute_python was called"),
    ),
    Scenario(
        name="plan_then_act",
        prompt="Make a 3-step plan to write a hello-world Python script, then create hello.py.",
        check=lambda ev, ws: ((ws / "hello.py").exists(), "hello.py created"),
    ),
]


def run_scenario(profile: str | None, sc: Scenario) -> tuple[bool, str]:
    from app.agent.harness import AgentSession
    from app.agent.permissions import PermissionGate
    from app.agent.registry import REGISTRY
    from app.config import default_generation_params, get_default_profile_name
    from app.database import SessionLocal
    from app.models import Conversation
    from app.providers.registry import build_provider
    from app.workspace.manager import workspace_dir

    prof = profile or get_default_profile_name()
    db = SessionLocal()
    try:
        conv = Conversation(id=uuid.uuid4().hex, title=sc.name, user_id="eval")
        db.add(conv)
        db.commit()
        db.refresh(conv)
        provider = build_provider(prof)
        gate = PermissionGate(db, REGISTRY, auto_approve=True)
        params = {**default_generation_params(), "max_tool_rounds": 8}
        session = AgentSession(db, conv, provider, REGISTRY, gate, params, prof, provider.model)
        events = "".join(
            session.run([
                {"role": "system", "content": params["system_prompt"]},
                {"role": "user", "content": sc.prompt},
            ])
        )
        return sc.check(events, workspace_dir(conv.id))
    finally:
        db.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None, help="provider profile to evaluate")
    args = ap.parse_args()

    from app.agent.registry import REGISTRY
    from app.agent.tools import register_builtin_tools
    from app.database import init_db

    init_db()
    if not REGISTRY.names():
        register_builtin_tools(REGISTRY)

    passed = 0
    for sc in SCENARIOS:
        try:
            ok, detail = run_scenario(args.profile, sc)
        except Exception as e:  # noqa: BLE001
            ok, detail = False, f"error: {e}"
        passed += ok
        print(f"[{'PASS' if ok else 'FAIL'}] {sc.name}: {detail}")
    print(f"\n{passed}/{len(SCENARIOS)} scenarios passed")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    sys.exit(main())
