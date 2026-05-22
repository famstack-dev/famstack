# pi-dev: sandboxed pi coding agent.
#
# pi and any npm package it pulls runs as an unprivileged user inside this
# image. The host is protected by mounts and runtime flags chosen in the
# launcher (--cap-drop, --read-only, --security-opt), not by anything in
# this Dockerfile.

FROM node:20-bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        git ripgrep ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# Install pi at build time so runtime never invokes npm. --ignore-scripts
# is pi's own recommendation: it blocks postinstall hooks across the dep
# tree.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent \
 && npm cache clean --force

# The base image already provides an unprivileged `node` user (UID 1000).
# Reuse it rather than creating a duplicate.
USER node
WORKDIR /workspace

CMD ["pi"]
