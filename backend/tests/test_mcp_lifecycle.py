"""Offline lifecycle/cancellation contracts, plus one real local stdio transport."""
import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent.registry import REGISTRY
from app.mcp.manager import McpManager


class FakeSession:
    def __init__(self, *, failure=None, hanging_init=False):
        self.failure = failure
        self.hanging_init = hanging_init
        self.started = threading.Event()
        self.cancelled = threading.Event()
        self.closed = threading.Event()
        self.enter_task = None
        self.exit_task = None

    async def initialize(self):
        if self.hanging_init:
            await asyncio.Future()
        if self.failure == "initialize":
            raise RuntimeError("initialization failed")

    async def list_tools(self):
        if self.failure == "list":
            raise RuntimeError("discovery failed")
        return SimpleNamespace(tools=[SimpleNamespace(name="echo", description="echo", inputSchema={})])

    async def call_tool(self, tool, arguments):
        self.started.set()
        if arguments.get("hang"):
            try:
                await asyncio.Future()
            finally:
                self.cancelled.set()
        return SimpleNamespace(content=[SimpleNamespace(text=arguments.get("text", "ok"))],
                               isError=False)


@pytest.fixture
def manager(monkeypatch):
    instance = McpManager(connect_timeout=0.15, call_timeout=0.2, cleanup_timeout=0.5)
    sessions = []

    @asynccontextmanager
    async def open_session(server):
        session = FakeSession(failure=server.get("failure"), hanging_init=server.get("hanging_init"))
        sessions.append(session)
        session.enter_task = asyncio.current_task()
        try:
            yield session
        finally:
            session.exit_task = asyncio.current_task()
            session.closed.set()

    monkeypatch.setattr(instance, "_open_session", open_session)
    yield instance, sessions
    thread = instance._thread
    instance.close()
    assert thread is None or not thread.is_alive()
    assert not instance._connections
    assert all(s.closed.is_set() and s.enter_task is s.exit_task for s in sessions)


def test_reconnect_replaces_session_and_tools_after_cleanup(manager):
    m, sessions = manager
    for i in range(3):
        result = m.connect({"name": "lifecycle-reconnect"})
        assert result["connected"]
        assert m.connected_servers() == ["lifecycle-reconnect"]
        assert len(result["tools"]) == 1
        assert all(s.closed.is_set() for s in sessions[:i])
        response = m.call_tool("lifecycle-reconnect", "echo", {"text": str(i)})
        assert not response.is_error and response.content == str(i)
    m.disconnect("lifecycle-reconnect")
    m.disconnect("lifecycle-reconnect")  # idempotent
    assert m.connected_servers() == []
    assert REGISTRY.get("mcp__lifecycle-reconnect__echo") is None


@pytest.mark.parametrize("failure", ["initialize", "list", "timeout"])
def test_failed_connection_cleans_up_and_can_retry(manager, failure):
    m, sessions = manager
    result = m.connect({"name": "lifecycle-failure", "failure": failure,
                        "hanging_init": failure == "timeout"})
    assert not result["connected"]
    assert result["error"]
    assert sessions[0].closed.is_set()
    assert m.connected_servers() == []
    assert not m._connections
    assert REGISTRY.get("mcp__lifecycle-failure__echo") is None
    assert m.connect({"name": "lifecycle-failure"})["connected"]


def test_timeout_cancels_local_call_without_claiming_remote_rollback(manager):
    m, sessions = manager
    m.connect({"name": "lifecycle-timeout"})
    result = m.call_tool("lifecycle-timeout", "echo", {"hang": True})
    assert result.is_error and "timed out" in result.content
    assert "unknown" in result.content
    assert sessions[0].cancelled.wait(1)
    assert not m.call_tool("lifecycle-timeout", "echo", {}).is_error


def test_completion_racing_poll_timeout_returns_the_completed_result(manager, monkeypatch):
    m, _ = manager

    class CompletedDuringTimeout:
        reads = 0

        def result(self, timeout=None):
            self.reads += 1
            if self.reads == 1:
                raise TimeoutError()
            return SimpleNamespace(content=[SimpleNamespace(text="completed")], isError=False)

        def done(self):
            return True

        def cancel(self):
            pytest.fail("a completed action must not be classified as cancelled")

    def submit(coro):
        coro.close()
        return CompletedDuringTimeout()

    monkeypatch.setattr(m, "_submit", submit)
    result = m.call_tool("racing", "echo", {})
    assert not result.is_error and result.content == "completed"


@pytest.mark.parametrize("stop_kind", ["stop", "disconnect", "close"])
def test_inflight_call_cancels_on_stop_or_connection_shutdown(manager, stop_kind):
    m, sessions = manager
    m.call_timeout = 10
    m.connect({"name": "lifecycle-cancel"})
    cancel = threading.Event()
    results = []
    worker = threading.Thread(target=lambda: results.append(
        m.call_tool("lifecycle-cancel", "echo", {"hang": True}, cancel_event=cancel)
    ), daemon=True)
    worker.start()
    assert sessions[0].started.wait(1)
    if stop_kind == "stop":
        cancel.set()
    elif stop_kind == "disconnect":
        m.disconnect("lifecycle-cancel")
    else:
        m.close()
    worker.join(2)
    assert not worker.is_alive()
    assert results[0].is_error and "unknown" in results[0].content
    assert sessions[0].cancelled.wait(1)


def test_proxy_forwards_preexisting_cancellation_without_dispatch(manager):
    m, sessions = manager
    m.connect({"name": "lifecycle-proxy"})
    cancel = threading.Event()
    cancel.set()
    proxy = REGISTRY.get("mcp__lifecycle-proxy__echo")
    result = proxy.run(SimpleNamespace(cancel_event=cancel), text="never sent")
    assert result.is_error and "before dispatch" in result.content
    assert not sessions[0].started.is_set()


def test_close_can_be_followed_by_new_lifespan(manager):
    m, _ = manager
    m.connect({"name": "lifecycle-restart"})
    old_thread = m._thread
    m.close()
    assert not old_thread.is_alive()
    assert m.connected_servers() == []
    assert m.connect({"name": "lifecycle-restart"})["connected"]
    assert m._thread is not old_thread


@pytest.mark.parametrize("failure_stage", [None, "bootstrap", "body"])
def test_app_lifespan_drains_mcp_even_when_startup_or_body_fails(manager, monkeypatch, failure_stage):
    from app import main

    m, sessions = manager
    threads = []

    def bootstrap():
        assert m.connect({"name": "lifecycle-app"})["connected"]
        threads.append(m._thread)
        if failure_stage == "bootstrap":
            raise RuntimeError("fixture failure")

    monkeypatch.setattr(main, "_bootstrap", bootstrap)
    monkeypatch.setattr("app.mcp.manager.mcp_manager", m)

    async def exercise():
        async with main.lifespan(main.app):
            if failure_stage == "body":
                raise RuntimeError("fixture failure")

    if failure_stage:
        with pytest.raises(RuntimeError, match="fixture failure"):
            asyncio.run(exercise())
    else:
        asyncio.run(exercise())
    assert sessions[0].closed.is_set() and not threads[0].is_alive()


def test_disconnecting_one_server_does_not_unregister_a_longer_name(manager):
    m, _ = manager
    m.connect({"name": "lifecycle-prefix"})
    m.connect({"name": "lifecycle-prefix__other"})
    m.disconnect("lifecycle-prefix")
    assert REGISTRY.get("mcp__lifecycle-prefix__other__echo") is not None
    assert m.call_tool("lifecycle-prefix__other", "echo", {}).content == "ok"


def test_slow_cleanup_blocks_replacement_without_interrupting_teardown(manager, monkeypatch):
    m, sessions = manager
    m.cleanup_timeout = 0.03
    release = asyncio.Event()
    original = m._open_session

    @asynccontextmanager
    async def slow_close(server):
        async with original(server) as session:
            try:
                yield session
            finally:
                await release.wait()

    monkeypatch.setattr(m, "_open_session", slow_close)
    assert m.connect({"name": "lifecycle-slow-close"})["connected"]
    try:
        with pytest.raises(RuntimeError, match="still shutting down"):
            m.disconnect("lifecycle-slow-close")
        assert m.connected_servers() == []
        assert REGISTRY.get("mcp__lifecycle-slow-close__echo") is None
        result = m.connect({"name": "lifecycle-slow-close"})
        assert not result["connected"] and "shutting down" in result["error"]
        assert len(sessions) == 1 and not sessions[0].closed.is_set()
    finally:
        m._loop.call_soon_threadsafe(release.set)
    assert sessions[0].closed.wait(1)
    assert m.connect({"name": "lifecycle-slow-close"})["connected"]


def test_real_stdio_transport_reconnect_and_close(tmp_path):
    script = tmp_path / "mcp_fixture.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('lifecycle-fixture')\n"
        "@server.tool()\n"
        "def echo(text: str) -> str:\n"
        "    return text\n"
        "server.run(transport='stdio')\n"
    )
    m = McpManager(connect_timeout=10, call_timeout=3, cleanup_timeout=3)
    config = {
        "name": "lifecycle-stdio", "command": "uv",
        "args": ["run", "--offline", "--no-sync", "--directory",
                 str(Path(__file__).resolve().parents[1]), str(script)],
        "env": {"UV_CACHE_DIR": str(tmp_path / "uv-cache")},
    }
    try:
        for _ in range(2):
            result = m.connect(config)
            assert result["connected"], result
            response = m.call_tool(config["name"], "echo", {"text": "real transport"})
            assert not response.is_error and "real transport" in response.content
        thread = m._thread
    finally:
        m.close()
    assert not thread.is_alive()
    assert REGISTRY.get("mcp__lifecycle-stdio__echo") is None
