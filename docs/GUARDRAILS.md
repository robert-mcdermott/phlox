# Guardrails (PII redaction & blocking)

Phlox can detect and **redact or block** PII and organization-specific patterns in the
content that flows **into** model providers (user messages, replayed history, tool
results) and **out of** them (streamed completions). One deployment-wide policy is
enforced at the shared model-call seams, so **interactive chat and the API gateway
(`/v1/*`) behave identically** — and the future agentic gateway endpoint inherits it
automatically. Content is inspected in memory only; nothing the detector sees is stored.

## 1. The policy

Admins configure the policy in **Settings → Guardrails** (applied live, no restart;
`config.yml`'s `guardrails:` section only seeds a fresh deployment — see
[ARCHITECTURE](ARCHITECTURE.md) §6 on the config overlay).

| Field | Meaning |
|---|---|
| **Enabled** | Master switch. Off (the default) means no filtering anywhere. |
| **Input action** | `off` \| `redact` \| `block` — applied to content sent **to** providers. |
| **Output action** | `off` \| `redact` \| `block` — applied to model output sent back to clients. |
| **Redaction text** | Default replacement for custom patterns (built-ins have their own tokens). |

- **`redact`** replaces each match (`jane@x.com` → `[EMAIL]`) and lets the request/response
  proceed.
- **`block`** refuses the request/response outright (and still redacts the content in any
  error detail, so blocked text is never echoed back).
- A direction set to **`off`** is not filtered at all.

### Built-in detectors

Each can be toggled individually; they follow the direction's global action and carry
their own replacement token. Hover the info icon in the UI to see the active regex.

| Detector | Replacement |
|---|---|
| Email address | `[EMAIL]` |
| Phone number | `[PHONE]` |
| Social Security number | `[SSN]` |
| Credit card number (major brands) | `[CREDIT_CARD]` |
| API key or token (OpenAI/Phlox `sk-`, GitHub, AWS, Slack, JWT shapes) | `[API_KEY]` |

### Custom patterns

Regular expressions (Python `re` syntax) for organization-specific identifiers — MRNs,
employee IDs, project codenames. Each pattern has its own **action** (`redact` or
`block`) and replacement, so a deployment can redact PII globally but hard-block a
codename. Invalid regexes are rejected at save time. Patterns are written by trusted
admins; keep them anchored (`\b…\b`) and linear to avoid pathological backtracking.

### Test patterns

The **Test patterns** box previews the input or output policy against sample text —
including your **unsaved** edits — via `POST /api/admin/config/guardrails/preview`.
Samples are processed in memory by the local admin API and never stored.

## 2. Where it's enforced

Enforcement lives at the model-call choke points (the same seams as budgets/usage):

| Path | Input | Output |
|---|---|---|
| **Interactive chat** | `block`: the turn is refused with a 400 before anything is persisted (like the budget 402). `redact`: the outbound copy of the history is scrubbed before **every** provider round — covering the system prompt, user turns, tool results, compaction, and sub-agents. The **persisted transcript keeps the user's original words**; only what leaves for the provider is redacted. | `redact`: streamed tokens (and reasoning deltas) pass through a holdback redactor. `block` (or a block-action custom pattern matching mid-stream): the turn ends with a visible "Response blocked by guardrails policy" notice; withheld text is discarded. |
| **Gateway `POST /v1/chat/completions`** | `block`: HTTP 400, OpenAI error envelope, `type: "guardrails_violation"`. `redact`: messages scrubbed before the provider call. | `redact`: buffered responses scrubbed whole; streaming responses pass through the holdback redactor (a block-pattern match terminates the stream with an error frame). `block`: **streaming requests are rejected up front** (a stream can't be un-sent); non-streaming responses are blocked after provider return (the model ran, usage is still recorded). |

### Streaming redaction (the holdback)

A match can straddle SSE chunk boundaries (`jane.d` + `oe@example.com`). The
`StreamRedactor` (in `app/guardrails.py`) holds back the last ~256 characters until
enough context arrives to prove they aren't the start of a match, so nothing partial is
ever emitted. Block-action matches are detected as soon as they complete, even while
still held back. Extremely long secrets (multi-hundred-character JWTs) can exceed the
holdback window in adversarial chunkings; the buffered (non-streaming) path has no such
limit.

## 3. Scope and caveats

- The policy protects the **provider boundary and API clients**. It does not rewrite the
  local database: chat transcripts keep the user's original words, and tool output shown
  in the chat UI (the user's own files) is not masked — only what is sent to the model is.
- Redaction is pattern-based, not semantic: it will not catch PII with no recognizable
  shape ("my neighbor Jane at the blue house"). For regulated data see the Tier 5
  data-governance items in [ROADMAP.md](ROADMAP.md).
- Detection quality is regex-grade: expect occasional false positives (e.g. a 16-digit
  order number reading as a credit card). Disable individual built-ins if they collide
  with your data.

## 4. Where it lives (code map)

| Concern | File |
|---|---|
| Detectors, rule compilation, `StreamRedactor` | `backend/app/guardrails.py` |
| Effective config (file + live overlay) | `backend/app/config.py` (`get_guardrails_config`) |
| Admin API (save + preview) | `backend/app/routers/admin_config.py` |
| Chat enforcement | `backend/app/routers/chat.py`, `backend/app/agent/harness.py` |
| Gateway enforcement | `backend/app/routers/gateway.py` |
| Admin UI | `frontend/src/components/settings/GuardrailsPanel.jsx` |
| Tests | `backend/tests/test_guardrails.py` |
