# Running Phlox in a container (Docker or Podman)

Phlox ships as a **single image** that runs both the FastAPI backend and the built
React SPA in one process on **one port (8000)**. Persistent state and the runtime
config live **outside** the image on mounted volumes, so the same image works across
deployments and your data survives `docker rm` / `podman rm` / image rebuilds.

**Docker and Podman are interchangeable here** — Podman's CLI is Docker-compatible, so
every `docker …` command below works as `podman …`. Where the two genuinely differ
(rootless volume permissions, SELinux labels, the host alias), it's called out inline and
summarised in [Podman notes](#podman-notes). If you like, alias it once: `alias docker=podman`.

---

## TL;DR (Compose)

The container uses the project's **own** `backend/config.yml` and `backend/data/` — the
exact same files you'd edit to run Phlox directly on the host. There is **no separate
container config**.

```bash
# from the repo root
cp backend/config.yml.example backend/config.yml   # if you don't already have one
$EDITOR backend/config.yml                          # set provider endpoint + auth (see below)

export PHLOX_JWT_SECRET=$(openssl rand -hex 32)     # keep this stable across restarts
docker compose up -d --build
docker compose logs -f phlox                        # watch it boot
```

**Podman:** use `podman compose` (Podman 4.x+, or install `podman-compose`) — same file,
same commands (`podman compose up -d --build`).

> On **rootless Podman**, files written into `backend/data/` may end up owned by a
> high-numbered subordinate UID on the host. That's expected — see [Podman notes](#podman-notes)
> for `:U`/`--userns=keep-id` if it gets in your way.

Open **http://localhost:8000** and sign in with the seeded admin **`admin` / `admin`**
(change it immediately under Settings → Admin → Users).

> **`backend/config.yml` must exist before you start** (the compose file bind-mounts it).
> If it's missing, create it from `config.yml.example` first — otherwise the container
> boots with no provider profiles.

---

## What goes where

The image holds only code. Everything that changes per-deployment or must persist is the
project's own `backend/` files, bind-mounted into the container:

| Thing | Location (host → container) | In image? | Why |
|---|---|---|---|
| App code + built SPA | baked into image | ✅ yes | immutable, rebuilt per release |
| **`config.yml`** | `backend/config.yml` → `/app/backend/config.yml` | ❌ **no** (mounted) | environment-specific endpoints + secrets; editable without a rebuild |
| SQLite DB (`phlox.db`) | `backend/data/` → `/app/backend/data` | ❌ no (mounted) | your data must persist |
| Embedded Qdrant (`backend/data/qdrant`) | `backend/data/` → `/app/backend/data` | ❌ no (mounted) | vector index persists |
| workspaces / uploads / attachments | `backend/data/` → `/app/backend/data` | ❌ no (mounted) | agent files + uploads persist |

### One config file, not two

The container deliberately mounts the project's **own** `backend/config.yml` (and
`backend/data/`) rather than introducing a separate container-only config. So there's a
single source of truth: the same file works whether you run Phlox in a container or directly
on the host. The image's defaults (`PHLOX_CONFIG=/app/backend/config.yml`,
`PHLOX_DATA=/app/backend/data`) already match the app's native paths, so the bind mounts line
up with no env-var overrides.

The **one value that depends on how you run it** is the provider `endpoint`: use
`host.docker.internal` when Phlox is containerized, or `localhost` when running directly on
the host (see the next section). Secrets stay out of the image because the file is mounted,
not baked in.

> **Don't run a host instance and the container against this same `backend/data/` at the
> same time** — the SQLite DB and embedded Qdrant lock to a single process. Run one at a time
> (the app is single-process by design), or give one of them a separate data dir.

---

## Reaching a LOCAL model endpoint (Ollama / LM Studio / vLLM)

Inside a container, `localhost` is **the container**, not your host. If Ollama/LM Studio
runs on the host, point Phlox at **`host.docker.internal`** instead of `localhost` in
`config.yml`:

```yaml
default_profile: local-ollama
profiles:
  local-ollama:
    type: openai
    label: "Ollama (host)"
    endpoint: http://host.docker.internal:11434/v1   # NOT localhost
    api_key: ollama
    model: qwen3:8b
    supports_tools: true
```

- **Docker Desktop (macOS/Windows):** `host.docker.internal` resolves automatically.
- **Docker on Linux:** the compose file adds `extra_hosts: ["host.docker.internal:host-gateway"]`,
  which makes it resolve. (With plain `docker run`, add `--add-host=host.docker.internal:host-gateway`.)
- **Podman:** also honours the `host.docker.internal:host-gateway` mapping in the compose
  file. In addition, Podman provides **`host.containers.internal`** out of the box (no
  `extra_hosts` needed) — you can use either hostname in `config.yml`.
- **Alternative on Linux only:** `--network host` (`--network=host` for `podman run`) lets the
  container use the host's network stack so `localhost:11434` works directly (not supported on
  Docker Desktop).

Also make sure the server listens on all interfaces, not just loopback — e.g. run Ollama with
`OLLAMA_HOST=0.0.0.0`.

> **⚠️ Editing `config.yml` may not be enough — the database overlay wins.** Once a provider
> profile has been saved or edited in the app's **Settings → (Admin) Configuration** panel,
> Phlox stores it in the database, and that overlay **takes precedence over `config.yml`**.
> `config.yml` is only the seed for a *fresh* database. So if you change the `endpoint` in
> `config.yml` but the app keeps hitting the old address (symptom: **"Model error: Connection
> error."** because it's still dialing `localhost` from inside the container), fix it in
> **Settings → (Admin) Configuration → provider profiles** instead — that applies live, no
> restart. This catches almost everyone who containerizes an existing Phlox instance.

---

## `docker run` / `podman run` (without Compose)

Identical flags for both — swap `docker` for `podman`. On **rootless Podman with SELinux**
(Fedora/RHEL/CentOS), append **`:Z`** to the bind mounts so the container can read/write them
(it relabels the host dirs). Harmless to include elsewhere; omit on systems without SELinux.

```bash
docker build -t phlox:latest .          # or: podman build -t phlox:latest .

# Config must exist on the host first (it's bind-mounted, not seeded):
cp backend/config.yml.example backend/config.yml   # if you don't have one
$EDITOR backend/config.yml                          # endpoint -> host.docker.internal, set auth

docker run -d --name phlox \
  -p 8000:8000 \
  -v "$PWD/backend/config.yml:/app/backend/config.yml" \
  -v "$PWD/backend/data:/app/backend/data" \
  -e PHLOX_JWT_SECRET="$(openssl rand -hex 32)" \
  --add-host=host.docker.internal:host-gateway \
  phlox:latest
```

Podman equivalent (note `:Z` on the mounts for SELinux hosts):

```bash
podman run -d --name phlox \
  -p 8000:8000 \
  -v "$PWD/backend/config.yml:/app/backend/config.yml:Z" \
  -v "$PWD/backend/data:/app/backend/data:Z" \
  -e PHLOX_JWT_SECRET="$(openssl rand -hex 32)" \
  --add-host=host.docker.internal:host-gateway \
  phlox:latest
```

---

## Environment variables

| Variable | Default (in image) | Purpose |
|---|---|---|
| `PHLOX_JWT_SECRET` | *(required)* | Strong, stable 32B+ secret; production startup fails if it is missing or a placeholder |
| `SEARXNG_URL` | — | Optional: use SearXNG for web search instead of the default ddgs |
| `PHLOX_CONFIG` | `/app/backend/config.yml` | Config path. Left at default so the mounted `backend/config.yml` is used — override only for an unusual layout |
| `PHLOX_DATA` | `/app/backend/data` | Data root. Left at default so the mounted `backend/data/` is used — override only for an unusual layout |
| `DATABASE_URL` | — (SQLite under `PHLOX_DATA`) | Set to deploy against Postgres instead, e.g. `postgresql+psycopg://user:pass@host:5432/phlox` — see below |

---

## Optional: Qdrant as a server (instead of embedded)

Embedded Qdrant locks its on-disk directory to a single process, which is fine for one
single-container instance. To move to a Qdrant **server** (e.g. before scaling out):

```bash
docker compose --profile with-qdrant up -d --build
```

Then in `config.yml`:

```yaml
vector_store:
  url: "http://qdrant:6333"     # compose service DNS name; no host networking needed
  collection: doc_chunks
```

SQLite remains the source of truth, so you can rebuild the index after switching
(`reindex_all`). `vector_store` is a bootstrap setting — change it in the file and restart.

## Optional: Postgres (instead of SQLite)

Phlox defaults to a SQLite file under `backend/data/` — nothing to configure. To point it
at Postgres instead, set `DATABASE_URL`. The image already bundles the `psycopg` driver, so
no rebuild is needed to switch.

A `postgres` service is included behind a profile if you want Compose to run it for you:

```bash
export DATABASE_URL="postgresql+psycopg://phlox:phlox@postgres:5432/phlox"
docker compose --profile with-postgres up -d --build
```

Phlox connects via the service name `postgres:5432` (Docker's internal DNS) — no
`localhost` involved. A Postgres running on the **host** instead would use
`host.docker.internal:5432` (`DATABASE_URL=postgresql+psycopg://user:pass@host.docker.internal:5432/phlox`),
exactly like the local-LLM case above. Running Phlox directly on the host (no container)
works the same way — just export `DATABASE_URL` before starting uvicorn, pointed at
`localhost:5432` instead.

Tables and any schema updates are created/applied automatically at startup — no separate
migration step. Switching backends does **not** migrate existing data between them; pick
one at deployment time (or migrate data out-of-band if you need to move later).

---

## Code-execution sandbox

**When Phlox runs in a container, use `sandbox.runner: local` (the default).** This is the
only supported sandbox mode for the dockerized deployment.

| Deployment | Sandbox mode | What isolates the code |
|---|---|---|
| **Dockerized** (this guide) | `local` only | The **Phlox container itself** — agent code runs as a subprocess inside it, not on your host |
| **On the host** (run directly, no container) | `local` or `container` | `container` mode spawns ephemeral, resource-limited sandbox containers per execution |

With the dockerized deployment, `local` already means "code runs inside the Phlox
container, not on the host machine" — a meaningful boundary for single-user/trusted use.
`execute_python` and `run_shell` work out of the box; `execute_node` needs Node in the
image (uncomment the `nodejs` install in the `Dockerfile`).

**`sandbox.runner: container` is not supported inside a container** and is intentionally
left undocumented for that case: it would require Phlox to drive the host's container engine
over a mounted socket *and* keep workspace bind-mount paths identical inside the container
and on the host — fragile, and it silently mounts the wrong directory if you get it wrong.
If you set `runner: container` in a dockerized instance with no engine reachable, Phlox logs
a warning and falls back to `local` automatically.

> **Need per-execution container isolation** (untrusted input, multi-user)? **Run Phlox
> directly on the host** (see the top-level [README](../README.md)) and set
> `sandbox.runner: container` — see [SANDBOX.md](SANDBOX.md). That's the supported path for
> strong per-run isolation with CPU/memory/PID caps.

---

## Common operations

Swap `docker` for `podman` throughout.

```bash
docker compose ps                 # status
docker compose logs -f phlox      # logs
docker compose restart phlox      # restart (e.g. after editing config.yml)
docker compose down               # stop & remove container (backend/config.yml + backend/data persist)
docker compose up -d --build      # rebuild after pulling new code

# Back up everything: just copy the host files
tar czf phlox-backup.tgz backend/config.yml backend/data
```

## Podman notes

Podman is a drop-in for the commands above; these are the only real differences.

- **Compose provider:** `podman compose` ships with Podman 4.x+. On older versions install
  [`podman-compose`](https://github.com/containers/podman-compose) (`pip install podman-compose`)
  and run `podman-compose …`. The `docker-compose.yml` here needs no changes.
- **SELinux bind mounts (Fedora/RHEL/CentOS):** add **`:Z`** to volumes so the container can
  access them, e.g. `-v "$PWD/backend/data:/app/backend/data:Z"`. With Compose, append it in
  the file (`./backend/data:/app/backend/data:Z`) or disable labeling per-service with
  `security_opt: ["label=disable"]`.
- **Rootless volume ownership:** rootless Podman maps container UID 0 to your host user via a
  subordinate-UID range, so files in `backend/data/` may show as owned by a high UID on the
  host. Options: add **`:U`** to a mount to have Podman chown it into the namespace
  (`-v "$PWD/backend/data:/app/backend/data:U"`), or run with **`--userns=keep-id`** so the
  container user maps back to your host UID. Plain reads/writes work fine either way.
- **Host alias:** use Podman's built-in **`host.containers.internal`** (no `extra_hosts`
  needed), or keep `host.docker.internal` via the `host-gateway` mapping already in the
  compose file — both resolve to the host.
- **Rootless port binding:** publishing `8000` works rootless. To bind a privileged port
  (<1024) rootless, lower `net.ipv4.ip_unprivileged_port_start` or run via a reverse proxy.

## Health check

```bash
curl http://localhost:8000/api/health    # -> {"status":"ok","tools":N}
```
