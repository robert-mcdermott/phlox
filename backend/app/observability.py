"""Observability: structured request logging, per-message token cost, and an OTel seam.

- **Token/cost tracking**: the harness records each assistant turn's token usage on the
  ``Message`` (see ``harness._finalize``); ``compute_cost`` prices it from ``config.yml``.
- **Structured request logging**: a middleware logs method/path/status/duration/user.
- **Tracing (OpenTelemetry)**: ``setup_observability`` instruments FastAPI **only if**
  ``observability.otel.endpoint`` is configured and the ``opentelemetry`` packages are
  installed — otherwise it's a no-op. See ``docs/OBSERVABILITY.md``.
"""
from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_observability_config

logger = logging.getLogger("phlox.request")
BOOT_ID = uuid.uuid4().hex[:12]


def lifecycle(event: str, **metadata) -> None:
    """Callers supply operational identifiers/states only, never request or tool bodies."""
    logging.getLogger("phlox.lifecycle").info(
        "at=%s pid=%s boot=%s event=%s %s",
        datetime.now(timezone.utc).isoformat(), os.getpid(), BOOT_ID, event,
        " ".join(f"{key}={value}" for key, value in metadata.items()),
    )


class TelemetryFailureFilter(logging.Filter):
    """Coalesce optional collector failures without logging endpoint credentials/bodies."""

    def __init__(self, interval=60):
        super().__init__()
        self.interval = interval
        self.next_notice = 0.0
        self.suppressed = 0
        self.lock = threading.Lock()

    def filter(self, record):
        if record.levelno < logging.WARNING:
            return True
        with self.lock:
            now = time.monotonic()
            if now < self.next_notice:
                self.suppressed += 1
                return False
            record.msg = (
                "Telemetry export failed; check the configured collector. "
                "Application execution is independent of trace delivery. "
                "%s similar messages suppressed since the previous notice."
            )
            record.args = (self.suppressed,)
            record.exc_info = record.exc_text = record.stack_info = None
            self.suppressed = 0
            self.next_notice = now + self.interval
            return True


def compute_cost(model: str | None, usage: dict | None) -> float | None:
    """Return USD cost for a turn from the configured per-model pricing, or None."""
    if not model or not usage:
        return None
    pricing = get_observability_config().get("pricing", {})
    rate = pricing.get(model)
    if not rate:
        return None
    inp = (usage.get("input", 0) / 1_000_000) * rate.get("input", 0)
    out = (usage.get("output", 0) / 1_000_000) * rate.get("output", 0)
    return round(inp + out, 6)


def _user_from_auth(authorization: str | None) -> str | None:
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    try:
        from app.auth.security import decode_access_token

        payload = decode_access_token(authorization.split(" ", 1)[1])
        return payload.get("sub") if payload else None
    except Exception:  # noqa: BLE001
        return None


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log one structured line per API request."""

    async def dispatch(self, request, call_next):
        if not request.url.path.startswith("/api"):
            return await call_next(request)
        start = time.monotonic()
        response = await call_next(request)
        dur_ms = round((time.monotonic() - start) * 1000, 1)
        user = _user_from_auth(request.headers.get("authorization"))
        logger.info(
            "%s %s -> %s %sms user=%s auth=%s",
            request.method, request.url.path, response.status_code, dur_ms, user or "-",
            getattr(request.state, "auth_failure", "-"),
        )
        return response


def setup_observability(app) -> None:
    """Wire request logging (always) and OpenTelemetry tracing (if configured)."""
    cfg = get_observability_config()
    if cfg.get("request_logging", True):
        app.add_middleware(RequestLoggingMiddleware)

    otel = cfg.get("otel", {})
    endpoint = otel.get("endpoint")
    if not endpoint:
        return  # tracing disabled
    exporter_logger = logging.getLogger("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    if not any(isinstance(f, TelemetryFailureFilter) for f in exporter_logger.filters):
        exporter_logger.addFilter(TelemetryFailureFilter())
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(
            resource=Resource.create({"service.name": otel.get("service_name", "phlox")})
        )
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
        trace.set_tracer_provider(provider)
        FastAPIInstrumentor.instrument_app(app)
        logging.getLogger("phlox").info("OpenTelemetry tracing enabled -> %s", endpoint)
    except ImportError:
        logging.getLogger("phlox").warning(
            "observability.otel.endpoint is set but OpenTelemetry packages are not installed; "
            "install with: uv pip install 'opentelemetry-sdk' "
            "'opentelemetry-exporter-otlp-proto-http' 'opentelemetry-instrumentation-fastapi'"
        )
