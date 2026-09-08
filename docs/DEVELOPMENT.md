# Development and verification

[User Guide](USER_GUIDE.md) · [Architecture](ARCHITECTURE.md) · [Agent instructions](../AGENTS.md)

For setup and provider configuration, use the User Guide. Read the architecture before
changing code. The backend uses Python 3.11+, FastAPI and SQLAlchemy, managed with uv;
the frontend uses React, Vite, Tailwind and Zustand. Development runs the backend and Vite
separately; production serves the built SPA from one backend process.

## Local workflow

From the repository root, `./scripts/start.sh dev` (PowerShell: `.\scripts\start.ps1 dev`)
starts both servers. Keep a separate config/data location if testing changes against a
throwaway instance; see [storage paths](USER_GUIDE.md#storage-and-environment-variables).
Do not use a production database for tests or live-model experiments.

## Automated checks

```bash
cd backend
uv sync --frozen --extra dev --inexact
uv run ruff check app tests
uv run pytest

cd ../frontend
npm ci
npm run build
npx playwright install chromium
npm run test:browser
```

Backend tests use isolated data, disabled auth or explicit test identities, and scripted
providers rather than real model credentials. Tests cover API ownership, agent behavior,
approvals, accounting, runs, source citations, and database operations. Chromium scenarios
run the real SPA against synthetic HTTP fixtures, including login, approvals, replay,
source inspection and exports. They do not certify every real provider or deployed service.

The [CI workflow](../.github/workflows/ci.yml) also runs SQLite/Postgres migration and restore
drills against a disposable PostgreSQL 16 service, with native dump/restore tools. Local
Postgres drills require the `postgres` extra, `PHLOX_TEST_POSTGRES_URL`, and optionally
`PHLOX_TEST_PG_BIN_DIR`; the role must be allowed to create disposable test databases.
See [test_operations.py](../backend/tests/test_operations.py) for the fixture contract.
Never point these variables at a production service.

For coverage, from `backend/`:

```bash
uv run pytest --cov=app --cov-report=term-missing
uv run pytest --cov=app --cov-report=html
```

Coverage configuration lives in `backend/pyproject.toml`; the HTML report is under
`backend/htmlcov/`. CI uploads coverage to Codecov. Existing dependency deprecation warnings
and large diagram-library build chunks are separate from test failures.

## Live-model evaluation

The optional evaluation suite calls a real configured provider and can incur costs. With a
separate test config/data environment, from `backend/`:

```bash
uv run -m evals.run_evals
```

See [evals/run_evals.py](../backend/evals/run_evals.py) for cases and arguments. Live checks
are not part of credential-free CI. The [sandbox guide](SANDBOX.md) also includes an explicit
AWS AgentCore integration check using a throwaway data directory.

## Extension guides

- [Adding a tool](ADDING_A_TOOL.md): registry, permission gate, cancellation and source capture.
- [Adding a provider](ADDING_A_PROVIDER.md): provider-neutral message/stream contract.
- [Theming](THEMING.md): semantic tokens and custom themes.
- [Backup and migrations](BACKUP_RESTORE.md): new Alembic revisions and prior-schema compatibility.
- [Roadmap](ROADMAP.md) and [wave log](IMPLEMENTATION_WAVES.md): scope, acceptance and boundaries.

When changing user-visible behavior, update [USER_GUIDE.md](USER_GUIDE.md), the relevant
focused guide, and configuration example comments. Keep the README as the short entry
point. Runtime/bootstrap switches such as `runs.enabled` must be discoverable from the
guide, with their defaults and restart requirements stated explicitly.
