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


_runner: SandboxRunner | None = None


def get_runner() -> SandboxRunner:
    """Return the process-wide sandbox runner, selected by config (local | container)."""
    global _runner
    if _runner is None:
        from app.config import get_sandbox_config

        cfg = get_sandbox_config()
        if cfg.get("runner") == "container":
            try:
                _runner = ContainerRunner(cfg["container"])
            except Exception as e:  # noqa: BLE001
                logger.warning("Container runner unavailable (%s); falling back to local", e)
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
