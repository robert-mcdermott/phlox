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
import time

from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_observability_config

logger = logging.getLogger("phlox.request")


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
            "%s %s -> %s %sms user=%s",
            request.method, request.url.path, response.status_code, dur_ms, user or "-",
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
