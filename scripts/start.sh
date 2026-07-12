#!/usr/bin/env bash
# Phlox — one-command build & start for macOS/Linux.
#
# Usage:
#   ./scripts/start.sh [dev|prod] [options]
#
# Modes:
#   dev    Backend with --reload (:8000) + Vite dev server (:5173), hot reload. Default.
#   prod   Builds the SPA once; a single Uvicorn process serves the API + SPA (:8000).
#
# Options:
#   -d, --detach       Start in the background and return immediately. Use stop.sh later.
#   --no-browser       Don't auto-open the app in a browser.
#   -h, --help         Show this help.
#
# What it does for you:
#   - Checks that `uv` is installed (tells you how to install it if not).
#   - Checks that Node/npm are installed (tells you how to install them if not).
#   - Runs `uv sync` for the backend and `npm install` for the frontend if needed.
#   - Creates backend/config.yml from the example on first run.
#   - Frees the port(s) if a previous run is still holding them.
#   - Starts the server(s), waits until they're actually ready, then opens your browser.
#   - Ctrl+C cleanly stops everything and frees the port(s) again.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT/.run"
LOG_DIR="$RUN_DIR/logs"

BACKEND_PORT="${PHLOX_BACKEND_PORT:-8000}"
FRONTEND_PORT="${PHLOX_FRONTEND_PORT:-5173}"

# ── output helpers ───────────────────────────────────────────────────────────
if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; RED=$'\033[31m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; CYAN=$'\033[36m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; RED=""; GREEN=""; YELLOW=""; CYAN=""; RESET=""
fi
step()    { echo "${BOLD}${CYAN}==>${RESET} $*"; }
info()    { echo "    $*"; }
success() { echo "${GREEN}✔${RESET} $*"; }
warn()    { echo "${YELLOW}!${RESET} $*"; }
error()   { echo "${RED}✘${RESET} $*" >&2; }

# ── argument parsing ─────────────────────────────────────────────────────────
MODE="dev"
DETACH=false
NO_BROWSER=false

for arg in "$@"; do
  case "$arg" in
    dev|prod) MODE="$arg" ;;
    -d|--detach) DETACH=true ;;
    --no-browser) NO_BROWSER=true ;;
    -h|--help)
      sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      error "Unknown argument: $arg"
      echo "Run '$0 --help' for usage."
      exit 1
      ;;
  esac
done

mkdir -p "$LOG_DIR"

# ── 1. prerequisite checks ───────────────────────────────────────────────────
step "Checking prerequisites..."

if ! command -v uv >/dev/null 2>&1; then
  error "uv is not installed. Phlox's backend uses uv to manage Python and its dependencies."
  echo
  echo "  Install it with:"
  echo "    curl -LsSf https://astral.sh/uv/install.sh | sh"
  echo
  echo "  Then open a new terminal (or run 'source \$HOME/.local/bin/env') and re-run this script."
  echo "  Docs: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi
success "uv found ($(uv --version))"

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  error "Node.js/npm is not installed. It's needed to build/run the frontend."
  echo
  echo "  Install Node 18+ from https://nodejs.org/, or via a version manager:"
  echo "    https://github.com/nvm-sh/nvm"
  echo "  macOS (Homebrew):  brew install node"
  exit 1
fi
success "Node found ($(node --version))"

# ── 2. backend dependencies ──────────────────────────────────────────────────
step "Checking backend dependencies..."
FIRST_BACKEND_SETUP=false
[ -d "$ROOT/backend/.venv" ] || FIRST_BACKEND_SETUP=true
if [ "$FIRST_BACKEND_SETUP" = true ]; then
  info "First run — installing backend dependencies with uv (this can take a minute)..."
fi
if ! (cd "$ROOT/backend" && uv sync --inexact); then
  error "uv sync failed. See the output above for details."
  exit 1
fi
success "Backend dependencies ready"

if [ ! -f "$ROOT/backend/config.yml" ]; then
  cp "$ROOT/backend/config.yml.example" "$ROOT/backend/config.yml"
  success "Created backend/config.yml from the example (defaults to a local Ollama profile)"
  info "Edit backend/config.yml to point at your model provider, or change it later"
  info "in the app under Settings → Admin → Configuration."
fi

# ── 3. frontend dependencies ─────────────────────────────────────────────────
if [ ! -d "$ROOT/frontend/node_modules" ]; then
  step "Installing frontend dependencies (npm install)..."
  if ! (cd "$ROOT/frontend" && npm install); then
    error "npm install failed. See the output above for details."
    exit 1
  fi
  success "Frontend dependencies ready"
else
  success "Frontend dependencies already installed"
fi

# ── 4. free any ports left over from a previous run ─────────────────────────
PORTS_NEEDED=("$BACKEND_PORT")
[ "$MODE" = "dev" ] && PORTS_NEEDED+=("$FRONTEND_PORT")

port_busy() {
  command -v lsof >/dev/null 2>&1 && lsof -nP -iTCP:"$1" -sTCP:LISTEN -t >/dev/null 2>&1
}

for port in "${PORTS_NEEDED[@]}"; do
  if port_busy "$port"; then
    warn "Port $port is already in use — stopping the existing process first."
    "$ROOT/scripts/stop.sh" --quiet --port "$port"
    sleep 1
    if port_busy "$port"; then
      error "Port $port is still in use by another application. Free it manually and re-run."
      exit 1
    fi
  fi
done

# ── 5. build (prod only) ─────────────────────────────────────────────────────
if [ "$MODE" = "prod" ]; then
  step "Building the frontend for production (npm run build)..."
  if ! (cd "$ROOT/frontend" && npm run build); then
    error "Frontend build failed. See the output above for details."
    exit 1
  fi
  success "Frontend built to frontend/dist"
fi

# ── 6. start the backend ─────────────────────────────────────────────────────
BACKEND_LOG="$LOG_DIR/backend.log"
: > "$BACKEND_LOG"

step "Starting the backend on :$BACKEND_PORT..."
if [ "$MODE" = "prod" ]; then
  PHLOX_RUN_ENV="production"
else
  PHLOX_RUN_ENV="development"
fi
if [ -t 1 ]; then
  PHLOX_BANNER_COLOR=1
else
  PHLOX_BANNER_COLOR=0
fi
if [ "$MODE" = "dev" ]; then
  ( cd "$ROOT/backend" && export PHLOX_ENV="$PHLOX_RUN_ENV" PHLOX_FORCE_COLOR="$PHLOX_BANNER_COLOR" PHLOX_STARTUP_CAPTURE_MARKERS=1 && exec nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" --reload ) >>"$BACKEND_LOG" 2>&1 </dev/null &
else
  ( cd "$ROOT/backend" && export PHLOX_ENV="$PHLOX_RUN_ENV" PHLOX_FORCE_COLOR="$PHLOX_BANNER_COLOR" PHLOX_STARTUP_CAPTURE_MARKERS=1 && exec nohup uv run uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" ) >>"$BACKEND_LOG" 2>&1 </dev/null &
fi
BACKEND_PID=$!
echo "$BACKEND_PID" > "$RUN_DIR/backend.pid"

wait_for_http() {
  local url="$1" pid="$2" logfile="$3" tries="${4:-60}"
  local i=0
  while [ "$i" -lt "$tries" ]; do
    if ! kill -0 "$pid" 2>/dev/null; then
      error "Process exited unexpectedly. Last log lines ($logfile):"
      tail -n 40 "$logfile" 2>/dev/null || true
      return 1
    fi
    if curl -fsS -o /dev/null "$url" 2>/dev/null; then
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

if ! wait_for_http "http://localhost:$BACKEND_PORT/api/health" "$BACKEND_PID" "$BACKEND_LOG" 60; then
  error "Backend didn't become ready in time. Check $BACKEND_LOG"
  kill "$BACKEND_PID" 2>/dev/null || true
  exit 1
fi
success "Backend ready on http://localhost:$BACKEND_PORT"

# ── 7. start the frontend dev server (dev mode only) ─────────────────────────
FRONTEND_PID=""
if [ "$MODE" = "dev" ]; then
  FRONTEND_LOG="$LOG_DIR/frontend.log"
  : > "$FRONTEND_LOG"
  step "Starting the frontend dev server on :$FRONTEND_PORT..."
  ( cd "$ROOT/frontend" && export PORT="$FRONTEND_PORT" && exec nohup npm run dev ) >>"$FRONTEND_LOG" 2>&1 </dev/null &
  FRONTEND_PID=$!
  echo "$FRONTEND_PID" > "$RUN_DIR/frontend.pid"

  if ! wait_for_http "http://localhost:$FRONTEND_PORT/" "$FRONTEND_PID" "$FRONTEND_LOG" 60; then
    error "Frontend didn't become ready in time. Check $FRONTEND_LOG"
    kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
    exit 1
  fi
  success "Frontend ready on http://localhost:$FRONTEND_PORT"
fi

# ── 8. open the browser ──────────────────────────────────────────────────────
if [ "$MODE" = "dev" ]; then
  APP_URL="http://localhost:$FRONTEND_PORT"
else
  APP_URL="http://localhost:$BACKEND_PORT"
fi

if [ "$NO_BROWSER" = false ]; then
  if command -v open >/dev/null 2>&1; then
    open "$APP_URL" >/dev/null 2>&1 &
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL" >/dev/null 2>&1 &
  elif command -v wslview >/dev/null 2>&1; then
    wslview "$APP_URL" >/dev/null 2>&1 &
  else
    info "Open $APP_URL in your browser."
  fi
fi

# ── 9. banner ─────────────────────────────────────────────────────────────────
awk '
  /^PHLOX_STARTUP_BEGIN$/ { showing=1; next }
  /^PHLOX_STARTUP_END$/ { showing=0; next }
  showing { print }
' "$BACKEND_LOG"
echo "${GREEN}${BOLD}Phlox is running.${RESET}"
echo "  App:      ${CYAN}${BOLD}$APP_URL${RESET}"
if [ "$MODE" = "dev" ]; then
  echo "  API:      http://localhost:$BACKEND_PORT  ${DIM}(hot-reload dev server)${RESET}"
else
  echo "  Mode:     production build (single process)"
fi
echo

if [ "$DETACH" = true ]; then
  disown "$BACKEND_PID" 2>/dev/null || true
  [ -n "$FRONTEND_PID" ] && { disown "$FRONTEND_PID" 2>/dev/null || true; }
  echo "Running in the background."
  echo "  Logs:  $LOG_DIR/"
  echo "  Stop:  ./scripts/stop.sh"
  exit 0
fi

echo "Press ${BOLD}Ctrl+C${RESET} to stop and free the port(s)."

cleanup() {
  trap - EXIT INT TERM
  echo
  step "Shutting down..."
  "$ROOT/scripts/stop.sh" --quiet --port "$BACKEND_PORT" >/dev/null 2>&1 || true
  [ "$MODE" = "dev" ] && { "$ROOT/scripts/stop.sh" --quiet --port "$FRONTEND_PORT" >/dev/null 2>&1 || true; }
  success "Stopped. Port(s) freed."
}
trap cleanup EXIT INT TERM

wait "$BACKEND_PID" 2>/dev/null
[ -n "$FRONTEND_PID" ] && wait "$FRONTEND_PID" 2>/dev/null
true
