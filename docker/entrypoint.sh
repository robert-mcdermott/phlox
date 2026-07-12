#!/bin/sh
# Phlox container entrypoint — a couple of startup sanity checks, then exec the server.
#
# Config and data are the project's own bind-mounted files:
#   backend/config.yml  -> /app/backend/config.yml
#   backend/data/       -> /app/backend/data
# (There is no separate container config — it's the same file you'd edit for a host run.)
set -e

CONFIG="/app/backend/config.yml"

if [ ! -f "$CONFIG" ]; then
    echo "Phlox: WARNING no config at $CONFIG."
    echo "       Create it on the host first:  cp backend/config.yml.example backend/config.yml"
    echo "       then edit it (point your provider 'endpoint' at host.docker.internal) and"
    echo "       restart. The app will start with no provider profiles until then."
fi

# Production auth must never start with a missing, short, or known placeholder secret.
case "${PHLOX_JWT_SECRET:-}" in
    ""|change-me|changeme|replace-me|secret|please-change-me-set-a-real-32B+-secret|dev-insecure-change-me-please-set-a-real-32B+-secret)
        echo "Phlox: ERROR PHLOX_JWT_SECRET is missing or is a known placeholder." >&2
        echo "       Generate and persist one with: openssl rand -hex 32" >&2
        exit 1
        ;;
esac
if [ "${#PHLOX_JWT_SECRET}" -lt 32 ]; then
    echo "Phlox: ERROR PHLOX_JWT_SECRET must contain at least 32 bytes." >&2
    exit 1
fi

exec "$@"
