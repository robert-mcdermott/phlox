"""Sandboxed command/code execution.

``SandboxRunner`` is the seam for isolation strategy. ``LocalSubprocessRunner`` runs in
a restricted subprocess rooted at the conversation workspace, with a timeout and output
caps, and reports newly-created files as *artifacts* (e.g. plots). To harden isolation,
implement a ``DockerRunner`` with the same interface and swap it in ``get_runner``.

NOTE: the local runner trusts the host. It is appropriate for a single-user/local
deployment. Treat untrusted multi-user use as requiring the Docker upgrade.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

# Output caps to keep tool results model-friendly.
MAX_OUTPUT_CHARS = 30_000
DEFAULT_TIMEOUT = 60

# Inline image preview extensions (rendered in the chat); all other produced files are
# still captured as downloadable artifacts and shown in the workspace files panel.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}

# Directories/files to ignore when detecting produced artifacts (internal/noise).
_IGNORE_DIRS = {".git", ".hutchchat", "__pycache__", "node_modules", ".venv", ".cache"}
_IGNORE_EXTS = {".pyc", ".pyo"}
MAX_ARTIFACTS = 30


@dataclass
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool = False
    duration_s: float = 0.0
    # New/changed files detected after the run, relative to the workspace.
    artifacts: list[dict] = field(default_factory=list)


def _truncate(s: str) -> str:
    if len(s) > MAX_OUTPUT_CHARS:
        return s[:MAX_OUTPUT_CHARS] + f"\n... [truncated {len(s) - MAX_OUTPUT_CHARS} chars]"
    return s


class SandboxRunner(ABC):
    @abstractmethod
    def run_command(self, argv: list[str], workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult: ...

    @abstractmethod
    def run_code(self, code: str, language: str, workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult: ...

    def close_session(self, workdir: Path) -> None:
        """Tear down any per-conversation remote session for this workspace.

        A concrete no-op so existing runners are unchanged: ``local`` and ``container``
        hold no session and inherit this. Remote runners that open a per-workspace
        session (e.g. the AgentCore runner) override it to release the session
        deterministically when a conversation is deleted — no leaked sessions, no
        TTL-reaper hack.
        """
        return None


def snapshot_dir(workdir: Path) -> dict[str, float]:
    snap: dict[str, float] = {}
    for p in workdir.rglob("*"):
        if p.is_file():
            try:
                snap[str(p)] = p.stat().st_mtime
            except OSError:
                pass
    return snap


def collect_artifacts(workdir: Path, before: dict[str, float]) -> list[dict]:
    """Capture any new/changed file a run produced (not just images), excluding
    internal/noise paths. Everything captured is downloadable; images also preview."""
    arts: list[dict] = []
    for p in workdir.rglob("*"):
        if not p.is_file():
            continue
        rel = p.relative_to(workdir)
        if any(part in _IGNORE_DIRS for part in rel.parts):
            continue
        if p.suffix.lower() in _IGNORE_EXTS:
            continue
        key = str(p)
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        is_new = key not in before
        is_changed = key in before and mtime > before[key]
        if is_new or is_changed:
            arts.append(
                {"name": str(rel), "path": str(rel), "ext": p.suffix.lower(), "size": p.stat().st_size}
            )
        if len(arts) >= MAX_ARTIFACTS:
            break
    return arts


class LocalSubprocessRunner(SandboxRunner):
    def _snapshot(self, workdir: Path) -> dict[str, float]:
        return snapshot_dir(workdir)

    def _collect_artifacts(self, workdir: Path, before: dict[str, float]) -> list[dict]:
        return collect_artifacts(workdir, before)

    def _exec(self, argv: list[str], workdir: Path, timeout: int, stdin: str | None = None) -> ExecResult:
        workdir.mkdir(parents=True, exist_ok=True)
        before = self._snapshot(workdir)
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(
                argv,
                cwd=str(workdir),
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
            )
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = e.stdout or ""
            stderr = (e.stderr or "") + f"\n[timed out after {timeout}s]"
            code = -1
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", "replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", "replace")
        except FileNotFoundError as e:
            return ExecResult(stdout="", stderr=f"Executable not found: {e}", exit_code=127)

        return ExecResult(
            stdout=_truncate(stdout),
            stderr=_truncate(stderr),
            exit_code=code,
            timed_out=timed_out,
            duration_s=round(time.monotonic() - start, 3),
            artifacts=self._collect_artifacts(workdir, before),
        )

    def run_command(self, argv: list[str], workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        return self._exec(argv, workdir, timeout)

    def run_code(self, code: str, language: str, workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        if language == "python":
            return self._exec([sys.executable, "-c", code], workdir, timeout)
        if language in ("node", "javascript"):
            return self._exec(["node", "-e", code], workdir, timeout)
        return ExecResult(stdout="", stderr=f"Unsupported language: {language}", exit_code=2)


# ---------------------------------------------------------------------------
# Container sandbox (Podman / Docker, via the Docker-compatible CLI)
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# Common Windows install locations to probe when the engine isn't on PATH.
_ENGINE_PROBE = [
    Path.home() / "AppData/Local/Programs/Podman/podman.exe",
    Path(r"C:\Program Files\RedHat\Podman\podman.exe"),
    Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe"),
]


def detect_container_engine(preference: str = "auto") -> str | None:
    """Return the path/name of a Docker-compatible engine, or None if unavailable."""
    if preference not in ("auto", "podman", "docker"):
        # An explicit path/command was configured.
        return preference if (shutil.which(preference) or Path(preference).exists()) else None
    candidates = ["podman", "docker"] if preference == "auto" else [preference]
    for name in candidates:
        found = shutil.which(name)
        if found:
            return found
    for p in _ENGINE_PROBE:
        if p.exists() and (preference == "auto" or preference in p.name):
            return str(p)
    return None


class ContainerRunner(SandboxRunner):
    """Run commands/code in an ephemeral, resource-limited container.

    Targets the **Docker-compatible CLI**, so it works with Podman (incl. Podman's
    Docker-compat mode) and Docker, on Windows/macOS/Linux. The conversation workspace is
    bind-mounted at /work; produced files therefore land back on the host and are captured
    as artifacts exactly like the local runner.
    """

    def __init__(self, config: dict):
        self.cfg = config
        self.engine = detect_container_engine(config.get("engine", "auto"))
        if not self.engine:
            raise RuntimeError("No container engine (podman/docker) found")
        self._fallback = LocalSubprocessRunner()
        logger.info("ContainerRunner using engine: %s", self.engine)

    def _limit_flags(self) -> list[str]:
        c = self.cfg
        flags = [
            "--rm", "--network", str(c.get("network", "none")),
            "--memory", str(c.get("memory", "512m")),
            "--cpus", str(c.get("cpus", "1.0")),
            "--pids-limit", str(c.get("pids_limit", 256)),
        ]
        return flags

    def _run(self, image: str, inner_argv: list[str], workdir: Path, timeout: int) -> ExecResult:
        workdir.mkdir(parents=True, exist_ok=True)
        before = snapshot_dir(workdir)
        argv = [
            self.engine, "run", *self._limit_flags(),
            "-v", f"{workdir}:/work", "-w", "/work",
            image, *inner_argv,
        ]
        start = time.monotonic()
        timed_out = False
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
            stdout, stderr, code = proc.stdout, proc.stderr, proc.returncode
        except subprocess.TimeoutExpired as e:
            timed_out = True
            stdout = (e.stdout or "") if isinstance(e.stdout, str) else (e.stdout or b"").decode("utf-8", "replace")
            stderr = ((e.stderr or "") if isinstance(e.stderr, str) else (e.stderr or b"").decode("utf-8", "replace"))
            stderr += f"\n[timed out after {timeout}s]"
            code = -1
        except FileNotFoundError as e:
            return ExecResult(stdout="", stderr=f"Container engine not found: {e}", exit_code=127)

        return ExecResult(
            stdout=_truncate(stdout), stderr=_truncate(stderr), exit_code=code,
            timed_out=timed_out, duration_s=round(time.monotonic() - start, 3),
            artifacts=collect_artifacts(workdir, before),
        )

    def run_command(self, argv: list[str], workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        # Normalize host shell wrappers (cmd /c, /bin/sh -c) to a container shell command.
        if len(argv) >= 3 and argv[0] in ("cmd", "/bin/sh", "sh") and argv[1] in ("/c", "-c"):
            command = argv[2]
        else:
            command = " ".join(argv)
        return self._run(self.cfg["python_image"], ["sh", "-c", command], workdir, timeout)

    def run_code(self, code: str, language: str, workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        run_dir = workdir / ".phlox"
        run_dir.mkdir(parents=True, exist_ok=True)
        if language == "python":
            (run_dir / "run.py").write_text(code, encoding="utf-8")
            return self._run(self.cfg["python_image"], ["python", "/work/.phlox/run.py"], workdir, timeout)
        if language in ("node", "javascript"):
            (run_dir / "run.js").write_text(code, encoding="utf-8")
            return self._run(self.cfg["node_image"], ["node", "/work/.phlox/run.js"], workdir, timeout)
        return ExecResult(stdout="", stderr=f"Unsupported language: {language}", exit_code=2)


# ---------------------------------------------------------------------------
# Remote sandbox (AWS Bedrock AgentCore Code Interpreter)
# ---------------------------------------------------------------------------
class AgentCoreCodeInterpreterRunner(SandboxRunner):
    """Run code/commands in an AWS Bedrock AgentCore Code Interpreter microVM.

    Unlike the local/container runners, agent-generated code runs **off-host** with
    kernel-level (Firecracker microVM) isolation — a real security upgrade for a
    hosted/multi-user Phlox, since untrusted code never touches the Phlox host.

    One Code Interpreter session is opened per conversation workspace (keyed by
    ``workdir.name``) and reused across calls. The **local workspace stays
    authoritative**: before each run the local files are synced *in*, and after each
    run files in the session are synced *out* into the local dir — so all of Phlox's
    local FS tools (read/write/edit/glob/grep) and mtime-based artifact detection keep
    working untouched. ``close_session()`` tears the session down deterministically when
    the conversation is deleted. Any AWS error falls back to the local runner so a
    misconfiguration degrades gracefully instead of breaking the turn.
    """

    def __init__(self, config: dict):
        self.cfg = config or {}
        self.identifier = self.cfg.get("identifier", "aws.codeinterpreter.v1")
        self.region = self.cfg.get("region")
        self.profile = self.cfg.get("profile")
        self.session_timeout = int(self.cfg.get("session_timeout_seconds", 900))
        # Skip syncing very large files in/out to bound per-call cost (full-sync is O(workspace)).
        self.max_sync_bytes = int(self.cfg.get("max_sync_file_bytes", 5_000_000))
        # Sync produced files back into the local workspace after each run (so the local
        # FS tools + artifact detection see them). Can be disabled for execute-only use.
        self.sync_back = bool(self.cfg.get("sync_back", True))
        self._client = None
        self._sessions: dict[str, str] = {}     # workdir.name -> sessionId
        self._lock = threading.Lock()
        self._fallback = LocalSubprocessRunner()

    # -- AWS client / session lifecycle -------------------------------------
    def _c(self):
        if self._client is None:
            import boto3  # already a Phlox dependency (used by the Bedrock provider)

            if self.profile:
                self._client = boto3.Session(
                    profile_name=self.profile, region_name=self.region
                ).client("bedrock-agentcore")
            else:
                self._client = boto3.client("bedrock-agentcore", region_name=self.region)
        return self._client

    def _session_for(self, workdir: Path) -> str:
        key = workdir.name
        with self._lock:
            sid = self._sessions.get(key)
            if sid:
                return sid
        resp = self._c().start_code_interpreter_session(
            codeInterpreterIdentifier=self.identifier,
            name=f"phlox-{key}"[:50],
            sessionTimeoutSeconds=self.session_timeout,
        )
        sid = resp["sessionId"]
        with self._lock:
            # Another thread may have created one concurrently; keep the first and
            # drop ours so we don't leak a session.
            existing = self._sessions.get(key)
            if existing:
                other = sid
            else:
                self._sessions[key] = sid
                other = None
        if other:
            self._stop(other)
            return existing
        return sid

    def _stop(self, sid: str) -> None:
        try:
            self._c().stop_code_interpreter_session(
                codeInterpreterIdentifier=self.identifier, sessionId=sid
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("AgentCore stop_session failed: %s", exc)

    def close_session(self, workdir: Path) -> None:
        with self._lock:
            sid = self._sessions.pop(workdir.name, None)
        if sid:
            self._stop(sid)

    # -- invoke + event-stream handling -------------------------------------
    def _invoke(self, sid: str, name: str, arguments: dict):
        return self._c().invoke_code_interpreter(
            codeInterpreterIdentifier=self.identifier, sessionId=sid, name=name, arguments=arguments
        )

    @staticmethod
    def _consume(resp) -> dict:
        """Drain an ``invoke_code_interpreter`` response into one result dict.

        The response carries an event stream under ``stream``; each ``result`` event has
        ``result.structuredContent`` (clean ``stdout``/``stderr``/``exitCode`` for
        execute* tools) and ``result.content[]`` (MCP-style ``text``/``resource`` blocks,
        used by the file tools). Shapes are from the ``bedrock-agentcore`` botocore model.
        """
        out = {"stdout": "", "stderr": "", "exit_code": 0, "is_error": False,
               "content": [], "text": ""}
        text_parts: list[str] = []
        for event in resp.get("stream", []):
            result = event.get("result")
            if not result:
                continue
            if result.get("isError"):
                out["is_error"] = True
            sc = result.get("structuredContent") or {}
            out["stdout"] += sc.get("stdout") or ""
            out["stderr"] += sc.get("stderr") or ""
            if sc.get("exitCode") is not None:
                try:
                    out["exit_code"] = int(sc["exitCode"])
                except (TypeError, ValueError):
                    pass
            for block in result.get("content", []) or []:
                out["content"].append(block)
                if block.get("type") == "text" and block.get("text"):
                    text_parts.append(block["text"])
        out["text"] = "\n".join(text_parts)
        return out

    # -- local <-> session file sync ----------------------------------------
    def _sync_in(self, sid: str, workdir: Path) -> None:
        files: list[dict] = []
        for p in workdir.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(workdir)
            if any(part in _IGNORE_DIRS for part in rel.parts):
                continue
            try:
                if p.stat().st_size > self.max_sync_bytes:
                    continue
            except OSError:
                continue
            entry: dict = {"path": str(rel)}
            try:
                entry["text"] = p.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                try:
                    entry["blob"] = p.read_bytes()   # binary file -> blob (model supports it)
                except OSError:
                    continue
            files.append(entry)
        if files:
            self._invoke(sid, "writeFiles", {"content": files})

    def _sync_out(self, sid: str, workdir: Path) -> None:
        if not self.sync_back:
            return
        try:
            listing = self._consume(self._invoke(sid, "listFiles", {"directoryPath": "."}))
            paths = self._listing_paths(listing)
            if not paths:
                return
            read = self._consume(self._invoke(sid, "readFiles", {"paths": paths}))
            self._write_back(read["content"], workdir)
        except Exception as exc:  # noqa: BLE001
            # Best-effort: the local dir stays authoritative, so a sync-out miss never
            # breaks the run — it just means produced files may not appear locally.
            logger.warning("AgentCore sync_out failed: %s", exc)

    @staticmethod
    def _safe_rel(path: str) -> str | None:
        """Return a workspace-relative path, or None if it escapes/looks unsafe."""
        if not path:
            return None
        path = path.lstrip("./")
        if not path or path.startswith("/") or ".." in Path(path).parts:
            return None
        return path

    def _listing_paths(self, listing: dict) -> list[str]:
        """Best-effort parse of ``listFiles`` output into relative file paths.

        listFiles surfaces entries either as ``resource`` content blocks (with a ``uri``)
        or as a text block (newline- or JSON-listed). We accept whichever appears and skip
        anything that doesn't look like a safe relative path.
        """
        paths: list[str] = []
        for block in listing.get("content", []):
            if block.get("type") in ("resource", "resource_link"):
                uri = block.get("uri") or (block.get("resource") or {}).get("uri") or block.get("name")
                rel = self._safe_rel(str(uri)) if uri else None
                if rel:
                    paths.append(rel)
            elif block.get("type") == "text" and block.get("text"):
                for line in block["text"].splitlines():
                    rel = self._safe_rel(line.strip().strip('",[]'))
                    if rel:
                        paths.append(rel)
        # De-dup, preserve order.
        seen: set[str] = set()
        return [p for p in paths if not (p in seen or seen.add(p))]

    def _write_back(self, content: list[dict], workdir: Path) -> None:
        """Write ``readFiles`` content blocks back into the local workspace."""
        for block in content:
            res = block.get("resource") if block.get("type") == "resource" else None
            uri = (res or {}).get("uri") or block.get("uri") or block.get("name")
            rel = self._safe_rel(str(uri)) if uri else None
            if not rel:
                continue
            dest = workdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            text = (res or {}).get("text", block.get("text"))
            blob = (res or {}).get("blob", block.get("data"))
            try:
                if text is not None:
                    dest.write_text(text, encoding="utf-8")
                elif blob is not None:
                    dest.write_bytes(blob if isinstance(blob, (bytes, bytearray)) else bytes(blob))
            except OSError as exc:
                logger.warning("AgentCore write_back %s failed: %s", rel, exc)

    # -- SandboxRunner interface --------------------------------------------
    def run_command(self, argv: list[str], workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        # Normalize host shell wrappers (cmd /c, /bin/sh -c) to a single command string.
        if len(argv) >= 3 and argv[0] in ("cmd", "/bin/sh", "sh") and argv[1] in ("/c", "-c"):
            command = argv[2]
        else:
            command = " ".join(argv)
        return self._run(
            lambda sid: self._invoke(sid, "executeCommand", {"command": command}),
            workdir,
            lambda: self._fallback.run_command(argv, workdir, timeout),
        )

    def run_code(self, code: str, language: str, workdir: Path, timeout: int = DEFAULT_TIMEOUT) -> ExecResult:
        if language == "python":
            lang = "python"
        elif language in ("node", "javascript"):
            lang = "javascript"   # AgentCore executeCode language token for Node
        elif language == "typescript":
            lang = "typescript"
        else:
            return ExecResult(stdout="", stderr=f"Unsupported language: {language}", exit_code=2)
        return self._run(
            lambda sid: self._invoke(sid, "executeCode", {"language": lang, "code": code}),
            workdir,
            lambda: self._fallback.run_code(code, language, workdir, timeout),
        )

    def _run(self, do_invoke, workdir: Path, fallback) -> ExecResult:
        workdir.mkdir(parents=True, exist_ok=True)
        start = time.monotonic()
        try:
            sid = self._session_for(workdir)
            before = snapshot_dir(workdir)
            self._sync_in(sid, workdir)
            res = self._consume(do_invoke(sid))
            self._sync_out(sid, workdir)
            exit_code = res["exit_code"]
            # isError without a non-zero code still means failure; surface it so tools'
            # `is_error = exit_code != 0` is correct.
            if res["is_error"] and exit_code == 0:
                exit_code = 1
            stdout = res["stdout"] or res["text"]
            return ExecResult(
                stdout=_truncate(stdout),
                stderr=_truncate(res["stderr"]),
                exit_code=exit_code,
                duration_s=round(time.monotonic() - start, 3),
                artifacts=collect_artifacts(workdir, before),
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("AgentCore runner failed (%s); falling back to local", e)
            return fallback()


_runner: SandboxRunner | None = None


def get_runner() -> SandboxRunner:
    """Return the process-wide sandbox runner, selected by config (local | container | agentcore)."""
    global _runner
    if _runner is None:
        from app.config import get_sandbox_config

        cfg = get_sandbox_config()
        runner_type = cfg.get("runner")
        if runner_type == "container":
            try:
                _runner = ContainerRunner(cfg["container"])
            except Exception as e:  # noqa: BLE001
                logger.warning("Container runner unavailable (%s); falling back to local", e)
                _runner = LocalSubprocessRunner()
        elif runner_type == "agentcore":
            try:
                _runner = AgentCoreCodeInterpreterRunner(cfg.get("agentcore", {}))
            except Exception as e:  # noqa: BLE001
                logger.warning("AgentCore runner unavailable (%s); falling back to local", e)
                _runner = LocalSubprocessRunner()
        else:
            _runner = LocalSubprocessRunner()
    return _runner


def reset_runner() -> None:
    """Drop the cached runner so the next ``get_runner()`` rebuilds it from current config.

    Called when an admin edits the sandbox limits in the UI, so the new memory/CPU/network
    caps apply without a backend restart. The runner *type* is read fresh from config too,
    but the UI never changes it (it stays file-only).
    """
    global _runner
    _runner = None
