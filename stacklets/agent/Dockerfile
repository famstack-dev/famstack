# Dockerfile — the agent runtime: the nanobot harness.
#
# nanobot from PyPI with the Matrix extra (matrix-nio[e2e], which needs libolm).
# config.json and the personality seeds are copied to /app and installed into
# the mounted ~/.nanobot by entrypoint.sh at start, so the bind-mounted volume
# never shadows them and config is refreshed from stack.toml-derived env.

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates git libolm-dev tzdata \
    && rm -rf /var/lib/apt/lists/*

# Pinned, and it has to stay pinned. `runtime/sitecustomize.py` patches
# nanobot's internals (there is no plugin seam), so a release that moves one
# of those symbols detaches a shim. The shims fail soft on purpose — a broken
# one must never stop the agent answering — so the container still starts,
# still reports healthy, and the Matrix channel is simply gone. Unpinned, that
# arrives on whatever unrelated `stack restart agent` happens to rebuild the
# image, which is how 0.2.x moving `nanobot.channels.matrix` from a module to
# a package took a live instance silently off Matrix.
#
# Bump this deliberately, in its own change, and re-verify every symbol in
# sitecustomize.py's PIN / RECHECK list against the new release.
RUN uv pip install --system --no-cache "nanobot-ai[matrix]==0.2.2"

# Non-root, matching nanobot's own image (uid 1000, HOME=/home/nanobot).
RUN useradd -m -u 1000 -s /bin/bash nanobot

# Config template + personality seeds, distributed into ~/.nanobot at start.
COPY config.json /app/config.json
COPY workspace/ /app/workspace-seed/
# Runtime-context shim: per-turn family briefing injected into nanobot's prompt.
# See runtime/README.md for what it patches and why.
COPY runtime/ /app/runtime/
# Container-side `stack` shim: forwards allowlisted commands to the host's
# famstack API (host.docker.internal) and prints the CLI's plaintext output.
# See client/stack and stacklets/core/famstack-api.py.
COPY client/stack /usr/local/bin/stack
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh /usr/local/bin/stack && chown -R nanobot:nanobot /app

USER nanobot
ENV HOME=/home/nanobot
# Put the runtime shim on the path so its sitecustomize.py auto-loads at
# interpreter startup and patches nanobot's per-turn context assembly.
ENV PYTHONPATH=/app/runtime
# Where the container-side `stack` shim reaches the host's famstack API (core).
ENV STACK_API_ADDR=host.docker.internal:42001

ENTRYPOINT ["entrypoint.sh"]
