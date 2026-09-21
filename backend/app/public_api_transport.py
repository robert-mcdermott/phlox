"""Bounded retries for explicitly reviewed, repeatable public API reads only."""
from datetime import timezone
from email.utils import parsedate_to_datetime
import random
import time

from app import public_api_adapters, web_fetch

MAX_ATTEMPTS = 3
BACKOFF_SECONDS = 1.0
JITTER_SECONDS = 0.25
RETRY_HTTP = frozenset({408, 429, 500, 502, 503, 504})
RETRY_ERRORS = frozenset({'connection_error', 'incomplete'})


def retry_after(value):
    """Accept RFC 9110 delta-seconds or HTTP-date; never echo an untrusted header."""
    if not isinstance(value, str) or len(value) > 128:
        return None
    value = value.strip()
    if value.isascii() and value.isdigit():
        return int(value)
    try:
        date = parsedate_to_datetime(value)
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        return max(0, date.timestamp() - time.time())
    except (TypeError, ValueError, OverflowError):
        return None


def wait(seconds, deadline):
    until = time.monotonic() + seconds
    while True:
        deadline.check()
        remaining = until - time.monotonic()
        if remaining <= 0:
            return
        if deadline.cancel:
            deadline.cancel.wait(min(remaining, 0.05))
        else:
            time.sleep(min(remaining, 0.05))


def read(adapter_name, operation, url, deadline, policy=None, *, body=None,
         response_format='json', authorize=None, retrieval=None):
    from app import public_api
    adapter = public_api_adapters.get(adapter_name)
    maximum = MAX_ATTEMPTS if adapter.retry_safe else 1
    trace = {'operation': operation, 'attempts': []}
    if retrieval is not None:
        retrieval.append(trace)
    for attempt in range(1, maximum + 1):
        deadline.check()
        if authorize:
            authorize()
        public_api.pace(deadline, adapter_name)
        deadline.check()
        if authorize:
            authorize()
        record = {}
        trace['attempts'].append(record)
        try:
            result = web_fetch.read_api_query(url, deadline, policy, body=body, response_format=response_format)
        except web_fetch.FetchError as exc:
            deadline.check()  # Stop/overall deadline must never become a retry.
            record.update(status=exc.status, http_status=exc.http_status)
            transient = exc.status in RETRY_ERRORS or (exc.status == 'http_error' and exc.http_status in RETRY_HTTP)
            if not adapter.retry_safe or not transient:
                raise
            delay = BACKOFF_SECONDS * 2 ** (attempt - 1) + random.uniform(0, JITTER_SECONDS)
            requested = retry_after(exc.retry_after)
            if requested is not None:
                delay = max(delay, requested)
                # A subsequent tool call (or another caller) must not bypass a server
                # delay, even when this call cannot fit a retry into its deadline.
                public_api.defer(adapter_name, delay)
            if attempt == maximum:
                raise web_fetch.FetchError(exc.status, f'{exc} Stopped after {attempt} HTTP attempts; try again later.', exc.http_status) from None
            if delay >= deadline.until - time.monotonic():
                raise web_fetch.FetchError('retry_deferred',
                    f'API temporarily unavailable after {attempt} HTTP attempt(s). The retry delay exceeds the remaining time; try again later.',
                    exc.http_status) from None
            record['retry_delay_seconds'] = round(delay, 6)
            wait(delay, deadline)
        else:
            record.update(status='ok', http_status=result[1])
            return result


def summary(retrieval):
    attempts = sum(len(item['attempts']) for item in retrieval)
    retries = sum(max(0, len(item['attempts']) - 1) for item in retrieval)
    return f'HTTP retrieval: {attempts} attempt(s), {retries} automatic retry/retries.\n' if retries else ''
