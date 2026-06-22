# syntax=docker/dockerfile:1
#
# Phlox "batteries-included" Node sandbox image.
#
# Used by the CONTAINER sandbox runner as `node_image` so the agent's execute_node
# tool can require() common libraries without installing them per call.
#
# Build:   ./docker/sandbox/build.sh            (or: podman build -f docker/sandbox/node.Dockerfile -t phlox-sandbox-node:latest .)
# Wire up: set sandbox.container.node_image: phlox-sandbox-node:latest in backend/config.yml
#
# Node resolves bare require('lodash') against NODE_PATH, so we install the common
# libs into a global node_modules and point NODE_PATH at it. The agent's code runs
# from /work (bind-mounted workspace) but still finds these globals.

FROM node:22-slim

# Common utility libraries, installed into a fixed global prefix.
# Versions pinned for reproducible rebuilds — bump deliberately and re-run build.sh.
RUN mkdir -p /opt/node_modules \
    && cd /opt \
    && npm install --no-fund --no-audit --prefix /opt \
        lodash@4.18.1 \
        axios@1.18.0 \
        dayjs@1.11.21 \
        mathjs@15.2.0 \
        d3@7.9.0 \
        csv-parse@7.0.0 \
        csv-stringify@6.8.0 \
    && npm cache clean --force

# require() in agent code (cwd /work) resolves these from the global path.
ENV NODE_PATH=/opt/node_modules

WORKDIR /work
