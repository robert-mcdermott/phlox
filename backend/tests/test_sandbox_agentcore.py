"""Tests for the AgentCore remote sandbox runner + the additive close_session hook.

No live AWS is needed: the boto3 client is replaced with a fake whose responses mirror
the ``bedrock-agentcore`` event-stream shapes (``result.structuredContent`` +
``result.content[]``). These lock in the runner's behavior — dispatch, fallback, output
parsing, file sync, and deterministic session teardown — independent of the real service.
"""
import pytest

from app.sandbox import runner as runner_mod
from app.sandbox.runner import (
    AgentCoreCodeInterpreterRunner,
    LocalSubprocessRunner,
)


# --------------------------------------------------------------------------- #
# A fake bedrock-agentcore client recording calls and returning modeled shapes
# --------------------------------------------------------------------------- #
def _event(content=None, structured=None, is_error=False):
    result = {"content": content or [], "isError": is_error}
    if structured is not None:
        result["structuredContent"] = structured
    return {"result": result}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.session_counter = 0
        self.stopped = []
        self.written = []          # captured writeFiles content
        self.files = {}            # path -> text, returned by listFiles/readFiles

    def start_code_interpreter_session(self, **kw):
        self.calls.append(("start", kw))
        self.session_counter += 1
        return {"sessionId": f"sess-{self.session_counter}"}

    def stop_code_interpreter_session(self, **kw):
        self.calls.append(("stop", kw))
        self.stopped.append(kw["sessionId"])
        return {}

    def invoke_code_interpreter(self, **kw):
        self.calls.append((kw["name"], kw))
        name, args = kw["name"], kw.get("arguments", {})
        if name == "executeCode":
            return {"stream": [_event(
                content=[{"type": "text", "text": "hello from code"}],
                structured={"stdout": "hello from code\n", "stderr": "", "exitCode": 0},
            )]}
        if name == "executeCommand":
            return {"stream": [_event(
                structured={"stdout": "cmd-out\n", "stderr": "warn\n", "exitCode": 3},
            )]}
        if name == "writeFiles":
            self.written = args.get("content", [])
            return {"stream": [_event(content=[{"type": "text", "text": "ok"}])]}
        if name == "listFiles":
            blocks = [{"type": "resource", "uri": p} for p in self.files]
            return {"stream": [_event(content=blocks)]}
        if name == "readFiles":
            blocks = [
                {"type": "resource", "resource": {"type": "text", "uri": p, "text": t}}
                for p, t in self.files.items()
                if p in args.get("paths", [])
            ]
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


def test_get_runner_falls_back_to_local_on_construction_error(monkeypatch):
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
        assert isinstance(runner_mod.get_runner(), LocalSubprocessRunner)
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
    paths = {f["path"] for f in fake.written}
    assert "a.txt" in paths
    assert any(f.get("text") == "local-content" for f in fake.written)


def test_sync_out_writes_produced_files_back_locally(fake_runner, tmp_path):
    r, fake = fake_runner
    # The session "produces" a new file that isn't on local disk yet.
    fake.files = {"out.csv": "x,y\n1,2\n"}
    res = r.run_code("write csv", "python", tmp_path)
    written_back = tmp_path / "out.csv"
    assert written_back.exists()
    assert written_back.read_text(encoding="utf-8") == "x,y\n1,2\n"
    # ...and it is detected as an artifact by the normal mtime diff.
    assert any(a["path"] == "out.csv" for a in res.artifacts)


def test_sync_out_ignores_unsafe_paths(fake_runner, tmp_path):
    r, fake = fake_runner
    fake.files = {"/etc/passwd": "root:x:0:0", "../escape": "no", "ok.txt": "fine"}
    r.run_code("x", "python", tmp_path)
    assert (tmp_path / "ok.txt").exists()
    assert not (tmp_path / "etc" / "passwd").exists()
    assert not (tmp_path.parent / "escape").exists()


# --------------------------------------------------------------------------- #
# AWS failure -> graceful local fallback
# --------------------------------------------------------------------------- #
def test_run_falls_back_to_local_on_aws_error(tmp_path):
    r = AgentCoreCodeInterpreterRunner({"region": "us-west-2"})

    class Boom:
        def start_code_interpreter_session(self, **kw):
            raise RuntimeError("network down")

    r._client = Boom()
    res = r.run_code("print('local fallback works')", "python", tmp_path)
    # The local runner actually executed the code.
    assert res.exit_code == 0
    assert "local fallback works" in res.stdout


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
