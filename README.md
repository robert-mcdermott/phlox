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

Phlox is a self-hosted AI assistant for conversation, document research, and tool-assisted
work. Connect AWS Bedrock or an OpenAI-compatible endpoint, including local model servers
such as Ollama, LM Studio, and vLLM. Model capabilities depend on the selected provider.

**[User Guide](docs/USER_GUIDE.md)** — installation, first login, providers, configuration,
everyday use, upgrades, and troubleshooting.

![Phlox chat interface](docs/phlox-screenshot.png)

## What you can do

- **Chat and create:** streaming answers, image input, Markdown, code, math, diagrams,
  editable messages, conversation search and export.
- **Work with tools:** code execution, files, shell, plans, sub-agents, and workspace
  checkpoints, with per-tool permissions and human approvals.
- **Return to running work:** opt-in [reconnectable runs](docs/RUNS.md) keep working across
  browser refreshes and chat switches, with explicit Stop and interruption recovery.
- **Research documents:** [track processing and retry uploads](docs/INGESTION.md), search
  documents and tables, and inspect [clickable citations](docs/SOURCES.md) with page/section locations.
- **Research the web:** discover pages with built-in search or SearXNG, then inspect
  [captured web citations](docs/WEB_SOURCES.md) with retained passages, URLs, and fetch times.
- **Reuse workflows:** custom assistants, private/shared skills, memory, and MCP tools.
- **Inspect outputs:** browse workspace files and preview HTML/Markdown in the artifact canvas.
- **Operate your own deployment:** accounts and Entra ID SSO, isolated execution options,
  usage/cost reporting, budgets, guardrails, live admin configuration, and an
  [OpenAI-compatible API gateway](docs/API_GATEWAY.md).

## Quick start

For a local installation, have Git, **uv**, Python **3.11+**, Node/npm (the repository's CI
uses Node **20**), and a reachable model provider. From your checkout:

```bash
git clone https://github.com/robert-mcdermott/phlox.git
cd phlox
./scripts/start.sh dev
```

On Windows PowerShell, run `.\scripts\start.ps1 dev` instead. The launcher installs project
dependencies, creates `backend/config.yml` if absent, and opens **http://localhost:5173**.
It does not install a model server or download models. Edit the example profiles to match
your provider; see [first-time setup](docs/USER_GUIDE.md#first-time-setup).

On a fresh database, use the temporary admin password printed at startup, then replace it
at first login. Stop with Ctrl+C or `./scripts/stop.sh` (Windows: `.\scripts\stop.ps1`).
Existing installations should follow [the upgrade steps](docs/USER_GUIDE.md#upgrades-and-backups)
before starting new code.

To let chat work continue across refreshes, add this top-level setting to your config and
restart. It is off by default and is independent of Agent mode:

```yaml
runs:
  enabled: true
```

With runs enabled, closing a tab does not stop execution; use the chat's **Stop** button.
See [RUNS.md](docs/RUNS.md) for behavior, limits, and recovery.

## Deploy and configure

The local runner executes code with the host application's access. For shared use, choose
an isolated runner. The `prod` launcher requires a stable `PHLOX_JWT_SECRET` and an isolated
runner when authentication is enabled; it is not a drop-in first-run shortcut. Run one
Phlox application process per database/data directory, including with Postgres.

| Guide | Start here for |
|---|---|
| [User Guide](docs/USER_GUIDE.md) | Setup, configuration precedence, usage, troubleshooting, and all feature guides |
| [Linux deployment](docs/DEPLOYMENT.md) | systemd, production configuration, reverse proxy and TLS |
| [Docker / Podman](docs/DOCKER.md) | Application image, persistent mounts, provider networking and sandbox choices |
| [Backup and restore](docs/BACKUP_RESTORE.md) | Existing databases, migrations, offline backups and recovery |
| [Architecture](docs/ARCHITECTURE.md) · [Development](docs/DEVELOPMENT.md) | Code navigation, extensions and verification |
| [Roadmap](docs/ROADMAP.md) · [Delivered waves](docs/IMPLEMENTATION_WAVES.md) | Planned improvements and verified delivery status |

Local inference can keep model requests on your own machines; cloud profiles, web tools,
MCP services, embeddings, and remote execution can send data elsewhere. Choose these
connections deliberately. Regulated/sensitive-data governance remains a separate deployment
track in the roadmap.

## License

Licensed under the **Apache License, Version 2.0** — see [LICENSE](LICENSE).
Copyright © 2026 Robert McDermott &lt;robert.c.mcdermott@gmail.com&gt;.
