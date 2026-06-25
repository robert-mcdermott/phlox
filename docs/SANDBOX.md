# Code-Execution Sandbox

Phlox runs agent code/shell tools (`execute_python`, `execute_node`, `run_shell`)
through a swappable **`SandboxRunner`** (`backend/app/sandbox/runner.py`). Three
implementations ship; pick one in `config.yml`.

| Runner | Isolation | When to use |
|---|---|---|
| `local` (default) | Subprocess on the host, rooted at the conversation workspace, with timeouts + output caps | Single-user / local / trusted dev |
| `container` | Ephemeral **Podman/Docker** container, resource-limited, network-isolated, workspace bind-mounted | Untrusted input, multi-user, anything shared, on your own host |
| `agentcore` | **Off-host** AWS Bedrock AgentCore Code Interpreter microVM (Firecracker, kernel-level isolation) | Untrusted/multi-user when you'd rather run code off the Phlox host entirely (no local Docker needed) |

All three capture **artifacts**: any file the run creates in the workspace is returned (shown
inline / in the **Workspace Files** panel). The container runner bind-mounts the conversation
workspace at `/work`; the AgentCore runner syncs the local workspace into the remote session
before each run and syncs produced files back out after — so in every case files land back in
the local workspace and Phlox's file tools + artifact detection work identically.

## Configure (config.yml)

```yaml
sandbox:
  runner: local            # local | container | agentcore
  container:
    engine: auto           # auto | podman | docker | <full path to the binary>
    python_image: python:3.12-slim
    node_image: node:22-slim
    memory: 512m
    cpus: "1.0"
    pids_limit: 256
    network: none          # none (most secure) | bridge (allow network, e.g. pip install)
  agentcore:               # only used when runner: agentcore (see below)
    identifier: aws.codeinterpreter.v1
    region: us-west-2
    session_timeout_seconds: 900
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

## Remote runner: AWS Bedrock AgentCore Code Interpreter (`agentcore`)

The `local` and `container` runners both execute agent-generated code **on the Phlox host**
(`container` adds a shared-kernel namespace; `local` runs as the Phlox process). The
`agentcore` runner instead executes it **off-host** in a managed AWS Bedrock AgentCore Code
Interpreter — a Firecracker microVM with **kernel-level isolation** — so untrusted code never
touches the Phlox host at all. Useful for hosted/multi-user Phlox where you'd rather not run
arbitrary code locally and don't want to operate Docker yourself.

**The local workspace stays authoritative.** Phlox's file tools (`read_file`/`write_file`/
`edit_file`/glob/grep) and artifact detection operate on the local `data/workspaces/<id>/`
dir. The runner therefore:

1. opens **one Code Interpreter session per conversation** (lazily, on first run),
2. **syncs the local workspace in** (`writeFiles`) before each run,
3. invokes `executeCode` / `executeCommand`, reading the clean `stdout`/`stderr`/`exitCode`
   from the response's `structuredContent`,
4. **syncs produced/changed files back out** (`listFiles` + `readFiles`) into the local dir,
   where the normal mtime diff picks them up as artifacts. The session's working dir is
   shared and pre-populated (interpreter internals like `node_modules`), so the runner
   snapshots a baseline at session start and only pulls back files that aren't part of it;
   unchanged inputs are not rewritten, so they aren't mis-flagged as artifacts,
5. tears the session down deterministically when the **conversation is deleted** (via the
   additive `SandboxRunner.close_session(workdir)` hook) — no leaked sessions, no TTL reaper.

On **any AWS error** (missing creds, region without Code Interpreter, throttling) the runner
**falls back to the local runner** for that call, so a misconfiguration degrades gracefully
instead of breaking the turn.

### Setup

1. **Credentials.** Uses the standard AWS credential chain (env vars, shared config,
   instance/role). Set `agentcore.profile` to use a named profile instead.
2. **Region.** Set `agentcore.region` to a region where AgentCore Code Interpreter is
   available (e.g. `us-west-2`).
3. **IAM.** The principal needs:
   ```json
   {
     "Effect": "Allow",
     "Action": [
       "bedrock-agentcore:StartCodeInterpreterSession",
       "bedrock-agentcore:InvokeCodeInterpreter",
       "bedrock-agentcore:StopCodeInterpreterSession"
     ],
     "Resource": "*"
   }
   ```
4. Set `sandbox.runner: agentcore` and restart the backend. (`boto3` is already a Phlox
   dependency — it's also used by the Bedrock model provider.)

### Tuning

| Key | Default | Meaning |
|---|---|---|
| `identifier` | `aws.codeinterpreter.v1` | The built-in Code Interpreter to use |
| `region` | _(none)_ | AWS region (else boto3's resolved default) |
| `profile` | _(none)_ | Named AWS profile; else the default credential chain |
| `session_timeout_seconds` | `900` | Idle session TTL (sessions are also closed on conversation delete) |
| `max_sync_file_bytes` | `5000000` | Skip syncing files larger than this in/out (bounds per-call cost) |
| `sync_back` | `true` | Sync session-produced files back into the local workspace |

> **Cost/latency note:** each run does a full workspace sync in (and, by default, out). That's
> cheap for typical scratch workspaces but scales with workspace size; raise/lower
> `max_sync_file_bytes` or set `sync_back: false` for execute-only workloads. Binary files are
> synced as blobs; very large or numerous files are best kept out of the workspace.

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
> or otherwise trusted use. Use the **container** runner (on-host, shared kernel) or the
> **agentcore** runner (off-host, kernel-level microVM isolation) for any untrusted or
> multi-user deployment.

## Extending

Add a new isolation strategy (e.g. another remote executor — E2B, Modal, Daytona, remote
Docker — or Firecracker/gVisor) by implementing `SandboxRunner` and returning it from
`get_runner()` in `sandbox/runner.py`. The shared helpers `snapshot_dir` /
`collect_artifacts` handle artifact capture for any runner. A runner that holds
per-conversation remote state should override the no-op `close_session(workdir)` hook to
release it when the conversation is deleted (as the `agentcore` runner does); `local` and
`container` hold no session and inherit the no-op unchanged. The `agentcore` runner is the
reference implementation for a **remote** backend: local workspace authoritative, sync
in/out around each run, graceful fallback to local on error.
