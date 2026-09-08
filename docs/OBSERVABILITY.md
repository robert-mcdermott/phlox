# Observability

Phlox exposes three layers of operational visibility, configured under
`observability:` in `config.yml`.

## 1. Model-call usage and cost

The metadata-only `UsageLedger` records each application generation call before dispatch,
then saves provider usage snapshots as they arrive. It covers chat rounds, children,
compaction, fallback, explicit compatibility retries, gateway calls, and connection probes.
Final `Message.usage` receipts aggregate the turn without another charge. See
[MODEL_CALLS.md](MODEL_CALLS.md) for statuses, parent attribution, context checks, and limits.

Configure USD-per-million `input`, `output`, `cache_read`, and `cache_write` rates in
`observability.pricing` or live in **Admin → Configuration → Model pricing**. Rates are
snapshotted at call start. Blank rates mean unknown; explicit zero means zero price.
Reported cached tokens require their cache rate. Old rows keep their original cost.

- Message footers show known tokens and partial/unknown usage or cost when appropriate.
- `GET /api/usage` returns the owner's ledger totals, including gateway and paused calls.
- Admin-only `GET /api/usage/by-user` groups metadata by month, user, department, and model.
  **Usage & Cost** supports month filtering and CSV export, with known subtotals and
  explicit unknown counts. Calls and distinct turns are separate API fields.
- Identity is snapshotted per call. The ledger has no content or foreign keys and survives
  account/conversation deletion, the deliberate privacy carve-out in [AUTH.md](AUTH.md).
- Startup backfills legacy message receipts idempotently, skipping new call-based receipts.
  Historical single-turn entries cannot reconstruct missing child usage or original rates.
- [Spend budgets](BUDGETS.md) reuse the ledger's known cost and gate subsequent calls;
  incomplete accounting is not an invoice total or a cost reservation.

## 2. Structured request logging

A middleware logs one line per API request: method, path, status, duration, and the user
(decoded from the bearer token). Always on (disable with `request_logging: false`).

```
INFO phlox.request: POST /api/chat -> 200 8421.3ms user=ab12cd34
```

## 3. Distributed tracing (OpenTelemetry) — optional

Tracing is **off by default** and a **no-op unless configured**. Set an OTLP endpoint and
install the OpenTelemetry packages to instrument FastAPI:

```yaml
observability:
  otel:
    endpoint: http://localhost:4318/v1/traces
    service_name: phlox
```

```bash
uv pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http \
  opentelemetry-instrumentation-fastapi
```

If the endpoint is set but the packages are missing, Phlox logs a warning and keeps
running (no tracing). Point the endpoint at any OTLP collector (Jaeger, Tempo, Langfuse's
OTLP ingest, etc.). The instrumentation seam lives in `app/observability.py`.
