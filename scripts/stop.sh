#!/usr/bin/env bash
# Phlox — stop any running dev/prod server and free the port(s) (macOS/Linux).
#
# Usage:
#   ./scripts/stop.sh                    # stop the default backend (8000) + frontend (5173)
#   ./scripts/stop.sh --port 8000        # stop just one port
#   ./scripts/stop.sh --quiet            # only print the final summary
#
# Safe to run any time, even if nothing is running — it just confirms the
# ports are free. This is what start.sh calls on Ctrl+C, and what you can run
# by hand if a terminal was closed without stopping the server first.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"

BACKEND_PORT="${PHLOX_BACKEND_PORT:-8000}"
FRONTEND_PORT="${PHLOX_FRONTEND_PORT:-5173}"

QUIET=false
PORTS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --port)
      PORTS+=("$2")
      shift 2
      ;;
    -q|--quiet)
      QUIET=true
      shift
      ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
  esac
done

[ ${#PORTS[@]} -eq 0 ] && PORTS=("$BACKEND_PORT" "$FRONTEND_PORT")

log() { [ "$QUIET" = true ] || echo "$@"; }

ANY_KILLED=false

port_pids() {
  command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$1" -sTCP:LISTEN -t 2>/dev/null || true
}

# 1. Stop anything tracked by a detached start.sh run (best-effort — the port
#    sweep below is the mechanism that's actually guaranteed to free things).
for name in backend frontend; do
  pidfile="$RUN_DIR/$name.pid"
  [ -f "$pidfile" ] || continue
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
    log "Stopping PID $pid (from $name.pid)"
    kill "$pid" 2>/dev/null || true
    ANY_KILLED=true
  fi
  rm -f "$pidfile"
done

# 2. Kill whatever is actually listening on the target port(s), plus up to a
#    few ancestor/descendant processes that look like our own tooling — this
#    catches uvicorn's --reload supervisor/worker pair and npm's vite wrapper,
#    which don't always share a PID with the one holding the socket.
if ! command -v lsof >/dev/null 2>&1; then
  log "lsof not found — can't verify/free ports automatically. Stop the process manually."
else
  looks_like_ours() {
    case "$1" in
      *uvicorn*|*app.main*|*vite*|*"npm-cli"*|*" npm "*|*uv\ run*) return 0 ;;
      *) return 1 ;;
    esac
  }

  for port in "${PORTS[@]}"; do
    for pid in $(port_pids "$port"); do
      # Walk up a few ancestor levels to also catch a supervisor process.
      cur="$pid"
      for _ in 1 2 3; do
        if [ -z "$cur" ] || [ "$cur" = "1" ]; then
          break
        fi
        cmd="$(ps -p "$cur" -o command= 2>/dev/null || true)"
        [ -n "$cmd" ] || break
        if [ "$cur" = "$pid" ] || looks_like_ours "$cmd"; then
          log "Stopping PID $cur on port $port ($cmd)"
          kill "$cur" 2>/dev/null || true
          ANY_KILLED=true
        fi
        cur="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ')"
      done
      # Also kill direct children (covers the case where $pid is the supervisor).
      for child in $(ps -eo pid,ppid 2>/dev/null | awk -v p="$pid" '$2==p{print $1}'); do
        kill "$child" 2>/dev/null || true
        ANY_KILLED=true
      done
    done
  done

  # Give processes a moment to exit gracefully, then escalate to SIGKILL.
  sleep 1
  for port in "${PORTS[@]}"; do
    for pid in $(port_pids "$port"); do
      kill -9 "$pid" 2>/dev/null || true
    done
  done
  sleep 0.3
fi

for port in "${PORTS[@]}"; do
  if [ -n "$(port_pids "$port")" ]; then
    log "Port $port is STILL in use."
  else
    log "Port $port is free."
  fi
done

[ "$ANY_KILLED" = true ] || log "Nothing was running."
