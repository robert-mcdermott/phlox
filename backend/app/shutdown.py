"""Process-local shutdown admission and cooperative cancellation (no database writes)."""
from contextlib import contextmanager
import threading
import weakref

from fastapi import HTTPException

stopping = threading.Event()
_lock = threading.RLock()
_active = {}
_interrupted = weakref.WeakSet()


def reject_new_work():
    if stopping.is_set():
        raise HTTPException(503, 'Server is shutting down. Retry after restart.', headers={'Retry-After': '5'})


def begin():
    """Idempotent. Preserve events already cancelled by their caller (e.g. user Stop)."""
    with _lock:
        stopping.set()
        for event in _active:
            cancel(event)


def cancel(event):
    with _lock:
        if not event.is_set():
            _interrupted.add(event)
            event.set()


def interrupted(event):
    with _lock:
        return event in _interrupted if event is not None else False


@contextmanager
def active(event=None):
    event = event if event is not None else threading.Event()
    with _lock:
        _active[event] = _active.get(event, 0) + 1
        if stopping.is_set():
            cancel(event)
    try:
        yield event
    finally:
        with _lock:
            _active[event] -= 1
            if not _active[event]:
                del _active[event]


def track(stream, event):
    with active(event):
        try:
            yield from stream
        finally:
            stream.close()


def reset():
    """Only for a clean lifespan; never reopen admission while writers remain active."""
    with _lock:
        if _active:
            raise RuntimeError('Cannot reset shutdown while work is active')
        stopping.clear()
        _interrupted.clear()


class AdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and stopping.is_set():
            from starlette.responses import JSONResponse
            response = JSONResponse({'detail': 'Server is shutting down. Retry after restart.'},
                                    status_code=503, headers={'Retry-After': '5'})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)
