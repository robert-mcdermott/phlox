"""Transient recovery, exact operation replay, cooldowns and revocation across adapters."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import json
import threading
import time
from types import SimpleNamespace

import pytest

from app import public_api, public_api_adapters, public_api_transport as transport, sources, web_fetch
from app.agent.tools.api_dataset import ExportApiDataset
from app.agent.tools.public_api import QueryPublicApi
from app.agent.tools.web import ReadWebSource
from app.models import Conversation
from app.workspace.manager import resolve_in_workspace
from test_public_api import ARGS as NIH, rows, site as nih_server
from test_pubmed import ARGS as PUBMED, pubmed_site as pubmed_server
from test_pubmed_details import detail_site as detail_server
from test_clinical_trials import ARGS as TRIALS, trial_site as trial_server
from test_web_formats import ctx as format_context

ctx = format_context
site = nih_server
pubmed_site = pubmed_server
detail_site = detail_server
trial_site = trial_server


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(transport, 'BACKOFF_SECONDS', 0.01)
    monkeypatch.setattr(transport, 'JITTER_SECONDS', 0)


def manifest(ctx, **arguments):
    result = ExportApiDataset().run(ctx, **arguments)
    assert not result.is_error, result.content
    artifact = next(a for a in result.artifacts if a['name'] == 'manifest.json')
    return json.loads(resolve_in_workspace(ctx.conversation_id, artifact['path']).read_text())


@pytest.mark.parametrize('fixture,arguments', [('site', NIH), ('pubmed_site', PUBMED), ('trial_site', TRIALS)])
def test_search_recovers_one_capture_and_export_provenance(ctx, request, fixture, arguments):
    server = request.getfixturevalue(fixture)
    server.statuses = {'/esearch.fcgi': [503, 200]} if fixture == 'pubmed_site' else [503, 200]
    result = QueryPublicApi().run(ctx, **arguments)
    assert not result.is_error, result.content
    assert '1 automatic retry/retries' in result.content
    assert len(rows(ctx)) == 1
    retrieval = rows(ctx)[0].location['retrieval']
    assert [a['http_status'] for a in retrieval[0]['attempts']] == [503, 200]
    count = 3 if fixture == 'pubmed_site' else 2
    assert len(server.requests) == count
    assert server.requests[0] == server.requests[1]
    assert '1 automatic retry/retries' in ReadWebSource().run(ctx, label='S1').content
    assert manifest(ctx, labels=['S1'])['sources'][0]['retrieval'] == retrieval
    assert len(server.requests) == count


def test_summary_retry_does_not_repeat_successful_search(ctx, pubmed_site):
    pubmed_site.statuses = {'/esummary.fcgi': [429, 200]}
    result = QueryPublicApi().run(ctx, **PUBMED)
    assert not result.is_error, result.content
    assert [p for p, _ in pubmed_site.requests] == ['/esearch.fcgi', '/esummary.fcgi', '/esummary.fcgi']
    assert pubmed_site.requests[1] == pubmed_site.requests[2]
    assert len(rows(ctx)) == 1
    assert [len(op['attempts']) for op in rows(ctx)[0].location['retrieval']] == [1, 2]


@pytest.mark.parametrize('kind', ['pubmed', 'clinical_trials'])
def test_detail_recovers_and_retains_export_lineage(ctx, request, kind):
    if kind == 'pubmed':
        request.getfixturevalue('pubmed_site')
        server = request.getfixturevalue('detail_site')
        arguments, identifier = PUBMED, '103'
    else:
        server = request.getfixturevalue('trial_site')
        arguments, identifier = TRIALS, 'NCT00000001'
    assert not QueryPublicApi().run(ctx, **arguments).is_error
    before = len(server.requests)
    server.statuses = [502, 200]
    result = QueryPublicApi().run(ctx, record_from='S1', record_id=identifier)
    assert not result.is_error, result.content
    assert len(server.requests) == before + 2
    assert server.requests[-1] == server.requests[-2]
    assert len(rows(ctx)) == 2
    retrieval = rows(ctx)[1].location['retrieval']
    assert retrieval[0]['operation'] == 'detail'
    assert len(retrieval[0]['attempts']) == 2
    assert manifest(ctx, labels=['S1'], detail_labels=['S2'])['record_detail_sources'][0]['retrieval'] == retrieval


def test_cursor_retry_does_not_skip_page(ctx, trial_site):
    assert not QueryPublicApi().run(ctx, **TRIALS).is_error
    trial_site.statuses = [503, 200]
    result = QueryPublicApi().run(ctx, continue_from='S1')
    assert not result.is_error, result.content
    assert [params.get('pageToken') for _, params in trial_site.requests] == [None, 'cursor:2', 'cursor:2']
    assert len(rows(ctx)) == 2


@pytest.mark.parametrize('status', sorted(transport.RETRY_HTTP))
def test_transient_exhaustion_has_no_evidence(ctx, site, status):
    site.status = status
    result = QueryPublicApi().run(ctx, **NIH)
    assert result.is_error and '3 HTTP attempts' in result.content
    assert len(site.requests) == 3
    assert not rows(ctx)


@pytest.mark.parametrize('status', [301, 400, 401, 403, 404, 422, 501, 505])
def test_permanent_http_errors_are_not_retried(ctx, site, status):
    site.status = status
    assert QueryPublicApi().run(ctx, **NIH).is_error
    assert len(site.requests) == 1
    assert not rows(ctx)


def test_invalid_success_payload_is_not_retried(ctx, site):
    site.raw = b'{malformed'
    assert QueryPublicApi().run(ctx, **NIH).is_error
    assert len(site.requests) == 1
    assert not rows(ctx)


def test_long_retry_after_defers_and_cannot_be_bypassed(ctx, site):
    site.status, site.retry_after = 429, '3600'
    started = time.monotonic()
    first = QueryPublicApi().run(ctx, **NIH)
    second = QueryPublicApi().run(ctx, **NIH)
    assert first.is_error and second.is_error
    assert 'remaining time' in first.content
    assert time.monotonic() - started < 2
    assert len(site.requests) == 1
    assert not rows(ctx)


def test_retry_after_is_minimum_wait(ctx, site):
    site.statuses, site.retry_after = [429, 200], '1'
    started = time.monotonic()
    result = QueryPublicApi().run(ctx, **NIH)
    assert not result.is_error, result.content
    assert time.monotonic() - started >= 1
    assert rows(ctx)[0].location['retrieval'][0]['attempts'][0]['retry_delay_seconds'] >= 1


@pytest.mark.parametrize('value,expected', [('0', 0), ('15', 15), ('-1', None), ('0.2', None),
    ('invalid private header', None), ('', None), ('9' * 129, None), (None, None)])
def test_retry_after_numeric_and_invalid(value, expected):
    assert transport.retry_after(value) == expected


def test_retry_after_dates_and_large_valid_delay(monkeypatch):
    now = datetime(2026, 9, 20, tzinfo=timezone.utc)
    monkeypatch.setattr(transport.time, 'time', lambda: now.timestamp())
    assert transport.retry_after(format_datetime(now + timedelta(seconds=7), usegmt=True)) == 7
    assert transport.retry_after(format_datetime(now - timedelta(seconds=7), usegmt=True)) == 0
    assert transport.retry_after('9' * 100) > 30


def test_stop_interrupts_backoff(ctx, site):
    site.statuses, site.retry_after = [429, 200], '3'
    ctx.cancel_event = threading.Event()
    timer = threading.Timer(0.2, ctx.cancel_event.set)
    started = time.monotonic()
    timer.start()
    try:
        result = QueryPublicApi().run(ctx, **NIH)
    finally:
        timer.cancel()
        timer.join()
    assert result.is_error and 'stop' in result.content.lower()
    assert time.monotonic() - started < 2
    assert len(site.requests) == 1
    assert not rows(ctx)


@pytest.mark.parametrize('change', ['revoked', 'scope', 'capacity', 'owner'])
def test_authorization_rechecked_after_wait(ctx, trial_site, monkeypatch, change):
    assert not QueryPublicApi().run(ctx, **TRIALS).is_error
    trial_site.statuses = [503, 200]
    original_wait = transport.wait

    def changed(seconds, deadline):
        original_wait(seconds, deadline)
        if change == 'revoked':
            source = rows(ctx)[0]
            sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), source.id)
        elif change == 'scope':
            ctx.research = SimpleNamespace(state={'options': {'scope': 'documents'}}, url_allowed=lambda url: True)
        elif change == 'owner':
            ctx.user_id = 'another-user'
        else:
            monkeypatch.setattr(sources, 'remaining_capacity', lambda *args: 0)
    monkeypatch.setattr(transport, 'wait', changed)
    result = QueryPublicApi().run(ctx, record_from='S1', record_id='NCT00000001')
    assert result.is_error, result.content
    assert len(trial_site.requests) == 2
    assert len(rows(ctx)) == 1


@pytest.mark.parametrize('error', ['connection_error', 'incomplete', 'tls_error', 'blocked', 'dns_error', 'unsupported_type'])
def test_transport_error_classification(monkeypatch, error):
    calls = []
    monkeypatch.setattr(public_api, 'pace', lambda *args: None)

    def fetch(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise web_fetch.FetchError(error, 'Fixture failure')
        return b'{}', 200
    monkeypatch.setattr(web_fetch, 'read_api_query', fetch)
    with web_fetch.Deadline() as deadline:
        if error in transport.RETRY_ERRORS:
            assert transport.read('nih_projects', 'search', 'https://example.invalid', deadline, body=b'{}') == (b'{}', 200)
            assert len(calls) == 2
        else:
            with pytest.raises(web_fetch.FetchError):
                transport.read('nih_projects', 'search', 'https://example.invalid', deadline, body=b'{}')
            assert len(calls) == 1


def test_unreviewed_adapter_is_not_retried(ctx, site, monkeypatch):
    adapter = public_api_adapters.get('nih_projects')
    monkeypatch.setitem(public_api_adapters.ADAPTERS, 'nih_projects', replace(adapter, retry_safe=False))
    site.status = 503
    assert QueryPublicApi().run(ctx, **NIH).is_error
    assert len(site.requests) == 1


def test_backoff_shares_existing_deadline(ctx, site, monkeypatch):
    site.status = 503
    monkeypatch.setattr(transport, 'BACKOFF_SECONDS', 0.2)
    monkeypatch.setattr(web_fetch, 'MAX_SECONDS', 0.5)
    started = time.monotonic()
    result = QueryPublicApi().run(ctx, **NIH)
    assert result.is_error and 'remaining time' in result.content
    assert time.monotonic() - started < 2
    assert len(site.requests) == 2
    assert not rows(ctx)


def test_address_policy_rechecked_on_retry(ctx, site, monkeypatch):
    original = web_fetch.checked_addresses
    checks = []
    site.statuses = [503, 200]

    def addresses(url, deadline):
        checks.append(url)
        if len(checks) > 1:
            raise web_fetch.FetchError('blocked', 'Address no longer allowed.')
        return original(url, deadline)
    monkeypatch.setattr(web_fetch, 'checked_addresses', addresses)
    result = QueryPublicApi().run(ctx, **NIH)
    assert result.is_error and 'Address no longer allowed' in result.content
    assert len(site.requests) == 1 and len(checks) == 2
    assert not rows(ctx)


def test_continuation_rechecks_revocation_after_wait(ctx, trial_site, monkeypatch):
    assert not QueryPublicApi().run(ctx, **TRIALS).is_error
    trial_site.statuses = [503, 200]

    def revoke(*args):
        sources.forget_web(ctx.db, ctx.db.get(Conversation, ctx.conversation_id), rows(ctx)[0].id)
    monkeypatch.setattr(transport, 'wait', revoke)
    result = QueryPublicApi().run(ctx, continue_from='S1')
    assert result.is_error and 'unavailable' in result.content
    assert len(trial_site.requests) == 2 and len(rows(ctx)) == 1


def test_cooldown_is_per_adapter(ctx, site, pubmed_site):
    site.status, site.retry_after = 429, '3600'
    assert QueryPublicApi().run(ctx, **NIH).is_error
    result = QueryPublicApi().run(ctx, **PUBMED)
    assert not result.is_error, result.content
    assert len(site.requests) == 1 and len(pubmed_site.requests) == 2


@pytest.mark.parametrize('failure', ['tls', 'peer'])
def test_connection_security_errors_are_not_transient(monkeypatch, failure):
    calls = []
    class Socket:
        def settimeout(self, value):
            pass
        def connect(self, address):
            calls.append(address)
        def getpeername(self):
            return ('127.0.0.1' if failure == 'peer' else '93.184.216.34', 443)
        def close(self):
            pass
    def wrap(*args, **kwargs):
        raise web_fetch.ssl.SSLCertVerificationError('Fixture certificate failure')
    monkeypatch.setattr(web_fetch.socket, 'socket', lambda *args: Socket())
    monkeypatch.setattr(web_fetch.ssl, 'create_default_context', lambda: SimpleNamespace(
        wrap_socket=wrap, set_alpn_protocols=lambda value: None, post_handshake_auth=None))
    with pytest.raises(web_fetch.FetchError) as error:
        web_fetch.connection('https://example.com', [(2, 1, 6, '', ('93.184.216.34', 443))] * 2, web_fetch.Deadline())
    assert error.value.status == ('tls_error' if failure == 'tls' else 'blocked')
    assert len(calls) == 1


def test_attempt_history_does_not_duplicate_identical_evidence(ctx, site):
    site.statuses = [503, 200]
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    original = rows(ctx)[0]
    retrieval = original.location['retrieval']
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    assert len(rows(ctx)) == 1 and len(site.requests) == 3
    assert rows(ctx)[0].location['retrieval'] == retrieval
    # Simulate an older capture without the optional metadata: same evidence identity.
    original.location = {k: v for k, v in original.location.items() if k != 'retrieval'}
    ctx.db.commit()
    assert not QueryPublicApi().run(ctx, **NIH).is_error
    assert len(rows(ctx)) == 1 and len(site.requests) == 4
    assert manifest(ctx, labels=['S1'])['sources'][0]['retrieval'] == []
