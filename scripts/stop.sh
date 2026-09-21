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
STOP_PIDS=()
STOP_COMMANDS=()

stop_pid() {
  local target="$1" command
  command="$(ps -p "$target" -o command= 2>/dev/null || true)"
  [ -n "$command" ] || return 0
  STOP_PIDS+=("$target")
  STOP_COMMANDS+=("$command")
  kill "$target" 2>/dev/null || true
  ANY_KILLED=true
}

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
    stop_pid "$pid"
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
      *uvicorn*|*app.main*|*app.server*|*app.dev*|*vite*|*"npm-cli"*|*" npm "*|*uv\ run*) return 0 ;;
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
          stop_pid "$cur"
        else
          # Do not cross an unrelated process to reach a coincidentally matching ancestor.
          break
        fi
        cur="$(ps -o ppid= -p "$cur" 2>/dev/null | tr -d ' ')"
      done
      # Also kill direct children (covers the case where $pid is the supervisor).
      for child in $(ps -eo pid,ppid 2>/dev/null | awk -v p="$pid" '$2==p{print $1}'); do
        stop_pid "$child"
      done
    done
  done

fi

# Wait for writers, even after they close their listening socket. Give app.server's
# deadline time to run before escalating; never kill a new process that takes the port.
grace="$(awk -v n="${PHLOX_SHUTDOWN_SECONDS:-30}" 'BEGIN { if (n !~ /^[0-9]+([.][0-9]+)?$/ || n < 1 || n > 300) n=30; print int(n)+6 }')"
deadline=$((SECONDS + grace))
while [ ${#STOP_PIDS[@]} -gt 0 ] && [ "$SECONDS" -lt "$deadline" ]; do
  alive=false
  for pid in "${STOP_PIDS[@]}"; do
    state="$(ps -p "$pid" -o stat= 2>/dev/null || true)"
    case "$state" in ""|*Z*) ;; *) alive=true ;; esac
  done
  [ "$alive" = true ] || break
  sleep 0.2
done
for ((i=0; i<${#STOP_PIDS[@]}; i++)); do
  pid="${STOP_PIDS[$i]}"
  command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  if [ -n "$command" ] && [ "$command" = "${STOP_COMMANDS[$i]}" ]; then
    kill -9 "$pid" 2>/dev/null || true
  fi
done

for port in "${PORTS[@]}"; do
  if [ -n "$(port_pids "$port")" ]; then
    log "Port $port is STILL in use."
  else
    log "Port $port is free."
  fi
done

[ "$ANY_KILLED" = true ] || log "Nothing was running."
