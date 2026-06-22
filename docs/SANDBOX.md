# Code-Execution Sandbox

Phlox runs agent code/shell tools (`execute_python`, `execute_node`, `run_shell`)
through a swappable **`SandboxRunner`** (`backend/app/sandbox/runner.py`). Two
implementations ship; pick one in `config.yml`.

| Runner | Isolation | When to use |
|---|---|---|
| `local` (default) | Subprocess on the host, rooted at the conversation workspace, with timeouts + output caps | Single-user / local / trusted dev |
| `container` | Ephemeral **Podman/Docker** container, resource-limited, network-isolated, workspace bind-mounted | Untrusted input, multi-user, anything shared |

Both capture **artifacts**: any file the run creates in the workspace is returned (shown
inline / in the **Workspace Files** panel). The container runner bind-mounts the
conversation workspace at `/work`, so files land back on the host identically.

## Configure (config.yml)

```yaml
sandbox:
  runner: local            # local | container
  container:
    engine: auto           # auto | podman | docker | <full path to the binary>
    python_image: python:3.12-slim
    node_image: node:22-slim
    memory: 512m
    cpus: "1.0"
    pids_limit: 256
    network: none          # none (most secure) | bridge (allow network, e.g. pip install)
```

The `runner` type and engine come from this file. The **container resource limits**
(`memory`, `cpus`, `pids_limit`, `network`, images) can also be edited **live** by an admin
in **Settings → (Admin) Configuration** — those overrides are stored in the DB, take
precedence over the file, and apply to the next execution with no restart (the cached runner
is rebuilt). The **runner type itself is never UI-editable** — flipping isolation
(`local`↔`container`) at runtime would be a security surprise, so changing `runner:` still
requires editing this file and restarting. If `runner: container` but no engine is found,
Phlox logs a warning and **falls back to `local`** so the app keeps working.

## Container engine setup

The container runner targets the **Docker-compatible CLI**, so it works with **Podman**
(including Podman's Docker-compatibility mode) and **Docker**, on Windows/macOS/Linux.

1. Install Podman or Docker. (On Windows, Podman runs a small WSL2 VM; `podman machine
   start` if needed.) The runner auto-detects the binary on `PATH` and in common install
   locations; or set `container.engine` to an explicit path.
2. Pre-pull the images (first use otherwise blocks on a pull):
   ```bash
   podman pull python:3.12-slim
   podman pull node:22-slim       # only if you use execute_node
   ```
3. Set `sandbox.runner: container` and restart the backend.

## Batteries-included images (opt-in)

Phlox **defaults to the small official `python:3.12-slim` / `node:22-slim` images** so a
fresh install pulls fast and just works. They're bare, though, which is fine for light
scripting but painful for data work.

**Why you'd want the batteries-included images:** every execution runs in a fresh `--rm`
container, so anything the agent `pip install`s does **not** persist to the next call. A
"analyze this CSV and plot it" request then turns into a slow loop of *import error →
install numpy → import error → install pandas → install matplotlib …*, burning tool-call
rounds and only working at all with `network: bridge` (network egress enabled). Baking the
common stack into the image removes that loop entirely **and** lets you switch to
`network: none` — the most secure mode — since the agent no longer needs the network to
fetch packages.

This is **opt-in**: build the images, then point the config at them. Phlox ships the
definitions under [`docker/sandbox/`](../docker/sandbox/):

```bash
./docker/sandbox/build.sh          # builds phlox-sandbox-python:latest + phlox-sandbox-node:latest
# or just Python:  ./docker/sandbox/build.sh python
```

Then point the runner at them in `config.yml`:

```yaml
sandbox:
  runner: container
  container:
    python_image: phlox-sandbox-python:latest
    node_image:   phlox-sandbox-node:latest
    network: bridge          # safe to lock down now — packages are already in the image
```

The Python image includes numpy, pandas, scipy, matplotlib, seaborn, scikit-learn,
statsmodels, openpyxl/xlsxwriter, pypdf, python-docx, pillow, requests/httpx,
beautifulsoup4/lxml, sympy, networkx, tabulate — plus `git/curl/wget/jq` and fonts for
matplotlib. The Node image preinstalls lodash, axios, dayjs, mathjs, d3, csv-parse
(via `NODE_PATH`). Edit the Dockerfiles and re-run `build.sh` to add more; the runner
picks up the new image on the next execution with no Phlox restart (image tag is read at
run time). Add packages your workflows need rather than leaving the agent to install them.

> With packages baked in you can set `network: none` — the most secure mode — because the
> agent no longer needs egress to fetch them. Keep `bridge` only if you still want the
> agent to install ad-hoc packages at runtime.

## Security properties

- **Filesystem:** only the conversation workspace is mounted (`/work`); the rest of the
  host is not visible to the container.
- **Network:** `network: none` blocks all egress (verified). Use `bridge` only if the
  agent legitimately needs the network (e.g. `pip install`), preferably with an internal
  registry/proxy.
- **Resources:** `memory`, `cpus`, and `pids_limit` cap runaway code; each run is
  `--rm` (ephemeral).
- **Timeouts:** every execution has a wall-clock timeout (per tool, default 60s).

> Note: the **local** runner trusts the host and is appropriate only for single-user/local
> or otherwise trusted use. Use the **container** runner for any untrusted or multi-user
> deployment.

## Extending

Add a new isolation strategy (e.g. a remote executor, Firecracker, gVisor) by implementing
`SandboxRunner` and returning it from `get_runner()` in `sandbox/runner.py`. The shared
helpers `snapshot_dir` / `collect_artifacts` handle artifact capture for any runner.
