"""Exercise the actual Uvicorn watcher against isolated runtime/source directories."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest


def test_dev_environment_keeps_configured_secret(monkeypatch):
    from app import config, dev

    monkeypatch.setenv('PHLOX_ENV', 'development')
    monkeypatch.delenv('PHLOX_JWT_SECRET', raising=False)
    monkeypatch.setattr(config, 'load_config', lambda: {'auth': {'jwt_secret': 'file-secret'}})
    dev.prepare_environment()
    assert os.environ['PHLOX_JWT_SECRET'] == 'file-secret'
    monkeypatch.setenv('PHLOX_JWT_SECRET', 'environment-secret')
    dev.prepare_environment()
    assert os.environ['PHLOX_JWT_SECRET'] == 'environment-secret'


@pytest.mark.parametrize('nested_data', [False, True])
def test_generated_python_does_not_reload_but_source_does(tmp_path, nested_data):
    source = tmp_path / 'application'
    source.mkdir()
    data = (source if nested_data else tmp_path) / 'runtime'
    data.mkdir()
    config = tmp_path / 'config.yml'
    config.write_text('auth:\n  enabled: true\n')
    probe = source / 'probe.py'
    probe.write_text('''import os, json, hashlib
async def app(scope, receive, send):
    if scope['type'] != 'http':
        return
    body = json.dumps({'pid': os.getpid(), 'secret': hashlib.sha256(os.environ['PHLOX_JWT_SECRET'].encode()).hexdigest()}).encode()
    await send({'type': 'http.response.start', 'status': 200})
    await send({'type': 'http.response.body', 'body': body})
''')
    launcher = tmp_path / 'serve.py'
    launcher.write_text(f'''from pathlib import Path
from app.dev import prepare_environment, server_options
import uvicorn
if __name__ == '__main__':
    prepare_environment()
    uvicorn.run('probe:app', lifespan='off', **server_options('127.0.0.1', int(__import__('sys').argv[1]), Path({str(source)!r}), Path({str(data)!r})))
''')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, PHLOX_DATA=str(data), PHLOX_CONFIG=str(config),
               PYTHONPATH=os.pathsep.join([str(Path(__file__).resolve().parents[1]), str(source)]))
    env.pop('PHLOX_JWT_SECRET', None)
    # Ignore developer-specified polling/exclude settings so CI tests the shipped options.
    for key in list(env):
        if key.startswith(('WATCHFILES_', 'UVICORN_')):
            env.pop(key)
    log = tmp_path / 'server.log'
    with log.open('w') as output:
        proc = subprocess.Popen([sys.executable, str(launcher), str(port)], env=env,
                                cwd=tmp_path, stdout=output, stderr=output,
                                start_new_session=os.name != 'nt')
        def read():
            with urllib.request.urlopen(f'http://127.0.0.1:{port}', timeout=.5) as response:
                return json.load(response)

        def wait_for(predicate):
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    result = read()
                    if predicate(result):
                        return result
                except (OSError, urllib.error.URLError):
                    pass
                if proc.poll() is not None:
                    break
                time.sleep(.1)
            pytest.fail(log.read_text())

        try:
            first = wait_for(lambda _: True)
            time.sleep(.5)  # Allow the watch thread to establish its initial snapshot.
            workspace = data / 'workspaces' / 'synthetic' / '.phlox'
            workspace.mkdir(parents=True)
            for n in range(3):
                (workspace / 'run.py').write_text(f'print({n})\n')
                (workspace.parent / 'build_report.py').write_text(f'# iteration {n}\n')
                time.sleep(.3)
            time.sleep(1)
            assert read() == first, log.read_text()
            probe.write_text(probe.read_text() + '\n# real source edit\n')
            second = wait_for(lambda result: result['pid'] != first['pid'])
            assert second['secret'] == first['secret']
            time.sleep(.5)
            assert read() == second
        finally:
            if os.name == 'nt':
                # Uvicorn owns a child server process; terminate that test tree too.
                subprocess.run(['taskkill', '/PID', str(proc.pid), '/T', '/F'],
                               capture_output=True, timeout=10, check=False)
            else:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == 'nt':
                    proc.kill()
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)


def test_both_launchers_use_shared_dev_entrypoint():
    root = Path(__file__).resolve().parents[2]
    assert 'uv run -m app.dev' in (root / 'scripts/start.sh').read_text()
    assert "'run', '-m', 'app.dev'" in (root / 'scripts/start.ps1').read_text()
