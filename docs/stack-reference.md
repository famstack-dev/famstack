# Stacklet Reference

A stacklet is the unit of deployment. The `stack` CLI is the runtime. The
runtime discovers stacklets by walking the filesystem, reads their manifests,
and manages their lifecycle. No central registry — if it's a directory under
`stacklets/` with a `stacklet.toml`, it exists.

Think Spring Boot for self-hosted services on a Mac.

---

## Directory Structure

A stacklet is a directory under `stacklets/` containing at minimum a
`stacklet.toml` manifest. Everything else is optional — include only what
you need.

```
stacklets/photos/
  stacklet.toml          ← manifest (required)
  docker-compose.yml     ← container definitions
  caddy.snippet          ← reverse proxy route (domain mode)
  hooks/
    on_configure.py      ← first run: interactive prompts
    on_install.py        ← first run: create dirs, install deps
    on_install_success.py← first run: obtain tokens, seed data
    on_start.py          ← every up: start native services
    on_stop.py           ← every down: stop native services
    on_destroy.py        ← teardown: remove native services
  cli/
    seed.py              ← CLI subcommand: stack photos seed
```

Every file is a convention. The runtime looks for it by name. If it's
there, it's used. If it's not, it's skipped. Hooks can be `.py`
(preferred) or `.sh` — the runtime checks for Python first.

| File | When it runs | Purpose |
|---|---|---|
| `stacklet.toml` | Always read | Identity, config, dependencies |
| `docker-compose.yml` | `stack up` / `stack down` | Container definitions |
| `caddy.snippet` | Caddyfile assembly (domain mode) | Reverse proxy route |
| `hooks/on_configure` | Once on first `stack up` | Interactive prompts (API keys, server names) |
| `hooks/on_install` | Once on first `stack up` | Create directories, install native deps |
| `hooks/on_install_success` | Once after first healthy start | Obtain tokens, seed data |
| `hooks/on_start` | Every `stack up` | Start native services |
| `hooks/on_stop` | Every `stack down` | Stop native services |
| `hooks/on_destroy` | On `stack destroy` | Remove native services |
| `cli/*.py` | On demand via `stack <id> <command>` | Stacklet-specific CLI commands |

---

## Manifest: `stacklet.toml`

The manifest declares what the stacklet is and what it needs. The runtime
reads it — the stacklet never reads it itself.

### Required Fields

```toml
id          = "photos"
name        = "Photos"
description = "Family photo library and mobile backup (Immich)"
version     = "0.1.0"
category    = "media"
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Unique identifier. Lowercase, no spaces. Used in CLI commands, file paths, container names, and secret namespacing. |
| `name` | string | Human-readable display name. Shown in `stack list` and status output. |
| `description` | string | One-line description. |
| `version` | string | Stacklet version (semver). |
| `category` | string | One of: `infrastructure`, `media`, `ai`, `communication`, `productivity`, `development`, `automation`. Used for grouping in `stack list`. |

### Optional Fields

```toml
port        = 42010
always_on   = true
type        = "host"
requires    = ["core", "messages"]
```

| Field | Type | Default | Description |
|---|---|---|---|
| `port` | int | none | LAN port for port mode. All ports live in the `42xxx` range. |
| `ports` | table | none | Additional named ports: `[ports]` → `element = 42030`, `synapse = 42031`. |
| `always_on` | bool | false | If true, `stack destroy` refuses to remove it (only `core` uses this). `stack down` still works. |
| `type` | string | `"docker"` | `"docker"` (default) or `"host"`. Host stacklets install native macOS software (brew, compiled binaries) alongside optional Docker containers. |
| `requires` | list | `[]` | Stacklet IDs that must be enabled before this one. The runtime enforces ordering on `stack up` and prevents destroying dependencies. |
| `build` | bool | false | If true, the stacklet has a local Dockerfile. `stack up` rebuilds the image on every run instead of pulling from a registry. Use for stacklets with custom code (bots, agents). |

### Upstream

Declares the primary Docker image for auto-update tracking.

```toml
[upstream]
image   = "ghcr.io/immich-app/immich-server"
channel = "patch"
```

| Field | Values | Description |
|---|---|---|
| `image` | string | Docker image reference. Watchtower monitors this for updates. |
| `channel` | `"patch"` / `"none"` | `patch`: Watchtower auto-updates. `none`: manual only. |

### Environment

The environment system renders configuration from templates. No `.env` files
to maintain — the runtime generates them on every `stack up` from these
declarations.

```toml
[env]
generate = ["DB_PASSWORD", "SECRET_KEY"]

[env.defaults]
UPLOAD_LOCATION  = "{data_dir}/photos/library"
DB_DATA_LOCATION = "{data_dir}/photos/postgres"
TZ               = "{timezone}"
DB_USERNAME      = "postgres"
ADMIN_USER       = "{admin_email}"
ADMIN_PASSWORD   = "{admin_password}"
```

**`generate`** — list of env var names. Values are auto-generated as
cryptographically random strings and stored in `.famstack/secrets.toml`,
namespaced by stacklet ID (`photos__DB_PASSWORD`). Idempotent — existing
secrets are never overwritten. Destroying a stacklet does not remove its
secrets, so re-enabling reuses the same credentials.

**`defaults`** — key-value pairs with `{template}` variables. Rendered
against `stack.toml` values on every `stack up`. The rendered output is
written to the stacklet's `.env` file.

Available template variables:

| Variable | Source |
|---|---|
| `{data_dir}` | `stack.toml` → `[core].data_dir` |
| `{domain}` | `stack.toml` → `[core].domain` |
| `{timezone}` | `stack.toml` → `[core].timezone` |
| `{stacklet_id}` | The stacklet's own `id` field |
| `{admin_username}` | Tech admin username (`stackadmin`) |
| `{admin_email}` | Tech admin email (`stackadmin@home.local`) |
| `{admin_password}` | Tech admin password, generated and stored in `secrets.toml` |
| `{ai_openai_url}` | Derived from `stack.toml` → `[ai].openai_url` |
| `{ai_openai_url_docker}` | Same, rewritten for container access via `host.docker.internal` |
| `{ai_openai_key}` | `stack.toml` → `[ai].openai_key` |
| `{ai_whisper_url_docker}` | Derived from `stack.toml` → `[ai].whisper_url` |
| `{ai_default_model}` | `stack.toml` → `[ai].default` |
| `{ai_tts_voice}` | Derived from `[ai].language` |
| `{messages_server_name}` | `stack.toml` → `[messages].server_name` |

### Hints

Post-setup messages shown to the user after `stack up`. Template variables
from the environment plus `{url}` and `{ip}` are available.

```toml
hints = [
    "Open {url} and create your admin account",
    "Install the Immich app on your phone — enter {url} as the server",
]
```

### Health

Defines how the runtime confirms the service is actually responding, not
just that the container is running. Checked after `stack up` and by
`stack status`.

```toml
[health]
url    = "http://localhost:42010/api/server/ping"
path   = "$.res"
expect = "pong"
```

| Field | Description |
|---|---|
| `url` | HTTP endpoint to poll. |
| `expect` | Expected HTTP status code (as string) or response body value. |
| `path` | JSONPath into the response body. If set, `expect` is compared against the extracted value instead of the status code. |

### Native Services (host stacklets)

Host stacklets (`type = "host"`) declare native macOS services that run
outside Docker. The runtime checks each service on `stack up` and
optionally starts it.

```toml
[services.omlx]
name        = "oMLX"
description = "MLX inference with SSD caching (Metal GPU)"
check_url   = "{ai_openai_url}/models"
start       = "brew services start omlx"
stop        = "brew services stop omlx"

[services.whisper]
name        = "Whisper"
description = "Speech-to-text (whisper.cpp, Metal GPU)"
check_url   = "http://localhost:42062/"
start       = "launchctl load ~/Library/LaunchAgents/dev.famstack.whisper.plist"
stop        = "launchctl unload ~/Library/LaunchAgents/dev.famstack.whisper.plist"
```

| Field | Description |
|---|---|
| `name` | Display name for status output. |
| `check_url` | URL polled to determine if the service is running. Supports `{template}` variables from `stack.toml` (e.g. `{ai_openai_url}`). |
| `start` | Shell command to start the service if not responding. Optional. |
| `stop` | Shell command to stop the service. Used by `down.sh`. Optional. |

### Bot Convention

A stacklet ships a bot by adding a `bot/` directory with a `bot.toml`
manifest and a Python file. The bot runner (in core) discovers bots
across all enabled stacklets and runs them in one async process.

Bot IDs always end with `-bot` (e.g. `archivist-bot`, `scribe-bot`).

```
stacklets/docs/
  bot/
    bot.toml          # declaration
    archivist.py      # MicroBot subclass
    messages/         # i18n templates (optional)
```

```toml
# stacklets/docs/bot/bot.toml
id          = "archivist-bot"
name        = "Archivist"
description = "Auto-files documents with AI classification"
room        = "documents"
room_topic  = "Drop files here — they get filed automatically."

[settings]
classify = true
reformat = true
```

| Field | Description |
|---|---|
| `id` | Bot identifier, ends with `-bot`. Becomes the Matrix username (`@archivist-bot:home`). |
| `name` | Display name in Matrix. |
| `room` | Room alias to create/join. Optional — omit for bots that only respond to DMs/invites. |
| `room_topic` | Topic set on auto-created room. |
| `settings` | Arbitrary key-value pairs passed as kwargs to the bot constructor. |

Module convention: strip `-bot` from the ID → `archivist.py` → class `ArchivistBot`.

Bot passwords are declared in the stacklet's `[env].generate` (e.g.
`"ARCHIVIST_BOT_PASSWORD"` in `docs/stacklet.toml`). The bot runner reads
passwords from `.stack/secrets.toml` on startup.

---

## Lifecycle

### States

A stacklet is in exactly one of three states. There is no separate
"enabled" registry — state is derived from what actually exists.

```
                stack up
  AVAILABLE ──────────────► RUNNING
       ▲                    │     ▲
       │                    │     │
       │ stack destroy      │     │ stack up
       │                    │     │
       │                stack down│
       │                    │     │
       │                    ▼     │
       └──────────────── STOPPED
            stack destroy
```

| State | How to detect | Meaning |
|---|---|---|
| **Available** | No containers (`docker ps -a`), no data dir | Defined in the repo, never started or fully destroyed |
| **Running** | Containers exist and are running | Active, serving requests |
| **Stopped** | Containers exist but not running | Paused, data intact, `stack up` resumes |

### `stack up <id>`

Brings a stacklet to the **running** state. Idempotent — safe to run
repeatedly. Every run refreshes config so changes in stack.toml take
effect.

```
 1. Check requires — fail if dependencies not running/stopped
 2. Render .env from templates + generate missing secrets
 3. First run only:
    a. hooks/on_configure.py — interactive prompts
    b. hooks/on_install.sh — create dirs, install deps, build
 4. Write .env to stacklet directory
 5. Bot runner discovers bots (if stacklet has bot/bot.toml)
 6. Assemble Caddyfile (domain mode)
 7. Build or pull Docker images
 8. hooks/on_start.sh — start native services (host stacklets)
 9. Start containers (docker compose up -d)
10. Wait for health check
11. First run only:
    a. hooks/on_install_success.py — obtain tokens, seed data
12. Reload Caddy (domain mode)
13. Show welcome screen with URL, login, hints
```

**First run detection:** `.famstack/{id}.setup-done` marker. Absent
means first run. Created after `on_install` completes. Deleted by
destroy.

### `stack down <id>`

Transitions from **running** to **stopped**. Data and containers are
preserved — `stack up` brings it back without re-running setup.

```
1. hooks/on_stop.sh — stop native services (host stacklets only)
2. docker compose stop — pause containers
```

### `stack destroy <id>`

Transitions to **available**. Removes everything — containers, data,
secrets, config. Requires confirmation.

```
1. hooks/on_stop.sh — stop native services
2. hooks/on_destroy.sh — remove native services (host stacklets only)
3. Render .env if missing (compose needs it to parse volume defs)
4. docker compose down -v --remove-orphans — remove containers + volumes
5. Delete .env
6. Delete stacklet secrets from secrets.toml ({id}__*)
7. Delete setup-done marker
8. Delete data directory (~/{data_dir}/{id}/)
9. Reassemble Caddyfile (domain mode)
```

Global secrets (`global__ADMIN_PASSWORD`) survive destroy. The user's
password doesn't change when they remove a single service.

### `stack uninstall`

Destroys all stacklets, removes Docker network, deletes runtime state
(`.famstack/`), and removes config files (`stack.toml`, `users.toml`).
The nuclear option — back to a fresh clone.

### Hooks

Each lifecycle transition can trigger stacklet-specific hooks.
Convention over configuration: if the file exists, it runs. If not,
the step is skipped. No registration needed.

```
First stack up:

  on_configure ──► on_install ──► on_start ──► health ──► on_install_success
  (config gate)    (system)       (services)   (wait)     (API work)

on_configure is the gate: it collects all required settings (provider
choice, API keys, server names) and persists them to stack.toml or
secrets.toml. If it fails or is interrupted, the next stack up re-enters
on_configure and picks up where it left off. on_install reads the config
that on_configure wrote and acts on it. Both hooks should be idempotent.

Subsequent stack up:

  on_start
  (services)

stack down:

  on_stop
  (services)

stack destroy:

  on_stop ──► on_destroy
              (teardown)
```

| Hook | Runs | Purpose |
|---|---|---|
| `on_configure` | Once | **Config gate.** Collects all required configuration via interactive prompts and writes it to `stack.toml` or `secrets.toml`. Must be idempotent — if it set some config values but the process was interrupted, the next run should detect what's already set and only ask for what's missing. `on_install` only proceeds when `on_configure` completes without error. |
| `on_install` | Once | **System setup.** Create directories, install native software, build from source. Should be idempotent — check whether each step was already done before doing it again (e.g. `brew list omlx` before `brew install omlx`, check if binary exists before building). |
| `on_install_success` | Once | Obtain API tokens, seed data, create accounts. Requires a running service. |
| `on_start` | Every up | **Validate config first**, then start native services. If required config is missing or invalid, raise with a clear message telling the user how to fix it. The framework stops the pipeline on failure — containers won't start. |
| `on_stop` | Every down | Stop native services. Only stops services we manage (.state/ markers). |
| `on_destroy` | Once | Remove native services entirely (unload plists, uninstall). |

**File resolution:** for each hook, the runtime looks for `.py` first,
then `.sh`. Only one can exist — not both. Python is preferred.

```
hooks/on_install.py   ← checked first (preferred)
hooks/on_install.sh   ← fallback
```

**Once-only hooks** (`on_configure`, `on_install`, `on_install_success`)
are gated by the `.famstack/{id}.setup-done` marker. Created after
`on_install` completes. Deleted by `on_destroy`. A future `stack up`
runs them again from scratch.

**Python hooks** (`run(ctx)`) receive a context dict:

| Key | Type | Description |
|---|---|---|
| `ctx.env` | `dict` | Rendered environment variables. |
| `ctx.secret(name)` | `callable` | Read a secret. `ctx.secret(name, value)` writes one. |
| `ctx.step(msg)` | `callable` | Print a progress line. |
| `ctx.shell(cmd)` | `callable` | Run a shell command with streaming output and error handling. |
| `ctx.http_post(url, body)` | `callable` | HTTP POST, returns parsed JSON. |
| `ctx.http_get(url)` | `callable` | HTTP GET, returns parsed JSON. |

**Shell hooks** receive environment variables: all rendered env vars
plus `FAMSTACK_DATA_DIR` and `FAMSTACK_DOMAIN`.

---

## Hook reference

### Hook interface

All Python hooks implement `run(ctx)`. The runtime calls it with a
context object. Shell hooks receive environment variables instead.

The context object (`ctx`) provides:

| Key | Type | Description |
|---|---|---|
| `ctx["env"]` | `dict` | Rendered environment variables (all templates resolved). |
| `ctx["secret"]` | `callable` | `secret(name)` reads a secret. `secret(name, value)` writes one. Lookup chain: stacklet-specific (`photos__X`) then global (`global__X`). Writes go to stacklet namespace. |
| `ctx["step"]` | `callable` | `step(msg)` prints a progress line to the user. |
| `ctx["http_post"]` | `callable` | `http_post(url, body, content_type=..., headers=...)` → parsed JSON. Form-encoded by default. |
| `ctx["http_get"]` | `callable` | `http_get(url, headers=...)` → parsed JSON. Pass auth explicitly: `headers={"Authorization": "Bearer ..."}`. |

Example `hooks/on_install.py` (system work with `ctx.shell()`):

```python
def run(ctx):
    data_dir = ctx.env["FAMSTACK_DATA_DIR"]
    ctx.step("Creating directories...")
    ctx.shell(f"mkdir -p {data_dir}/docs/paperless/media")
    ctx.shell(f"mkdir -p {data_dir}/docs/postgres")
    ctx.shell(f"chmod 700 {data_dir}/docs/postgres")
```

Example `hooks/on_install_success.py` (API work):

```python
def run(ctx):
    secret = ctx["secret"]
    step = ctx["step"]

    existing = secret("API_TOKEN")
    if existing:
        # Verify it still works (may be stale after destroy + up)
        try:
            ctx["http_get"](
                "http://localhost:42020/api/documents/",
                headers={"Authorization": f"Token {existing}"},
            )
            return  # still valid
        except Exception:
            step("Stored token invalid — obtaining new one")

    step("Obtaining API token...")
    data = ctx["http_post"](
        "http://localhost:42020/api/token/",
        f"username={ctx['env']['ADMIN_USER']}&password={ctx['env']['ADMIN_PASSWORD']}",
    )
    secret("API_TOKEN", data["token"])
    step("API token saved")
```

---

## CLI Commands

Any `.py` file under `cli/` (except files starting with `_` and
`post_setup.py`) becomes a subcommand.

```
stacklets/photos/cli/seed.py  →  stack photos seed
stacklets/messages/cli/send.py →  stack messages send
```

The convention:

| Attribute | Purpose |
|---|---|
| `HELP` | Module-level string. Shown in `stack <id> --help`. |
| Module body | Executed when the command runs. Has access to `sys.argv` for arguments. |

Files starting with `_` are private helpers (e.g., `_matrix.py`), not
exposed as commands.

---

## Docker Compose Conventions

Every `docker-compose.yml` follows a set of naming and wiring conventions.
The runtime relies on these for discovery, cleanup, cross-stacklet
communication, and auto-updates.

### Project Name

```yaml
name: stack-docs
```

Set explicitly. Format: `stack-{stacklet_id}`. This prevents Docker from
deriving a project name from the directory path, which breaks when the
repo is cloned to a different location.

### Container Names

```yaml
services:
  stack-docs-paperless:
    container_name: stack-docs-paperless
```

Format: `stack-{stacklet_id}-{service}`. Both the service key and
`container_name` use the same value. Single-container stacklets can
omit the service suffix: `stack-chatai`, `stack-bots`.

This convention enables:
- `stack ps` to group containers by stacklet
- `stack uninstall` to find and remove all famstack containers
  (`docker ps -a --filter "name=^stack-"`)
- Inter-container references by predictable name
  (`http://stack-docs-paperless:8000`)

### Network

```yaml
networks:
  stack:
    external: true
```

All containers join the shared `stack` network, declared as external.
Created by `stack init`, removed by `stack uninstall`. Every service in
every stacklet must include this.

Containers reference each other by container name across stacklets:
```yaml
environment:
  PAPERLESS_URL: http://stack-docs-paperless:8000
  MATRIX_HOMESERVER: http://stack-messages-synapse:8008
```

Native macOS services (oMLX, whisper.cpp) are reached from containers
via `host.docker.internal`.

### Port Binding

```yaml
ports:
  - "${PORT_BIND_IP:-127.0.0.1}:42020:8000"
```

The `PORT_BIND_IP` variable controls access scope. The runtime sets it:
- Port mode: `0.0.0.0` (reachable from the LAN)
- Domain mode: `127.0.0.1` (only Caddy reaches it)

The host port is the stacklet's declared `port` from `stacklet.toml`.
The container port is whatever the upstream service uses internally.

### Volumes

```yaml
volumes:
  - ${PAPERLESS_DATA_DIR}/media:/usr/src/paperless/media
  - ${PAPERLESS_DB_DATA}:/var/lib/postgresql/data
```

Bind mounts to `{data_dir}/{stacklet_id}/`. All paths come from
environment variables rendered by the runtime — never hardcoded.
This makes the data directory discoverable and consistent:
`~/famstack-data/docs/`, `~/famstack-data/photos/`, etc.

Named Docker volumes are avoided. Bind mounts are explicit, visible in
the filesystem, and easy to back up.

### Auto-Updates (Watchtower)

```yaml
labels:
  - "com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}"
```

Every container gets this label. Watchtower (in the `core` stacklet)
monitors labeled containers and pulls new images on the nightly schedule.
Set `channel = "none"` in `stacklet.toml` to disable.

### Health Checks

```yaml
healthcheck:
  test: ["CMD-SHELL", "pg_isready -U ${DB_USER} -d ${DB_NAME}"]
  interval: 10s
  timeout: 5s
  retries: 5
```

Supporting services (databases, caches) should declare Docker health
checks so `depends_on` with `condition: service_healthy` works. The main
service uses `[health]` in `stacklet.toml` instead — the runtime polls
it after `docker compose up`.

### Restart Policy

```yaml
restart: unless-stopped
```

All containers use `unless-stopped`. They survive Docker daemon restarts
and host reboots, but stay down if explicitly stopped with `stack down`.

### Dependencies

```yaml
depends_on:
  stack-docs-db:
    condition: service_healthy
  stack-docs-redis:
    condition: service_healthy
```

Within a stacklet, use `depends_on` with health conditions. Across
stacklets, use `requires` in `stacklet.toml` — the runtime enforces
ordering at the CLI level.

### Complete Example

```yaml
name: stack-docs

services:
  stack-docs-paperless:
    container_name: stack-docs-paperless
    image: ghcr.io/paperless-ngx/paperless-ngx:latest
    labels:
      - "com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}"
    networks:
      - famstack
    depends_on:
      stack-docs-db:
        condition: service_healthy
    volumes:
      - ${PAPERLESS_DATA_DIR}/data:/usr/src/paperless/data
    environment:
      PAPERLESS_DBHOST: stack-docs-db
      PAPERLESS_DBPASS: ${DB_PASSWORD}
    ports:
      - "${PORT_BIND_IP:-127.0.0.1}:42020:8000"
    restart: unless-stopped

  stack-docs-db:
    container_name: stack-docs-db
    image: postgres:16-alpine
    labels:
      - "com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}"
    networks:
      - famstack
    volumes:
      - ${PAPERLESS_DB_DATA}:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${DB_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

networks:
  famstack:
    external: true
```

---

## Port Mode vs Domain Mode

The runtime operates in one of two modes based on `stack.toml`:

**Port mode** (`domain = ""`):
- Services bind to `0.0.0.0:<port>`
- URLs are `http://hostname:port`
- Caddy does not start
- Zero DNS setup required

**Domain mode** (`domain = "home.internal"`):
- Services bind to `127.0.0.1:<port>` (only Caddy reaches them)
- URLs are `http://photos.home.internal`
- Caddy assembles routes from `caddy.snippet` files
- Requires wildcard DNS on router

### Caddy Snippets

Each stacklet can include a `caddy.snippet` file. The runtime assembles
all snippets into a single Caddyfile on every `stack up`.

```
# stacklets/docs/caddy.snippet
docs.{$FAMSTACK_DOMAIN} {
    reverse_proxy stack-docs-paperless:8000
}
```

The `{$FAMSTACK_DOMAIN}` variable is set by the runtime in Caddy's
environment.

---

## Secrets

### Generated Secrets

Declared in `[env].generate`. Stored in `.famstack/secrets.toml`,
namespaced by stacklet ID:

```toml
photos__DB_PASSWORD = "xK7mQp3JvR2nYs8LwB4dN6..."
docs__DB_PASSWORD = "aB3cD4eF5gH6iJ7kL8mN9..."
docs__API_TOKEN = "060068ace4f65db88c52da..."
global__ADMIN_PASSWORD = "muw7suf7"
```

Properties:
- Auto-generated on first `stack up` if missing
- Never overwritten on subsequent runs
- Preserved across `stack destroy` (so re-enable reuses credentials)
- Gitignored
- 32-character alphanumeric for service secrets
- 8-character lowercase+digits for admin passwords (typed on phones)

### Admin Password

A single admin password is generated once and shared across all stacklets.
Stored as `global__ADMIN_PASSWORD` in secrets.toml. Available to templates
as `{admin_password}`.

Identity (who the admin is) lives in `users.toml`. Credentials (the
password) live in `secrets.toml`. Never in the same file.

---

## Users

`users.toml` defines the family members. Identity only — no passwords.

```toml
[[users]]
id = "arthur"
name = "Arthur"
email = "arthur@home.local"
role = "admin"

[[users]]
id = "sarah"
name = "Sarah"
email = "sarah@home.local"
role = "member"
stacklets = ["photos", "documents"]
```

| Field | Description |
|---|---|
| `id` | Username. Used in CLI commands and as default login name. |
| `name` | Display name. |
| `email` | Email address. Used as login for services that require one. |
| `role` | `admin` (created on every stacklet), `member`, or `restricted`. |
| `stacklets` | Which services this user gets an account on. Admins ignore this — they're created everywhere. |

---

## Global Configuration: `stack.toml`

One file, committed to the repo. User edits it directly.

```toml
[core]
domain   = ""                    # empty = port mode
data_dir = "~/famstack-data"
timezone = "Europe/Berlin"

[updates]
schedule = "0 0 3 * * *"        # Watchtower cron (3am nightly)

[ai]
openai_url = "http://localhost:8000/v1"
openai_key = "local"
default    = "mlx-community/Qwen2.5-14B-Instruct-4bit"
whisper_url = "http://localhost:6111/v1"
language   = "en"
```

Stacklets never read `stack.toml` directly. The runtime resolves
template variables and passes everything through the rendered `.env`.

---

## Runtime State: `.famstack/`

Gitignored. Created by `stack init`. Contains:

| File | Purpose |
|---|---|
| `secrets.toml` | Auto-generated credentials (passwords, API tokens). |
| `*.setup-done` | Marker files. Gates once-only hooks (`on_install`, `on_install_success`). |
| `caddy/conf.d/*.snippet` | Assembled Caddy snippets (domain mode). |

No `enabled` file — stacklet state is derived from Docker containers
and the filesystem. See [States](#states).

Deleted entirely by `stack uninstall`.
