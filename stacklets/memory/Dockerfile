# Dockerfile — family wiki (Quartz v4)
#
# The wiki is a thin shell around an upstream Quartz checkout. We
# clone the Quartz repo at a pinned tag, install its node_modules,
# and overlay our config files. The container's job at runtime is
# `npx quartz build --serve`, which builds the site once and then
# watches the bind-mounted vault and rebuilds on change.
#
# Quartz expects `quartz.config.ts` and `quartz.layout.ts` at the
# repo root — the COPY layer overwrites the ones shipped in the
# upstream tag with our own.

FROM node:22-alpine

# Quartz v4 — pinned. Bump by hand after eyeballing the upstream
# changelog at https://github.com/jackyzha0/quartz/releases. There
# is no upstream Docker image to track, so Watchtower is a no-op
# here; this string is the version surface.
ARG QUARTZ_TAG=v4.5.2

# `git` clones Quartz at build time. `tini` reaps zombies left by
# Quartz's child build process under `--serve`, which forks a
# bundler on every rebuild. Without it, defunct processes pile up.
# `coreutils` gives us GNU `env`: Quartz's CLI shebang uses
# `#!/usr/bin/env -S node ...`, and BusyBox's `env` (Alpine's default)
# doesn't understand `-S`, so the container crash-loops without it.
RUN apk add --no-cache git tini coreutils

WORKDIR /app

# Clone shallow at the pinned tag and install dependencies. The
# repo is the runtime; we do not eject it. `npm ci` uses the
# upstream lockfile so the dep tree is reproducible across builds.
RUN git clone --depth 1 --branch "${QUARTZ_TAG}" https://github.com/jackyzha0/quartz.git . \
 && npm ci

# Mermaid, served from our own host. Upstream's diagram renderer
# imports itself from cdnjs at page-view time; the overlay below points
# it at /static/mermaid instead, and this is where that comes from.
# Same version upstream pins, so the swap is location-only.
#
# Only the minified ESM graph is copied. The full dist is 61MB, mostly
# type declarations, docs and source maps a browser never asks for; the
# entry plus its chunks is 2.5MB, and the browser fetches only the
# chunks a given diagram type actually needs.
RUN npm install --no-save --no-audit --no-fund mermaid@11.4.0 \
 && mkdir -p quartz/static/mermaid/chunks \
 && cp node_modules/mermaid/dist/mermaid.esm.min.mjs quartz/static/mermaid/ \
 && cp -r node_modules/mermaid/dist/chunks/mermaid.esm.min quartz/static/mermaid/chunks/ \
 && find quartz/static/mermaid -name '*.map' -delete

# Overlay our config on top of the upstream defaults. `quartz/*.ts`
# in this stacklet directory is the source of truth for site title,
# theme, plugins, and the "edit on Forgejo" link template.
COPY quartz/quartz.config.ts ./quartz.config.ts
COPY quartz/quartz.layout.ts ./quartz.layout.ts

# The famstack look. `custom.scss` is Quartz's sanctioned override slot
# (upstream ships it near-empty for exactly this) and `fonts.scss`
# carries the @font-face rules, because `fontOrigin: "local"` makes
# Quartz emit no font CSS of its own. `static/` holds the woff2 files,
# which the Static emitter copies into the built site.
COPY quartz/custom.scss ./quartz/styles/custom.scss
COPY quartz/fonts.scss ./quartz/styles/fonts.scss
COPY quartz/static/ ./quartz/static/

# Two upstream components we keep our own copy of, each to remove a
# third-party request no config option can reach. Both carry a comment
# saying what changed and why. On a QUARTZ_TAG bump, re-copy them from
# the new tag and re-apply the edit rather than merging into these.
COPY quartz/components/Head.tsx ./quartz/components/Head.tsx
COPY quartz/components/scripts/mermaid.inline.ts ./quartz/components/scripts/mermaid.inline.ts

# Our own components: the sidebar lockup and the home-page greeting.
# These are additions, not overrides, so a tag bump leaves them alone.
# quartz.layout.ts imports them directly, which is why upstream's
# components/index.ts does not need overlaying.
COPY quartz/components/FamstackTitle.tsx ./quartz/components/FamstackTitle.tsx
COPY quartz/components/Welcome.tsx ./quartz/components/Welcome.tsx

# The entrypoint serves the vault with Quartz — a pure view. The
# curator sidecar owns the git pull. See quartz/entrypoint.sh.
COPY quartz/entrypoint.sh /usr/local/bin/wiki-entrypoint.sh
RUN chmod +x /usr/local/bin/wiki-entrypoint.sh

# Quartz's preview server defaults to port 8080. Node's
# `server.listen(port)` with no host binds 0.0.0.0, so the port
# publishes cleanly to the Docker network without an explicit
# `--bind` flag (which Quartz v4 doesn't expose).
EXPOSE 8080

# `-g` so a stop signal reaches the whole process group — both Quartz
# and the background vault-sync loop — not just Quartz.
ENTRYPOINT ["/sbin/tini", "-g", "--"]
CMD ["/usr/local/bin/wiki-entrypoint.sh"]
