# API Gateway (OpenAI-compatible)

Phlox can be called programmatically as an **authenticated, OpenAI-compatible LLM gateway**:
one endpoint, all your configured models (Bedrock + any OpenAI-compatible backend), with the
same per-user / per-department cost accounting as the chat UI. Point any OpenAI SDK or tool
at `<phlox>/v1` and it works unmodified.

> **Status — Phase 1 (raw passthrough).** Implements `POST /v1/chat/completions` and
> `GET /v1/models`: **exactly one model call per request**, so cost is predictable. The
> agentic pipeline (`POST /v1/agent/completions`, RAG + tools + MCP, fan-out usage grouped
> by `parent_request_id`) is **Phase 2** — the auth + accounting layer below is already
> built to absorb it (see [ROADMAP.md](ROADMAP.md) and issue #5).

## 1. API keys

Every user mints their own keys in **Settings → API Keys** (or via the REST API below).

- A key looks like `phlox-sk-<random>`. The **full secret is shown exactly once** at
  creation and is **never recoverable** — only a SHA-256 **hash** is stored
  (`ApiKey.key_hash`), plus a short non-secret `prefix` for display.
- Keys can be **revoked** (soft-delete; the audit row remains) and given an **expiry**.
- A key resolves to its **owning user**, which is the **billable identity**: all gateway
  usage attributes to that user and their department, exactly like interactive chat.
- Deleting a user **hard-deletes their keys** (they can never authenticate again), while the
  usage they already incurred survives in the ledger (identity snapshotted) — consistent
  with the chargeback rationale in [AUTH.md](AUTH.md).

### Key management REST API (browser session / JWT auth)

| Method & path | Purpose |
|---|---|
| `GET /api/api-keys` | List your keys (metadata only — never the secret). |
| `POST /api/api-keys` | Create a key. Body: `{ "name": "...", "expires_at": "<ISO>"? }`. Response includes `key` (the plaintext) **once**. |
| `DELETE /api/api-keys/{id}` | Revoke a key you own (404 if not yours — no existence leak). |

## 2. Gateway endpoints (API-key auth)

Authenticate with `Authorization: Bearer phlox-sk-...`. These accept **only** an API key
(not the browser JWT), so programmatic traffic is always tied to a named, revocable key.
Errors use the OpenAI error envelope (`{"error": {"message", "type"}}`).

### `GET /v1/models`

Lists every callable model across all profiles. Each `id` is fully-qualified
`profile/model` (since the same model id can exist under multiple profiles); a bare model
id also works and resolves against the catalog (falling back to the default profile).

When the key's owner is over a monthly **spend budget**, priced models are annotated with a
non-standard `"phlox_blocked": true` (clients may ignore it; calling a blocked model still
returns a 402). See [BUDGETS.md](BUDGETS.md).

### `POST /v1/chat/completions`

OpenAI-spec request/response. Supports streaming (`"stream": true`, SSE
`chat.completion.chunk` frames terminated by `data: [DONE]`) and buffered responses.
Honored fields: `model`, `messages`, `temperature`, `max_tokens`, `stream`. Other fields
(`top_p`, `stop`, `user`, …) are accepted and ignored in Phase 1. **Tools/RAG/agentic
behavior are intentionally not exposed here** — that's Phase 2's `/v1/agent/completions`.
The same applies to **custom assistants** (personas + shared knowledge bases): they are an
interactive-chat feature and are not selectable through the gateway in Phase 1.

If the key's owner (or their department) is over a monthly **spend budget** and the
requested model is priced, the call is rejected with **HTTP 402** in the OpenAI error
envelope (`type: "insufficient_quota"`) before any upstream call. Models with no assigned
cost are never blocked. See [BUDGETS.md](BUDGETS.md).

### Examples

```bash
# List models
curl https://phlox.example.com/v1/models \
  -H "Authorization: Bearer phlox-sk-..."

# One completion
curl https://phlox.example.com/v1/chat/completions \
  -H "Authorization: Bearer phlox-sk-..." -H "Content-Type: application/json" \
  -d '{"model": "gpt-4o", "messages": [{"role": "user", "content": "Hello"}]}'
```

```python
from openai import OpenAI
client = OpenAI(api_key="phlox-sk-...", base_url="https://phlox.example.com/v1")
resp = client.chat.completions.create(
    model="my-profile/gpt-4o",                       # or just "gpt-4o"
    messages=[{"role": "user", "content": "Hello"}],
)
print(resp.choices[0].message.content)
```

## 3. Cost / token accounting

Usage is captured at the **model-call layer**, not the endpoint, so attribution is identical
to interactive chat and is ready to absorb Phase 2's multi-call agentic requests. Each
gateway model call appends one row to the durable **`UsageLedger`**
(`usage_ledger.record_gateway_usage`):

- Cost is computed from the `model_pricing` table (`observability.pricing`, also live-editable
  in **Settings → Configuration**) — see [OBSERVABILITY.md](OBSERVABILITY.md).
- The synthetic per-call `request_id` is stored in the ledger's unique `message_id` column,
  so a retried write can't double-count.
- `parent_request_id` (stored in `conversation_id`) is reserved to group the N calls of one
  agentic request in Phase 2.

Gateway usage therefore appears automatically in the admin **Usage & Cost** view
(`GET /api/usage/by-user`, "usage by month × user × department × model") and the per-user
`GET /api/usage` meter — no extra wiring. The same ledger also drives **spend budgets**, so
gateway spend counts toward a user's/department's monthly cap. See [BUDGETS.md](BUDGETS.md).

## 4. Where it lives (code map)

| Concern | File |
|---|---|
| `ApiKey` table | `backend/app/models.py` |
| Key generate / hash / resolve / CRUD service | `backend/app/api_keys.py` |
| Key management router | `backend/app/routers/api_keys.py` |
| Bearer-key auth dependency (`require_api_key`) | `backend/app/auth/deps.py` |
| Gateway router (`/v1/*`) | `backend/app/routers/gateway.py` |
| Model id → `(profile, model)` resolution + catalog | `backend/app/providers/registry.py` |
| Per-call ledger write | `backend/app/usage_ledger.py` |
| Settings → API Keys UI | `frontend/src/components/settings/ApiKeysPanel.jsx` |
