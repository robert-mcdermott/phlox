# Production deployment on Linux (no container)

This guide runs Phlox **directly on a Linux server** under **systemd** — no container for the
app itself. It covers **Ubuntu 24.04+** and **RHEL 9.7** (and RHEL-family: Rocky/Alma 9).
Steps are identical across both except where a **Ubuntu** / **RHEL** box calls out the
difference.

In production Phlox is a **single process**: FastAPI serves the API *and* the pre-built React
SPA from one Uvicorn process on one port. There is no separate frontend service.

> **Single process is required, not just simpler.** The default embedded Qdrant vector store
> locks its on-disk directory to one process, so do **not** run Uvicorn with `--workers > 1`
> and do not run two instances against the same `backend/data/`.

## Assumptions / prerequisites

Already installed (this guide does **not** cover installing them):

- **`uv`** (Python project/dependency manager) — available on `PATH`.
- **`podman`** — only needed if you enable the container code-execution sandbox (see
  [§8](#8-optional-container-code-execution-sandbox)). The app itself does not need it.
- **Node.js 18+** — needed **once** to build the frontend (`npm run build`). If you don't want
  Node on the server, build `frontend/dist` on another machine and copy it over (see
  [§4](#4-build-the-frontend-spa)).
- **A model provider** reachable from the server — e.g. **Ollama on `localhost:11434`**.
  Because Phlox runs on the host here, use `localhost` in `config.yml` (no
  `host.docker.internal`).
- `git`, a non-root sudo account, and (recommended) a TLS-terminating reverse proxy.

Paths below use `/opt/phlox` and a dedicated `phlox` service user. Adjust to taste.

---

## 1. Create a service user and directory

```bash
sudo useradd --system --create-home --home-dir /opt/phlox --shell /usr/sbin/nologin phlox
```
> **RHEL:** the nologin shell is `/sbin/nologin`. Use
> `--shell /sbin/nologin` instead.

```bash
sudo install -d -o phlox -g phlox /opt/phlox
```

## 2. Clone the repository

```bash
sudo -u phlox git clone https://github.com/robert-mcdermott/phlox.git /opt/phlox/app
cd /opt/phlox/app
```

Everything below runs from `/opt/phlox/app` (the repo root). The backend lives in `backend/`,
the frontend in `frontend/`.

## 3. Install backend dependencies

`uv sync` creates a self-contained virtualenv at `backend/.venv` from the locked
dependencies. `--no-dev` skips the test/lint extras.

```bash
cd /opt/phlox/app/backend
sudo -u phlox uv sync --frozen --no-dev
```

This produces `backend/.venv/bin/uvicorn`, which the systemd unit calls directly.

## 4. Build the frontend SPA

FastAPI serves the **built** SPA from `frontend/dist`. Build it once (and on every upgrade):

```bash
cd /opt/phlox/app/frontend
sudo -u phlox npm ci
sudo -u phlox npm run build        # outputs frontend/dist
```

> **No Node on the server?** Run the two commands above on any machine with Node 18+, then
> copy the result:  `rsync -a frontend/dist/ phlox@server:/opt/phlox/app/frontend/dist/`.

## 5. Configure

### 5a. `config.yml`

```bash
cd /opt/phlox/app/backend
sudo -u phlox cp config.yml.example config.yml
sudo -u phlox $EDITOR config.yml
```

For a host (non-container) deployment, point the provider at `localhost` and pick a model you
actually have pulled:

```yaml
default_profile: local-ollama
profiles:
  local-ollama:
    type: openai
    label: "Ollama (local)"
    endpoint: http://localhost:11434/v1     # localhost is correct when Phlox runs on the host
    api_key: ollama
    model: qwen3:8b                          # a model present in `ollama list`
    supports_tools: true
```

Leave `vector_store` at the embedded default (no Qdrant server) unless you're scaling out.
The database defaults to SQLite too — see [§5d](#5d-optional-postgres-instead-of-sqlite) to
use Postgres instead. Review `auth` and the sandbox section (see §8).

This guide sets `PHLOX_ENV=production`; with authentication enabled, startup therefore
requires `sandbox.runner: container` or `agentcore`. Complete §8 before starting the service.

> **Config precedence:** `config.yml` is only the **seed for a fresh database**. Once a
> provider profile is edited in the app's **Settings → (Admin) Configuration** panel, that
> value is stored in the DB and **overrides `config.yml`**. After go-live, change provider
> settings in the admin UI, not the file.

### 5b. Secrets via an environment file

Keep the JWT secret out of the repo. Create a root-owned, locked-down env file:

```bash
sudo install -d -o root -g phlox -m 750 /etc/phlox
printf 'PHLOX_JWT_SECRET=%s\n' "$(openssl rand -hex 32)" | sudo tee /etc/phlox/phlox.env >/dev/null
sudo chown root:phlox /etc/phlox/phlox.env
sudo chmod 640 /etc/phlox/phlox.env
```

Keep this value **stable** — changing it invalidates all existing login sessions.

### 5c. Data directory ownership

The app writes the SQLite DB (unless you're using Postgres — see below), embedded Qdrant
index, workspaces, uploads, and attachments under `backend/data/`. Make sure the service
user owns the tree:

```bash
sudo install -d -o phlox -g phlox /opt/phlox/app/backend/data
sudo chown -R phlox:phlox /opt/phlox/app
```

### 5d. Optional: Postgres instead of SQLite

SQLite (a single file under `backend/data/`) is the default and needs no setup. For a
deployment that wants a separate, network-reachable database — easier off-box backups,
multiple app instances, an existing Postgres you already run — point Phlox at it instead:

1. Install the driver into the backend venv (skip this if you already ran `uv sync` with
   the extra):
   ```bash
   cd /opt/phlox/app/backend
   sudo -u phlox uv sync --frozen --no-dev --extra postgres
   ```
2. Add `DATABASE_URL` to the same env file as the JWT secret:
   ```bash
   printf 'DATABASE_URL=postgresql+psycopg://phlox:yourpassword@dbhost:5432/phlox\n' \
     | sudo tee -a /etc/phlox/phlox.env >/dev/null
   ```
   (`localhost` works if Postgres runs on the same box.) `DATABASE_URL` takes precedence
   over any `database.url` set in `config.yml` — use whichever you find easier to manage;
   the env var is usually the better fit alongside the JWT secret above.
3. Restart: `sudo systemctl restart phlox`. Tables are created automatically on first boot
   against the new database — there's no separate migration step, but it starts **empty**;
   this doesn't migrate existing SQLite data over (do that out-of-band, e.g.
   `pgloader`, if you're moving an existing deployment rather than starting fresh).

## 6. systemd service

Create `/etc/systemd/system/phlox.service`:

```ini
[Unit]
Description=Phlox AI assistant
After=network-online.target
Wants=network-online.target

[Service]
Type=exec
User=phlox
Group=phlox
WorkingDirectory=/opt/phlox/app/backend
EnvironmentFile=/etc/phlox/phlox.env
Environment=PHLOX_ENV=production
# Bind to loopback and put a TLS reverse proxy in front (see §7).
# To expose Phlox directly instead, change 127.0.0.1 to 0.0.0.0 and open the firewall.
ExecStart=/opt/phlox/app/backend/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=3
TimeoutStopSec=20

# --- Hardening (relax if you enable the podman sandbox; see §8) ---
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/phlox/app/backend/data
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictSUIDSGID=true

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now phlox.service
sudo systemctl status phlox.service
journalctl -u phlox -f            # follow logs
```

Verify locally:

```bash
curl http://127.0.0.1:8000/api/health
curl --fail http://127.0.0.1:8000/api/readiness
```

On a clean database, the startup journal prints a random one-time administrator password.
Sign in with it and Phlox requires a replacement password before any application route is
available. Treat the first-run journal as sensitive and preserve it only until setup succeeds.

## 7. Reverse proxy + TLS (recommended)

Terminate HTTPS at nginx/Caddy and proxy to `127.0.0.1:8000`. Minimal nginx server block
(add TLS via certbot/your CA):

```nginx
server {
    listen 80;
    server_name phlox.example.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # SSE streaming: don't buffer the chat response stream.
        proxy_buffering off;
        proxy_read_timeout 300s;
    }
}
```

> **RHEL / SELinux:** allow nginx to make the upstream connection:
> `sudo setsebool -P httpd_can_network_connect 1`

### Firewall

- **Ubuntu (ufw):**
  ```bash
  sudo ufw allow 'Nginx Full'        # 80+443; or: sudo ufw allow 8000/tcp for direct access
  ```
- **RHEL (firewalld):**
  ```bash
  sudo firewall-cmd --permanent --add-service=http --add-service=https
  # or for direct access:  sudo firewall-cmd --permanent --add-port=8000/tcp
  sudo firewall-cmd --reload
  ```

## 8. Required isolation for shared production

Auth-enabled production refuses `sandbox.runner: local`. Running on the host lets you use
the per-execution container sandbox:
`sandbox.runner: container` in `config.yml` makes Phlox run agent code in ephemeral,
resource-limited **podman** containers (recommended for untrusted/multi-user use). See
[SANDBOX.md](SANDBOX.md). It needs **rootless podman working for the `phlox` user**:

1. **subuid/subgid ranges** (most installers add these automatically; add if missing):
   ```bash
   grep -q '^phlox:' /etc/subuid || sudo usermod --add-subuids 100000-165535 \
       --add-subgids 100000-165535 phlox
   ```
2. **Keep the user's services alive without a login session** (so rootless podman has a
   runtime dir):
   ```bash
   sudo loginctl enable-linger phlox
   ```
   Then add to the `[Service]` section so podman finds its socket/runtime dir:
   ```ini
   Environment=XDG_RUNTIME_DIR=/run/user/%U
   ```
3. **Relax the hardening that breaks rootless podman.** `newuidmap`/`newgidmap` are setuid, so
   in `[Service]` set:
   ```ini
   NoNewPrivileges=false
   RestrictSUIDSGID=false
   ```
4. **Pre-pull the sandbox images** as the `phlox` user (first run otherwise blocks on a pull):
   ```bash
   sudo -u phlox podman pull docker.io/library/python:3.12-slim
   sudo -u phlox podman pull docker.io/library/node:22-slim   # only if you use execute_node
   ```
5. Set `sandbox.runner: container` in `config.yml` and `sudo systemctl restart phlox`.

If no engine is reachable, Phlox logs a warning and falls back to the `local` runner, so the
app keeps working. For a single-user/trusted box, the default `local` runner is fine and needs
none of the above.

## 9. Updating

```bash
cd /opt/phlox/app
sudo -u phlox git pull
sudo -u phlox sh -c 'cd backend  && uv sync --frozen --no-dev'   # backend deps
sudo -u phlox sh -c 'cd frontend && npm ci && npm run build'     # rebuild SPA
sudo systemctl restart phlox
```

The schema migrates on startup on either backend; `backend/data/` persists across upgrades.
Back it up by copying `backend/config.yml` + `backend/data/` while the service is stopped (or
snapshot the DB file) — or, on Postgres, back up the database itself (`pg_dump` et al.) instead
of `backend/data/`.

---

## Distro quick-reference

| | Ubuntu 24.04+ | RHEL 9.7 (Rocky/Alma 9) |
|---|---|---|
| nologin shell | `/usr/sbin/nologin` | `/sbin/nologin` |
| Firewall | `ufw` | `firewalld` |
| Reverse-proxy → app | works out of the box | `setsebool -P httpd_can_network_connect 1` (SELinux) |
| SELinux | not enforcing by default | **enforcing** — files under `/opt` are fine for the service; the podman sandbox + proxy need the booleans noted above |

## Production checklist

- [ ] Changed the seeded `admin`/`admin` password
- [ ] Strong, stable `PHLOX_JWT_SECRET` in `/etc/phlox/phlox.env` (mode 640, `root:phlox`)
- [ ] `auth.enabled: true` (default) — don't disable auth for shared/production use
- [ ] TLS reverse proxy in front; app bound to `127.0.0.1`
- [ ] Provider `endpoint` set to `localhost` and a real `model:` pulled
- [ ] Decided sandbox mode (`local` for trusted single-user, `container` for untrusted)
- [ ] Decided database: SQLite (default) or Postgres (`DATABASE_URL`) — see §5d
- [ ] `backend/data/` (or the Postgres database, if used) backed up on a schedule
- [ ] Single process only (no `--workers`, one instance per data dir)
```
