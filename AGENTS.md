# AGENTS.md — start here

This file orients AI coding agents (and humans) working on **Phlox**.

## What this is
A feature-rich, ChatGPT-style web app: chat + an agentic
tool-using harness (code execution, filesystem, shell, web), document RAG, and MCP
integration — over **any** model provider (AWS Bedrock or any OpenAI-compatible endpoint,
including local models like Ollama).

## Read this before changing code
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** — the system map and the request
lifecycle. Then the focused guides:
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md) — installation, configuration, usage, and troubleshooting
- [docs/CONVERSATION_ALTERNATIVES.md](docs/CONVERSATION_ALTERNATIVES.md) — preserved edits/retries, selected history and saved answer files
- [docs/PROJECTS.md](docs/PROJECTS.md) — private projects, context selection/records, and migration
- [docs/RUNS.md](docs/RUNS.md) — opt-in reconnectable execution, Stop, and recovery
- [docs/INGESTION.md](docs/INGESTION.md) — document queue/retry, embedding identity, staged index rebuilds
- [docs/WEB_SOURCES.md](docs/WEB_SOURCES.md) — bounded web fetching, captured passages/failures, private citations
- [docs/SOURCES.md](docs/SOURCES.md) — document citations and snapshot access/retention
- [docs/ROADMAP.md](docs/ROADMAP.md) — active improvement plan and milestone acceptance criteria
- [docs/CODEBASE_REVIEW.md](docs/CODEBASE_REVIEW.md) — implementation findings behind the plan
- [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md) — migrations, offline backup/restore, and recovery drills
- [docs/MODEL_CALLS.md](docs/MODEL_CALLS.md) — call accounting, prices, and context fit
- [docs/APPROVALS.md](docs/APPROVALS.md) — claims, recovery, counters, expiry, and interruption
- [docs/IMPLEMENTATION_WAVES.md](docs/IMPLEMENTATION_WAVES.md) — delivered work and next wave
- [docs/ADDING_A_TOOL.md](docs/ADDING_A_TOOL.md)
- [docs/ADDING_A_PROVIDER.md](docs/ADDING_A_PROVIDER.md)
- [docs/AUTH.md](docs/AUTH.md) — auth, roles, multi-user isolation, Entra ID SSO
- [docs/API_GATEWAY.md](docs/API_GATEWAY.md) — OpenAI-compatible API gateway: keys + `/v1/*`
- [docs/GUARDRAILS.md](docs/GUARDRAILS.md) — PII/custom-pattern redaction & blocking at the model-call seams
- [docs/SANDBOX.md](docs/SANDBOX.md) — local vs Podman/Docker code-exec sandbox
- [docs/THEMING.md](docs/THEMING.md)
- [docs/MCP.md](docs/MCP.md)

## Stack
- **Backend**: Python 3.11+, FastAPI, SQLAlchemy + SQLite, SSE streaming. Managed with
  `uv`. Code in `backend/app/`.
- **Frontend**: React 18 + Vite + Tailwind + Zustand. Code in `frontend/src/`.

## Run it
Easiest: `./scripts/start.sh dev` (macOS/Linux) or `.\scripts\start.ps1 dev` (Windows). It
checks for `uv` + Node prerequisites, runs `uv sync`/`npm install` if needed, seeds
`backend/config.yml`, starts both servers, and opens your browser. `./scripts/stop.sh` (or
`.\scripts\stop.ps1`) frees the ports again. `prod` instead of `dev` builds the SPA once and
runs a single Uvicorn process (`:8000`) instead of two dev servers.

Manual equivalent:
```bash
# backend  (terminal 1)
cd backend && uv sync --inexact
# Create config.yml from config.yml.example only if absent; edit profiles.
uv run -m app.dev --port 8000

# frontend (terminal 2)
cd frontend && npm install && npm run dev    # http://localhost:5173
```

## Test it (run before you claim something works)
```bash
cd backend && uv sync --extra dev
uv run ruff check app tests        # lint
uv run pytest                      # unit + API + scripted-provider agent-loop tests
cd ../frontend && npm run build
npx playwright install chromium   # once per Playwright browser version
npm run test:browser               # isolated Chromium approval/accounting/isolation tests
```
Tests run with `auth.enabled` off and a scripted **test** provider — no creds/network
needed. CI (`.github/workflows/ci.yml`) runs the same. Live-model checks (real provider)
live in `backend/evals/run_evals.py` and are not part of CI.

## Conventions
- Schema changes require a new Alembic revision in `backend/app/migrations/versions/`.
  Keep the baseline snapshot immutable and test populated upgrades on both databases.
  See [docs/BACKUP_RESTORE.md](docs/BACKUP_RESTORE.md).
- The **agent loop** lives in `backend/app/agent/harness.py`; it is provider-agnostic.
  Don't put provider-specific logic there — it belongs in `backend/app/providers/`.
- **All tools** (built-in, MCP, RAG) register into one `REGISTRY`
  (`backend/app/agent/registry.py`). Add tools via the guide, not ad hoc.
- **Theme with semantic tokens** (`bg-surface`, `text-content`, `text-accent`), not
  hard-coded colors, so themes keep working. Brand-locked bits may use `hutch-*` colors.
- The **permission gate** (`agent/permissions.py`) is the safety seam; default mutating/
  exec tools to `ask`.
- Code execution uses the **sandbox interface** (`sandbox/runner.py`). The local runner
  trusts the host; `ContainerRunner` (Podman/Docker) is the isolated option, selected via
  `sandbox.runner: container`. Add further strategies by implementing `SandboxRunner`.
- **Privacy is strict:** per-user data has no admin read-bypass and ownership checks return
  404 (not 403). The one deliberate carve-out is the metadata-only `UsageLedger` (chargeback).
- **Config has two layers.** `config.yml` is the seed; admin UI edits live in a DB overlay
  (`app_config.py` + `AppConfig`) that the `config.py` getters merge over the file. Read
  config through those getters (never `load_config()` directly) so live overrides apply. New
  admin-editable section? Add it to the overlay + `routers/admin_config.py`, and keep
  provider secrets write-only/masked. Bootstrap/security settings (`auth`, `vector_store`,
  sandbox runner type) stay file-only.

## Status
The original feature tiers are preserved in [docs/ROADMAP_LEGACY.md](docs/ROADMAP_LEGACY.md).
Implemented foundations include streaming/tool use, approval snapshots, hybrid RAG, memory,
sub-agents, skills, checkpoints, multimodal input, auth/Entra SSO, container/AgentCore
sandboxes, usage/budgets, and the Phase 1 API gateway. **Optional Postgres already exists.**
The [active roadmap](docs/ROADMAP.md) prioritizes correctness and durable runs, then cited
research, projects/artifact editing, controlled autonomy, and self-hosted release quality.
**Wave 1 (F01/F02) is implemented:** MCP lifecycle/cancellation, permission defaults, inherited
child context, bounded read-only children, and sequential child mutation. **Wave 2 (F03 +
initial F05) is implemented:** atomic approval claims, cumulative counters, current-policy
checks, UI recovery, and browser regression tests. **Wave 3 (F04) is implemented:**
per-call usage, price snapshots, unknown-cost reporting, and bounded context checks.
**Wave 4 (F06) is implemented:** checked Alembic adoption, offline backup/restore, and
SQLite/Postgres recovery drills. **Wave 5 (F07) is implemented:** opt-in reconnectable runs,
explicit Stop, and conservative interruption recovery. **Wave 6 (document F08) is implemented:**
private source snapshots, stable citations, source inspection and reauthorized Markdown exports
(see [docs/SOURCES.md](docs/SOURCES.md)). **Wave 7 (bounded M2.2) is implemented:** document
processing/retry, PDF page and DOCX table provenance, embedding identity, staged rebuilds,
and explicit keyword degradation. **Wave 8 (web F08) is implemented:** DNS-pinned bounded
fetching, private web snapshots/failure records, source inspection/removal, and citations
through approval/replay/export. M1/M2 remain in progress; OCR, semantic retrieval evaluation,
and a bounded Research workflow are still roadmap work. Consult the wave log
for verification and remaining boundaries. Sensitive-data/PHI governance remains a separate
deployment gate. Extend along the documented seams above.

**Waves 9–11 also ship:** opt-in Research/admin search, automatic model discovery, and private
projects with context inspection. Project search ceilings live in `ToolContext.document_scope`
and must survive delegation/resume; empty scope means no documents. Per-turn `ContextRecord`
rows track complete retained passages in fitted outbound input and cascade with conversations.
**Wave 12 ships conversation alternatives:** use `branches.active()` for model history and
exports; `Conversation.messages` contains every saved path and is for ownership/deletion.
Persist answers with their pinned parent, including approval resumes. Request-bound streams
and durable runs block branch/project mutation while active. File snapshots retain bounded
answer output separately from the shared workspace; selection never restores files.
**Wave 13 adds editable artifacts:** immutable bounded text versions live in SQL; saving
and restoring are independent of explicit hash-checked workspace publication. Selected-text
AI revision uses the shared model-call accounting/budget seam and both guardrail directions;
proposals require review and never write automatically. Observe run/approval admission
guards for mutations and preserve the original answer snapshot. See [docs/ARTIFACTS.md](docs/ARTIFACTS.md).
Current schema head is `0008_artifacts`; older revision metadata must remain checkable.
