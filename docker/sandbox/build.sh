#!/usr/bin/env bash
#
# Build the Phlox batteries-included sandbox images.
#
# These back the CONTAINER sandbox runner (sandbox.runner: container). Point
# backend/config.yml at the resulting tags:
#
#   sandbox:
#     container:
#       python_image: phlox-sandbox-python:latest
#       node_image:   phlox-sandbox-node:latest
#
# Re-run after editing the Dockerfiles to pick up new packages. The runner uses
# whatever the tag currently points at — no Phlox restart needed for image changes.
set -euo pipefail

# Use the same engine the runner is configured for; default to podman, fall back to docker.
ENGINE="${ENGINE:-}"
if [[ -z "$ENGINE" ]]; then
  if command -v podman >/dev/null 2>&1; then ENGINE=podman
  elif command -v docker >/dev/null 2>&1; then ENGINE=docker
  else echo "No podman or docker found on PATH." >&2; exit 1
  fi
fi

# Build context is the dir holding the Dockerfiles (they need no extra files).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ">> Building phlox-sandbox-python:latest with $ENGINE ..."
"$ENGINE" build -f "$DIR/python.Dockerfile" -t phlox-sandbox-python:latest "$DIR"

# Build Node image too unless called as: ./build.sh python
if [[ "${1:-}" != "python" ]]; then
  echo ">> Building phlox-sandbox-node:latest with $ENGINE ..."
  "$ENGINE" build -f "$DIR/node.Dockerfile" -t phlox-sandbox-node:latest "$DIR"
fi

echo ">> Done. Set python_image/node_image in backend/config.yml to the tags above."
