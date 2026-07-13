# koffan stacklet - design

Date: 2026-07-13
Status: approved, ready for implementation

## Purpose

Add a `koffan` stacklet that runs [Koffan](https://github.com/PanSalut/Koffan),
a lightweight shared shopping-list web app, as a family grocery list. Koffan is
a single Go binary with SQLite storage, real-time WebSocket sync, and a PWA
front end. One shared password logs the whole family in.

## Scope

In scope:

- A single-container docker stacklet following the framework conventions.
- Auto-generated shared login password surfaced to the family.
- Caddy route for domain mode.
- Language and timezone inherited from the stack config.

Out of scope (v1):

- Backups. Koffan stores a single mutable SQLite file (`/data/shopping.db`).
  The only implemented backup mechanism is the append-only `[[backup.archive]]`
  (files added, never modified, with a ransomware canary), which does not fit a
  database that is rewritten in place. This mirrors the current state of the
  `photos` stacklet, whose Postgres data is likewise not yet backed up. A
  `[[backup.snapshot]]` primitive is planned framework-wide and would cover
  Koffan later.
- Per-user accounts. Koffan has one shared password by design; we do not layer
  famstack's per-user model on top.
- Bots, CLI plugins, and lifecycle hooks. None are needed.

## Architecture

Single service, no database sidecar (SQLite is embedded).

```
stacklets/koffan/
  stacklet.toml        # manifest
  docker-compose.yml   # one service: stack-koffan
  caddy.snippet        # koffan.{domain} -> koffan:8080
```

### Service

- Container name: `stack-koffan`
- Image: `ghcr.io/pansalut/koffan` (compose tag `:latest`; Watchtower manages
  updates)
- Network: `stack` (external)
- Port: host `42080` -> container `8080`, bound as
  `${PORT_BIND_IP:-127.0.0.1}:42080:8080`
- Volume: `${KOFFAN_DATA_DIR}:/data` (SQLite lives at `/data/shopping.db`)
- `restart: unless-stopped`
- Watchtower label: `com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}`

Port `42080` is the next free slot in the 42xxx convention (42010-42070 are in
use).

## Manifest (`stacklet.toml`)

```toml
id          = "koffan"
name        = "Koffan"
description = "Shared family shopping list (Koffan)"
version     = "0.1.0"
category    = "productivity"
port        = 42080

hints = [
    "Open {url}",
    "Log in with the shared password: {koffan__APP_PASSWORD}",
    "Install it as an app from your phone browser (Add to Home Screen)",
]

[upstream]
image   = "ghcr.io/pansalut/koffan"
channel = "patch"

[env]
generate = ["APP_PASSWORD"]

[env.defaults]
KOFFAN_DATA_DIR = "{data_dir}/koffan"
TZ              = "{timezone}"
DB_PATH         = "/data/shopping.db"
DEFAULT_LANG    = "{language}"
# development keeps cookies working over plain HTTP in port mode (the famstack
# default). production would force the Secure cookie flag and break port-mode
# login. Domain mode still works fine under development.
APP_ENV         = "development"
DISABLE_AUTH    = "false"

[health]
url    = "http://localhost:42080"
expect = "200"
```

Notes:

- `generate = ["APP_PASSWORD"]` makes the framework mint a random password once,
  store it in `.stack/secrets.toml` as `koffan__APP_PASSWORD`, inject it into the
  container env, and expose it to hints as `{koffan__APP_PASSWORD}`. It survives
  across `stack up` runs and is regenerated only after `stack destroy`.
- `DEFAULT_LANG` uses the `{language}` template var. Koffan supports
  pl/en/de/es/fr/pt/uk/no/lt/el/sk/ru and falls back to `en` for anything else.

## Data flow

1. `stack up koffan` renders `.env` from `[env.defaults]`, generating
   `APP_PASSWORD` on first run.
2. Docker Compose starts `stack-koffan`, mounting `{data_dir}/koffan` at `/data`.
3. Koffan serves the PWA on `:8080`; the family reaches it at
   `http://<ip>:42080` (port mode) or `https://koffan.<domain>` (domain mode).
4. Family members log in once per device with the shared password; the cookie
   keeps them signed in.

## Compose (`docker-compose.yml`)

```yaml
name: stack-koffan

services:
  stack-koffan:
    container_name: stack-koffan
    image: ghcr.io/pansalut/koffan:latest
    labels:
      - "com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}"
    networks:
      - stack
    volumes:
      - ${KOFFAN_DATA_DIR}:/data
    environment:
      APP_ENV: ${APP_ENV:-development}
      APP_PASSWORD: ${APP_PASSWORD}
      DISABLE_AUTH: ${DISABLE_AUTH:-false}
      DB_PATH: ${DB_PATH:-/data/shopping.db}
      DEFAULT_LANG: ${DEFAULT_LANG:-en}
      TZ: ${TZ}
    ports:
      - "${PORT_BIND_IP:-127.0.0.1}:42080:8080"
    restart: unless-stopped

networks:
  stack:
    external: true
```

## Caddy route (`caddy.snippet`)

```
koffan.{$FAMSTACK_DOMAIN} {
    reverse_proxy koffan:8080
}
```

Koffan uses a WebSocket for real-time sync; Caddy's `reverse_proxy` upgrades
WebSocket connections automatically, so no extra directive is required.

## Error handling

- Health check hits `http://localhost:42080` and expects `200`; a container that
  fails to start shows as `failing`/`degraded` in `stack list` with the standard
  framework hints.
- No custom hooks means no custom failure paths; the framework's up/down/destroy
  lifecycle applies unchanged.

## Testing

- Add a per-stacklet unit test under `tests/stacklets/` that asserts the manifest
  parses, declares port 42080, generates `APP_PASSWORD`, and renders the expected
  env (mirroring existing per-stacklet tests). No Docker required.
- Follow existing patterns in `tests/stacklets/`; do not invent a new test shape.
- A Docker integration test is not warranted for v1: the stacklet crosses no
  container boundary beyond a plain reverse proxy and adds no bespoke logic.

## Success criteria

1. `stack up koffan` on a fresh instance pulls the image, generates a password,
   starts the container, and passes the health check.
2. The post-up hints print the URL and the shared password.
3. The list persists across `stack down` / `stack up` (SQLite file on the bind
   mount).
4. In domain mode, `koffan.<domain>` reverse-proxies including WebSocket sync.
5. The new per-stacklet test passes under `uv run --extra test pytest
   tests/stacklets/`.
