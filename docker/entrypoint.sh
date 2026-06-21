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

# Warn if the JWT secret was left at the insecure default.
if [ -z "$PHLOX_JWT_SECRET" ]; then
    echo "Phlox: WARNING PHLOX_JWT_SECRET is not set — using an insecure default."
    echo "       Set it (e.g. PHLOX_JWT_SECRET=\$(openssl rand -hex 32)) before sharing access."
fi

exec "$@"
