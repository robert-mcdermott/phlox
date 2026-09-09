# Database migrations, backup, and restore

[User Guide](USER_GUIDE.md) · [Project overview](../README.md)

Implemented in [Wave 4](IMPLEMENTATION_WAVES.md). The supported operating model is **one
Phlox process** per data directory/database, with SQLite or Postgres. These commands are
operator tools; they are not exposed through the web API or agent tools.

## Upgrade an existing installation

Install the release's locked dependencies (`uv sync --frozen`, plus `--extra postgres` for
Postgres). Stop the application and any other writers before upgrading. Preserve a complete
backup before first starting the new release. The backup command below can read an
unstamped, compatible prior-version database without upgrading it.

From `backend/`, using the deployment's normal `PHLOX_DATA`, `PHLOX_CONFIG`, and database
environment:

```bash
uv run -m app.ops db status
uv run -m app.ops db check
uv run -m app.ops backup --output /backups/phlox-before-wave4 --stopped
uv run -m app.ops db upgrade
```

`status` reports the current revision and code's head; `check` validates the schema without
changing it. Neither requires stopping the server, though a consistent pre-upgrade check
should follow shutdown. `upgrade` also runs automatically at startup, before bootstrap
accounts, tool preferences, MCP connections, or index work. A migration failure aborts
startup. `/api/readiness` is 503 when the database revision is unavailable/out of date or
the selected sandbox is unavailable.

The current head is `0007_branches`, adding message ancestry and saved conversation
selection ([Conversation alternatives](CONVERSATION_ALTERNATIVES.md)). `0006_projects` adds private projects, per-turn context records, and
a nullable conversation project association ([Projects](PROJECTS.md)). `0005_ingestion` adds nullable document processing, chunk provenance,
and embedding identity metadata for [Wave 7](INGESTION.md). `0004_sources` adds private
evidence snapshots, turn-source links, and nullable message citations; `0003_runs` adds
run/replay/action tables. Revisions `0001_wave3`, `0002_ledger_width`, `0003_runs`, `0004_sources`, `0005_ingestion`, and `0006_projects` remain
readable by check/backup before upgrade. The initial revision, `0001_wave3`, contains a **frozen** schema snapshot and
the previous releases' explicit additive-column allowlist. Existing tables are inspected
for column types/lengths/nullability, primary keys, uniqueness, foreign keys, and named
index shapes before adoption. Only known missing columns and missing application tables /
indexes are created; unknown columns/tables, incompatible constraints/types, and unknown
revision IDs cause failure. Known old approval rows receive their `pending` default; usage
metadata stays nullable. Existing records, ownership, claimed approvals, and costs remain
intact. The specific historical `usage_ledger.message_id VARCHAR(32)` shape is accepted
by check/backup and baseline adoption; revision `0002_ledger_width` widens it to `VARCHAR(64)`.
SQLite rebuilds only that table transactionally; Postgres alters the column in place. Rows,
null values, usage metadata, indexes, and uniqueness are preserved. Already-wide databases
pass through unchanged. This corrects an omission in the initial Wave-4 adoption check;
it does not relax other length/type/constraint checks. Custom SQLite ledger triggers require
a specific preservation migration. No blind `stamp head` or destructive downgrade is provided.

Migration DDL and the revision update share a transaction. SQLite explicitly starts
`BEGIN IMMEDIATE`; Postgres uses a transaction advisory lock. Failure rolls back both,
including first-install DDL. Independent processes serialize on the database; concurrent
Alembic commands within one process also serialize. This does not make the rest of Phlox
multi-worker capable. The [Alembic connection recipe](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands)
explains the transaction integration.

If `check` fails, keep the original untouched and investigate a copy with the matching
release. The diagnostic identifies incompatible schema fields. Customized schemas and narrower types other than the documented ledger-ID compatibility
case are not silently rewritten. Obtain a specific repair
migration, or restore the backup using its original release. Restoring a backup, rather
than dropping columns, is the rollback procedure for this baseline.

## What a backup contains

`backup --output NEW_DIRECTORY --stopped` produces a private directory with:

- `database.sqlite` (SQLite backup API, including committed WAL data, then integrity check)
  or `database.dump` (Postgres custom-format dump). This includes chats, documents/chunks /
  embeddings, citation snapshots/bindings, users, permissions, approval/run state and action evidence, usage, API-key
  hashes, and DB config overlays. Restored runs are not automatically replayed on startup;
  see [interruption recovery](RUNS.md#run-and-restart-behavior).
  Wave 8 web snapshots and failure records use the existing source tables; their retained
  passages, URLs, fetch times and turn bindings are restored without fetching the sites.
  Expiry still applies after restore. Wave 8 introduces no schema revision.
- `data/`: source uploads, images, workspaces, complete Git checkpoint repositories, empty
  directories, and other regular files under the configured data directory.
- `config.yml`: an exact copy of the seed config, including any secrets it contains.
- `manifest.json`: format/app/schema versions, UTC timestamp, file sizes and SHA-256 hashes,
  directory inventory, and **names only** of configured environment variables relevant to
  credentials/deployment. Environment secret values are never copied.

The active SQLite DB/WAL/SHM files are excluded from `data/` because the database snapshot
is stored separately. Embedded Qdrant is excluded; its authoritative chunks and vectors are
in SQL. External Qdrant, external tool data, sandbox/container volumes outside `PHLOX_DATA`,
mounted files outside that directory, environment files, keychain entries, and remote secret
stores are not captured. Symlinks/special files are refused; back up their targets explicitly
through your infrastructure procedure. Preserve external secrets independently, including a
stable `PHLOX_JWT_SECRET`, provider credentials, and credentials for the restored database.

**Protect the entire bundle like the live deployment.** It contains private conversations,
password hashes, provider secrets stored in the DB overlay, and possibly config secrets.
On POSIX the bundle root is mode 0700 and copied files are owner-only (owner execute is
preserved for workspace scripts/hooks). Windows deployments must set equivalent ACLs.
Checksums detect accidental corruption, not authenticity: restore only trusted bundles.
Encryption, off-host replication, retention, and key escrow belong to the operator.

`--stopped` confirms that all app processes and external writers are stopped. A cooperative
file lock refuses a running Wave-4 server or another maintenance operation. Postgres also
uses a database-wide session advisory lock, including when different local data directories
are used. Older releases and external SQL/filesystem writers do not participate in this
lock; stop them explicitly. This is an **offline** coordinated snapshot, not a hot-backup
or point-in-time recovery system. Tools already launched outside the process must finish
before backup. Output directories must be new and outside the source data directory.

## Restore SQLite into a separate instance

Verification is read-only and needs no database credentials or configured live app:

```bash
cd /path/to/phlox/backend
uv run -m app.ops verify /backups/phlox-before-wave4
uv run -m app.ops restore /backups/phlox-before-wave4 \
  --output /srv/phlox-restored --stopped
```

The destination must **not exist**. Restore checks the complete inventory, hashes, paths,
and format before creating a destination; builds it privately; upgrades the restored DB;
then publishes the directory. It never overwrites an existing deployment. Failures remove
temporary files and leave the original bundle/data unchanged.

The result contains `data/`, the unchanged `config.original.yml`, and an adjusted `config.yml`.
The adjusted config points SQLite and embedded Qdrant at the restored directory, including
when the original deployment used an external Qdrant server. It does not reconnect to that
original vector server. Other provider, auth, and integration settings are preserved;
review them and restore external secrets before startup. Existing MCP entries can reconnect
when the app starts. A copy used for testing should use isolated integrations.

Select the restored environment explicitly; an old `DATABASE_URL` would otherwise override
its new SQLite config:

```bash
unset DATABASE_URL
export PHLOX_DATA=/srv/phlox-restored/data
export PHLOX_CONFIG=/srv/phlox-restored/config.yml
# Restore PHLOX_JWT_SECRET and other required secrets through your normal secret manager.
uv run -m app.ops db check
uv run -m app.ops reindex
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
```

`reindex` is offline and rebuilds the configured vector collection from saved embeddings,
without making model calls. On a restored config that target is a new local Qdrant path.
It stages a new collection and records the active collection in SQL before switching; old
collection cleanup is best effort. Check the target environment first. It does not change
embedding models, fill missing vectors, or establish unknown embedding identities. Startup
does not re-embed automatically. Use the admin Documents panel's **Rebuild search index**
to explicitly re-embed ready passages after a model change or legacy-library upgrade.
Restored unfinished document jobs become interrupted; retry them manually. See [INGESTION.md](INGESTION.md).

Check login, a saved chat, document retrieval, attachment downloads, workspace files,
checkpoint history, permissions, and usage before switching traffic. Claimed approvals stay
claimed after restore; they are never automatically replayed. Database restore cannot prove
whether a remote tool action happened before the backup; inspect that system separately.

## Postgres backup and restore

Install the existing `postgres` extra and Postgres client tools (`pg_dump`, `pg_restore`) on
the machine running the command. Use a client version compatible with the server; the
verified CI combination is Postgres **16** with version-16 tools. Pass `--pg-bin-dir PATH`
if they are not on PATH. The app container does not bundle these OS tools: use a maintenance
host that can read the mounted data/config and reach the database, with the app stopped.

The normal configured `DATABASE_URL` selects the backup source. To restore, provision an
**empty, dedicated database** and give its role permission to create objects. Put its URL
in `PHLOX_RESTORE_DATABASE_URL` using your secret manager, not shell history:

```bash
uv run -m app.ops backup --output /backups/phlox-postgres --stopped
uv run -m app.ops restore /backups/phlox-postgres \
  --output /srv/phlox-restored --stopped
```

Use `--database-url-env NAME` for a different environment variable. Restore refuses existing
user tables/views/sequences/functions or custom schemas. It uses `pg_restore --no-owner
--no-privileges --single-transaction --exit-on-error`; source DB roles/grants are not restored.
See the [Postgres restore reference](https://www.postgresql.org/docs/16/app-pgrestore.html).
Connection credentials are passed through the subprocess environment, never command arguments
or manifests. Supported URL options: `sslmode`, `sslrootcert`, `sslcert`, `sslkey`,
`connect_timeout`. This baseline supports the default public schema.

A Postgres transaction and filesystem publication cannot commit atomically together. If the
dump restore succeeds but a later migration/publication fails, the **new destination DB may
remain populated**; no data is dropped automatically. Inspect it, discard/recreate that
explicitly provisioned destination, and retry. The original DB and bundle remain untouched.
After success, explicitly set `DATABASE_URL` to the restored DB and `PHLOX_DATA` /
`PHLOX_CONFIG` to the new directory. The generated Postgres config deliberately has an
unusable placeholder so missing destination credentials cannot reconnect to the source.
Rebuild the index and perform the same smoke checks as SQLite. Cross-database conversion
(SQLite ↔ Postgres), DB-role migration, and managed-service snapshots are outside this tool.

## Extending and verifying migrations

New schema changes require a new file in `backend/app/migrations/versions/`, linked by
`down_revision`, with explicit Alembic operations. Do not change `schema_v1.json` or
`baseline.py` for new features, import evolving ORM tables into old revisions, or add another
startup ALTER-table loop. The final current-schema validation uses ORM metadata. For SQLite
changes needing table reconstruction, use Alembic batch operations and prove data/constraint
preservation on a populated fixture. Use transactional operations; nontransactional DDL
requires a separately designed recovery procedure. Keep one linear revision head.

```bash
cd backend
uv run --frozen --extra dev ruff check app tests
uv run --frozen --extra dev pytest
# Optional local integration run; URL must identify a disposable test server.
# Its role must be allowed to create/drop new, uniquely named test databases.
uv run --frozen --extra dev --extra postgres pytest tests/test_operations.py
```

Set `PHLOX_TEST_POSTGRES_URL` and optionally `PHLOX_TEST_PG_BIN_DIR` for the last command.
Without the URL, PostgreSQL cases skip. CI provisions a disposable Postgres 16 service and
runs both engines. Fixtures cover populated adoption, repeated/concurrent upgrade, schema
mismatch, unknown revisions, failure rollback, file/database locks, checksums/path rejection,
restore into a new DB, real checkpoint preservation, offline index rebuild and ownership
filters, and startup/readiness failure. No live model or user deployment is used.
