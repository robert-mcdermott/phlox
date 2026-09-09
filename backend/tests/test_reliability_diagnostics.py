import logging
import time

import jwt


def test_auth_diagnostics_distinguish_expiry_and_signature_without_token_content(client, monkeypatch, caplog):
    from app.auth import deps, security

    secret = 'diagnostic-fixture-secret-with-32-bytes'
    monkeypatch.setattr(deps, 'get_auth_config', lambda: {'enabled': True})
    monkeypatch.setattr(security, 'get_auth_config', lambda: {'jwt_secret': secret})
    expired = jwt.encode({'sub': 'synthetic', 'exp': int(time.time()) - 10}, secret, algorithm='HS256')
    wrong = jwt.encode({'sub': 'synthetic', 'exp': int(time.time()) + 300}, 'other-diagnostic-fixture-secret-32-bytes', algorithm='HS256')
    with caplog.at_level(logging.INFO, logger='phlox.request'):
        for token, category in [(expired, 'expired'), (wrong, 'invalid_signature'), ('invalid', 'invalid_token')]:
            caplog.clear()
            response = client.get('/api/auth/me', headers={'Authorization': f'Bearer {token}'})
            assert response.status_code == 401
            assert response.json()['detail'] == 'Invalid or expired token'
            assert f'auth={category}' in caplog.text
            assert f'Bearer {token}' not in caplog.text
            if token != 'invalid':
                assert token not in caplog.text
            assert secret not in caplog.text


def test_exporter_failures_are_coalesced_and_redacted(monkeypatch):
    from app.observability import TelemetryFailureFilter

    clock = [100.0]
    monkeypatch.setattr('app.observability.time.monotonic', lambda: clock[0])
    notice = TelemetryFailureFilter(interval=60)

    def record():
        return logging.LogRecord('exporter', logging.ERROR, __file__, 1,
                                 'Failed https://secret@example.invalid %s', ('private body',), None)

    first = record()
    assert notice.filter(first)
    assert 'secret' not in first.getMessage()
    assert 'private body' not in first.getMessage()
    for _ in range(10):
        assert not notice.filter(record())
    clock[0] += 61
    later = record()
    assert notice.filter(later)
    assert '10 similar messages suppressed' in later.getMessage()


def test_lifecycle_has_time_process_and_boot_identity(caplog):
    from app.observability import lifecycle

    with caplog.at_level(logging.INFO, logger='phlox.lifecycle'):
        lifecycle('fixture_event', status='interrupted')
    for field in ['at=', '+00:00', 'pid=', 'boot=', 'event=fixture_event', 'status=interrupted']:
        assert field in caplog.text
