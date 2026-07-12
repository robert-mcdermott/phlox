<div align="center">
  <img src="frontend/public/phlox-logo.svg" alt="Phlox" height="72" />
  <h1>Phlox</h1>
  <p>A feature-rich, ChatGPT-style, self-hostable AI assistant.</p>

  <p>
    <a href="https://github.com/robert-mcdermott/phlox/actions/workflows/ci.yml"><img src="https://github.com/robert-mcdermott/phlox/actions/workflows/ci.yml/badge.svg" alt="CI" /></a>
    <a href="https://codecov.io/gh/robert-mcdermott/phlox"><img src="https://codecov.io/gh/robert-mcdermott/phlox/branch/main/graph/badge.svg" alt="Coverage" /></a>
    <a href="https://www.codefactor.io/repository/github/robert-mcdermott/phlox"><img src="https://www.codefactor.io/repository/github/robert-mcdermott/phlox/badge" alt="CodeFactor" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-blue.svg" alt="License: Apache 2.0" /></a>
  </p>
</div>

Phlox is a self-hostable chat application with an agentic harness, document RAG, code
execution, and MCP integration — running over **any** model provider: **AWS Bedrock** or
**any OpenAI-compatible endpoint** (OpenAI, Ollama, OpenRouter, vLLM, LiteLLM, LM Studio, local
models).

![phox-screenshot](docs/phlox-screenshot.png)

## Features

- 💬 **Streaming chat** with conversation history, rename/delete, search & export, message
  edit/regenerate, markdown with highlighted/copyable code, **Mermaid diagrams** and
  **LaTeX math**.
- 🤖 **Agentic harness** (inspired by PI Coder): the model uses tools in a loop —
  filesystem (`read_file`/`write_file`/`edit_file`/`glob`/`grep`), `run_shell`,
  `execute_python`/`execute_node`, `search_documents`, opt-in live `web_search` +
  `web_fetch`, plus **planning** (`update_todos`), **sub-agents** (`spawn_subagent`),
  **memory** (`save_memory`), and **checkpoints** — each scoped to a per-conversation
  sandboxed workspace.
- 🤝 **Human-in-the-loop approvals** — pause on sensitive tools, approve/deny, resume.
- 🧰 **Code execution** with captured output and **artifacts** shown inline + a
  **Workspace Files** panel to browse/download everything the agent created.
- 🖥️ **Artifact canvas** — HTML and Markdown files the agent writes open in a live,
  resizable side panel (sandboxed preview + raw-source toggle) instead of a plain download.
- 🗂️ **Workspace checkpoints** — git-backed snapshots with one-click restore.
- 📚 **Documents / RAG** — upload PDF/DOCX/TXT/MD/code; explicitly search the document
  library per prompt, attach documents to a specific message, or reference existing
  documents with `@`; **hybrid (dense+sparse) search** over **Qdrant** with reranking +
  citations; global or per-conversation scoping. Works offline via a fallback embedder.
- 🌐 **Opt-in live web search** — a per-prompt composer toggle exposes `web_search`
  backed by zero-config `ddgs` (or optional SearXNG), so the agent can discover current
  sources before fetching pages with `web_fetch`.
- 🎭 **Custom assistants** — admins define named personas (name, avatar, description) on
  top of any configured base model, with a custom **system prompt**, **starter prompt
  suggestions**, an optional shared **knowledge base** (documents all users of the
  assistant can search), and per-assistant **capability limits** (web search /
  personal-document search / agent tools). Users pick an assistant on the new-chat screen;
  the choice is pinned per conversation.
- ✨ **Agent skills** — named, reusable markdown workflows the agent can follow
  ([Anthropic Agent Skills](https://github.com/anthropics/skills)–compatible, with
  SKILL.md import/export): invoke one explicitly with a **`/` slash command** in the
  composer, or let the agent **auto-activate** relevant skills via progressive disclosure
  (opt-out toggle). Users manage private skills; admins publish shared ones. See
  [docs/SKILLS.md](docs/SKILLS.md).
- 🧠 **Cross-conversation memory** — durable facts recalled across chats.
- 🖼️ **Multimodal** — attach images to messages for vision models.
- 🔌 **MCP integration** — connect Model Context Protocol servers; their tools join
  automatically.
- 🔀 **Any provider** — named profiles for Bedrock / OpenAI-compatible endpoints,
  switchable live, with a connection tester.
- 🚪 **OpenAI-compatible API gateway** — mint per-user API keys and call Phlox from any
  OpenAI SDK/tool (`POST /v1/chat/completions`, `GET /v1/models`); every call flows through
  the same per-user/department cost accounting. See [docs/API_GATEWAY.md](docs/API_GATEWAY.md).
- 🏠 **Runs fully local** — point at **Ollama**, **LM Studio**, or **vLLM** (any
  OpenAI-compatible server) for offline, self-hosted inference with no cloud API key; RAG
  embeddings can run locally too.
- 🔐 **Auth & multi-user** — local accounts (or **Entra ID SSO**), `user`/`admin` roles,
  per-user data isolation, an **admin panel** (assistants, users, MCP, tools, auth). See
  [docs/AUTH.md](docs/AUTH.md).
- 💵 **Usage & cost accounting** — per-message token/cost in the UI, plus an admin
  **chargeback** view: usage by **month × user × department × model**, CSV export for
  finance, and a durable ledger that keeps a departed user's costs billable after their
  account is deleted. See [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md).
- 🧮 **Spend budgets** — admins set a monthly USD cap per **user** or **department**; users
  get a warning banner at an adjustable threshold (default 90%) and **priced** models are
  blocked once a budget is reached (free models stay usable), enforced for both chat and the
  API gateway and resetting each month. See [docs/BUDGETS.md](docs/BUDGETS.md).
- 🛡️ **Guardrails (PII redaction & blocking)** — built-in detectors (email, phone, SSN,
  credit card, API keys) plus **custom regex patterns** that **redact or block** sensitive
  content flowing to model providers and back to clients, streaming included. One
  admin-set policy enforced identically in chat and the API gateway, with a live
  test-pattern preview. See [docs/GUARDRAILS.md](docs/GUARDRAILS.md).
- ⚙️ **Live admin configuration** — edit provider profiles (keys write-only), model
  pricing, resilience, generation defaults, and sandbox limits from an admin-only
  **Configuration** panel, applied without a server restart. `config.yml` remains the seed.
- 📦 **Container / remote sandbox** — run code in an isolated **Podman/Docker** container
  (resource limits + network isolation), or **off-host** in an AWS Bedrock AgentCore Code
  Interpreter microVM (kernel-level isolation). See [docs/SANDBOX.md](docs/SANDBOX.md).
- 🎨 **Theming** — Phlox Dark (default) + Phlox Light/Light/Dark/Fred Hutch/Hutch
  Night/Sandstone/**Terminal** (CRT phosphor-green), instant switching. See
  [docs/THEMING.md](docs/THEMING.md).
- 🛡️ **Per-tool permissions** — `auto | ask | deny`, with an "Agent mode" toggle.
  Live web and document-library search are separate per-prompt toggles and are off by
  default unless the user directly references a document.

## Documentation

| Doc | What it covers |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System map, request lifecycle, module guide — **start here** |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What's done and what's next (Tiers 1–5) |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Production deployment on Linux (Ubuntu/RHEL) under **systemd** |
| [docs/DOCKER.md](docs/DOCKER.md) | Running Phlox in a container (**Docker or Podman**) |
| [docs/AUTH.md](docs/AUTH.md) | Local accounts, roles, multi-user isolation, **Entra ID SSO** setup |
| [docs/SANDBOX.md](docs/SANDBOX.md) | Code-execution sandbox: **local** vs **Podman/Docker container** vs **AWS AgentCore** (off-host microVM) |
| [docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) | Token usage/cost, structured logs, OpenTelemetry tracing |
| [docs/BUDGETS.md](docs/BUDGETS.md) | Monthly spend budgets per user/department: warnings + enforcement |
| [docs/API_GATEWAY.md](docs/API_GATEWAY.md) | OpenAI-compatible API gateway: API keys + `/v1/*` endpoints |
| [docs/GUARDRAILS.md](docs/GUARDRAILS.md) | PII/custom-pattern **redaction & blocking** at the model-call seams |
| [docs/MCP.md](docs/MCP.md) | Connecting MCP servers |
| [docs/SKILLS.md](docs/SKILLS.md) | Agent skills: slash commands, auto-activation, SKILL.md import/export |
| [docs/THEMING.md](docs/THEMING.md) | The theme token system + adding themes |
| [docs/ADDING_A_TOOL.md](docs/ADDING_A_TOOL.md) · [docs/ADDING_A_PROVIDER.md](docs/ADDING_A_PROVIDER.md) | Extension guides |
| [AGENTS.md](AGENTS.md) | Orientation for AI coding agents working on the repo |

## Architecture

Two processes: a **FastAPI** backend (LLM orchestration, agent harness, MCP, RAG, code
exec, auth, SQLite persistence) and a **React/Vite** frontend. Full details in
**[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.

```
backend/   FastAPI app (app/), config.yml, SQLite + Qdrant under data/
frontend/  React + Vite + Tailwind SPA
docs/      ARCHITECTURE, ROADMAP, DEPLOYMENT, DOCKER, AUTH, SANDBOX, MCP, THEMING, BUDGETS, GUARDRAILS, ADDING_A_*
scripts/   start.sh / start.ps1 (build + run), stop.sh / stop.ps1 (shut down + free ports)
```

## Quick start

The fastest way to get running — one command builds/starts everything and opens your
browser. It checks for [`uv`](https://docs.astral.sh/uv/) and Node (with install
instructions if either is missing), installs dependencies on first run, and seeds
`backend/config.yml` from the example:

```bash
# macOS/Linux
./scripts/start.sh          # dev mode: hot-reload backend (:8000) + Vite (:5173)
./scripts/start.sh prod     # prod mode: builds the SPA once, single process on :8000

# Windows (PowerShell)
.\scripts\start.ps1
.\scripts\start.ps1 prod
```

Press **Ctrl+C** to stop and free the port(s) — or, if you started it detached
(`-d`/`-Detach`) or just want to be sure everything's stopped, run `./scripts/stop.sh`
(`.\scripts\stop.ps1` on Windows) any time.

Prefer to run it by hand instead? Prerequisites: **Python 3.11+** with `uv`, **Node 18+**,
and a model provider (a local [Ollama](https://ollama.com) is the easiest):

```bash
# 1. Backend
cd backend
uv sync
cp config.yml.example config.yml        # edit: set your provider profile(s)
uv run uvicorn app.main:app --reload --port 8000

# 2. Frontend (separate terminal)
cd frontend
npm install
npm run dev                              # open http://localhost:5173
```

### Configure a provider

Edit `backend/config.yml` (full examples in `config.yml.example`). Any
**OpenAI-compatible** server works with `type: openai` — just point `endpoint` at it. That
covers the popular **local** runtimes, so Phlox can run **entirely offline** with no
cloud API key:

```yaml
default_profile: local-ollama
profiles:
  local-ollama:
    type: openai
    label: "Ollama (local)"
    endpoint: http://localhost:11434/v1
    api_key: ollama            # required by the client, ignored by Ollama
    model: qwen3.6:35b
    # Optional: restrict/seed the model dropdown. If omitted, /api/providers
    # tries to list models from the endpoint.
    models: [qwen3.6:35b, glm-4.7-flash:latest]
    supports_tools: true       # set false for models without tool-calling

  # LM Studio (local) — enable its server under the "Developer" tab (default port 1234).
  lmstudio:
    type: openai
    label: "LM-Studio (local)"
    endpoint: http://localhost:1234/v1
    api_key: none            # required by the client, ignored by LM-Studio
    model: qwen/qwen3.6-27b
    # Optional: restrict/seed the model dropdown. If omitted, /api/providers
    # tries to list models from the endpoint.
    models: [qwen/qwen3.6-27b]
    supports_tools: true       # set false for models without tool-calling
```

The same `type: openai` shape also covers **OpenAI**, **LiteLLM**, and any other
OpenAI-compatible gateway — set the `endpoint` and `api_key`. For **AWS Bedrock**, use
`type: bedrock` with a `model` id and `aws_region` (credentials resolve via the standard
AWS chain; for temporary STS creds also set `aws_session_token`).

Define as many profiles as you like and switch between them live in **Settings → Model**
(there's a built-in connection tester). Embeddings for document RAG can also run locally —
e.g. Ollama's `nomic-embed-text` — so the whole stack stays offline.

> **Edit config without a restart.** `config.yml` is the seed; an admin can edit provider
> profiles, model pricing, resilience, generation defaults, and sandbox limits **live** in
> **Settings → (Admin) Configuration** (overrides are stored in the DB and applied
> immediately). API keys there are write-only/masked. Bootstrap-sensitive settings (`auth`,
> `vector_store`, `web_fetch` (SSRF-guard allowlist), the sandbox runner *type*, OTel) stay
> file-only and need a restart. See [docs/AUTH.md](docs/AUTH.md) §admin config.

### Sign in

Auth is **on by default**. On a clean database Phlox creates the `admin` account with a
random one-time password, shows it once in the startup console, and requires a password
change before the app can be used. Manage users, reset passwords, and view/configure SSO
under **Settings → (Admin) Users / Authentication**. Production startup requires a stable,
strong `PHLOX_JWT_SECRET`; see [docs/AUTH.md](docs/AUTH.md). To run single-user with no
login, set `auth.enabled: false`.

### Code-execution sandbox

By default code runs in a **local subprocess** (fast, trusts the host). For isolation, set
`sandbox.runner: container` to run each execution in an ephemeral **Podman/Docker**
container with CPU/memory/PID limits and network isolation, or `sandbox.runner: agentcore`
to run it **off-host** in an AWS Bedrock AgentCore Code Interpreter microVM (Firecracker,
kernel-level isolation — no local Docker needed). For data-analysis workloads you
can optionally build the **batteries-included images** (numpy/pandas/matplotlib/… preinstalled)
so the agent doesn't pip-install packages on every run — see [docs/SANDBOX.md](docs/SANDBOX.md).

## Production build

```bash
./scripts/start.sh prod      # or: .\scripts\start.ps1 prod   (Windows)
```
Or by hand:
```bash
cd frontend && npm run build      # outputs frontend/dist
cd ../backend && uv run uvicorn app.main:app --port 8000
```
FastAPI serves the built SPA from `frontend/dist` at `/`. For a real deployment (systemd,
reverse proxy, TLS) see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) instead — this quick `prod`
mode is meant for trying Phlox locally in something closer to its production shape, not
for serving real traffic.

## Testing

The backend has a pytest suite (unit + FastAPI `TestClient` API tests + scripted-provider
agent-loop/fallback tests); the frontend is verified by a production build. The same checks
run in **GitHub Actions CI** (`.github/workflows/ci.yml`) on every push/PR.

```bash
# Backend: lint + tests (from backend/)
cd backend
uv sync --extra dev          # installs ruff + pytest
uv run ruff check app tests
uv run pytest                # or: uv run pytest -k usage   to run a subset

# Frontend: the CI check is the build (from frontend/)
cd ../frontend && npm run build
```

The tests run against an in-memory/temp SQLite DB with `auth.enabled` off (a synthetic dev
admin), so no provider credentials or network are needed — agent-loop tests use a built-in
**scripted "test" provider**. The suite exercises the chargeback ledger surviving user
deletion (`tests/test_api.py::test_usage_ledger_survives_user_deletion`), among many others.

### Code coverage

`pytest-cov` is included in the `dev` extra. Run the suite with a coverage report from
`backend/`:

```bash
uv run pytest --cov=app --cov-report=term-missing   # per-file % + uncovered lines
uv run pytest --cov=app --cov-report=html           # browsable report at htmlcov/index.html
```

Coverage config (source = `app`) lives in `[tool.coverage.*]` in `backend/pyproject.toml`, so
a bare `uv run pytest --cov` works too. In CI a `coverage.xml` is uploaded to **Codecov**,
which powers the coverage badge above; the **CodeFactor** badge tracks automated code quality.

### Live-model evals (optional)

`backend/evals/run_evals.py` exercises the agent against a **real** configured provider
(tool use, RAG, multi-step). It needs a working `config.yml` profile and is **not** part of
CI:

```bash
cd backend && uv run python -m evals.run_evals
```

## Security notes

- **Auth:** save the first-run admin password securely, replace it at first login, and set a
  strong, stable `PHLOX_JWT_SECRET` before production startup. Data is isolated per user; admin features
  are role-gated.
- **Sandbox:** the local runner trusts the host (fine for single-user/local). For
  untrusted/multi-user execution use `sandbox.runner: container` (on-host, isolated) or
  `sandbox.runner: agentcore` (off-host AWS microVM) ([docs/SANDBOX.md](docs/SANDBOX.md)).
- Mutating/execution tools default to the **`ask`** permission policy; "Agent mode"
  auto-approves for a turn. Sub-agents (`spawn_subagent`) inherit that same approval
  state rather than granting themselves a bypass — if the turn isn't in Agent mode,
  ask-tier tools a sub-agent tries to run are denied, not silently allowed.
- **`web_fetch`** refuses private/loopback/link-local addresses (incl. the cloud metadata
  IP) by default, so it can't be used to reach your internal network. Configurable via
  `web_fetch` in `config.yml` (file-only) — see `config.yml.example`.
- **Database:** SQLite by default; set `DATABASE_URL` to deploy against Postgres instead
  ([docs/DOCKER.md](docs/DOCKER.md)).
- **Sensitive data (PHI):** audit logging, secrets management, and data governance are
  tracked as **Tier 5** in the [roadmap](docs/ROADMAP.md) and are required before any
  deployment touching sensitive data.

## License

Licensed under the **Apache License, Version 2.0** — see [LICENSE](LICENSE).
Copyright © 2026 Robert McDermott &lt;robert.c.mcdermott@gmail.com&gt;.
