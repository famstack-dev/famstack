# Dockerfile — memory garden (Quartz v4)
#
# The garden is a thin shell around an upstream Quartz checkout. We
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

# Overlay our config on top of the upstream defaults. `quartz/*.ts`
# in this stacklet directory is the source of truth for site title,
# theme, plugins, and the "edit on Forgejo" link template.
COPY quartz/quartz.config.ts ./quartz.config.ts
COPY quartz/quartz.layout.ts ./quartz.layout.ts

# Quartz's preview server defaults to port 8080. Node's
# `server.listen(port)` with no host binds 0.0.0.0, so the port
# publishes cleanly to the Docker network without an explicit
# `--bind` flag (which Quartz v4 doesn't expose).
EXPOSE 8080

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["npx", "quartz", "build", "--serve", "--directory", "/vault", "--port", "8080"]
