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
- [docs/ROADMAP.md](docs/ROADMAP.md) — status + what's next (Tiers 1–5)
- [docs/ADDING_A_TOOL.md](docs/ADDING_A_TOOL.md)
- [docs/ADDING_A_PROVIDER.md](docs/ADDING_A_PROVIDER.md)
- [docs/AUTH.md](docs/AUTH.md) — auth, roles, multi-user isolation, Entra ID SSO
- [docs/SANDBOX.md](docs/SANDBOX.md) — local vs Podman/Docker code-exec sandbox
- [docs/THEMING.md](docs/THEMING.md)
- [docs/MCP.md](docs/MCP.md)

## Stack
- **Backend**: Python 3.11+, FastAPI, SQLAlchemy + SQLite, SSE streaming. Managed with
  `uv`. Code in `backend/app/`.
- **Frontend**: React 18 + Vite + Tailwind + Zustand. Code in `frontend/src/`.

## Run it
```bash
# backend  (terminal 1)
cd backend && uv sync && cp config.yml.example config.yml   # edit profiles
uv run uvicorn app.main:app --reload --port 8000

# frontend (terminal 2)
cd frontend && npm install && npm run dev    # http://localhost:5173
```
Or use `scripts/dev.ps1` (Windows) / `scripts/dev.sh` (POSIX).

## Test it (run before you claim something works)
```bash
cd backend && uv sync --extra dev
uv run ruff check app tests        # lint
uv run pytest                      # unit + API + scripted-provider agent-loop tests
cd ../frontend && npm run build    # the frontend CI check
```
Tests run with `auth.enabled` off and a scripted **test** provider — no creds/network
needed. CI (`.github/workflows/ci.yml`) runs the same. Live-model checks (real provider)
live in `backend/evals/run_evals.py` and are not part of CI.

## Conventions
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
**Tiers 1–4 complete** (see [docs/ROADMAP.md](docs/ROADMAP.md)): streaming chat + resumable
agent loop with approvals, RAG (hybrid Qdrant + rerank), cross-conversation memory,
sub-agents, checkpoints, multimodal, auth/multi-user + Entra SSO, container sandbox,
observability + usage/cost chargeback, and tests/CI. **Tier 5** (Postgres, PHI/data
governance) is deferred and gates any sensitive-data deployment. Extend along the documented
seams above.
