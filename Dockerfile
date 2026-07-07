# syntax=docker/dockerfile:1

# ─────────────────────────────────────────────────────────────────────────────
# Phlox — single-image build.
#
# Stage 1 builds the React/Vite SPA into frontend/dist.
# Stage 2 installs the FastAPI backend (via uv) and serves both the API and the
# built SPA from one process on one port (8000).
#
# Persistent data (SQLite DB by default, or Postgres via DATABASE_URL + embedded Qdrant +
# workspaces/uploads/attachments) and the runtime config.yml live OUTSIDE the image on
# mounted volumes — the project's own backend/config.yml and backend/data/ are bind-mounted
# in. See docs/DOCKER.md.
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: build the frontend ──────────────────────────────────────────────
FROM node:20-slim AS frontend
WORKDIR /app/frontend

# Install deps first (cached unless the lockfile changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the SPA -> /app/frontend/dist
COPY frontend/ ./
RUN npm run build


# ── Stage 2: backend runtime ─────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

# uv: fast, reproducible Python dependency install from uv.lock.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Runtime OS deps:
#   git  -> workspace checkpoints are git-backed
#   tini -> proper PID 1 / signal handling
# (Add nodejs here too if you need the execute_node tool with the LOCAL sandbox.)
RUN apt-get update \
    && apt-get install -y --no-install-recommends git tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend

# The app resolves the SPA at BACKEND_DIR.parent/frontend/dist, so the backend
# MUST live at /app/backend and the built SPA at /app/frontend/dist.
#
# We intentionally do NOT override PHLOX_CONFIG / PHLOX_DATA: the app defaults to
# /app/backend/config.yml and /app/backend/data, and the compose file bind-mounts the
# project's own backend/config.yml and backend/data/ there. That keeps ONE config file
# and one data dir, used whether you run Phlox in a container or directly on the host.
ENV UV_PROJECT_ENVIRONMENT=/app/backend/.venv \
    PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

# Install Python deps first (cached unless pyproject/uv.lock change). Includes the
# `postgres` extra (psycopg) so DATABASE_URL can point at Postgres with no rebuild —
# SQLite (the default) needs no driver, so this only adds a few MB either way.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --extra postgres

# App source.
COPY backend/app ./app

# Built SPA from stage 1.
COPY --from=frontend /app/frontend/dist /app/frontend/dist

# Thin entrypoint: a couple of startup sanity checks, then exec the server.
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

EXPOSE 8000
VOLUME ["/app/backend/data"]

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
