"""Real Phlox/Uvicorn processes, isolated data, scripted models and real open SSE clients."""
import json
import os
from pathlib import Path
import signal
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX signal process tests; Windows launch command checked separately')

FIXTURE = '''
import os, time
from pathlib import Path
from app import main, shutdown
from app.config import DATA_DIR
from app.agent.registry import REGISTRY
from app.agent.tools.base import Tool, ToolResult
from app.providers.base import StreamDelta, ToolCall
from app.routers import chat
from app.server import run
mode = os.environ['FIXTURE_MODE']

class Probe(Tool):
    name = 'shutdown_probe'
    default_permission = 'ask'
    parameters = {'type': 'object', 'properties': {}}
    def run(self, ctx, **kw):
        path = DATA_DIR / 'action-count'
        path.write_text(str(int(path.read_text()) + 1) if path.exists() else '1')
        (DATA_DIR / 'entered').write_text('tool')
        while 'stall' in mode or not ctx.cancel_event.wait(.02):
            time.sleep(.02)
        return ToolResult('Tool stopped after saving its output.')

class Provider:
    model = 'shutdown-fixture'
    supports_tools = True
    context_window = 128000
    def stream(self, messages, tools, params):
        yield StreamDelta(type='usage', usage={'input': 10, 'output': 10, 'total': 20})
        if 'tool' in mode or mode == 'approval':
            yield StreamDelta(type='tool_calls', tool_calls=[ToolCall('action', 'shutdown_probe', {})])
        else:
            yield StreamDelta(type='text', text='Saved partial progress. ' * 50)
            (DATA_DIR / 'entered').write_text('provider')
            if mode != 'finish':
                while 'stall' in mode or not shutdown.stopping.wait(.02):
                    time.sleep(.02)
            yield StreamDelta(type='done', stop_reason='stop')

original = main._bootstrap
def bootstrap():
    original()
    REGISTRY.register(Probe())
main._bootstrap = bootstrap
chat.build_provider = lambda *args: Provider()
chat._build_fallback = lambda *args: None
if mode == 'cleanup':
    from app.mcp.manager import mcp_manager
    mcp_manager.close = lambda: time.sleep(120)
if mode == 'blocked_loop':
    @main.app.get('/fixture-block')
    async def block():
        (DATA_DIR / 'entered').write_text('event-loop')
        time.sleep(120)
    main.app.router.routes.insert(0, main.app.router.routes.pop())
if __name__ == '__main__':
    run('app.main:app', host='127.0.0.1', port=int(os.environ['FIXTURE_PORT']), log_level='warning')
'''


def wait_for(predicate, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = predicate()
            if value:
                return value
        except (OSError, ValueError, sqlite3.Error):
            pass
        time.sleep(.05)
    raise AssertionError('Timed out waiting for isolated fixture')


@pytest.fixture
def server(tmp_path):
    processes, clients = [], []
    data = tmp_path / 'data'
    cfg = tmp_path / 'config.yml'
    script = tmp_path / 'fixture.py'
    script.write_text(FIXTURE)
    config = {'auth': {'enabled': False}, 'runs': {'enabled': True}, 'default_profile': 'test',
              'profiles': {'test': {'type': 'openai', 'model': 'shutdown-fixture', 'endpoint': 'http://127.0.0.1:1/v1'}}}

    def launch(mode, durable=True):
        import yaml
        config['runs']['enabled'] = durable
        cfg.write_text(yaml.safe_dump(config))
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = dict(os.environ, PHLOX_DATA=str(data), PHLOX_CONFIG=str(cfg), PHLOX_ENV='production',
                   PHLOX_SHUTDOWN_SECONDS='3', FIXTURE_MODE=mode, FIXTURE_PORT=str(port),
                   PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        env.pop('DATABASE_URL', None)
        log = tmp_path / f'{len(processes)}.log'
        with log.open('w') as output:
            proc = subprocess.Popen([sys.executable, str(script)], env=env, stdout=output, stderr=output)
        processes.append(proc)
        base = f'http://127.0.0.1:{port}'
        def ready():
            if proc.poll() is not None:
                raise AssertionError(log.read_text())
            # base is the loopback HTTP server started by this fixture.
            return urllib.request.urlopen(base + '/api/health', timeout=.5).read()  # nosec B310
        wait_for(ready)
        return proc, base, log

    def request(base, path, body=None):
        req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json'})
        # Only fixture-generated loopback HTTP URLs are passed by these tests.
        response = urllib.request.urlopen(req, timeout=8)  # nosec B310
        clients.append(response)
        return response

    yield launch, request, data
    for client in clients:
        client.close()
    for proc in processes:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=8)


def rows(data, table):
    with sqlite3.connect(data / 'phlox.db') as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute('select * from ' + table)]


@pytest.mark.parametrize('durable', [False, True])
@pytest.mark.parametrize('mode', ['cooperate_provider', 'cooperate_tool', 'stall_provider', 'stall_tool'])
def test_shutdown_with_open_stream_and_restart(server, mode, durable):
    launch, request, data = server
    proc, base, log = launch(mode, durable)
    stream = request(base, '/api/chat', {'message': 'Run the shutdown fixture', 'auto_approve': True})
    # urllib keeps the SSE connection open while the process receives SIGTERM.
    assert stream.status == 200
    wait_for(lambda: (data / 'entered').exists())
    if durable:
        wait_for(lambda: rows(data, 'run_events'))
    began = time.monotonic()
    proc.send_signal(signal.SIGTERM)
    if 'stall' in mode:
        time.sleep(.7)
        from app.maintenance import maintenance_lock
        with pytest.raises(RuntimeError, match='using this data directory'):
            with maintenance_lock(data):
                pytest.fail('Maintenance lock released while a writer remained alive')
    proc.wait(timeout=7)
    assert time.monotonic() - began < 7, log.read_text()
    assert proc.returncode == 75 if 'stall' in mode else proc.returncode != 75
    saved_before = rows(data, 'messages')
    if 'stall' not in mode:
        assert any(json.loads(r['usage'] or '{}').get('outcome') == 'interrupted' for r in saved_before)
    second, base, _ = launch('finish', durable)
    if durable:
        run = rows(data, 'runs')[0]
        assert run['status'] == 'interrupted' and run['active_conversation_id']
        assert rows(data, 'run_events'), 'Retain recorded progress'
        if mode == 'stall_tool':
            assert rows(data, 'tool_executions')[0]['status'] == 'outcome_unknown'
    assert rows(data, 'messages') == saved_before
    if 'tool' in mode:
        assert (data / 'action-count').read_text() == '1', 'Restart must not replay tools'
    second.send_signal(signal.SIGTERM)
    second.wait(timeout=7)


def test_stalled_cleanup_keeps_lock_until_deadline(server):
    launch, _, data = server
    proc, _, log = launch('cleanup')
    proc.send_signal(signal.SIGTERM)
    time.sleep(.7)
    from app.maintenance import maintenance_lock
    with pytest.raises(RuntimeError):
        with maintenance_lock(data):
            pytest.fail('Early maintenance unlock')
    proc.wait(timeout=7)
    assert proc.returncode == 75 and 'shutdown_deadline_exceeded' in log.read_text()


def test_deadline_starts_at_signal_even_with_blocked_event_loop(server):
    launch, _, data = server
    proc, base, log = launch('blocked_loop')
    from concurrent.futures import ThreadPoolExecutor
    def blocked_request():
        try:
            # Fixed route on the loopback server started above.
            urllib.request.urlopen(base + '/fixture-block', timeout=7).read()  # nosec B310
        except OSError:
            pass
    with ThreadPoolExecutor() as pool:
        future = pool.submit(blocked_request)
        wait_for(lambda: (data / 'entered').exists())
        proc.send_signal(signal.SIGTERM)
        proc.wait(timeout=7)
        future.result(timeout=2)
    assert proc.returncode == 75 and 'shutdown_deadline_exceeded' in log.read_text()


@pytest.mark.parametrize('mode', ['stall_provider', 'stall_tool'])
def test_forced_kill_during_work_never_replays(server, mode):
    launch, request, data = server
    proc, base, _ = launch(mode)
    request(base, '/api/chat', {'message': 'Run fixture', 'auto_approve': True})
    wait_for(lambda: (data / 'entered').exists())
    proc.kill()
    proc.wait(timeout=5)
    second, _, _ = launch('finish')
    assert rows(data, 'runs')[0]['status'] == 'interrupted'
    if mode == 'stall_tool':
        assert rows(data, 'tool_executions')[0]['status'] == 'outcome_unknown'
        assert (data / 'action-count').read_text() == '1'
    second.send_signal(signal.SIGTERM)
    second.wait(timeout=7)


def test_completed_run_survives_restart(server):
    launch, request, data = server
    proc, base, _ = launch('finish')
    request(base, '/api/chat', {'message': 'Finish fixture'}).read()
    wait_for(lambda: rows(data, 'runs')[0]['status'] == 'completed')
    saved = rows(data, 'messages')
    proc.kill()
    proc.wait(timeout=5)
    second, _, _ = launch('finish')
    assert rows(data, 'runs')[0]['status'] == 'completed'
    assert rows(data, 'messages') == saved
    second.send_signal(signal.SIGTERM)
    second.wait(timeout=7)


@pytest.mark.skipif(not shutil.which('lsof'), reason='stop.sh port discovery requires lsof')
def test_stop_launcher_allows_server_deadline(server, tmp_path):
    launch, request, data = server
    proc, base, log = launch('stall_tool')
    request(base, '/api/chat', {'message': 'Run fixture', 'auto_approve': True})
    wait_for(lambda: (data / 'entered').exists())
    script = tmp_path / 'checkout' / 'scripts' / 'stop.sh'
    script.parent.mkdir(parents=True)
    shutil.copy(Path(__file__).resolve().parents[2] / 'scripts' / 'stop.sh', script)
    result = subprocess.run(['bash', str(script), '--port', base.rsplit(':', 1)[1]],
                            env=dict(os.environ, PHLOX_SHUTDOWN_SECONDS='3'),
                            capture_output=True, text=True, timeout=12)
    proc.wait(timeout=1)
    assert result.returncode == 0, result.stdout + result.stderr
    assert proc.returncode == 75, log.read_text()
    assert 'shutdown_deadline_exceeded' in log.read_text()


def test_forced_kill_preserves_pending_approval_and_does_not_execute(server):
    launch, request, data = server
    proc, base, _ = launch('approval')
    response = request(base, '/api/chat', {'message': 'Request fixture action', 'auto_approve': False})
    response.read()
    wait_for(lambda: rows(data, 'runs')[0]['status'] == 'awaiting_approval')
    proc.kill()
    proc.wait(timeout=5)
    second, _, _ = launch('finish')
    run = rows(data, 'runs')[0]
    assert run['status'] == 'awaiting_approval'
    assert rows(data, 'pending_approvals')[0]['status'] == 'pending'
    assert not (data / 'action-count').exists()
    second.send_signal(signal.SIGTERM)
    second.wait(timeout=7)
