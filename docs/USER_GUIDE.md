# Phlox User Guide

[Project overview](../README.md) · [Configuration example](../backend/config.yml.example)

Phlox combines chat with tools, document retrieval, reusable assistants and skills, and a
workspace for generated files. You provide a model endpoint; Phlox manages the conversation,
permissions, and local application state. Start with a local installation, then use the
specialized deployment guides when making it available to others.

## Contents

- [Choose an installation](#choose-an-installation)
- [First-time setup](#first-time-setup)
- [Configure model providers](#configure-model-providers)
- [How configuration works](#how-configuration-works)
- [Appearance](#appearance)
- [Reconnectable runs and Stop](#reconnectable-runs-and-stop)
- [Chat and everyday use](#chat-and-everyday-use)
- [Documents, search, and citations](#documents-search-and-citations)
- [Assistants, skills, memory, and MCP](#assistants-skills-memory-and-mcp)
- [Accounts, execution, and costs](#accounts-execution-and-costs)
- [Storage and environment variables](#storage-and-environment-variables)
- [Upgrades and backups](#upgrades-and-backups)
- [Troubleshooting](#troubleshooting)
- [Detailed guides](#detailed-guides)

## Choose an installation

| Installation | Use it for | Start with |
|---|---|---|
| Local development launcher | Trying Phlox or developing on a trusted computer | First-time setup below |
| Host production launcher | A built frontend served by one backend on port 8000 | Production preparation below |
| Linux systemd service | Persistent shared service with a reverse proxy | [Linux deployment](DEPLOYMENT.md) |
| Docker / Podman application container | Reproducible app image with external config/data mounts | [Container deployment](DOCKER.md) |

Phlox supports **one application process per database/data directory**. Postgres and a
Qdrant server do not enable multiple Uvicorn workers or replicas. Development uses a Vite
frontend and a backend with reload; production serves the built frontend from the backend.

## First-time setup

### Prerequisites

Install Git, uv, and Node/npm. The backend requires Python **3.11+**; uv manages its project
environment. CI and the application-image frontend build use Node **20**. You also need a
reachable model server or cloud provider credentials. The launcher checks for uv and Node
and prints installation guidance if missing; it does not install those programs for you.
See the providers section before choosing a model: vision, tools, and available context
vary by endpoint/model.

### Local installation

On macOS/Linux:

```bash
git clone https://github.com/robert-mcdermott/phlox.git
cd phlox
# Create the config only if it does not already exist.
if [ ! -f backend/config.yml ]; then
  cp backend/config.yml.example backend/config.yml
fi
# Edit backend/config.yml with your editor before starting.
./scripts/start.sh dev
```

On Windows PowerShell:

```powershell
git clone https://github.com/robert-mcdermott/phlox.git
Set-Location phlox
if (!(Test-Path backend/config.yml)) {
  Copy-Item backend/config.yml.example backend/config.yml
}
# Edit backend/config.yml with your editor before starting.
.\scripts\start.ps1 dev
```

The example contains several illustrative profiles; it does not provision any of their
models. Set `default_profile` and its matching `profiles` entry to a model you can actually
use. The default example points to a local Ollama server. If you start before editing it,
you can sign in and configure providers through the admin Configuration panel.

The launcher syncs backend dependencies, installs frontend dependencies when absent,
starts the backend on **127.0.0.1:8000** and frontend on **http://localhost:5173**, and opens
a browser. Logs are under `.run/logs/` in the checkout. It can stop processes occupying its
chosen ports; select unused ports when running other local services.

### First login and existing data

Authentication is enabled by default. With a **fresh database**, startup prints a temporary
password for `admin` (or the configured bootstrap username). Sign in and set a replacement
password before using the app. Registration is off unless explicitly enabled.

An existing database keeps its users, conversations, settings, and provider overrides. Use
your existing login; a new admin password is not generated on every restart. Do not copy
the example over your working config, delete the database, or select a new data directory
to enable a feature. See [upgrades and backups](#upgrades-and-backups).

In development, omitting `PHLOX_JWT_SECRET` creates an ephemeral signing secret: restarting
the backend signs you out. Set a strong, stable secret if you want sessions to survive
restarts. Never commit the secret or your populated config. [AUTH.md](AUTH.md) covers
accounts, password resets, and Entra ID SSO.

### Start, stop, and production preparation

| Action | macOS/Linux, from checkout root | Windows PowerShell |
|---|---|---|
| Start development | `./scripts/start.sh dev` | `.\scripts\start.ps1 dev` |
| Start in background | `./scripts/start.sh dev --detach` | `.\scripts\start.ps1 dev -Detach` |
| Suppress browser launch | `./scripts/start.sh dev --no-browser` | `.\scripts\start.ps1 dev -NoBrowser` |
| Stop servers | `./scripts/stop.sh` | `.\scripts\stop.ps1` |
| Build and serve production | `./scripts/start.sh prod` | `.\scripts\start.ps1 prod` |

Ctrl+C also stops a foreground launcher. Use the chat's **Stop** and wait for active work
before planned shutdown. Browser disconnection and server shutdown have different effects;
see [reconnectable runs](#reconnectable-runs-and-stop).

Before `prod` with auth enabled, configure `sandbox.runner: container` or `agentcore`, make
that runner available, and supply **`PHLOX_JWT_SECRET` with at least 32 bytes of high-entropy
secret material**. Generate a secret once, store it securely, and load the same value on
subsequent starts. Set it in the launching shell or service secret environment. Replace the
placeholder below with your generated value before running either command:

```bash
export PHLOX_JWT_SECRET='REPLACE_WITH_YOUR_SAVED_RANDOM_SECRET'
```

```powershell
$env:PHLOX_JWT_SECRET = 'REPLACE_WITH_YOUR_SAVED_RANDOM_SECRET'
```

These shell assignments last only for that shell/session; use a protected service/secret
configuration for persistent hosting. For example, `openssl rand -hex 32` generates a value on a host with
OpenSSL; it does not persist or configure the value for you. Both launchers set
`PHLOX_ENV=production` for `prod` and run a preflight. The default auth-enabled/local-sandbox
combination intentionally fails that preflight. See [sandbox setup](SANDBOX.md).

For a shared deployment, follow [Linux/systemd](DEPLOYMENT.md) or [Docker/Podman](DOCKER.md),
including authentication, isolated execution, HTTPS, persistent storage and backups. The
application container's local runner has access to its mounted application data; putting
Phlox itself in a container does not provide a separate execution sandbox for each user.

### Manual startup

For development, from `backend/` after preparing the config:

```bash
uv sync --frozen --inexact
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

In another terminal, from `frontend/`:

```bash
npm ci
npm run dev
```

Install the backend's `postgres` extra if needed. After dependency updates, rerun `npm ci`
even if `node_modules` exists. In production, build the frontend with `npm run build` and
run a single backend without `--reload`, with the production environment and security
settings above. The Linux guide includes the service definition.

## Configure model providers

Admins manage the available profiles in **Settings → Configuration**; users select a
profile/model under **Settings → Model**. Use its connection test, then send a short chat
message. A connection test is a real model call and may incur cost. Existing conversations
can carry their own selected settings, and custom assistants can fix their model choice.

### Local or compatible endpoint

Merge this shape into your config, replacing the model placeholder with an installed model
ID. `default_profile` must match the profile key:

```yaml
default_profile: local
profiles:
  local:
    type: openai
    label: Local model
    endpoint: http://localhost:11434/v1
    api_key: ollama
    model: YOUR_INSTALLED_MODEL_ID
    supports_tools: true
```

`type: openai` is Phlox's compatible-protocol adapter; it also handles Ollama, LM Studio,
vLLM, LiteLLM, and other compatible servers. Run the server and load/download a model
separately. For a server with no authentication, use a nonempty placeholder key if required
by the client. For an authenticated endpoint, supply its real credential.

- Ollama's example endpoint is `http://localhost:11434/v1`; `ollama list` shows installed IDs.
- LM Studio's example endpoint is `http://localhost:1234/v1`; enable its model server.
- vLLM must use a port different from Phlox's backend, such as `http://localhost:8001/v1`.
  Configure tool calling in the model server as required by that server/model.
- Set `supports_tools: false` for models that cannot handle tool calls. This does not add
  tool capability to an unsupported model. Images likewise require a vision-capable model.
- Enable **Automatic** model discovery in **Settings → Configuration → Provider profiles**
  to find newly installed models when the searchable Model picker opens. Existing explicit
  `models` lists remain curated until you enable Automatic. Refresh and custom model IDs
  remain available; failed discovery preserves the previous list and your selection.
  See [model discovery](MODEL_DISCOVERY.md) for provider support and setup.
- `context_window` optionally sets a provider ceiling; see [model-call limits](MODEL_CALLS.md).

When Phlox runs in a container, `localhost` refers to that container. Use the host alias
and networking instructions in [DOCKER.md](DOCKER.md). Provider endpoints saved in the
admin UI override file profiles, including when moving an existing installation into a container.

### Cloud-compatible endpoint or AWS Bedrock

For a cloud compatible endpoint, use the same profile structure with its HTTPS `/v1`
endpoint, model ID, and API key. Set the key in the profile or the admin Configuration
panel, where keys are masked/write-only. The current adapter does **not** read
`OPENAI_API_KEY` automatically: when a profile key is absent/empty it supplies a placeholder
for local servers. Config files do not perform generic `${VARIABLE}` interpolation.
Do not leave `sk-...` in a real cloud profile or assume an exported key replaces it.

For Bedrock:

```yaml
profiles:
  bedrock:
    type: bedrock
    label: AWS Bedrock
    aws_region: us-west-2
    model: YOUR_ACCESSIBLE_BEDROCK_MODEL_OR_INFERENCE_PROFILE_ID
    supports_tools: true
    # Optional: aws_profile: your-named-aws-profile
```

Set `default_profile: bedrock` if this is your initial default. The host's AWS credential
chain is used when profile credentials are omitted. Phlox also accepts a named AWS profile,
explicit access/secret credentials (plus `aws_session_token` for temporary credentials), or
`aws_bedrock_api_key`. See the commented [configuration example](../backend/config.yml.example)
for these alternatives. The chosen principal and region must have access to the selected
model. Bedrock model-provider credentials and AgentCore sandbox credentials are configured
separately; choosing Bedrock for chat does not select a remote sandbox.

## How configuration works

There are three layers. This explains why a file edit may appear to have no effect:

1. **File and environment:** `backend/config.yml` seeds deployment defaults and supplies
   startup-only options. `PHLOX_CONFIG` selects another file. Restart after file changes.
2. **Admin database overrides:** settings saved in **Configuration** or **Guardrails** take
   precedence for those sections. They apply live and survive restarts/upgrades. Providers,
   prices, suggestions, and guardrails replace their file sections; generation, resilience,
   and container settings merge over file values. Deleting a provider from an override does
   not reveal that provider's file definition again.
3. **User and conversation settings:** saved model/generation choices override deployment
   defaults where applicable; an assistant may additionally select settings and restrict
   capabilities. Changing initial defaults does not reset all existing user/chat choices.

The file is still used on an existing database for options without overrides and for all
file-only settings. **Editing the file does not clear a DB override.** Use the matching
admin panel for sections already managed there. Do not erase your database to reset config.

| Setting | Where to change it | When it applies |
|---|---|---|
| `profiles`, `observability.pricing` | File seed or admin Configuration | UI edits apply live; saved sections win over file |
| `defaults`, `resilience`, `suggestions` | File seed or admin Configuration | UI edits apply live; user/chat choices may override defaults |
| `guardrails` | File seed or admin Guardrails | Live policy override |
| `sandbox.container` engine, images, limits, network | File seed or admin Configuration | Next execution uses saved changes |
| `sandbox.runner`, `sandbox.agentcore` | File | Restart required |
| `runs.enabled` | File | Restart required; default `false` |
| `auth` / Entra settings | File; JWT secret via environment for production | Restart required |
| `database.url`, `vector_store`, `embeddings` | File; `DATABASE_URL` can override database URL | Restart; index maintenance may be needed |
| Web search engine | Settings → Configuration → Web search; DDG default, Serper/SearXNG optional | Live |
| `web_fetch` network policy | File | Restart for file changes |
| `observability.request_logging`, `observability.otel` | File | Restart required |
| `default_profile` | File | Initial/default selection; does not reset saved selections |
| Active profile, generation, system prompt, theme | Settings → Model / Appearance; conversation controls | Saved user/chat preferences |
| Accounts, assistants, skills, MCP, tools, budgets | Their Settings panels | Stored in DB, not new YAML sections |

Use YAML booleans `true` and `false`, not quoted strings. Merge snippets into existing
sections; do not duplicate top-level `profiles`, `sandbox`, or `runs` keys.

Generation defaults in the example are temperature `0.3`, max output tokens `8192`, max
context tokens `16000`, and max tool rounds `12`. Output tokens cap a single response;
context limits cover input/history and need room for output and tool schemas. Compaction
summarizes older history, and estimated fit checks can reject oversized requests before
sending. Round limits are cumulative across approval resumes. [MODEL_CALLS.md](MODEL_CALLS.md)
explains estimation, provider ceilings, retries, fallback, and accounting boundaries.

## Appearance

Open **Settings → Appearance** to choose from 18 color themes. Each card previews
the theme's chat, sidebar, and accent colors. Choices range from Phlox Dark and Light
to Outrun, Blade Runner 2049, Chaos Theory, Cyberpunk, Synthwave, and softer palettes
such as Catppuccin Mocha and Nord. Themes apply immediately and are remembered in
your user settings and browser; Phlox Dark remains the default.

See [Theming](THEMING.md) for the full catalog and custom-theme instructions.

## Reconnectable runs and Stop

Add this top-level section to your existing config and restart:

```yaml
runs:
  enabled: true
```

The default is `false`. Enabling it needs no new service or database: normal startup applies
checked migrations to your existing database. Back up before starting an upgraded release.
It changes who owns execution, so it is file-only rather than a live UI switch.

| Behavior | Default, `false` | Enabled, `true` |
|---|---|---|
| Execution lifetime | Bound to the chat request; disconnect requests cancellation | Owned by the server worker; disconnect detaches the viewer |
| Refresh / change chat / close tab | Does not preserve an active stream for continued execution | Work continues; reopen its conversation for saved progress |
| Explicit Stop | Aborts the request and signals cancellation | Sends a cancellation request; wait for Stopping to resolve |
| Approval pause | Saved and recoverable | Saved on the same run, recoverable across tabs/restarts |
| Server restart during active work | No automatic continuation | Interrupted work is shown for review; no automatic action replay |

**Stop means the Stop button in the chat.** With runs enabled, leaving the page or logging
out does not prevent further model calls/tool actions. Stop requests cancellation; it cannot
undo a file write or external action already performed, and remote tools may not terminate
immediately. Wait for confirmation before deleting a running conversation or shutting down.

One top-level run executes at a time. Limits are 32 unresolved runs per deployment, 4 per
user, and 1 per conversation; pending approvals and unacknowledged interruptions count.
Review interrupted work before selecting **I reviewed the results — allow a new turn**.
Terminal replay expires after seven days; final messages remain. Source snapshots have
their separate 30-day policy. See [RUNS.md](RUNS.md) and [SOURCES.md](SOURCES.md).

Runs and **Agent mode** are independent: runs controls execution/reconnection; Agent mode
allows `ask` tools for a turn. Enabling runs does not automatically approve tools. To disable
runs, stop the server, set `false`, and restart. Saved records remain; linked approvals need
runs enabled again to resume. This is not scheduled execution or distributed worker recovery.

## Chat and everyday use

Start a new conversation from the sidebar, optionally choose an assistant, and send a
message. Use Settings → Model to select a provider and model. Conversation controls can
retain model/generation choices for that chat. The sidebar supports history, search,
rename, delete, and Markdown export. Editing a message or regenerating an answer can cause
new model calls; unresolved approvals/runs must be handled first.

Attach images for a vision model. Answers support Markdown, highlighted/copyable code,
LaTeX math, and Mermaid diagrams. If a model writes files, open **Workspace Files** to browse
and download them. HTML/Markdown artifacts can open in the resizable canvas with preview
and source views. Workspace checkpoints let you restore file snapshots; they do not undo
external tool actions or replace an application backup.

The compact toolbar below the message field contains the **Chat / Research** selector
and tool toggles. Enabled toggles are highlighted and marked with a dot:

- **Agent mode:** off by default. Allows tools whose policy is `ask` for this turn; it does
  not enable denied tools or override an assistant's restrictions.
- **Web search:** exposes live discovery for that prompt; leave off for offline use.
- **Documents:** exposes retrieval from uploaded documents. Explicit document
  references also enable document grounding, subject to assistant capabilities.
- **Skills:** controls automatic skill activation; explicit `/` invocations are separate.

Select the **context percentage** to expand estimated token counts, the output limit, and
the last response's usage when available. Press Escape to close the details. These controls
wrap onto a second row on smaller screens.

Review tool arguments in approval cards, then **Approve & run** or **Deny**. Pending approvals
survive reload and expire 24 hours after each pause. Dismiss terminal/expired notices when
appropriate. A failed budget or policy check can leave approval pending for retry; a claimed
or uncertain action must not simply be replayed. See [APPROVALS.md](APPROVALS.md).

## Documents, search, and citations

Upload documents in **Settings → Documents**, attach one to a message, or type `@` in the
composer to reference an existing ready document. The library supports global and
conversation-scoped documents. Wait for indexing to finish; only ready documents can be
used as evidence. PDF, DOCX, text, Markdown, and code/text files are supported; scanned
PDFs need a text layer because automatic OCR is not implemented. Processing progress appears
in the document library and assistant knowledge settings. Use **Retry processing** after a
failure/restart or **Reprocess document** to parse a ready file again. Uploads are limited to
20 MiB. The document worker runs independently of the optional chat runs feature.

Enable **Search documents** and ask a specific question, such as “Summarize this policy and
cite the passages you use.” Document search uses Qdrant with dense and sparse retrieval
and lexical reranking. The index defaults to embedded storage; a separate Qdrant server
is optional. Configured embeddings can send document text to that provider.

```yaml
embeddings:
  profile: local
  model: YOUR_INSTALLED_EMBEDDING_MODEL_ID
```

The profile must refer to an embedding-capable OpenAI-compatible endpoint. With no profile,
Phlox deliberately uses local hash vectors, which are not semantic embeddings. Configured
provider/index failures instead produce an explicit **keyword-only search** notice; existing
vectors are preserved. Model, endpoint, or `embeddings.version` changes require an explicit
rebuild, including same-dimension changes. Startup never automatically re-embeds documents.

In **Settings → Documents**, an admin can **Rebuild search index**. This embeds all ready
passages with the current configuration, then publishes a staged index only after success.
It can incur provider costs. After upgrading an older library, rebuild to populate its
unknown embedding identities; keyword search remains available meanwhile. Then reprocess
older PDFs/DOCX files to add page/table locations. The offline `app.ops reindex` command
only rebuilds saved vectors. See [Document processing](INGESTION.md) for upgrade steps,
embedding version configuration, limits, and the distinction between reprocess and rebuild.

New document answers can use stable **[S1]** citations. Click one to inspect the retained
excerpt, filename, available PDF page or section/table location, chunk range, and capture time. Changed/truncated passages
are marked; invented references are unverified. Model-generated citations in older messages
are not retroactively upgraded. This feature is always available in both chat modes, with
no separate flag. It currently covers personal documents and assistant knowledge bases;
successfully fetched HTML/text web pages also use the captured-evidence registry. Search
snippets remain discovery leads until Phlox fetches their pages.

Access is rechecked on each source read/export. Source excerpts expire 30 days after last
capture; deletion removes source snapshots and leaves unavailable labels. Historical answer/
tool text and previously downloaded files may still quote the source. A valid label is
not proof of claim support. See [SOURCES.md](SOURCES.md) for the exact limits, cleanup,
privacy, export, and deletion contract.

Web search uses DuckDuckGo without a key by default. Administrators can select Serper or
a public JSON-enabled SearXNG instance under **Settings → Configuration → Web search**.
DuckDuckGo remains the fallback on configured-engine errors. See [Research and search
configuration](RESEARCH.md) for setup, pacing, credentials, public-instance limitations,
and testing. `web_fetch` can retrieve
pages but rejects private/loopback/link-local addresses by default. Configure an explicit
`web_fetch.allowlist_hosts` for intended internal destinations instead of disabling the
guard broadly. These tools require network access even when your model is local.

Ask Phlox to read the pages it finds and cite supporting passages. Web citations show the
original URL, fetch time, and retained text. Failed/denied fetches have no supporting passage;
the source panel explains the failure. Captures survive reload, approval pauses, runs, and
Markdown export. **Remove retained snapshot** erases that web passage while leaving its
citation unavailable; it does not rewrite messages or exports. Chat Stop interrupts active
fetching. See [Web research and captured sources](WEB_SOURCES.md) for configuration,
privacy, limits, and unsupported sites. This needs no new flag or embedding rebuild.

## Assistants, skills, memory, and MCP

- **Assistants:** admins create them in Settings → Assistants, with prompt, model, avatar,
  starter suggestions, visibility, capability restrictions, and optional knowledge-base
  documents. Choose one on the new-chat screen. The assistant is pinned to its conversation;
  prompt edits can apply to existing chats. Its knowledge base is available to users who
  can access that assistant, so upload only documents intended for that audience.
- **Skills:** manage private skills in Settings → Skills; admins can publish shared ones.
  Invoke explicitly with `/`, import/export `SKILL.md`, or allow automatic selection.
  Skills provide instructions, not new privileges. See [SKILLS.md](SKILLS.md).
- **Memory:** saved facts can be recalled in later conversations. Review/delete them in
  Settings → Memory; deleting a chat is not the same as deleting a separate memory.
- **MCP:** admins add stdio, SSE, or Streamable HTTP servers in Settings → MCP Servers.
  Install command-server dependencies on the **Phlox host/container**, not only your browser
  computer. Use absolute paths for filesystem access. MCP tools join the tool manager and
  default to `ask`. Remote connections may have their own credentials and data access;
  the code-execution sandbox does not contain external MCP services. See [MCP.md](MCP.md).

## Accounts, execution, and costs

Admins manage accounts and departments in Settings → Users. Entra ID can provide SSO;
local login remains available. Admins can manage deployment settings and usage metadata,
but cannot read another user's private conversations/documents. Shared assistant content
has its own visibility rules. Account deletion removes private data while the metadata-only
usage ledger remains for chargeback. Details: [AUTH.md](AUTH.md).

Choose execution isolation deliberately:

| Runner | What it means |
|---|---|
| `local` | Code runs with the application's host access; for trusted local development |
| `container` | Each code execution uses an ephemeral, resource-limited Podman/Docker container; supported when Phlox runs on the host |
| `agentcore` | Code runs in an AWS AgentCore session with workspace file synchronization; needs separately configured AWS access |

Set the runner in the file and restart. A configured isolated runner fails closed when
unavailable. For packages needed on every execution, build the optional bundled sandbox
images rather than installing them again in each ephemeral container. [SANDBOX.md](SANDBOX.md)
covers image preparation, network access, limits, and remote cancellation boundaries.

In Settings → Tools, admins control enabled status and `auto`, `ask`, or `deny` policies.
Mutating/code tools normally ask; background child agents cannot stop for their own approval
and deny unresolved `ask` calls unless the parent turn's policy already allows them.

For costs, enter USD-per-million rates under Configuration → Model pricing. Blank means
**unknown**, and explicit zero means zero price. Token/price reporting depends on provider
usage; a partial subtotal is not a complete invoice. Usage & Cost has chargeback filters
and CSV export. Budgets gate subsequent calls using known costs; they are not a reservation
system or guaranteed billing ceiling. See [MODEL_CALLS.md](MODEL_CALLS.md),
[OBSERVABILITY.md](OBSERVABILITY.md), and [BUDGETS.md](BUDGETS.md).

Guardrails can redact/block selected patterns at model-call boundaries. Configure them in
Settings → Guardrails; they are disabled by default. They are not a full database-redaction
or regulated-data compliance system. See [GUARDRAILS.md](GUARDRAILS.md).

For external API clients, create a key in Settings → API Keys and follow
[API_GATEWAY.md](API_GATEWAY.md). The gateway exposes `/v1/models` and `/v1/chat/completions`;
it does not expose the interactive chat worker, document retrieval, or agent tool loop.
In development use the backend port 8000 for `/v1`, since Vite only proxies `/api`.

## Storage and environment variables

By default, config is `backend/config.yml`; SQLite is `backend/data/phlox.db`. The data root
also contains uploads, attachments, workspaces and the default embedded index. App config
overrides, users, histories, runs, citations, permissions and accounting live in SQL.
Selecting another database/data path does not copy the old data to it.

| Variable | Purpose |
|---|---|
| `PHLOX_CONFIG` | Config file path; defaults to `backend/config.yml` resolved from app location |
| `PHLOX_DATA` | Data root; defaults to `backend/data` resolved from app location |
| `DATABASE_URL` | Overrides `database.url`; otherwise SQLite under the data root |
| `PHLOX_JWT_SECRET` | Stable signing secret; required for auth-enabled production |
| `PHLOX_ENV` | `production` or `prod` enables production validation; launchers set it from mode |
| AWS credential variables | Standard AWS chain when explicit profile credentials are not selected |
| `SEARXNG_URL` | Overrides the file's SearXNG search URL |
| `PHLOX_BACKEND_PORT` | Launcher backend port; default 8000 |
| `PHLOX_FRONTEND_PORT` | Launcher development frontend port; default 5173 |

Use **absolute paths** for custom data/config locations and export environment variables
before starting. The plain host launcher does not automatically source a `.env` file.
Compose has its own environment interpolation; it forwards only variables listed in the
service environment. Add needed provider/AWS/search variables or credentials to your
container deployment explicitly.

A custom `PHLOX_DATA` does not override an explicit `vector_store.path`. In particular, the
example's `./data/qdrant` resolves against `backend/`; remove that `path` to use the default
under your custom data root, or set a matching absolute path. Use the same paths and
credentials for maintenance commands as for the server. In dev, changing the backend port
also requires updating the `/api` proxy target in `frontend/vite.config.js`, currently fixed
to port 8000; changing only the launcher variable does not change that proxy.

For Postgres install the driver with `uv sync --frozen --extra postgres` from `backend/`,
then configure `DATABASE_URL` or `database.url`. Keep that extra on later explicit syncs.
The launchers use `uv sync --inexact` to preserve already-installed extras. The application
image includes the driver. Switching database engines does not migrate existing records;
cross-engine conversion is outside the supported backup/restore workflow.

## Upgrades and backups

For an existing installation, preserve your **current config, data paths, database URL and
secret environment**. Enabling runs or citations does not require a fresh database.

1. Stop active work, resolve/review approvals as appropriate, and stop the application and
   other writers. Use the launcher stop command, systemd, or Compose matching your deployment.
2. Make and verify an offline backup under the normal database/config environment. From
   `backend/`, using a new output directory outside your data root:

   ```bash
   uv run -m app.ops db status
   uv run -m app.ops db check
   uv run -m app.ops backup --output /absolute/backups/phlox-before-upgrade --stopped
   uv run -m app.ops verify /absolute/backups/phlox-before-upgrade
   ```

3. Update code and dependencies, preserving optional extras and your config. Rebuild the
   frontend (`npm ci` then `npm run build`) for production. The development launcher does
   not refresh existing frontend dependencies automatically after every lockfile change.
4. Start normally. Checked Alembic migrations run before application bootstrap. Current
   head is `0005_ingestion`; this includes the earlier run/source migrations even with runs
   disabled. Do not stamp a database manually or overwrite it with an empty one.
5. Check `/api/readiness`, sign in, and verify an existing conversation and document.

A compatible prior schema can be checked/backed up before upgrading. If startup reports
schema drift, preserve the backup and investigate a copy; the specific historical
`usage_ledger.message_id VARCHAR(32)` case has a supported migration. Do not manually widen
or stamp it based on an old workaround.

Backups include SQL and files; a copy of `phlox.db` alone is insufficient. Postgres requires
matching native dump/restore tools. Retain environment secrets separately, and rehearse
restore into a new destination. Restored active runs require interruption review, not
automatic replay. Full commands and limitations: [BACKUP_RESTORE.md](BACKUP_RESTORE.md).

## Troubleshooting

| Symptom | What to check |
|---|---|
| Production preflight fails | Stable `PHLOX_JWT_SECRET`; isolated runner selected and available; see startup error and SANDBOX.md |
| Config edit has no effect | Correct `PHLOX_CONFIG`, restart after file edits, admin DB overrides, saved user/chat choices |
| Connection error / wrong model | Endpoint reachable from the backend, installed/accessible model ID, credentials, and saved provider override; container `localhost` is not the host |
| Tool calls fail or never appear | `supports_tools`, actual model/server tool support, assistant restrictions, Tools enabled/policy, per-prompt search toggles |
| Output ends early / context rejection | Output cap versus context budget, provider ceiling, tool output size; see MODEL_CALLS.md |
| Chat appears locked | Resolve pending approval, wait for Stop, or review/acknowledge interruption; unresolved work prevents edits/new turns |
| Run admission is full | Pending approvals and interruptions count toward limits; resolve them in existing conversations |
| Refresh lost ongoing work | `runs.enabled` defaults false; enable in the active file and restart. Past request-bound work is not made durable retroactively |
| Source missing or unverified | Document readiness, access/assistant visibility, snapshot expiry/deletion, unknown model label; see SOURCES.md |
| Poor document retrieval | Check processing status and extraction quality; an admin can rebuild unknown/changed embedding identities. Keyword-only search may miss paraphrases; see [INGESTION.md](INGESTION.md) |
| Code import fails | Package exists inside the selected execution environment; ephemeral containers do not retain ad-hoc installs |
| MCP connection fails | Command/dependencies installed where Phlox runs, correct transport/URL/credentials, current connection state and tool policy |
| Logged out on restart | Development ephemeral JWT secret; load a stable secret to retain sessions |
| Unexpected empty app | Check `PHLOX_DATA`, `DATABASE_URL`, mounted storage, and login identity before creating replacement data |
| Schema check or data lock failure | Preserve data; check the matching upgrade guide and stop the other application process; no manual stamping |
| Dev page opens but API fails | Backend readiness and Vite proxy target, especially after changing backend ports |

For launcher logs use `.run/logs/backend.log` and `frontend.log`; service/container logs use
`journalctl -u phlox` or `docker compose logs phlox`. Startup logs can contain the temporary
admin password; restrict their access. `/api/health` indicates a running app;
`/api/readiness` also checks the database revision and selected sandbox availability.
Neither certifies model credentials or document retrieval quality; use a connection test
and a representative document question after setup.

## Detailed guides

| Topic | Guide |
|---|---|
| Server installation and containers | [DEPLOYMENT.md](DEPLOYMENT.md), [DOCKER.md](DOCKER.md) |
| Model catalogs and provider setup | [MODEL_DISCOVERY.md](MODEL_DISCOVERY.md) |
| Runs, approvals, document evidence | [RUNS.md](RUNS.md), [APPROVALS.md](APPROVALS.md), [SOURCES.md](SOURCES.md), [INGESTION.md](INGESTION.md) |
| Accounts and Entra ID | [AUTH.md](AUTH.md) |
| Execution environments | [SANDBOX.md](SANDBOX.md) |
| Usage, context, pricing, budgets | [MODEL_CALLS.md](MODEL_CALLS.md), [OBSERVABILITY.md](OBSERVABILITY.md), [BUDGETS.md](BUDGETS.md) |
| Guardrails and API clients | [GUARDRAILS.md](GUARDRAILS.md), [API_GATEWAY.md](API_GATEWAY.md) |
| MCP connections and reusable skills | [MCP.md](MCP.md), [SKILLS.md](SKILLS.md) |
| Migrations and recovery | [BACKUP_RESTORE.md](BACKUP_RESTORE.md) |
| Theme tokens and custom themes | [THEMING.md](THEMING.md) |
| Development and extension | [DEVELOPMENT.md](DEVELOPMENT.md), [ARCHITECTURE.md](ARCHITECTURE.md), [ADDING_A_TOOL.md](ADDING_A_TOOL.md), [ADDING_A_PROVIDER.md](ADDING_A_PROVIDER.md), [AGENTS.md](../AGENTS.md) |
| Plans and implementation history | [ROADMAP.md](ROADMAP.md), [IMPLEMENTATION_WAVES.md](IMPLEMENTATION_WAVES.md), [CODEBASE_REVIEW.md](CODEBASE_REVIEW.md), [archived roadmap](ROADMAP_LEGACY.md) |

## Research mode and chat continuity

**Chat is the default.** Change **Chat** to **Research** in the composer toolbar for a bounded
plan/gather/report workflow over selected documents, the web, or both. This is separate
from the deep-research skill and Agent mode. See [Research mode](RESEARCH.md).

Unsent text drafts survive chat switches and refresh in the same browser tab; logout clears
them. Attachments and mode selections are not restored. Scrolling upward during a reply
pauses automatic following; use **Jump to latest** to return to new output.
