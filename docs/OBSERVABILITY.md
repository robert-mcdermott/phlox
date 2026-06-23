# Observability

Phlox exposes three layers of operational visibility, configured under
`observability:` in `config.yml`.

## 1. Per-message token usage + cost

Every assistant turn records its token usage (`input`/`output`/`total`) on the `Message`
(`Message.usage`), captured from the provider's real usage events (Bedrock + OpenAI/Ollama).
If you configure **pricing**, each turn's **cost** is computed and stored too.

```yaml
observability:
  pricing:                       # USD per 1,000,000 tokens
    us.anthropic.claude-sonnet-4-6: { input: 3.0, output: 15.0 }
    gpt-4o: { input: 2.5, output: 10.0 }
```

> **Pricing is also editable in the UI.** This map can be set in `config.yml` *or* edited
> live by an admin in **Settings → (Admin) Configuration** — the DB overlay takes precedence
> and new per-turn costs use the updated rates immediately, with no restart. See
> [ARCHITECTURE.md](ARCHITECTURE.md) §6.

- **In the UI:** the per-message footer shows `… N tok · $cost`; the composer's token
  meter shows live context usage vs the limit.
- **Aggregate (self):** `GET /api/usage` returns the signed-in user's totals (input/output
  tokens, cost, turn count, and a `by_model` breakdown).
- **Aggregate (admin chargeback):** `GET /api/usage/by-user` (admin-only) returns usage
  grouped by **month × user × department × model**, for departmental chargebacks. The
  **Usage & Cost** admin tab renders this with a month filter and a **CSV export** for
  finance.

### Durable usage ledger (survives user deletion)

The chargeback view reads an append-only **`UsageLedger`** (`models.py`,
`usage_ledger.py`), not live messages. Each finalized assistant turn appends a ledger row
capturing tokens/cost/model **plus a snapshot of the billable identity**
(username/email/department) at that moment. The ledger:

- has **no foreign keys** to users/conversations and is **excluded from
  `delete_user_data`**, so a departing user's usage — and the department to bill — survive
  account deletion (the key requirement for mid-month chargebacks);
- stores **usage metadata only, never message content** (the one deliberate exception to
  the deletion-purge guarantee in [AUTH.md](AUTH.md));
- is **backfilled** once at startup from any pre-existing `Message.usage` history
  (idempotent), so historical cost isn't lost when the feature ships;
- snapshots **department per turn**, so reassigning a user mid-month bills each department
  for the usage incurred while the user was assigned to it;
- also captures **API-gateway** traffic: each `/v1/chat/completions` call writes a ledger
  row via `usage_ledger.record_gateway_usage` (keyed on a unique `request_id`, so retries
  can't double-count), so programmatic usage shows up in the same chargeback view as chat.
  See [API_GATEWAY.md](API_GATEWAY.md).

> **The ledger also powers spend budgets.** Monthly USD caps per user/department are
> enforced by summing this ledger over the current month — no separate accounting. The
> `pricing` map above doubles as the definition of a "priced" (and therefore blockable)
> model. See [BUDGETS.md](BUDGETS.md).

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

## Notes
- Cost is only computed for models present in the `pricing` map; others store usage without
  a cost.
- Usage is **per assistant turn** (it sums all tool-call rounds in that turn). For
  multi-turn totals use `/api/usage`.
