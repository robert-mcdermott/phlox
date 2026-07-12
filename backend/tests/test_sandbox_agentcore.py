"""Tests for the AgentCore remote sandbox runner + the additive close_session hook.

No live AWS is needed: the boto3 client is replaced with a ``FakeClient`` whose responses
mirror the **real** ``bedrock-agentcore`` Code Interpreter shapes captured from a live
session — ``result.structuredContent`` (stdout/stderr/exitCode) for execute*, and a small
virtual filesystem behind ``writeFiles``/``listFiles``/``readFiles`` (one-level
``resource_link`` listings with name+description; ``resource`` blocks with ``file:///``
uris on read). These lock in dispatch, fallback, output parsing, the baseline-aware file
sync, and deterministic session teardown — independent of the real service.
"""
import pytest

from app.sandbox import runner as runner_mod
from app.sandbox.runner import (
    AgentCoreCodeInterpreterRunner,
    LocalSubprocessRunner,
)


# --------------------------------------------------------------------------- #
# A fake bedrock-agentcore client backed by a virtual session filesystem
# --------------------------------------------------------------------------- #
def _event(content=None, structured=None, is_error=False):
    result = {"content": content or [], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return {"result": result}


# Mirrors the live session's pre-populated working dir (interpreter internals).
_SYSTEM_FILES = {"node_modules/x.js": "//", "package.json": "{}", "log/app.log": ""}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.session_counter = 0
        self.stopped = []
        # path -> str|bytes. Starts with system files; writeFiles/run add ours.
        self.fs: dict[str, object] = dict(_SYSTEM_FILES)
        # Files an executeCode call "produces" — merged into fs *after* session start, so
        # they're not part of the baseline (i.e. they look like genuine run output).
        self.produce: dict[str, object] = {}

    def start_code_interpreter_session(self, **kw):
        self.calls.append(("start", kw))
        self.session_counter += 1
        return {"sessionId": f"sess-{self.session_counter}"}

    def stop_code_interpreter_session(self, **kw):
        self.calls.append(("stop", kw))
        self.stopped.append(kw["sessionId"])
        return {}

    def _children(self, dirpath):
        prefix = "" if dirpath in (".", "") else dirpath.rstrip("/") + "/"
        dirs, files = set(), set()
        for path in self.fs:
            if prefix and not path.startswith(prefix):
                continue
            rest = path[len(prefix):]
            if "/" in rest:
                dirs.add(rest.split("/", 1)[0])
            elif rest:
                files.add(rest)
        blocks = []
        for d in sorted(dirs):
            blocks.append({"type": "resource_link", "uri": f"file:///{prefix}{d}",
                           "name": d, "description": "Directory"})
        for f in sorted(files):
            blocks.append({"type": "resource_link", "uri": f"file:///{prefix}{f}",
                           "name": f, "description": "File"})
        return blocks

    def invoke_code_interpreter(self, **kw):
        self.calls.append((kw["name"], kw))
        name, args = kw["name"], kw.get("arguments", {})
        if name == "executeCode":
            self.fs.update(self.produce)
            return {"stream": [_event(
                content=[{"type": "text", "text": "hello from code"}],
                structured={"stdout": "hello from code", "stderr": "", "exitCode": 0},
            )]}
        if name == "executeCommand":
            return {"stream": [_event(
                structured={"stdout": "cmd-out\r\n", "stderr": "warn\r\n", "exitCode": 3},
            )]}
        if name == "writeFiles":
            for f in args.get("content", []):
                self.fs[f["path"]] = f["text"] if "text" in f else f.get("blob")
            n = len(args.get("content", []))
            return {"stream": [_event(content=[{"type": "text", "text": f"Successfully wrote all {n} files"}])]}
        if name == "listFiles":
            return {"stream": [_event(content=self._children(args.get("directoryPath", ".")))]}
        if name == "readFiles":
            blocks = []
            for p in args.get("paths", []):
                if p not in self.fs:
                    return {"stream": [_event(
                        content=[{"type": "text", "text": f"Error executing tool read_files: File {p} not found"}],
                        is_error=True)]}
                v = self.fs[p]
                res = {"uri": f"file:///{p}"}
                if isinstance(v, (bytes, bytearray)):
                    res["blob"] = bytes(v)
                else:
                    res["text"] = v
                blocks.append({"type": "resource", "resource": res})
            return {"stream": [_event(content=blocks)]}
        return {"stream": [_event()]}


@pytest.fixture
def fake_runner():
    r = AgentCoreCodeInterpreterRunner({"region": "us-west-2"})
    fake = FakeClient()
    r._client = fake          # bypass boto3 entirely
    return r, fake


# --------------------------------------------------------------------------- #
# get_runner() dispatch + fallback
# --------------------------------------------------------------------------- #
def test_get_runner_returns_agentcore_when_configured(monkeypatch):
    monkeypatch.setattr(
        runner_mod, "get_runner", runner_mod.get_runner
    )  # keep ref; we patch config below
    from app import config

    monkeypatch.setattr(
        config, "get_sandbox_config",
        lambda: {"runner": "agentcore", "agentcore": {"region": "us-west-2"}},
    )
    runner_mod.reset_runner()
    try:
        r = runner_mod.get_runner()
        assert isinstance(r, AgentCoreCodeInterpreterRunner)
    finally:
        runner_mod.reset_runner()


def test_get_runner_fails_closed_on_construction_error(monkeypatch):
    from app import config

    monkeypatch.setattr(
        config, "get_sandbox_config",
        lambda: {"runner": "agentcore", "agentcore": {"region": "us-west-2"}},
    )

    def boom(_cfg):
        raise RuntimeError("no creds")

    monkeypatch.setattr(runner_mod, "AgentCoreCodeInterpreterRunner", boom)
    runner_mod.reset_runner()
    try:
        with pytest.raises(RuntimeError, match="no creds"):
            runner_mod.get_runner()
    finally:
        runner_mod.reset_runner()


# --------------------------------------------------------------------------- #
# run_code / run_command parse structuredContent (the resolved INFERRED bit)
# --------------------------------------------------------------------------- #
def test_run_code_parses_structured_stdout(fake_runner, tmp_path):
    r, fake = fake_runner
    res = r.run_code("print('hi')", "python", tmp_path)
    assert res.stdout.strip() == "hello from code"
    assert res.exit_code == 0
    # a session was opened and reused
    assert any(c[0] == "start" for c in fake.calls)


def test_run_code_node_maps_to_javascript(fake_runner, tmp_path):
    r, fake = fake_runner
    r.run_code("console.log(1)", "node", tmp_path)
    code_call = next(c for c in fake.calls if c[0] == "executeCode")
    assert code_call[1]["arguments"]["language"] == "javascript"


def test_run_command_surfaces_stderr_and_exit_code(fake_runner, tmp_path):
    r, _ = fake_runner
    res = r.run_command(["/bin/sh", "-c", "do-thing"], tmp_path)
    assert res.stdout.strip() == "cmd-out"
    assert res.stderr.strip() == "warn"
    assert res.exit_code == 3


def test_is_error_without_exit_code_becomes_nonzero(fake_runner, tmp_path, monkeypatch):
    r, fake = fake_runner

    def err_invoke(**kw):
        fake.calls.append((kw["name"], kw))
        if kw["name"] == "executeCode":
            return {"stream": [{"result": {"content": [], "isError": True}}]}
        return {"stream": [{"result": {"content": []}}]}

    fake.invoke_code_interpreter = err_invoke
    res = r.run_code("boom", "python", tmp_path)
    assert res.exit_code != 0


# --------------------------------------------------------------------------- #
# file sync (in + out) keeps the local workspace authoritative
# --------------------------------------------------------------------------- #
def test_sync_in_uploads_local_files(fake_runner, tmp_path):
    r, fake = fake_runner
    (tmp_path / "a.txt").write_text("local-content", encoding="utf-8")
    r.run_code("print(1)", "python", tmp_path)
    # writeFiles landed the local file into the session FS.
    assert fake.fs.get("a.txt") == "local-content"


def test_sync_in_uploads_binary_as_blob(fake_runner, tmp_path):
    r, fake = fake_runner
    payload = b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01"   # non-UTF-8 bytes -> blob path
    (tmp_path / "pic.bin").write_bytes(payload)
    r.run_code("print(1)", "python", tmp_path)
    assert fake.fs.get("pic.bin") == payload


def test_sync_in_preserves_crlf_text_byte_for_byte(fake_runner, tmp_path):
    r, fake = fake_runner
    # A valid-UTF-8 file with CRLF must round-trip unchanged (no newline translation).
    (tmp_path / "crlf.txt").write_bytes(b"line1\r\nline2\r\n")
    r.run_code("print(1)", "python", tmp_path)
    assert fake.fs.get("crlf.txt") == "line1\r\nline2\r\n"


def test_sync_out_writes_produced_files_back_locally(fake_runner, tmp_path):
    r, fake = fake_runner
    # The session produces new files (text + nested + binary) after it starts — these are
    # not in the baseline, so they should sync back into the local workspace.
    fake.produce = {
        "out.csv": "x,y\n1,2\n",
        "produced/nested.txt": "deep",
        "bin.dat": bytes(range(16)),
    }
    res = r.run_code("write files", "python", tmp_path)
    assert (tmp_path / "out.csv").read_text(encoding="utf-8") == "x,y\n1,2\n"
    assert (tmp_path / "produced" / "nested.txt").read_text(encoding="utf-8") == "deep"
    assert (tmp_path / "bin.dat").read_bytes() == bytes(range(16))
    # ...and they're detected as artifacts by the normal mtime diff.
    art_paths = {a["path"] for a in res.artifacts}
    assert {"out.csv", "produced/nested.txt", "bin.dat"} <= art_paths


def test_sync_out_skips_interpreter_system_files(fake_runner, tmp_path):
    r, fake = fake_runner
    r.run_code("noop", "python", tmp_path)
    # The pre-existing interpreter files must NOT be dragged into the local workspace.
    assert not (tmp_path / "node_modules").exists()
    assert not (tmp_path / "package.json").exists()
    assert not (tmp_path / "log").exists()


def test_sync_out_unchanged_input_not_reflagged_as_artifact(fake_runner, tmp_path):
    r, _ = fake_runner
    (tmp_path / "input.txt").write_text("unchanged", encoding="utf-8")
    res = r.run_code("noop", "python", tmp_path)
    # The input round-trips out unchanged; write_back must skip it so it isn't a false artifact.
    assert all(a["path"] != "input.txt" for a in res.artifacts)


def test_uri_to_rel_rejects_escapes(fake_runner):
    r, _ = fake_runner
    assert r._uri_to_rel("file:///hello.txt") == "hello.txt"
    assert r._uri_to_rel("file:///sub/dir/nested.txt") == "sub/dir/nested.txt"
    assert r._uri_to_rel("file:///./produced/out.csv") == "produced/out.csv"
    assert r._uri_to_rel("file:///../escape") is None
    assert r._uri_to_rel("") is None


def test_write_back_ignores_unsafe_uris(fake_runner, tmp_path):
    r, _ = fake_runner
    content = [
        {"type": "resource", "resource": {"uri": "file:///../escape", "text": "no"}},
        {"type": "resource", "resource": {"uri": "file:///ok.txt", "text": "fine"}},
    ]
    r._write_back(content, tmp_path)
    assert (tmp_path / "ok.txt").read_text(encoding="utf-8") == "fine"
    assert not (tmp_path.parent / "escape").exists()


# --------------------------------------------------------------------------- #
# AWS failure -> graceful local fallback
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("reason", ["missing credentials", "throttled", "network down", "stream failed"])
def test_run_never_falls_back_to_local_on_aws_error(tmp_path, reason):
    r = AgentCoreCodeInterpreterRunner({"region": "us-west-2"})

    class Boom:
        def start_code_interpreter_session(self, **kw):
            raise RuntimeError(reason)

    r._client = Boom()
    marker = tmp_path / "must-not-exist"
    res = r.run_code(f"open({str(marker)!r}, 'w').write('local')", "python", tmp_path)
    assert res.exit_code != 0
    assert "Remote sandbox execution failed" in res.stderr
    assert not marker.exists()


# --------------------------------------------------------------------------- #
# close_session: deterministic teardown on the remote runner; no-op elsewhere
# --------------------------------------------------------------------------- #
def test_close_session_stops_remote_session(fake_runner, tmp_path):
    r, fake = fake_runner
    r.run_code("print(1)", "python", tmp_path)        # opens a session
    sid = r._sessions[tmp_path.name]
    r.close_session(tmp_path)
    assert sid in fake.stopped
    assert tmp_path.name not in r._sessions
    # Idempotent: closing again does nothing.
    r.close_session(tmp_path)


def test_close_session_is_noop_for_local_runner(tmp_path):
    # The base no-op must not raise — existing runners inherit it unchanged.
    LocalSubprocessRunner().close_session(tmp_path)


def test_close_session_session_reused_across_runs(fake_runner, tmp_path):
    r, fake = fake_runner
    r.run_code("print(1)", "python", tmp_path)
    r.run_code("print(2)", "python", tmp_path)
    starts = [c for c in fake.calls if c[0] == "start"]
    assert len(starts) == 1     # one session reused, not re-created per call


# --------------------------------------------------------------------------- #
# config defaults
# --------------------------------------------------------------------------- #
def test_sandbox_config_has_agentcore_defaults():
    from app.config import get_sandbox_config

    cfg = get_sandbox_config()
    assert cfg["agentcore"]["identifier"] == "aws.codeinterpreter.v1"
    assert cfg["agentcore"]["session_timeout_seconds"] == 900
