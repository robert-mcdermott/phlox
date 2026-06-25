#!/usr/bin/env python3
"""End-to-end test for the AgentCore Code Interpreter sandbox runner (live AWS).

Exercises the exact runner contract the Phlox agent harness depends on
(``runner.run_code`` / ``run_command`` / ``close_session``, plus the local-workspace
file sync) against a real AWS Bedrock AgentCore Code Interpreter — proving the feature
works end-to-end without needing an LLM provider or the HTTP server.

It deliberately defeats the runner's silent local fallback: it preflights a real session
(failing loudly if AWS is misconfigured) and asserts after every run that the remote
session map is populated — so a green result genuinely means AWS executed the code, not
that we quietly ran locally.

Dependency-light: the core checks need only ``boto3`` (so it runs in the same venv you used
for the API probe). Two **optional** sections — config/``get_runner()`` dispatch and the
real tool layer — run automatically *if* the full Phlox backend deps are importable, and
are skipped with a note otherwise.

Prereqs:
  - ``boto3`` installed; AWS credentials in the environment (env / shared config / SSO /
    role); a region where Code Interpreter is enabled.
  - IAM: bedrock-agentcore:StartCodeInterpreterSession, :InvokeCodeInterpreter,
         :StopCodeInterpreterSession.

Usage (run from the repo root):
  python scripts/e2e_agentcore.py --region us-west-2
  python scripts/e2e_agentcore.py --region us-west-2 --profile my-profile
  python scripts/e2e_agentcore.py --region us-west-2 --keep        # keep the temp workspace
  python scripts/e2e_agentcore.py --region us-west-2 --skip-node   # skip the Node check

Exit code 0 = every check passed; 1 = a check failed (or AWS was unreachable).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import textwrap
import traceback
from pathlib import Path

# --------------------------------------------------------------------------- #
# Tiny check harness
# --------------------------------------------------------------------------- #
_RESULTS: list[tuple[bool, str, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    _RESULTS.append((bool(ok), name, detail))
    line = f"[{'PASS' if ok else 'FAIL'}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line, flush=True)
    return bool(ok)


def section(title: str) -> None:
    print("\n" + "=" * 72 + f"\n## {title}\n" + "=" * 72, flush=True)


def is_error(res) -> bool:
    """Mirror how Phlox's tools derive error state from an ExecResult."""
    return res.exit_code != 0


# --------------------------------------------------------------------------- #
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Live E2E for the AgentCore sandbox runner.")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION"))
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE"))
    ap.add_argument("--identifier", default="aws.codeinterpreter.v1")
    ap.add_argument("--skip-node", action="store_true", help="skip the execute_node check")
    ap.add_argument("--keep", action="store_true", help="keep the temp workspace/data dir")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    if not args.region:
        print("ERROR: no region. Pass --region or set AWS_REGION.", file=sys.stderr)
        return 2

    # Make `app` importable when run from the repo root (only app.sandbox.runner is
    # required for the core checks; it imports only stdlib + boto3 lazily).
    backend = Path(__file__).resolve().parent.parent / "backend"
    sys.path.insert(0, str(backend))

    try:
        from app.sandbox.runner import AgentCoreCodeInterpreterRunner
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: cannot import the runner ({exc}). Run from the repo root with boto3 installed.",
              file=sys.stderr)
        return 2

    print(f"region={args.region!r} profile={args.profile!r} identifier={args.identifier!r}")

    ac_cfg = {"identifier": args.identifier, "region": args.region}
    if args.profile:
        ac_cfg["profile"] = args.profile
    runner = AgentCoreCodeInterpreterRunner(ac_cfg)

    # A throwaway local workspace (stands in for data/workspaces/<conversation_id>/).
    tmp = Path(tempfile.mkdtemp(prefix="phlox-e2e-agentcore-"))
    ws = tmp / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    print(f"workspace: {ws}")

    # ---------------------------------------------------------------- #
    section("0. (optional) config + get_runner() dispatch")
    # ---------------------------------------------------------------- #
    _optional_config_dispatch(tmp, args)

    # ---------------------------------------------------------------- #
    section("1. Preflight: real AWS session (defeats silent local fallback)")
    # ---------------------------------------------------------------- #
    # _session_for is OUTSIDE _run's try/except, so an AWS error raises here loudly
    # instead of being swallowed by the fallback-to-local path.
    try:
        sid0 = runner._session_for(ws)
        check("opened a live Code Interpreter session", bool(sid0), f"sessionId={sid0}")
    except Exception as exc:  # noqa: BLE001
        check("opened a live Code Interpreter session", False, f"{type(exc).__name__}: {exc}")
        print("\nABORT: could not reach AgentCore. Check credentials, region, and IAM:")
        print("  bedrock-agentcore:StartCodeInterpreterSession / InvokeCodeInterpreter / StopCodeInterpreterSession")
        traceback.print_exc()
        return _summary_and_cleanup(runner, ws, tmp, keep=args.keep)

    def assert_remote_path(label: str) -> None:
        """After any run, the session map must be populated — else we fell back to local."""
        check(f"{label}: ran on AWS (not local fallback)", ws.name in runner._sessions,
              f"sessions={list(runner._sessions)}")

    # ---------------------------------------------------------------- #
    section("2. run_code(python) — stdout / exit / structuredContent")
    # ---------------------------------------------------------------- #
    res = runner.run_code("print('hello from agentcore')", "python", ws)
    check("stdout captured", "hello from agentcore" in res.stdout, repr(res.stdout[:120]))
    check("exit 0 -> not error", is_error(res) is False, f"exit={res.exit_code}")
    check("duration recorded", res.duration_s >= 0.0, f"{res.duration_s}s")
    assert_remote_path("run_code")

    res = runner.run_code("raise SystemExit(5)", "python", ws)
    check("nonzero exit -> error", is_error(res) is True, f"exit={res.exit_code} stderr={res.stderr[:80]!r}")

    # ---------------------------------------------------------------- #
    section("3. run_command — shell + CRLF normalization")
    # ---------------------------------------------------------------- #
    res = runner.run_command(["/bin/sh", "-c", "echo shell-works && pwd"], ws)
    check("run_command stdout captured", "shell-works" in res.stdout, repr(res.stdout[:160]))
    check("CRLF normalized to \\n (no stray \\r)", "\r" not in res.stdout)

    # ---------------------------------------------------------------- #
    section("4. sync IN — a local file is visible to the session")
    # ---------------------------------------------------------------- #
    (ws / "input.txt").write_text("payload-12345", encoding="utf-8")
    res = runner.run_code("print(open('input.txt').read())", "python", ws)
    check("local file synced into session", "payload-12345" in res.stdout, repr(res.stdout[:160]))

    # ---------------------------------------------------------------- #
    section("5. sync OUT — produced files (text + binary) come back as artifacts")
    # ---------------------------------------------------------------- #
    payload_hex = "89504e470d0a1a0aff00fe01"   # non-UTF-8 bytes -> exercises the blob path
    code = textwrap.dedent(
        f"""
        import os
        os.makedirs('produced', exist_ok=True)
        open('produced/out.csv', 'w').write('a,b\\n1,2\\n')
        open('blob.bin', 'wb').write(bytes.fromhex('{payload_hex}'))
        print('wrote files')
        """
    )
    res = runner.run_code(code, "python", ws)
    names = {a["name"] for a in res.artifacts}
    check("produced text file detected as artifact", "produced/out.csv" in names, str(names))
    check("produced binary file detected as artifact", "blob.bin" in names, str(names))
    out_csv = ws / "produced" / "out.csv"
    check("text artifact synced to local workspace with correct content",
          out_csv.exists() and out_csv.read_text(encoding="utf-8") == "a,b\n1,2\n")
    blob = ws / "blob.bin"
    check("binary artifact round-tripped byte-for-byte",
          blob.exists() and blob.read_bytes() == bytes.fromhex(payload_hex),
          f"{blob.stat().st_size if blob.exists() else 'missing'} bytes")

    # ---------------------------------------------------------------- #
    section("6. interpreter system files are NOT dragged into the workspace")
    # ---------------------------------------------------------------- #
    for junk in ("node_modules", "package.json", "package-lock.json", ".ipython"):
        check(f"workspace clean of '{junk}'", not (ws / junk).exists())

    # ---------------------------------------------------------------- #
    section("7. unchanged input is not re-flagged as an artifact")
    # ---------------------------------------------------------------- #
    res = runner.run_code("print('noop')", "python", ws)
    names = {a["name"] for a in res.artifacts}
    check("unchanged out.csv not re-reported as artifact", "produced/out.csv" not in names, str(names))

    # ---------------------------------------------------------------- #
    section("8. session is reused across runs")
    # ---------------------------------------------------------------- #
    sid_now = runner._sessions.get(ws.name)
    check("same session reused (no per-call session churn)", sid_now == sid0,
          f"start={sid0} now={sid_now}")

    # ---------------------------------------------------------------- #
    section("9. close_session tears the session down deterministically")
    # ---------------------------------------------------------------- #
    runner.close_session(ws)
    check("session removed from runner map", ws.name not in runner._sessions)
    runner.run_code("print('after close')", "python", ws)   # lazily opens a NEW session
    sid_new = runner._sessions.get(ws.name)
    check("next run opens a fresh session", bool(sid_new) and sid_new != sid0,
          f"old={sid0} new={sid_new}")

    # ---------------------------------------------------------------- #
    if not args.skip_node:
        section("10. run_code(node) — JavaScript runtime")
        res = runner.run_code("console.log('node ' + (1+2))", "node", ws)
        check("run_code(node) ran and printed", "node 3" in res.stdout, repr(res.stdout[:160]))

    # ---------------------------------------------------------------- #
    section("11. (optional) real tool layer via ToolContext")
    # ---------------------------------------------------------------- #
    _optional_tool_layer(runner, ws)

    return _summary_and_cleanup(runner, ws, tmp, keep=args.keep)


def _optional_config_dispatch(tmp: Path, args: argparse.Namespace) -> None:
    """If the full backend deps are present, verify config selection + get_runner().

    Wrapped whole (imports *and* calls) because some app modules import heavy deps like
    sqlalchemy lazily at call time, not import time.
    """
    data_dir = tmp / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = tmp / "config.yml"
    profile_line = f"    profile: {args.profile}\n" if args.profile else ""
    cfg_path.write_text(
        textwrap.dedent(
            f"""\
            auth:
              enabled: false
            sandbox:
              runner: agentcore
              agentcore:
                identifier: {args.identifier}
                region: {args.region}
            """
        )
        + profile_line,
        encoding="utf-8",
    )
    os.environ["PHLOX_CONFIG"] = str(cfg_path)
    os.environ["PHLOX_DATA"] = str(data_dir)
    try:
        from app.config import get_sandbox_config
        from app.sandbox.runner import (
            AgentCoreCodeInterpreterRunner,
            get_runner,
            reset_runner,
        )

        cfg = get_sandbox_config()
        check("config selects runner=agentcore", cfg.get("runner") == "agentcore", str(cfg.get("runner")))
        check("config has agentcore defaults",
              cfg.get("agentcore", {}).get("identifier") == args.identifier
              and cfg["agentcore"].get("session_timeout_seconds") == 900,
              str(cfg.get("agentcore")))
        reset_runner()
        r = get_runner()
        check("get_runner() returns AgentCoreCodeInterpreterRunner",
              isinstance(r, AgentCoreCodeInterpreterRunner), type(r).__name__)
        reset_runner()
    except Exception as exc:  # noqa: BLE001
        print(f"(skipped — full backend deps not available: {type(exc).__name__}: {exc})")
        print("  The core runner checks below still fully validate the feature. To enable")
        print("  this section, run with the backend venv (e.g. `pip install -e ./backend`).")


def _optional_tool_layer(runner, ws: Path) -> None:
    """If the tool classes are importable (need sqlalchemy etc.), drive one via ToolContext."""
    try:
        from app.agent.tools.base import ToolContext
        from app.agent.tools.code import ExecutePython

        ctx = ToolContext(conversation_id=ws.name, workspace=ws, db=None, runner=runner)
        result = ExecutePython().run(ctx, code="print('tool-layer ok')")
        check("execute_python tool runs through ToolContext + runner",
              "tool-layer ok" in result.content and result.is_error is False, repr(result.content[:120]))
    except Exception as exc:  # noqa: BLE001
        print(f"(skipped — tool layer not available: {type(exc).__name__}: {exc})")


def _summary_and_cleanup(runner, ws, tmp: Path, keep: bool) -> int:
    section("Cleanup")
    try:
        runner.close_session(ws)
        print("closed session (if any).")
    except Exception as exc:  # noqa: BLE001
        print(f"close_session during cleanup failed: {exc}")
    if keep:
        print(f"kept temp dir: {tmp}")
    else:
        shutil.rmtree(tmp, ignore_errors=True)
        print(f"removed temp dir: {tmp}")

    passed = sum(1 for ok, _, _ in _RESULTS if ok)
    failed = [name for ok, name, _ in _RESULTS if not ok]
    section("Summary")
    print(f"{passed}/{len(_RESULTS)} checks passed")
    if failed:
        print("FAILED:")
        for name in failed:
            print(f"  - {name}")
        print("\nRESULT: ❌ FAIL")
        return 1
    print("\nRESULT: ✅ ALL CHECKS PASSED — feature works end-to-end against live AWS.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
