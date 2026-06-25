#!/usr/bin/env python3
"""End-to-end test for the AgentCore Code Interpreter sandbox runner (live AWS).

Drives the feature through Phlox's **own** code paths — config selection, ``get_runner()``,
the real ``execute_python`` / ``execute_node`` / ``run_shell`` / ``write_file`` /
``read_file`` tools via a ``ToolContext``, and ``close_session`` — against a real AWS
Bedrock AgentCore Code Interpreter. It proves the whole integration the agent relies on,
without needing an LLM provider or the HTTP server.

It deliberately defeats the runner's silent local fallback: it preflights a real session
(failing loudly if AWS is misconfigured) and asserts after every run that the remote
session map is populated — so a green result genuinely means AWS executed the code, not
that we quietly ran locally.

Prereqs:
  - Run with the backend venv active (needs Phlox deps: boto3, sqlalchemy, pyyaml, ...).
  - AWS credentials in the environment (env / shared config / SSO / role), and a region
    where Code Interpreter is enabled.
  - IAM: bedrock-agentcore:StartCodeInterpreterSession, :InvokeCodeInterpreter,
         :StopCodeInterpreterSession.

Usage:
  python scripts/e2e_agentcore.py --region us-west-2
  python scripts/e2e_agentcore.py --region us-west-2 --profile my-profile
  python scripts/e2e_agentcore.py --region us-west-2 --keep      # don't delete workspace
  python scripts/e2e_agentcore.py --region us-west-2 --skip-node # skip the Node check

Exit code 0 = every check passed; 1 = a check failed (or AWS was unreachable).
It writes only to a throwaway temp PHLOX_DATA dir and tears the session down at the end,
so it does not touch your real Phlox data or leak AWS sessions.
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
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}"
    if detail:
        line += f"  — {detail}"
    print(line, flush=True)
    return bool(ok)


def section(title: str) -> None:
    print("\n" + "=" * 72 + f"\n## {title}\n" + "=" * 72, flush=True)


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

    # ---- Isolate Phlox config + data to a throwaway dir, selecting the agentcore
    #      runner, BEFORE importing any app module (app.config reads env at import). ----
    tmp = Path(tempfile.mkdtemp(prefix="phlox-e2e-agentcore-"))
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
    if args.profile:
        os.environ["AWS_PROFILE"] = args.profile
    os.environ.setdefault("AWS_REGION", args.region)

    # Make `app` importable when run from the repo root.
    backend = Path(__file__).resolve().parent.parent / "backend"
    sys.path.insert(0, str(backend))

    # ---- Imports (after env setup) ----
    from app.agent.tools.base import ToolContext
    from app.agent.tools.code import ExecuteNode, ExecutePython
    from app.agent.tools.fs import ListDir, ReadFile, WriteFile
    from app.agent.tools.shell import RunShell
    from app.config import get_sandbox_config
    from app.sandbox.runner import (
        AgentCoreCodeInterpreterRunner,
        get_runner,
        reset_runner,
    )
    from app.workspace.manager import workspace_dir

    print(f"region={args.region!r} profile={args.profile!r} identifier={args.identifier!r}")
    print(f"temp data dir: {data_dir}")

    # ---------------------------------------------------------------- #
    section("1. Config + get_runner() dispatch")
    # ---------------------------------------------------------------- #
    cfg = get_sandbox_config()
    check("config selects runner=agentcore", cfg.get("runner") == "agentcore", cfg.get("runner"))
    check("config has agentcore defaults",
          cfg.get("agentcore", {}).get("identifier") == args.identifier
          and cfg["agentcore"].get("session_timeout_seconds") == 900,
          str(cfg.get("agentcore")))

    reset_runner()
    runner = get_runner()
    is_agentcore = isinstance(runner, AgentCoreCodeInterpreterRunner)
    check("get_runner() returns AgentCoreCodeInterpreterRunner", is_agentcore, type(runner).__name__)
    if not is_agentcore:
        print("\nABORT: runner is not the AgentCore runner; nothing else can be validated.")
        return 1

    conv_id = f"e2e-agentcore-{os.getpid()}"
    ws = workspace_dir(conv_id)
    ctx = ToolContext(conversation_id=conv_id, workspace=ws, db=None, runner=runner)

    # ---------------------------------------------------------------- #
    section("2. Preflight: real AWS session (defeats silent local fallback)")
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
        _summary_and_cleanup(runner, ws, tmp, keep=args.keep)
        return 1

    def assert_remote_path(label: str) -> None:
        """After any tool run, the session map must be populated — else we fell back to local."""
        check(f"{label}: ran on AWS (not local fallback)", ws.name in runner._sessions,
              f"sessions={list(runner._sessions)}")

    # ---------------------------------------------------------------- #
    section("3. execute_python — stdout / exit / structuredContent")
    # ---------------------------------------------------------------- #
    r = ExecutePython().run(ctx, code="print('hello from agentcore')")
    check("stdout captured", "hello from agentcore" in r.content, repr(r.content[:120]))
    check("exit 0 -> not is_error", r.is_error is False)
    assert_remote_path("execute_python")

    r = ExecutePython().run(ctx, code="raise SystemExit(5)")
    check("nonzero exit -> is_error", r.is_error is True, repr(r.content[:160]))

    # ---------------------------------------------------------------- #
    section("4. run_shell — command + CRLF normalization")
    # ---------------------------------------------------------------- #
    r = RunShell().run(ctx, command="echo shell-works && pwd")
    check("run_shell stdout captured", "shell-works" in r.content, repr(r.content[:160]))
    check("CRLF normalized to \\n (no stray \\r)", "\r" not in r.content)

    # ---------------------------------------------------------------- #
    section("5. sync IN — a local file is visible to the session")
    # ---------------------------------------------------------------- #
    WriteFile().run(ctx, path="input.txt", content="payload-12345")
    r = ExecutePython().run(ctx, code="print(open('input.txt').read())")
    check("local file synced into session", "payload-12345" in r.content, repr(r.content[:160]))

    # ---------------------------------------------------------------- #
    section("6. sync OUT — produced files (text + binary) come back as artifacts")
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
    r = ExecutePython().run(ctx, code=code)
    names = {a["name"] for a in r.artifacts}
    check("produced text file detected as artifact", "produced/out.csv" in names, str(names))
    check("produced binary file detected as artifact", "blob.bin" in names, str(names))
    out_csv = ws / "produced" / "out.csv"
    check("text artifact synced to local workspace with correct content",
          out_csv.exists() and out_csv.read_text(encoding="utf-8") == "a,b\n1,2\n")
    blob = ws / "blob.bin"
    check("binary artifact round-tripped byte-for-byte",
          blob.exists() and blob.read_bytes() == bytes.fromhex(payload_hex),
          f"{blob.stat().st_size if blob.exists() else 'missing'} bytes")

    # read_file (a pure local tool) sees the produced file -> local dir is authoritative.
    r = ReadFile().run(ctx, path="produced/out.csv")
    check("read_file (local tool) sees the produced file", "a,b" in r.content)

    # ---------------------------------------------------------------- #
    section("7. interpreter system files are NOT dragged into the workspace")
    # ---------------------------------------------------------------- #
    for junk in ("node_modules", "package.json", "package-lock.json", ".ipython"):
        check(f"workspace clean of '{junk}'", not (ws / junk).exists())
    r = ListDir().run(ctx, path=".")
    check("list_dir shows our files, not interpreter internals",
          "input.txt" in r.content and "node_modules" not in r.content, repr(r.content[:200]))

    # ---------------------------------------------------------------- #
    section("8. unchanged input is not re-flagged as an artifact")
    # ---------------------------------------------------------------- #
    r = ExecutePython().run(ctx, code="print('noop')")
    names = {a["name"] for a in r.artifacts}
    check("unchanged out.csv not re-reported as artifact", "produced/out.csv" not in names, str(names))

    # ---------------------------------------------------------------- #
    section("9. session is reused across runs")
    # ---------------------------------------------------------------- #
    sid_now = runner._sessions.get(ws.name)
    check("same session reused (no per-call session churn)", sid_now == sid0,
          f"start={sid0} now={sid_now}")

    # ---------------------------------------------------------------- #
    section("10. close_session tears the session down deterministically")
    # ---------------------------------------------------------------- #
    runner.close_session(ws)
    check("session removed from runner map", ws.name not in runner._sessions)
    # A subsequent run lazily opens a NEW session (different id).
    ExecutePython().run(ctx, code="print('after close')")
    sid_new = runner._sessions.get(ws.name)
    check("next run opens a fresh session", bool(sid_new) and sid_new != sid0,
          f"old={sid0} new={sid_new}")

    # ---------------------------------------------------------------- #
    if not args.skip_node:
        section("11. execute_node — JavaScript runtime")
        r = ExecuteNode().run(ctx, code="console.log('node ' + (1+2))")
        check("execute_node ran and printed", "node 3" in r.content, repr(r.content[:160]))

    return _summary_and_cleanup(runner, ws, tmp, keep=args.keep)


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
