# Phlox Backend

FastAPI backend for Phlox. Start with the [User Guide](../docs/USER_GUIDE.md) for first login,
providers, configuration precedence, runs, and existing-data upgrades. For development,
read [Architecture](../docs/ARCHITECTURE.md) and [Development](../docs/DEVELOPMENT.md).

From this directory, after creating/editing `config.yml` **only if it does not already exist**:

```bash
uv sync --frozen --inexact
uv run -m app.dev --host 127.0.0.1 --port 8000
```

Run Vite separately for development, or use the repository launchers to start both servers.
Startup runs checked Alembic migrations; preserve a backup before upgrading an existing
installation. See [BACKUP_RESTORE.md](../docs/BACKUP_RESTORE.md). Production requires the
secret and execution isolation described in the User Guide.
