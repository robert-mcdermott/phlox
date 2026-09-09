"""Admin-selected search with bounded transport, pacing and one DuckDuckGo fallback.

No shared query cache: private research queries/results never cross user boundaries.
Only provider health/timing is process-global (the supported deployment is one process).
"""
from __future__ import annotations

import json
import logging
import threading
import time
from urllib.parse import urlencode, urlsplit

from app import web_fetch

_lock = threading.Lock()
_next = 0.0
_failed_until = {}
logger = logging.getLogger('phlox.search')
RESPONSE_TIMEOUT_SECONDS = 20


class SearchError(ValueError):
    def __init__(self, message, *, http_status=None):
        super().__init__(message)
        self.http_status = http_status


def endpoint_url(value):
    if urlsplit(value.strip()).fragment:
        raise ValueError('Use an instance URL without a fragment.')
    url = web_fetch.normalize_url(value.strip())
    parts = urlsplit(url)
    if parts.scheme != 'https' or parts.query or parts.fragment:
        raise ValueError('Use an HTTPS SearXNG instance URL without credentials or query parameters.')
    return url.rstrip('/').removesuffix('/search')


def json_request(url, cancel=None, payload=None, headers=None):
    """Pinned public-network connection; no redirects or credentials in error messages."""
    url = web_fetch.normalize_url(url)
    with web_fetch.Deadline(cancel) as deadline:
        conn = web_fetch.connection(url, web_fetch.checked_addresses(url, deadline, {}), deadline)
        try:
            # Connection/TLS establishment keeps its short timeout, but search APIs
            # need longer to produce results. The overall deadline and Stop watcher
            # still interrupt this socket, including while waiting for headers.
            deadline.check()
            conn.sock.settimeout(min(RESPONSE_TIMEOUT_SECONDS, deadline.until - time.monotonic()))
            parts = urlsplit(url)
            request_headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity',
                               'User-Agent': web_fetch.USER_AGENT, 'Connection': 'close', **(headers or {})}
            body = json.dumps(payload).encode() if payload is not None else None
            if body:
                request_headers['Content-Type'] = 'application/json'
            conn.request('POST' if body else 'GET', parts.path + ('?' + parts.query if parts.query else ''),
                         body=body, headers=request_headers)
            response = conn.getresponse()
            if response.status != 200:
                raise SearchError(f'Search service returned HTTP {response.status}.', http_status=response.status)
            if response.getheader('Content-Encoding', 'identity') != 'identity':
                raise SearchError('Search service returned unsupported compression.')
            data = bytearray()
            while True:
                deadline.check()
                block = response.read1(min(65536, 1048577 - len(data)))
                if not block:
                    break
                data.extend(block)
                if len(data) > 1048576:
                    raise SearchError('Search response exceeded 1 MiB.')
            deadline.check()
            result = json.loads(data)
            if not isinstance(result, dict):
                raise SearchError('Search service returned an invalid response.')
            return result
        except Exception:
            deadline.check()
            raise
        finally:
            conn.close()


def _check(cancel):
    if cancel and cancel.is_set():
        raise SearchError('Search stopped.')


def _attempt(engine, cfg, query, limit, cancel, ddg):
    global _next
    # No unbounded wait queue, retries, or parallel bursts against free services.
    until = time.monotonic() + 30
    while not _lock.acquire(timeout=.1):
        _check(cancel)
        if time.monotonic() >= until:
            raise SearchError('Search service is busy. Try again later.')
    try:
        _check(cancel)
        while time.monotonic() < _next:
            _check(cancel)
            if time.monotonic() >= until:
                raise SearchError('Search pacing wait expired. Try again later.')
            time.sleep(.05)
        _next = time.monotonic() + max(1, float(cfg.get('interval_seconds', 2)))
        if engine == 'ddg':
            raw = ddg(query, limit)
        elif engine == 'serper':
            raw = json_request('https://google.serper.dev/search', cancel,
                               {'q': query, 'num': limit}, {'X-API-KEY': cfg.get('serper_api_key', '')}).get('organic')
        else:
            data = json_request(endpoint_url(cfg['searxng_url']) + '/search?' + urlencode(
                {'q': query, 'format': 'json', 'categories': 'general'}), cancel)
            raw = data.get('results')
            if not raw and data.get('unresponsive_engines'):
                raise SearchError('SearXNG upstream engines are unavailable.')
        _check(cancel)
        if not isinstance(raw, list):
            raise SearchError('Search service returned an invalid result list.')
        return raw
    finally:
        _lock.release()


def search(cfg, query, limit, cancel, ddg):
    engine = cfg.get('engine', 'ddg')
    key = (engine, cfg.get('searxng_url', ''), cfg.get('serper_api_key', ''))
    fallback = None
    _check(cancel)
    remaining = _failed_until.get(key, 0) - time.monotonic()
    if remaining > 0:
        # Skipped attempts must not restart the cooldown: active research could
        # otherwise prevent the configured provider from ever being retried.
        fallback = 'Configured search service is cooling down after a failure.'
        logger.info('Search cooldown engine=%s remaining_seconds=%.1f fallback=ddg', engine, remaining)
    else:
        try:
            results = _attempt(engine, cfg, query, limit, cancel, ddg)
            _failed_until.pop(key, None)
            return results, engine, fallback
        except Exception as exc:
            _check(cancel)
            _log_failure(engine, 'primary', exc)
            if engine == 'ddg':
                raise SearchError('DuckDuckGo search is unavailable or rate-limited. Try again later.') from None
            # Never expose exception text from SDKs or remote response bodies (may contain keys).
            fallback = str(exc) if isinstance(exc, SearchError) else 'Configured search service is unavailable.'
            if len(_failed_until) > 32:
                _failed_until.clear()
            _failed_until[key] = time.monotonic() + 60
    try:
        return _attempt('ddg', cfg, query, limit, cancel, ddg), 'ddg', fallback
    except Exception as exc:
        _check(cancel)
        _log_failure('ddg', 'fallback', exc)
        raise SearchError('Configured search and DuckDuckGo fallback are unavailable. Try again later.') from None


def _log_failure(engine, role, exc):
    # Exception messages/tracebacks may contain SDK request URLs, queries or keys.
    logger.warning('Search failed engine=%s role=%s error_type=%s http_status=%s category=%s',
                   engine, role, type(exc).__name__,
                   exc.http_status if isinstance(exc, SearchError) else None,
                   exc.status if isinstance(exc, web_fetch.FetchError) else 'provider_error')


def reset_health():
    _failed_until.clear()
