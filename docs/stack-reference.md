# Stacklet Reference

A stacklet is the unit of deployment. The stack is a composition of stacklets. 
The `stack` CLI is the runtime. It discovers stacklets by walking the filesystem, reads their manifests,
and manages their lifecycle. No central registry — if it's a directory under
`stacklets/` with a `stacklet.toml`, it exists.

It is convention over configuration to "sew" services together.
Think Spring Boot for self-hosted services on a Mac.

---

## Stacklet vs core

The distinction is **infrastructure vs feature**. Core hosts cross-cutting
infrastructure that every running stack needs: Watchtower (auto-updates),
the bot runtime, the API socket, the scheduler, the LLM tools server. Vertical stacklets
host features a user might rationally decline: photos, documents, backup,
the AI stack. The test: could a user legitimately want this off? If yes,
it belongs in its own stacklet, even when multiple other stacklets opt
into it via manifest contracts (e.g. `[[backup.archive]]` declared by
photos and docs, consumed by the `backup` stacklet).

This file holds what a stacklet author needs. Core's pages and configuration
are in [core-reference.md](core-reference.md). The memory's internals are in
[memory-reference.md](memory-reference.md).

---

## Directory Structure

A stacklet is a directory containing at minimum a `stacklet.toml`
manifest. Everything else is optional — include only what you need.

Two directories are searched, in this order:

| Location | What belongs there |
|---|---|
| `stacklets/` in the repo | The stacklets a release ships and supports. |
| an extension dir (`[core] extension_dirs`, default `["~/<product>-extensions"]`) | Everything else: a private stacklet, a community one, one still being designed. Outside the repo, so an upgrade cannot take it, and each can be its own git repo. |

`extension_dirs` is a list, searched in the order given. The first tree
to claim an `id` keeps it, so the repo wins a clash with an extension and
an earlier extension dir wins a clash with a later one. Discovery skips a
directory that does not exist. Only `stack extensions add` creates one: the
first extension dir, when it installs an extension there.

`stack extensions` installs extensions (ADR-022):

- `stack extensions list` shows the extensions of the catalog
  (`[core] extension_catalog`) and the installed extensions.
- `stack extensions add <id>` installs an extension from the catalog.
- `stack extensions add <git-url> [<id>]` installs an extension from a
  different git repository. It shows a warning first.
- A repository with `stacklet.toml` at its root contains one extension. In
  other repositories, each folder one level down with a `stacklet.toml`
  contains one extension.
- `add` copies the tree of the extension into
  `<first extension dir>/<id>/`. The folder name is the `id` of the manifest.
- `add` writes the copy into `<first extension dir>/.incoming/` first and then
  renames it into place. Discovery does not look into `.incoming`.
- `add` writes `<id>/.source.toml` with `url`, `path`, `branch`, `commit`
  and `tree`. `tree` is the git tree of `path` at `commit`.
- Discovery reads the folder like any other stacklet folder.
- `stack extensions` never changes a folder without `.source.toml`. Such a
  folder was put there by hand.
- `stack up` of an extension from a repository other than the catalog shows
  the URL of that repository in one line.

The runtime resolves a stacklet by `id`, never by path, so `stack up <id>`,
hooks, CLI plugins, secrets and template variables like `{photos_url}`
behave identically wherever it lives. `stack list` and `stack status`
group extensions in their own section, and every stacklet in the JSON
output carries `source` (`repo` or `extension`) and `path`.

One asymmetry: a compose file cannot iterate a list, so the bot runner
bind-mounts the **first** extension dir only. A stacklet that ships a bot
belongs there, and `stack up` says so when it is somewhere else. While
that directory does not exist, the mount gets an empty stand-in under
`.stack/`, because a bind mount needs a source and an instance without
extensions should not grow a directory in the admin's home. Any
`stack up` re-renders core's env, so the real one takes over by itself.

```
stacklets/photos/
  stacklet.toml          ← manifest (required)
  docker-compose.yml     ← container definitions
  caddy.snippet          ← reverse proxy route (domain mode)
  hooks/
    on_configure.py      ← first run: setup questions, as forms
    on_prepare.py        ← long setup outside the queue: installs, downloads
    on_install.py        ← first run: create dirs, install deps
    on_install_success.py← first run: obtain tokens, seed data
    on_start.py          ← every up: validate config, start native services (before containers)
    on_start_ready.py    ← every up: seed data, sync accounts (after health checks)
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
| `hooks/on_configure` | Once on first `stack up` | Setup questions as forms (API keys, server names) |
| `hooks/on_prepare` | `stack <id> prepare`, and every `stack up` before its turn | Long installs and downloads that need neither Docker nor core |
| `hooks/on_install` | Once on first `stack up` | Create directories, install native deps |
| `hooks/on_install_success` | Once after first healthy start | Obtain tokens, seed data |
| `hooks/on_start` | Every `stack up`, before containers | Validate config, start native services |
| `hooks/on_start_ready` | Every `stack up`, after health checks | Seed data, sync accounts (service is healthy) |
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
stage       = "incubating"
```

| Field | Type | Default | Description |
|---|---|---|---|
| `port` | int | none | LAN port for port mode. All ports live in the `42xxx` range. |
| `ports` | table | none | Additional named ports: `[ports]` → `element = 42030`, `synapse = 42031`. |
| `always_on` | bool | false | If true, `stack destroy` refuses to remove it (only `core` uses this). `stack down` still works. |
| `type` | string | `"docker"` | `"docker"` (default) or `"host"`. Host stacklets install native macOS software (brew, compiled binaries) alongside optional Docker containers. |
| `requires` | list | `[]` | Stacklet IDs that must be set up before this one. `stack up` starts a requirement that is not set up first, in the same run, when its first start asks no questions and has no `attended_setup`; it refuses for any other. `stack destroy` refuses to remove a requirement. |
| `build` | bool | false | If true, the stacklet has a local Dockerfile. `stack up` rebuilds the image on every run instead of pulling from a registry. Use for stacklets with custom code (bots, agents). |
| `stage` | string | `"stable"` | How finished the stacklet is. `"stable"` is silent. Any other value (`"beta"`, `"incubating"`, `"deprecated"`, or your own) is shown in `stack list` and warned about on every `stack up`. `"deprecated"` marks a stacklet that still works but will be removed in a future release. |
| `open_path` | string | "" | The page a screen opens below the stacklet's address, for a service with nothing at its root: the ai stacklet's `/admin`, oMLX's admin page. `stack list` adds it to `url` and `browser_url`; the template variables `{url}` and `{<id>_url}` stay the root. |
| `dashboard` | bool | true | If false, the dashboard and the setup page show the stacklet nowhere: no row, no tile, no step. `stack list` (`dashboard` in its JSON) and the command line show it as before. Code sets it, because the memory's start sets Code up. |
| `secure_context` | bool | false | Its web app runs only in a secure context, HTTPS or localhost, as Element does for WebCrypto. In port mode only a browser on this Mac opens it, at `{browser_url}`; screens offer other devices no link to it. |
| `attended_setup` | bool | false | Its setup needs a person at this Mac: macOS asks for permission along the way (a dialog, a grant in System Settings). `stack list` reports it, and a screen offers its terminal command for this Mac, never a button, whatever its questions are. The backup stacklet sets it. |
| `ai_waits` | bool | false | It runs without the AI, but parts of it wait for the AI. While `[ai] provider = "none"`, `stack list` marks it `ai_waits` and its service card says what waits. The memory sets it: its wiki texts and its diary wait. |
| `is_the_ai` | bool | false | The stacklet is the AI. While `[ai] provider = "none"`, `stack list` marks its row `ai_off` and screens say there is no AI. While the AI server's kept answer is a lasting refusal (401, 403, 404, 507), `stack list` shows its row degraded with the server's error. The ai stacklet sets it. |
| `needs_ai` | bool | false | It does nothing useful without the AI. While `[ai] provider = "none"`, `stack up` refuses it, `stack list` marks it `waits_for_ai` and a screen shows "Waits for the AI" instead of a Start. The agent and ChatAI set it. |
| `required_secrets` | list | `[]` | Secret names (unprefixed, as `ctx.secret()` reads them) the stacklet cannot work without. `stack doctor` reports any that are absent and points at `stack setup <id>`. Declare a secret here when it is minted by `on_install_success`, since that hook never runs again on an instance that is already installed. |

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
cryptographically random strings and stored in `.stack/secrets.toml`,
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
| `{ip}` | `[core].host` if set, else auto-detected LAN IP (falls back to `localhost`) — used in port-mode URLs |
| `{language}` | `stack.toml` → `[core].language` (falls back to `[ai].language`, then `en`) |
| `{timezone}` | `stack.toml` → `[core].timezone` |
| `{shared_bucket}` | `stack.toml` → `[core].shared_bucket` (defaults to `"family"`) — slug for the shared/institutional bucket inside the memory vault |
| `{stacklet_id}` | The stacklet's own `id` field |
| `{admin_username}` | Tech admin username (`stackadmin`) |
| `{admin_email}` | Tech admin email (`stackadmin@home.local`) |
| `{admin_password}` | Tech admin password, generated and stored in `secrets.toml` |
| `{family_names}` | Comma-separated first names of everyone in `users.toml` |
| `{ai_off}` | `"true"` while `[ai].provider = "none"` (no AI for now), else empty. The bot runner and the curator read it as `AI_OFF`: the archivist files without tags, the memory compiles without the model |
| `{ai_speech_off}` | `"true"` while nothing speaks: `[ai].speech = false`, `[ai].provider = "none"`, or `STACK_AI_NO_VOICE=1` (`stack up ai --no-voice`); else empty. The agent reads it as `AGENT_SPEECH_OFF`: a reminder then has no spoken twin |
| `{ai_openai_url}` | Derived from `stack.toml` → `[ai].openai_url`; empty while `[ai].provider = "none"`, as are the whisper and speech addresses and the upstreams |
| `{ai_openai_url_docker}` | Same, as a container reaches it: `localhost`, `127.0.0.1`, `::1` and `0.0.0.0` become `host.docker.internal`. An unusable URL (no scheme, a bad port) stays unchanged, never empty. The other `_docker` variables follow the same rule |
| `{ai_openai_key}` | `stack.toml` → `[ai].openai_key` |
| `{ai_whisper_url_docker}` | Derived from `stack.toml` → `[ai].whisper_url`, or the AI server when unset |
| `{ai_openai_upstream}`, `{ai_whisper_upstream}` | The AI server and the speech server as a proxy upstream: scheme, host and port, no path; `localhost` and `127.0.0.1` become `host.docker.internal`. Used by the ai stacklet's `caddy.snippet` |
| `{ai_speech_url}`, `{ai_speech_url_docker}` | The server that speaks, as its root (requests go to `/v1/audio/speech`): `stack.toml` → `[ai].speech_url`, or the AI server when unset |
| `{ai_speech_key}` | `stack.toml` → `[ai].speech_key` with a speech server of its own, otherwise the AI key |
| `{ai_speech_voices_json}` | The voice of each language as JSON: `stack.toml` → `[ai.voices.<lang>]` (`model`, `voice`, `clip`) over the stack's own voices; read back with `stack.ai.voice.voices` |
| `{ai_whisper_key}` | `stack.toml` → `[ai].whisper_key` with a dedicated speech server, otherwise the AI key |
| `{ai_whisper_model}` | `stack.toml` → `[ai].whisper_model`, default `whisper-1` |
| `{ai_default_model}` | `stack.toml` → `[ai].default` |
| `{messages_server_name}` | `stack.toml` → `[messages].server_name` |
| `{url}`, `{<id>_url}`, `{home_url}` | Public URLs: `http://<ip>:<port>` in port mode; `https://<id>.<domain>` in domain mode once `[core].dns_provider` is set, `http://` before that |
| `{browser_url}` | The address to open in a browser on this Mac: `{url}` in domain mode, `http://localhost:<port>` in port mode, because a web app that needs WebCrypto (Element) only runs over HTTPS or on localhost |
| `{url_host}` | The host of `{url}`: `<id>.<domain>` in domain mode, `{ip}` in port mode. For a protocol the stacklet serves under the same name besides HTTP, such as Forgejo's SSH |
| `{<id>__<NAME>}` | A secret from `secrets.toml`, e.g. `{docs__API_TOKEN}`, `{infra__DNS_API_TOKEN}` |
| `{oidc_issuer}`, `{oidc_client_id}`, `{oidc_client_secret}` | Single sign-on for a stacklet with an `[oidc]` table, see [Single Sign-On](#single-sign-on-oidc). All three empty until the provider has registered the stacklet |

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

For several endpoints, use `[[health.checks]]`, which additionally takes a
`name`, a `hint` shown when the check fails, and `[health.checks.headers]`.

```toml
[[health.checks]]
name          = "Whisper"
url           = "http://localhost:42062/"
hint          = "Whisper not running — check whisper-server logs"
skip_when_env = "STACK_AI_NO_VOICE"
```

`skip_when_env` names an environment variable that, when set to `"1"`,
drops the check entirely. Use it for a part of a stacklet the user can
choose not to start: `stack up ai --no-voice` leaves the speech services
out, and without this the start sequence waits a full timeout for each of
them and then reports services it was told to skip as broken. Any other
value, including `"0"` or empty, leaves the check in place, so an
accidentally exported variable cannot quietly disable monitoring.

`skip_when_off` names a key in the stacklet's own section of `stack.toml`.
When that key is the boolean `false`, the check is dropped. A missing key
keeps the check. Use it for a part that stays off across runs, because
`stack status` and `stack doctor` run in their own process and do not see
an environment variable of a start. The ai stacklet marks its Whisper
check with `skip_when_off = "speech"`, because `[ai] speech = false` turns
speech off ([Memory profiles](#memory-profiles)).

`skip_when_set` names a key in the same section. While that key holds a
value (not missing, not empty), the check is dropped, because another
server serves that part. The ai stacklet marks its Whisper check with
`skip_when_set = "whisper_model"`: the stack sets `[ai] whisper_model`
only when the AI server transcribes voice messages, and then the first
start builds no whisper.cpp.

A stacklet that can be served by another machine declares a top-level
`remote = "{template}"` in its manifest. When it renders to an address,
`stack list` shows the row as `remote` with that host, or `localhost` when
the address is this machine, meaning something on it that the stack does
not manage serves it. Only the checks marked `remote = true` probe that
server; the others probe this Mac, so they run only while the stacklet's
own containers run here as well.
The row's page (`url`, and `browser_url` on this Mac) is then that
server's root with the manifest's `open_path`, not the stacklet's own
port: `http://localhost:8000/admin` for the oMLX app at
`http://localhost:8000/v1`. Another device opens a server on this Mac's
loopback at the Mac's address on the home network.

### Memory profiles

A **memory profile** says what a Mac of one memory size gets. The module
`lib/stack/profile.py` decides it from `sysctl hw.memsize`:

| Memory | Profile | Chat model | Speech at install | oMLX memory |
|---|---|---|---|---|
| Less than 24 GB | `small` | Qwen3.5-9B 4-bit | off | 9 GB ceiling, chunked prefill, model unloaded after 10 idle minutes |
| 24 to 31 GB | `medium` | Qwen3.5-9B 4-bit | on | oMLX's own settings |
| 32 GB or more | `large` | Qwen3.6-35B-A3B 4-bit | on | oMLX's own settings |

The installer writes the chat model as `[ai] default`. On a small Mac it
also writes `[ai] speech = false`. With speech off, `stack up ai` neither
builds nor starts Whisper. It also downloads no house voice and skips the
Whisper health check. This is the same as `stack up ai --no-voice`, but it stays for every
run. Each start of the ai stacklet gives oMLX the profile's memory
settings. An idle timeout that the admin set in oMLX's settings stays.

To treat a Mac as a different size, set the profile in `stack.toml`:

```toml
[core]
profile = "small"
```

A Mac whose memory macOS does not report gets `medium`. A value that the
admin sets in `stack.toml` wins over the profile. An instance without a
`voice` key keeps speech on. On a small Mac, `stack doctor` then warns
that speech keeps about 1.6 GB loaded and names the line that turns it
off.

On a small Mac, the first `stack up agent` asks first. The agent's long
prompts push a small Mac into swap. The question is the agent's
setup form `agent.small-mac`, default no. A no starts nothing. Without a
terminal, `--answer start_anyway=yes` answers it.

### Native Services (host stacklets)

Host stacklets (`type = "host"`) declare native macOS services that run
outside Docker. The table describes them; the runtime does not read it.
The stacklet's `on_start` hook starts its services and `on_stop` stops
them, which lets it leave alone a service the admin installed. To have a
service probed, declare it in `[health]` as well. A stacklet without a
compose file is running from `stack up` to `stack down`, see
[States](#states).

```toml
[services.omlx]
name        = "oMLX"
description = "MLX inference with SSD caching (Metal GPU)"
check_url   = "{ai_openai_url}/models"
start       = "./stack up ai"
stop        = "./stack down ai"

[services.whisper]
name        = "Whisper"
description = "Speech-to-text (whisper.cpp, Metal GPU)"
check_url   = "http://localhost:42062/"
start       = "./stack up ai"
stop        = "./stack down ai"
```

| Field | Description |
|---|---|
| `name` | Display name. |
| `check_url` | Where the service answers. Supports `{template}` variables from `stack.toml` (e.g. `{ai_openai_url}`). |
| `start` | The command that starts the service, for the admin. Optional. The stacklet's hooks start it. |
| `stop` | The command that stops the service, for the admin. Optional. The stacklet's hooks stop it. |

### Bot Convention

A stacklet ships a bot by adding a `bot/` directory with a `bot.toml`
manifest and a Python file. The bot runner (in core) discovers bots
across all enabled stacklets and runs them in one async process.

Bot IDs always end with `-bot` (e.g. `archivist-bot`, `stacker-bot`).

A bot is a subclass of `MicroBot`, the bot runtime in the framework
library (`lib/stack/bot/`). It logs the bot in, keeps its session and keys
across restarts, accepts invitations, runs the sync loop and turns voice
messages into text before a handler sees them:

```python
from stack.bot import MicroBot


class ArchivistBot(MicroBot):
    name = "archivist"

    def register_callbacks(self, client):
        ...
```

Before 0.5 the runtime lived in the bot runner as `microbot`. An
extension bot that still imports `from microbot import MicroBot` loads
until 0.6, and the bot runner's log names its file and the import to use.

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
| `join_rooms` | Aliases of rooms another stacklet owns that the bot takes part in. Joined on every start when they exist; never created, and no one else is joined to them. |
| `settings` | Arbitrary key-value pairs passed as kwargs to the bot constructor. |
| `module` | The code that answers as the account, when it is not the file named after the ID: `"<stem>"` in this stacklet's `bot/`, `"<stacklet>/<stem>"` in another stacklet's, shipped or extension. Empty: the runner only provisions the account, and something outside it answers (the agent's nanobot). Optional. |

Module convention: strip `-bot` from the ID → `archivist.py` → class `ArchivistBot`.
With `module`, the stem it names: `"kettle/agent"` → `kettle/bot/agent.py` →
class `AgentBot`. The account stays the declaring stacklet's whichever code
answers as it: its password, its login state in that stacklet's data dir,
its settings.

Bot passwords are declared in the stacklet's `[env].generate` (e.g.
`"ARCHIVIST_BOT_PASSWORD"` in `docs/stacklet.toml`). The bot runner reads
passwords from `.stack/secrets.toml` on startup.

### The room of a message, and per-room settings

A handler gets the room a message came from as a `RoomContext`
(`stack.bot.RoomContext`). It is a frozen value, built once for the
message:

| Field | What it holds |
|---|---|
| `room_id` | The room's id, for replies. |
| `alias` | The local part of the canonical alias (`documents`), or `""`. |
| `name` | The room's display name. |
| `members` | The member user ids, sorted. |
| `is_dm` | True when the room has two members and the bot is one of them. |
| `config` | The bot's settings for the room (`!config`), read-only. Empty when nobody set one, None when it was not read (`_room_context`). |

People set a bot's per-room settings in the room with `!config`:

```
!config                      shows each option, its value and what it can be
!config <option> <value>     sets the option for this room
```

`MicroBot.CONFIG_OPTIONS` declares the options. One entry gives the
default and the allowed values, and `!config` validates, shows and
confirms from it. The framework declares `process` (`auto` files every
message, `react` waits for a reaction). A bot adds an option to the dict
in its subclass, and reads the room's value from the message's context:

```python
from nio import RoomMessageText

from stack.bot import MicroBot


class KitchenBot(MicroBot):
    name = "kitchen"
    CONFIG_OPTIONS = {
        **MicroBot.CONFIG_OPTIONS,
        "units": {
            "default": "metric",
            "describe": {
                "metric": "grams and litres",
                "imperial": "cups and ounces",
            },
        },
    }

    def register_callbacks(self, client):
        self.add_event_callback(self._on_text, RoomMessageText)

    async def _on_text(self, room, event):
        # `!config` first, before routing: a room set to `react` can
        # always switch back with `!config process auto`.
        if await self._maybe_handle_config_command(room, event):
            return
        ctx = await self._message_context(room)
        if not self._should_react(ctx, mentioned=self._is_bot_mentioned(event)):
            return
        await self.answer(room, event, self.room_option(ctx, "units"))

    async def answer(self, room, event, units):
        """The bot's own work: a recipe in `units`."""
```

In a room where someone sent `!config units imperial`, `answer` gets
`imperial`; in every other room it gets the default, `metric`. The
archivist's per-room `language` works the same way.

- **Handle `!config` before routing.** `_maybe_handle_config_command`
  stores the setting and answers it. Called after `_should_react`, a room
  set to `react` could not switch back without naming the bot, and the
  command would reach the handler as a message.
- **Read the config once per message.** `_message_context(room)` reads
  the bot's room account data (`dev.famstack.room`) from the homeserver
  and puts it on the context. Pass that context down; routing
  (`_should_react`) and the handlers read the same `ctx.config`. The
  read happens for a message that names the bot too: the mention decides
  the routing, and the handler still reads its settings.
- **Read an option with `room_option(ctx, name)`.** It returns the room's
  value, or the default from `CONFIG_OPTIONS`, so the default has one
  home.
- **`_room_context(room)` has no config.** It reads only what the bot
  already holds, with no network call, and its `config` is None. Use it
  for questions that need no setting, such as which room a message is in
  before it is routed. `_should_react` and `room_option` raise a
  TypeError for such a context, because reading the missing config as
  the defaults would ignore a room set to `react`.
- **`_room_mode_allows_react` is a plain method.** It was async before
  0.5. An override with `async def` makes `_should_react` raise a
  TypeError, because an unawaited coroutine counts as True.
- **The settings are the bot's own.** They are stored as the bot's room
  account data, because a bot cannot write room state without power
  level 50. A failed read gives an empty `config`, and every option then
  has its default.

### Devices and keys

How a bot and `stack messages send` keep their Matrix devices and keys is in
[core-reference.md](core-reference.md#how-do-bots-and-commands-keep-their-matrix-devices).

### Bot Events

Matrix is famstack's canonical event ledger — every stacklet emits
typed envelopes into the room timeline, and any state in the system
must be derivable from replaying those events. Two delivery shapes:

1. **Visible event** — `m.room.message` carrying a `dev.famstack.event`
   content field. The body renders for humans in Element; bots and
   the deriver read the structured envelope alongside it. One event,
   full picture.
2. **Silent event** — `dev.famstack.event`-typed message. Element
   ignores unknown event types, so the event acts as a pure machine
   signal with no chat noise.

Both shapes carry the same envelope schema:

```
{
  "source": "<stacklet id>",     // "docs", "messages", "photos", …
  "type":   "<verb.noun>",       // "document.filed", "voice.transcribed"
  "summary": "<one-liner>",      // human-readable activity description
  "data":   { … },               // structured payload
  "actor":  "@user:home" | "bot",
  "ts":     "<ISO-8601 UTC>"
}
```

Examples shipping today:

| Type | Delivery | Emitter | `data` keys |
|---|---|---|---|
| `document.filed` | visible (on m.room.message) | `archivist-bot` | `paperless_id, title, date, topics[], persons[], correspondent, document_type, summary, facts[], action_items[], url` |
| `document.reclassified` | visible | `archivist-bot` | same as `document.filed` + `user_hint` |

Finding famstack events in a room: enumerate the timeline and treat any
event whose content has a `dev.famstack.event` key OR whose Matrix
`type` is `dev.famstack.event` as a ledger entry. Both deliver the
same envelope.

No shipped bot sends a silent event. A bot sends one with
`self.client.room_send(room_id, "dev.famstack.event", envelope)`.

To attach an envelope to a visible message, pass `metadata` on a
text-sending helper (see `archivist.py`'s `_send`):

```python
await self._send(
    room_id, body_text, reply_to,
    metadata={"dev.famstack.event": envelope},
)
```

`_send` sends `body_text` as markdown in `body` and as HTML in
`formatted_body`, converted by `stack.matrix.html.matrix_html`, the one
converter every bot uses (task lines show as ☐ and ☑). `stack messages
send` on the host has no `markdown` package and keeps a standard-library
subset (paragraphs whose newlines stay breaks, `- ` lists, bold, code)
that a test holds to that converter's output.

A message whose HTML shows more than its markdown can passes `html`:
`_send` and `_answer` then send it as `formatted_body` unchanged, and
`body_text` stays what a client without HTML reads. The archivist's
answer to a correction uses it to fold the card into `<details>`. On the
host, `stack messages send --html <html>` does the same, and
`--mention-nobody` adds the empty `m.mentions` the archivist's card
replies carry.

`stack messages send --event <json>` puts a famstack envelope (a JSON
object, `{source, type, summary, data, actor, ts}`) into the message
under `dev.famstack.event`, as a bot's own messages carry one. A bot
reads it from the message a reply answers with
`_reply_parent_envelope`, which returns it only when the bot's own
account sent that message. Messages send as the stacker bot by default,
so the stacker trusts what the host sent this way. The Server room's
notice of a waiting browser carries its code like this (type
`device.waits`), and the stacker bot allows the browser when an admin
replies `allow` to it. The bot never reads a code from a message's text.

Both paths are best-effort; transport failures are logged but never
raised. A downstream bot being offline must not take the emitter's
main path with it.

Subscribing to custom events on the bot side isn't wired yet — that
comes with the first consumer bot. The emit contract is stable and
tested end-to-end (see `tests/e2e/test_archivist_e2e.py`).

### Persistent links and card ids

A link a bot posts into a room stays in the timeline for good, so it
never points at a service or a file path. It points at
`<home>/go/card/<card_id>` for a card, or `<home>/go/<kind>/<name or id>`, and
core's tools server resolves it when someone opens it. `stack.links` builds
these paths (`go_card` for a card, `go_docs`, `go_topic`, `go_person`,
`go_list`, `go_capture`, `go_diary`, and `go_page` for a page an edit
touched); `stacklets/core/tools-server/resolver.py` is the only place that
knows what they resolve to.

The pages that a link opens, the dashboard and the setup page are core's. They are described in
[core-reference.md](core-reference.md#what-does-the-home-address-serve).

The memory's cards, card ids, views and the curator's API are in
[memory-reference.md](memory-reference.md).

### Backup

A stacklet declares the data it wants backed up. The `backup` stacklet
discovers these declarations across all enabled stacklets and routes them
through configured targets (see `stack.toml` → `[backup.targets.*]`).
The stacklet itself never reads the manifest — discovery is the runtime's
job.

```toml
# stacklets/photos/stacklet.toml
[[backup.archive]]
name = "library"
path = "{data_dir}/photos/library/library"
```

| Field | Description |
|---|---|
| `name` | Short slug for this source. Combined with the stacklet id, this becomes the global source id (`photos/library`), which is also the directory it occupies on a vault (`data/photos/library/`). Used in `stack backup status` output and (future) `--source=` selection. |
| `path` | Filesystem path to sync. Template variables from the rendered environment are available (`{data_dir}`, etc.). |

There is no threshold to declare. The engine judges each source against
the number of files it held on the previous run, recorded in its own
history, and refuses to sync one that is suddenly empty or has lost more
than half its files. This replaced a `min_files` constant, which could
not work: a number written at authoring time cannot know the scale of the
household it guards, so a library of 50,000 photos reduced to 11 passed
`min_files = 10` without complaint. A source that has never held anything
is skipped rather than failing the run.

**`[[backup.archive]]`** declares an append-only store: files are added,
never modified, never deleted. The engine commits to kernel-enforced
immutability where the filesystem supports it. If a stacklet's data is
genuinely append-only (photo originals, archived PDFs), this is the
right section. (Storage-industry vocabulary calls this WORM — Write
Once Read Many.)

The stack's [snapshots](admin-guide.md#snapshots-and-rollback) read
`[[backup.archive]]` too. A snapshot leaves these paths out, because a
copy of the photo library or the document originals can fill the disk.
A rollback never replaces them. Declare only append-only folders here,
because a folder that changes and is declared here gets no snapshot.

**`[[backup.snapshot]]`** declares mutable state that cannot be rsynced.
This page calls what it makes a **dump**. A snapshot is the stack's copy
of a stacklet's state before a change, which is a different thing.
A database changes under you continuously and a copy taken mid-write will
not restore, so it is dumped instead: one `pg_dump` per run, packed with
whatever small files must travel beside it, into a dated `.tar.gz`. Each
run adds a tarball and never touches an older one, which gives mutable
state the same append-only shape an archive has.

```toml
# stacklets/messages/stacklet.toml
[[backup.snapshot]]
name     = "synapse"
postgres = { container = "stack-messages-db", database = "synapse", user = "synapse" }
include  = ["{data_dir}/messages/synapse/homeserver.yaml",
            "{data_dir}/messages/synapse/*.signing.key"]
```

| Field | Description |
|---|---|
| `name` | Short slug, as for an archive. Becomes the source id (`messages/synapse`). |
| `postgres` | How to capture the state, namespaced by what captures it. `container` is where the database runs (the dump goes through `docker exec`), `database` and `user` are what to dump and as whom. |
| `git` | The other capture: `repo` is a git repository on the host (`{data_dir}` is rendered). The dump holds `<name>.bundle`, made with `git bundle create --all`, and the restore command is `git clone <name>.bundle <name>`. An entry sets `postgres` or `git`, never both. |
| `include` | Files that must travel with the dump for a restore to be possible. Globs are allowed. A path that this install never created is skipped, and the dump does not fail. |

The capture key is namespaced, because a stacklet can keep its state
somewhere other than Postgres. Such a stacklet declares a dump with its
own key, and the contract does not have to treat every database as
Postgres.
`stack.postgres` drives a containerised Postgres. That code is not in
the backup coordinator, because the code that restores a database needs
it too.

The memory stacklet declares the second kind, for the curator's clone of
the family memory:

```toml
# stacklets/memory/stacklet.toml
[[backup.snapshot]]
name = "vault"
git  = { repo = "{data_dir}/memory/vault" }
```

An archive cannot hold a git repository, because the repository's files
change and the engine never overwrites a file. A bundle carries every
branch and the remote-tracking refs. A commit that has not reached the
remote yet is therefore in the backup too. The bundle is made on the
host and only reads the repository: no fetch, no gc, no lock. The
manifest records the repository's refs. When the refs are the same as in
the newest local tarball, the run writes no tarball and reports the
dump as unchanged.

Dumps run before the sync, because a dump must never be newer than the
files it references. Nothing stops and nobody is logged out while
`pg_dump` runs, because it takes its own consistent view. The tarball
records the image versions and digests that produced it. Nothing reads
them yet. They are recorded now, because they cannot be added to a dump
afterwards. A restore must be able to refuse a target that does not
match.

A stacklet may declare zero, one, or several entries of each kind. Sources
flow to every configured target whose engine supports the declared
section type.

### Single Sign-On (OIDC)

A stacklet can let people log in through an OpenID Connect provider that
another stacklet runs. The two sides do not name each other: the client
does not know which provider it gets, and the provider does not know the
client's env.

A **client** declares `[oidc]` and reads three template variables:

```toml
[env.defaults]
OIDC_ISSUER        = "{oidc_issuer}"
OIDC_CLIENT_ID     = "{oidc_client_id}"
OIDC_CLIENT_SECRET = "{oidc_client_secret}"

[oidc]
name      = "Documents"     # shown on the provider's consent page; default: the stacklet name
callbacks = ["{url}/accounts/oidc/sso/login/callback/"]
```

`callbacks` are rendered against the client's own template variables, so
`{url}` is the client's public URL. The three `{oidc_*}` variables are
set together or not at all: they stay empty while no provider exists, and
until the provider has registered the client. Test one of them in the
compose file to switch the login on, and keep the service's own login as
the fallback.

A **provider** declares `[oidc_provider]` (an empty table). The issuer is
its public URL, `{url}`. Its hooks do the registration through the
`Stack` instance in `ctx.stack`:

| Method | What it does |
|---|---|
| `oidc_clients()` | Every stacklet with `[oidc]`: `stacklet`, `name`, rendered `callbacks`, and the stored `client_id` and `client_secret` (None before the first registration) |
| `store_oidc_client(stacklet_id, client_id, client_secret)` | Stores the credentials the provider issued |

Credentials are kept in the provider's secret namespace
(`<provider>__CLIENT_<ID>_ID` and `_SECRET`). `stack destroy <provider>`
deletes them, and each client falls back to its own login on its next
`stack up`. A client reads new credentials only when its containers are
recreated, so a provider should not rotate a stored secret on every run,
and should tell the admin which client to `stack up` after storing new
credentials. If more than one stacklet declares `[oidc_provider]`, the
first one discovered is used.

Brought up in one command, such as `stack up all`, the provider starts
before its clients, so they render their env after it has registered
them. A client brought up alone does not pull the provider in.

`stack doctor` warns about a running client that a running provider has
not registered, and about a second provider being ignored. A provider
that is present but was never brought up raises nothing.

A provider may need domain mode with HTTPS, and should refuse to start
without it in `on_install` and `on_start`. A passkey login is the usual
reason: browsers allow WebAuthn only on an `https://` origin. The
framework does not check this, because the requirement belongs to the
provider, not to OIDC.

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

**A stacklet without containers** (no `docker-compose.yml`; its `on_start`
hook starts services on the host) has nothing for Docker to report. `stack
up` writes `.stack/<id>.up` once `on_start` has succeeded, holding the
commit the stacklet started from, and `stack down` and `stack destroy`
delete it. While the file is there the stacklet is running; without it, a
set-up stacklet is stopped and one never set up is available. A stacklet
with a compose file never gets the file.

**A service that launchd runs** (core's host API, the AI's oMLX and
Whisper) is stopped and loaded through `stack.launchd`. Use its `stop`
and `load` in hooks, not `launchctl` directly, because launchd's exit
codes do not say what happened. `launchctl remove` and `unload` return
before launchd lets the job go. A `launchctl load` in that moment loads
nothing and exits 0. `launchd.stop` returns only after launchd no longer
lists the job. `launchd.load` fails when launchd does not have the job
afterwards.

Running is not healthy, with containers or without. `stack up` waits for
the `[health]` checks before `on_start_ready`, and `stack list` and `stack
status` show a running stacklet whose checks fail as degraded, with their
hints. A start whose checks fail still ends with exit code 0, and its
result names them (`unanswered`, each check's `name` and `hint`). The
stacklet that is the AI (`is_the_ai`) is then not called running: the
start names each failed check with its hint instead. The AI is also
degraded while the server refused its one tiny question with an error that
lasts: a refused key (401, 403), a missing model (404) or a model it cannot
load (507). Another server error or a timeout does not count, and the next
good answer clears it
(`.stack/ai.answer.json`, written by `stack ai check` and the last step of
`stack up ai`, `stack.ai.kept_answer`), because a server can list its
models and still refuse every answer. An answer kept for another address,
model or key does not count. The commit in the file is what `stack doctor` and a bare `stack
restart` compare with the checkout, as they do a container's `stack.commit`
label.

**Core's host API** is a process on the Mac that launchd runs, so it has no
label either. When it starts, it writes the commit it runs to
`.stack/host-api.commit`. launchd does not restart a running process when
its code changes on disk. Thus core's `on_start` compares that commit with
the checkout, by the rule for a stale container (commits since then changed
core or `lib/`). When the code is older, the hook restarts the host API and
says "Restarted the <product> API: its code changed". When the code is
current, the host API keeps running. A host API on older code makes core
stale in `stack list` (`host_api_stale` is true in `--json`), and `stack
doctor` names the <product> API with the fix `stack restart core`. A host
API without the file started before the file existed. Core's start
restarts it and says "Restarted the <product> API: it predates the code
stamp". `stack list` and `stack doctor` do not report it, because an
unknown answer is not a finding.

`stack list --json` reports each stacklet's state as fields (`enabled`,
`online`, `starting`, `failing`, `degraded` with `health_issues`), and
`asks_on_first_up`: whether its first `up` would ask the admin anything.
That is a stacklet not set up whose `on_configure` has a form left
(`questions(ctx, {})` is not None), or asks for itself (a shell hook,
`run(ctx)`), or whose `questions` fails. `asks_in_forms` says it is the
first: a screen can show those forms and answer them (`stack configure
<id> --json --answers -`, through the host API's `configure`). For the
others a screen offers the terminal command, not a button.

`url` is the address a person opens from another device, `browser_url`
the one a browser on this Mac opens (`{browser_url}`), and `urls` one for
each port the manifest names under `[ports]`, such as the chat server
(`synapse`) a phone's app connects to. `secure_context` is the manifest's:
a screen links such a stacklet on another device only over HTTPS. So is
`attended_setup`: a screen offers such a stacklet's terminal command for
this Mac and says why, never Add, whatever `asks_in_forms` says. The
Server room answers `stack up <id>` for such a stacklet that is not set up
with that command too, and starts nothing.

`prepares` says that the stacklet has an `on_prepare` hook. Its first
start can take long (the ai stacklet installs oMLX and downloads its
models), but it holds only the stacklet's run lock, so a screen starts it
as a job and follows it. The Server room does not start such a stacklet
that is not set up: its answer must come within the host API's limit, and
its run does not wait its turn. It names the setup page and `stack up
<id>` instead.

`failed_run` is in the row of a stacklet that is not set up. It is the
result of the stacklet's last run when that run failed (its `error` or
`cancelled`), else null. The run log holds it (`.stack/runs/<id>.jsonl`).
A screen that keeps no job of that run shows the failure and its reason
from it, for example after a restart of core.

### `stack up <id>`

Brings a stacklet to the **running** state. Idempotent — safe to run
repeatedly. Every run refreshes config so changes in stack.toml take
effect.

```
 1. Refuse a start that loses the way back to a snapshot
    (`stack.snapshot.guard`), before any setup question,
    preparation or download: one whose last snapshot failed, one a
    stopped rollback holds
 2. Check requires: start a requirement that is not set up and asks
    nothing first (its own steps, inside this run, waiting for another
    run of it); fail for one that asks questions or has `attended_setup`
 3. Render .env from templates + generate missing secrets
 4. hooks/on_configure.py (first run only): setup questions, as forms
 5. hooks/on_prepare.py: long installs and downloads, every run,
    holding only this stacklet's run lock; the queue of runs is
    entered after it
 6. hooks/on_install.sh (first run only): create dirs, install deps, build
 7. Write .env to stacklet directory; a first run renders it again
    first, so a secret `on_install` wrote reaches the container
 8. Bot runner discovers bots (if stacklet has bot/bot.toml)
 9. hooks/on_start.sh: start native services (host stacklets); without
    a compose file, write `.stack/<id>.up`
10. Write the Caddyfile, counting this stacklet as up (domain mode)
11. Build or pull Docker images
12. Start containers (docker compose up -d)
13. Reload Caddy (domain mode)
14. Wait for health check, with containers or without
15. hooks/on_install_success.py (first run only): obtain tokens, seed data
16. hooks/on_start_ready.py: service is healthy, seed data, sync accounts
17. Show welcome screen with URL, login, hints
```

**A snapshot is opt-in:** `stack up` starts a running stacklet on another
release than the checkout on the checkout's code, without a snapshot.
`stack restart <id> --with-snapshot` applies a release with one. Step 1
refuses only a stacklet that waits: its error names `stack restart <id>
--with-snapshot` and `stack restart <id>`, and the Server room gets no
notice, because nothing started and nothing failed.

**First run detection:** `.stack/{id}.setup-done` marker. Absent
means first run. Created after `on_install` completes. Deleted by
destroy.

**Setup answers:** `--answer key=value` (repeatable) and `--answers
<file.json>` (`-` reads stdin) answer the first run's setup questions
([Setup questions](#setup-questions-on_configure-as-forms)). In a terminal
the rest are asked; without one, a question left open stops the run before
anything is installed, with an error naming the form and its fields and the
form itself in the result (`form`). Pass a secret in the file or on stdin:
arguments are visible to every process on the Mac.

**Following a run:** `stack up <id> --events` reports the run on stdout
as it happens, one JSON object per line ([ADR-020](adr/adr-020-live-events-for-screens.md)):

```
{"id": 6, "type": "step.started", "time": "2026-10-26T09:14:02.114Z", "data": {"text": "Pulling images"}}
{"id": 7, "type": "step.done", "time": "2026-10-26T09:14:09.530Z", "data": {"text": "Pulling images", "started": 6}}
```

- `id` counts up from 1 within the run; `time` is UTC.
- `type` is `step.done` or `step.failed` for a step, `step.started` then
  `step.done` or `step.failed` for a slow one (its `data.started` names
  the start), `step.progress` for how far a slow step is (`data.percent`,
  0 to 100, and `data.started`), `warning`, and `result` last, exactly
  once. A hook reports the percent through the spinner of its step:
  `with ctx.stack.output.spinner("Downloading the model") as step:
  step.progress(42)`. A job's snapshot shows it as the running step's
  `percent`.
- `result` carries what of `up`'s result a screen may show, listed in
  `stack.live.envelope.result_for_screens`: `ok`, `error` or `cancelled`,
  `stacklet`, `name`, `stage`, `first_run`, `warnings`, `migrations`,
  `healthy` (whether every health check passed; a run can be `ok` without)
  and `running` (who runs the stacklet, when the run is
  [refused](#one-run-per-stacklet)), and an error's `key` and `values`.
  Never the rendered `env` or the hints, which can hold a password; a
  caller that needs them asks the host API without `stream`.
- A step's text and a warning are words for people and never carry a
  secret's value.
- A step, a warning or an error can carry a `key` and `values` beside its
  English text. A screen shows its own words for a key it knows, in the
  page's language, and the text for any other. Code gives a text a key
  with `stack.said.Said`: `ctx.step(Said("Starting oMLX", "ai.start_omlx"))`.
  A hook raises `Said.error(text, key, **values)`, and `stack <id>
  prepare` puts its key and values into the result. The words for each
  key live in the tools server (`run_words.py`).
- stdout carries nothing else. What people read goes to stderr, and so
  does anything a hook or a tool writes to stdout meanwhile. The exit
  code is the one `stack up` always has.

The host API follows runs from their run logs (see "The run log" below),
so a caller sees a run whoever started it:

| Request | Reply |
|---|---|
| `{"cmd": "up", "stacklet": "<id>", "surface": "dashboard", "stream": true}` | starts `stack up <id>`, then the run's events from its log, a `result` last. A run of `<id>` that goes on already is followed instead, and no second run starts. |
| `{"cmd": "follow", "stacklet": "<id>", "after": 0, "stream": true}` | the current or last run's events with an id above `after`, a `result` last |
| `{"cmd": "runs", "stream": true}` | a `run` event for each run going on, then for each run that starts; an empty line every 30 s while there is none |

`surface` names where the run was started, in a refused second run's
message. Only `up`, `follow` and `runs` stream (`STREAMED_COMMANDS` in
`stacklets/core/famstack-api.py`). A run that the API started and that
writes nothing for 21 minutes, longer than any step of `up` may take, is
ended, and the API writes the `result` itself. A run that stops without a
result (Ctrl-C, a crash) gets one that says so, as soon as its lock is
free. Without `"stream"` the reply stays one line. Such a run, the Server
room's `up`, `down` and `restart` and the setup page's `ai_switch`, does
not wait its turn: while another run holds the stack, it is refused at
once and its reply names that run (see "One run per stacklet").

The pieces are modules of `stack.live`, each usable on its own: `envelope`
(the event), `emitter` (what `--events` writes through), `follow` (what
the host API relays with), `client` (both of the API's protocols, for
code in containers), `hub` (the latest events, each with a cursor, and
`after(cursor, timeout)` for every reader) and `jobs` (runs a screen
follows, one per action and target, folded into snapshots).

### One run per stacklet

A **run** is one `stack up`, `down`, `restart`, `destroy` or `prepare` of a stacklet.
Only one run of a stacklet goes on at a time, from any surface: a
terminal, the host API, the menu bar app. For example, a second
`stack up photos` while the first is still installing does not start.

Each run holds the stacklet's run lock, `.stack/runs/<id>.lock`, from its
start to its end. The lock is an advisory `flock` that the running process
holds (`stack.runs`). The kernel releases it when the process ends.
This is also true for Ctrl-C, a crash and a kill. Thus no stale lock stays behind.

Runs of different stacklets do not overlap either. After its own lock, a
run takes the stack's lock, `.stack/runs/one-run.lock`. A run that finds
it held waits, and its first step says which run it waits for:

```
  •  Waiting for another run to finish: up photos (from the dashboard, since 18:40, pid 4711)...
```

The run has begun while it waits: its run log is open, so the dashboard
shows the waiting step on the stacklet's tile. A first start asks its
setup questions before it waits, so a question that nobody answers holds
up no other run. This is true for `stack up`, `stack up all` and `stack
restart`. A start that sets up a requirement on its way (memory sets up
code) does not wait for a run of that requirement: while one is queued or
going on, the start stops at once, names that run and says to start again
once it ended. `stack doctor` names the run that holds the stack's
lock when it comes from a terminal, where it may wait for an answer, or
has gone on for more than five minutes. The reason is core: a run
that ends a first start recreates core's bot runner, and every container
of core that runs with values that are no longer current, which closes
core's card door and stops the bot runner for seconds. A run that used
core in those seconds failed. The run that recreates core ends only after
everything that answered before the recreate answers again, core's card
door included.

Core's tools server serves the setup page and the dashboard. It gets only
the values of core's `.env` that its code reads, listed under
`environment:` in core's compose file, so a value only the bot runner
reads (the AI's address and key, which the setup page's AI step writes)
does not recreate it. The tokens that documents and photos write in their
first run are not on the list: the tools server reads them from the host
API (`stack env core`) when a search needs them. A key the tools server's
code reads must be on that list: a unit test fails when it is not.

A stacklet's preparation does not wait in this queue. `stack <id>
prepare` runs the stacklet's `on_prepare` hook holding only that
stacklet's run lock, and `stack up <id>` runs it after the setup
questions and before it waits. The hook installs and downloads what needs
neither Docker nor core (the ai stacklet: oMLX from Homebrew, the
language model, Whisper), so `stack up docs` starts at once while the AI
downloads its model for half an hour. A second run of the same stacklet
is refused as usual (exit 75). With `STACK_RUN_QUEUE=no`, which takes the
stack's lock first, the preparation runs inside it.

A failed preparation is the failure of its stacklet only. In `stack up
all`, `stack up a b` or a restart, the stacklet whose preparation failed
is reported with its reason and is neither stopped nor started; the
others start, and the command exits 1. `stack setup <id>` runs the
preparation before the setup hook, so a changed setting such as `[ai]
default` is downloaded there.

Because a preparation does not hold the stack's lock, three things check
for it. `stack down all` holds the run locks of the stacklets it does
not stop, and `stack uninstall` those of every stacklet, without a run
of them (`runs.reserve`: no run log, so each stacklet's last run stays
as it was). Both are refused while any stacklet is prepared. A command
that changes what a stacklet's runs read holds that stacklet's run lock
the same way for the change: `stack ai switch` is refused while the AI
is prepared or started, and a refused command reads "switching to
another server". And every write to `stack.toml` reads, changes
and writes the file under a short lock of its own
(`.stack/stack.toml.lock`), so a preparation and another run that write
at the same time keep both values.

| When | Then |
|---|---|
| a run of the same stacklet goes on | the command does nothing and exits with code 75 |
| a run of another stacklet goes on | the command waits until that run ended, then runs |
| `stack update` while a run goes on | it waits until the run ended, then moves the checkout and names what to restart as it is then |
| `restart` | it holds the lock across its `down` and its `up` |

A refused command names the stacklet, the surface of the run, its start
time and its process id:

```
  ✗  photos is already starting (from the terminal, since 18:40, pid 4711)

      Wait until that run ends, then try again.
      Follow its containers meanwhile: stack logs photos
```

With `--events`, the `result` carries the same facts in `running`
(`surface`, `started`, `pid`, `action`). With `--json`, the answer on
stdout is that object (`error`, `stacklet`, `running`, `hints`). To name its surface, a caller
sets `STACK_RUN_SURFACE=dashboard` in the command's environment. Without
it, the surface is `terminal`.

#### The run log

Each run also writes its events to the stacklet's run log,
`.stack/runs/<id>.jsonl` (`stack.run_log`). Another surface can read it to
follow a run that it did not start: the host API follows runs from it
(`stack.live.follow`), and the dashboard shows every run of a stacklet on
its tile, wherever it was started. The terminal output and `--events` on
stdout do not change.

The log has one event per line, in the envelope of ADR-020:

1. One `run` event: `stacklet`, `surface`, `started`, `pid` and `action`,
   the same details as the run lock.
2. The steps and warnings, when they occur.
3. One `result`, with only the fields that a screen may show
   (`result_for_screens`). The log never holds the rendered env, the hints
   or an `--answer` value.

| When | Then |
|---|---|
| a run takes the lock | it replaces the log, so the log holds the current run or the last run |
| a second run is refused | the log does not change, because only the holder of the lock writes it |
| a run ends with Ctrl-C, a crash or a kill | the log has no `result`; the free lock shows that the run is over |
| a stacklet never ran | it has no run log |

A run of several stacklets (`up all`, `restart a b`) writes the steps of
each stacklet to the log of that stacklet.

### `stack configure <id>`

Asks or answers a stacklet's setup questions on their own: the walk a first
`stack up` makes, without installing or starting anything. It takes the
same `--answer` and `--answers`.

- In a terminal it asks form by form, then applies the answers. On a
  configured stacklet it says what `stack.toml` holds for it (credentials
  hidden) and changes nothing.
- Without a terminal, a question left open fails with the form named.
- `--json` never asks. stdout is the next form, `{"form": {...}}`, or
  `{"ready": true}` once nothing is left and the answers are applied (at
  once for a configured stacklet). Both exit 0; a screen renders the form
  and calls again with every answer so far. A stacklet whose hook asks for
  itself (`run(ctx)`) is refused with `{"error": ...}`. When `apply`
  refuses the answers, the command prints `{"error": ...}` and exits 1.
  An answer that ends the setup does the same. A refusal adds its `reason`, for example `{"error": "Quit
  the oMLX app first ...", "reason": "omlx_app"}`.

### `stack down <id>...`

Transitions from **running** to **stopped**. Data and containers are
preserved — `stack up` brings it back without re-running setup.

Several ids stop together, separated by spaces or commas
(`stack down memory code docs` or `stack down memory,code,docs`), in
reverse dependency order. Every id is checked first, so an unknown one
stops nothing. `stack up` and `stack destroy` take ids the same way (up
in dependency order, destroy in reverse, each destroy confirmed on its
own unless `--yes`). `stack restart` stops them all, then starts them in
dependency order.

Use `stack down all` to stop every currently-running stacklet in reverse
dependency order (dependents first, deps last), with containers or without.

```
1. hooks/on_stop.sh — stop native services (host stacklets only)
2. Delete `.stack/<id>.up` (stacklets without containers)
3. Write the Caddyfile without this stacklet (domain mode)
4. docker compose stop — pause containers
5. Reload Caddy (domain mode)
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
7. Delete setup-done marker and `.stack/<id>.up`
8. Delete data directory (~/{data_dir}/{id}/)
9. Write the Caddyfile without this stacklet, reload Caddy (domain mode)
```

Global secrets (`global__ADMIN_PASSWORD`) survive destroy. The user's
password doesn't change when they remove a single service.

A stacklet that is not set up can still have a data folder. A failed
setup leaves one, and so does a declined uninstall.
Destroy then deletes only that folder. When the folder holds files, it
names the folder and its file count and waits for `destroy` to be
typed. `--yes` skips the question. Without a terminal and without
`--yes` it refuses and deletes nothing.

### `stack uninstall`

Tears down all stacklets: containers, hooks and setup markers. Then it
removes the instance's config files (`stack.toml`, `users.toml`) and its
runtime state (`.stack/`) except `secrets.toml`. The last question is
about the data folder. Only the answer `delete` deletes the data and the
secrets. Any other answer, a closed terminal and Ctrl+C keep both. The
secrets hold the passwords of the databases in the data folder, and a
new install needs them to open the kept data.

Only `[core] data_dir` in a `stack.toml` that parses names the data
folder. The folder can also be missing: a disk that is not mounted, or
a moved folder. In both cases the uninstall asks no data question and
deletes no data. It keeps `secrets.toml`. The secrets go without the
question only when nothing was set up: no setup marker, no stacklet's
secret, no container and no stacklet's data folder.

### Hooks

Each lifecycle transition can trigger stacklet-specific hooks.
Convention over configuration: if the file exists, it runs. If not,
the step is skipped. No registration needed.

```
First stack up:

  on_configure ──► on_prepare ──► on_install ──► on_start ──► health ──► on_install_success ──► on_start_ready ──► migrations
  (config gate)    (long setup,    (system)       (services)   (wait)     (first-run API work)   (every-up API work)   (pending ones)
                    no queue)

on_configure is the gate: it collects all required settings (provider
choice, API keys, server names) and persists them to stack.toml or
secrets.toml. If it fails or is interrupted, the next stack up re-enters
on_configure and picks up where it left off. on_install reads the config
that on_configure wrote and acts on it. Both hooks should be idempotent.

Subsequent stack up:

  on_prepare ──► on_start ──► health ──► on_start_ready ──► migrations
  (skips what    (services)   (wait)     (API work)          (pending ones)
   is done)

stack down:

  on_stop
  (services)

stack destroy:

  on_stop ──► on_destroy
              (teardown)
```

| Hook | Runs | Purpose |
|---|---|---|
| `on_configure` | Once | **Config gate.** Asks the setup questions as forms and writes the answers to `stack.toml`, the secret store or the Keychain (see [Setup questions](#setup-questions-on_configure-as-forms)). A hook may still define `run(ctx)` and ask for itself. Must be idempotent — if it set some config values but the process was interrupted, the next run should detect what's already set and only ask for what's missing. `on_install` only proceeds when `on_configure` completes without error. |
| `on_prepare` | Every up, `stack setup <id>`, and `stack <id> prepare` | **Long setup outside the queue.** Homebrew installs, builds, model downloads: work that needs neither Docker, nor core, nor another stacklet. It runs after the setup questions and before the run takes its turn in the [queue of runs](#one-run-per-stacklet), holding only the stacklet's run lock, so other stacklets start meanwhile. Must skip each step that is done and go on with one that stopped, because it runs on every `stack up`. A failure or a `Cancelled` is recorded in the stacklet's run log and ends that stacklet's part of the command before its turn; the other stacklets of the command still start. |
| `on_install` | Once | **System setup.** Create directories, install native software, build from source. Should be idempotent — check whether each step was already done before doing it again (e.g. `brew list omlx` before `brew install omlx`, check if binary exists before building). |
| `on_install_success` | Once | Obtain API tokens, seed initial data, create accounts. Runs after first healthy start. |
| `on_start` | Every up | **Runs before containers start.** Validate config, start native services. If required config is missing or invalid, raise with a clear message — the framework stops the pipeline and containers won't start. |
| `on_start_ready` | Every up | **Runs after health checks pass.** The service is healthy and accepting API calls. Seed data, sync accounts, anything that needs the service running. Must be idempotent. Its `ctx.env` is rendered after `on_install_success`, so secrets written there are in it. |
| `on_stop` | Every down | Stop native services. Only stops services we manage (.state/ markers). |
| `on_destroy` | Once | Remove native services entirely (unload plists, uninstall). |
| `on_restore` | On `stack backup restore` | **Reserved — not yet invoked.** Runs after the backup engine has put a stacklet's files back on disk. Owns stacklet-specific recovery: DB import, search-index rebuild, account re-seed. Photos can ship an empty stub (Immich re-indexes from the library on its own); Docs needs `pg_restore` + Paperless reindex. Hook signature will match the other `run(ctx)` hooks. |

**File resolution:** for each hook, the runtime looks for `.py` first,
then `.sh`. Only one can exist — not both. Python is preferred.

```
hooks/on_install.py   ← checked first (preferred)
hooks/on_install.sh   ← fallback
```

**Once-only hooks** (`on_configure`, `on_install`, `on_install_success`)
are gated by the `.stack/{id}.setup-done` marker. Created after
`on_install` completes. Deleted by `on_destroy`. A future `stack up`
runs them again from scratch.

**Python hooks** (`run(ctx)`) receive a context dict:

| Key | Type | Description |
|---|---|---|
| `ctx.env` | `dict` | Rendered environment variables. |
| `ctx.secret(name)` | `callable` | Read a secret. `ctx.secret(name, value)` writes one. |
| `ctx.step(msg)` | `callable` | Print a progress line. |
| `ctx.shell(cmd)` | `callable` | Run a shell command with streaming output and error handling. The error message includes the command. |
| `ctx.run_in_container(argv, service=None, user=None)` | `callable` | Run `argv` in the stacklet's container (`stack-<id>`, or `stack-<id>-<service>`) with `docker exec`, as a list and without a shell. Returns stdout. On failure it raises with the container, the exit code and the last stderr line, never the arguments, so a password or secret can be passed. |
| `ctx.language` | `str` | The language the hook speaks to the person: the language the surface that asks names, else `[core] language`, else `en`. The installer asks setup forms in English, because all its other lines are English. |
| `ctx.http_post(url, body)` | `callable` | HTTP POST, returns parsed JSON. |
| `ctx.http_get(url)` | `callable` | HTTP GET, returns parsed JSON. |

**Shell hooks** receive environment variables: all rendered env vars
plus `FAMSTACK_DATA_DIR` and `FAMSTACK_DOMAIN`.

---

### Setup questions (on_configure as forms)

A stacklet's first-run questions are forms (ADR-021): each step's
questions as a JSON Schema object in a small subset, with the form's
`id`, `title`, an explanation `text` and per-field `errors`
(`stack.forms.Form`). The terminal renders and asks them; a caller
without a terminal receives the open form and answers it. `on_configure.py`
defines two functions instead of `run(ctx)`:

```python
from stack.forms import Form

SERVER_NAME = Form(
    id="messages.server-name", title="Messages server name", text=WHY,
    schema={"type": "object",
            "properties": {"server_name": {"type": "string", "title": "Server name",
                                           "minLength": 1}},
            "required": ["server_name"]})

def questions(ctx, answers):          # the next form, None when nothing is left
    if ctx.cfg("server_name"):
        return None                   # configured: nothing is asked or applied
    return SERVER_NAME.pending(answers)

def apply(ctx, answers):              # once, after questions returned None
    ctx.cfg("server_name", answers["server_name"])
```

- **`questions` reads and checks, and never writes.** The answers so far
  are its whole state, so the terminal, a screen and a test call it alike.
  A check against the machine (is the disk mounted?) is made here, and a
  failed one returns the same form `with_errors(field="why")`.
  `Form.pending(answers)` is `None` once the form is answered and valid,
  else the form with an error on each answer that breaks its schema.
- **`questions(ctx, {})` returning `None`** means the stacklet is
  configured. `apply` is not called, whatever answers were given. `stack
  list` asks exactly that of every stacklet not set up, to report
  `asks_on_first_up` and `asks_in_forms`, with a context that has no env (rendering it would
  generate the stacklet's secrets): read the configuration through
  `ctx.cfg`, `ctx.secret` and `ctx.stack`.
- **The framework walks the forms.** Each answer given up front is offered
  once, to the form that asks for it, read by its field's type (`yes`/`no`
  for a boolean). An open form is asked in the terminal, or, without one,
  ends the run with `error` naming the form and its fields and `form`
  carrying it. `apply` receives the answers to the forms that were reached.
- **`NeedsAnswers(form, answers)`** from `apply` asks one more form. Use
  it for what only a server's reply shows, such as a refused key or a
  list of models. The form is asked or answered as any other, and `apply`
  runs again with the new answers.
- **`Refused(message, reason)`** from `apply` refuses the answers and
  writes nothing. The terminal prints the message. The result carries
  `error` and `reason`, a short word that a screen says in its own words.
  The ai stacklet refuses famstack's oMLX with `homebrew` and `omlx_app`,
  and a server with `key` or `unreachable`.
- **`Cancelled`** from `apply` (an answer of no) stops the command without
  it being a failure, as from any hook.
- **Field keywords:** `type` (string, boolean, integer), `title`,
  `description`, `default` (proposed, never an answer), `enum`, `oneOf` of
  `{const, title}`, `examples`, `pattern`, `minLength`, `format` (`time` as
  `HH:MM`, `uri`, `hostname`), `writeOnly` (a secret), `readOnly` (shown,
  not asked). Beyond the standard: `x-pattern-error`, the sentence shown
  when `pattern` does not match, and `x-confirm`, a yes/no question the
  terminal asks about the answer (`{value}`), where a no asks again.
- **Secrets** (`writeOnly`) are read with `getpass`, never carried as a
  form's default, never quoted by the validator, and blanked out of any
  error `questions` or `apply` raises. Progress lines must not carry them.
- **A hook with only `run(ctx)`**, or a shell hook, runs as before.

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

**A stacklet's code is the package `stacklets.<id>`**, wherever the
stacklet lives (`stack.stacklet_code`). The framework loads a command, a
hook, a migration and a bot as the module its path names:
`stacklets/core/cli/schedule.py` is `stacklets.core.cli.schedule`, and
`migrations/0001_lists.py` is `stacklets.memory.migrations.0001_lists`.
So a stacklet's code imports the rest of its stacklet relatively and puts
nothing on `sys.path`:

```python
# stacklets/memory/cli/ask.py
from ._common import dispatch_capture  # stacklets/memory/cli/_common.py
from ..lib import search_memory        # stacklets/memory/lib.py
```

Two stacklets can each have a module of the same name (`oauth.py`), even
when one process runs both, as `stack restart all` does, and a stacklet
named like a standard module (`code`) leaves that module alone. A process
that imports stacklet modules by name rather than through a loader, as
core's API imports its scheduler, names its stacklet directories once at
its start (`import_stacklets`). A stacklet found in two directories is
the first one's, as for the stack.

Until every stacklet imports this way, the loaders also put the
stacklet's directory on `sys.path` while its code runs, so a helper
imported by bare name (`from oauth import sync`) is found too. The
framework forgets those modules and restores `sys.path` when the hook or
command returns.

A hook that raises stops the command and is reported as a failure. A
hook that asked the admin and got a no raises `stack.hooks.Cancelled`
instead: `stack up` stops with `{"cancelled": <message>}`, prints the
message once, exits 1, and does not tell the family room that the
stacklet failed to start.

```python
from stack.hooks import Cancelled
from stack.prompt import confirm

def run(ctx):
    if not confirm("Replace the remote AI server with this Mac?", default=False):
        raise Cancelled("Cancelled. Nothing changed.")
```

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

## Migrations

A stacklet that changes the shape of data it already wrote (moves files,
renames a field, rewrites records) ships the change as a migration. The
framework runs each one once per instance, in order, records it with the
data, and stops at the first failure.

```
stacklets/<id>/migrations/0001_move_todos_to_lists.py
stacklets/<id>/migrations/0002_kind_for_variants.py
```

- **Name:** four digits, an underscore, snake_case. Numbers are per
  stacklet, never reused or renumbered; gaps are fine. Two files with one
  number stop `stack up` before anything runs, and a unit test keeps that
  out of the tree.
- **Docstring:** its first line is what `stack up` and `stack doctor`
  show (`Migrating memory 0001: Move each topic's old todos.md to the
  family's lists.`). The rest says what changes, how to undo it, and
  which conflict check it relies on.
- **`run(ctx)`:** the hook context above, plus `ctx.migration`
  (`memory/0001`) and `ctx.trailer` (`Migration: memory/0001`). A
  migration is a module of its stacklet's package, so `from ..lib import
  ...` reaches the stacklet's helpers.
  Returning is done, raising is failed, finding nothing to do is a
  return. Like a hook it runs on the host with the stdlib; work that needs
  a container goes through `ctx.run_in_container`, or through a command in
  the bot runner (`stack.bot_runner.call`, which returns the command's
  stdout and raises on failure).
- **Safe to run again.** Twice is the same as once. Write in one step,
  or so that a rerun resumes: the record is written only after `run`
  returns, so an interrupted migration is still pending.
- **Never change an applied migration's meaning.** Fix forward with a new
  number. There are no down migrations and no dry run: `stack doctor`
  listing what is pending is the preview. Migrations are kept until 1.0.

**When.** `stack up <id>` runs the pending ones after `on_start_ready`,
and only when every health check passed; otherwise they wait, and one
line says so. A command that starts several stacklets (`stack up a b`,
`stack up all`, `stack restart` with or without ids) starts all of them
first, then runs migrations per stacklet in dependency order, so a
migration may use a service of a stacklet its own one requires.
`stack update` never runs them: it names the ones the release brings,
which run on the restart it asks for. A fresh install runs them all, and
they find nothing to do.

**What waits for them.** Until no installed stacklet has a migration left
to run, the bot runner holds its bots, the memory curator waits for
memory's, and the agent's commands wait (up to a minute, then exit 75).
They read the files and `.migrations.json` (`stack.migrations.until_migrated`),
so a message that arrives meanwhile stays on its room's timeline and is
handled after. Commands run in the bot runner with `docker exec` are not
held, so a migration can still use one. `stack up` prints `Bots wait until
these migrations have run: ...` before it starts anything, and with
nothing pending nothing waits and nothing is printed.

**Failure.** The first migration that raises stops that stacklet's run.
The error is printed and recorded, `stack up` exits 1, and the
containers keep running. The ones after it wait; the next `stack up`
retries it first. The bots stay held until then, and `stack up` says so.

**Record.** It lives with the data it describes, so restoring an older
backup brings the older record back and the migration runs again:

- `<data_dir>/<id>/.migrations.json` records every migration that ran,
  by number, with the time, and the last error of one that failed.
- A migration that changes a git repository on the code host, such as the
  family memory, declares it with `REPO = "family/memory"`, writes one
  commit whose message ends in `ctx.trailer`, and returns the commit's
  sha (None when it changed nothing). The record then points at that
  commit, and the migration counts as applied while the repository has
  it: restore the repository from a backup older than the commit and it
  runs again. A code host that does not answer leaves the record as it
  is.

A migration of the family memory writes its commit through the curator
(`memory_for_migration`), each file on the version it has in the
curator's clone. The curator refuses the commit when a file changed in
between, so a concurrent writer (a bot filing a note) makes the
migration fail and rerun rather than overwrite; the bots keep running.
Git history is the undo.

```python
"""Move each topic's old todos.md to the family's lists.

One memory-bot commit, through the curator. Undo: revert the commit in
family/memory.
"""
from lib import memory_for_migration, move_todos_to_lists

REPO = "family/memory"

def run(ctx):
    memory, vault = memory_for_migration(ctx)
    moved, commit = move_todos_to_lists(memory, vault, trailer=ctx.trailer)
    return commit
```

**Visibility.** With nothing pending, `stack up` prints nothing about
migrations and changes nothing; it reads the record and asks the code
host about each recorded commit. `stack doctor` lists pending
migrations with their summaries (warning), failed ones with their error
(error), and migrations recorded here that the checkout does not ship
(warning: data migrated by a newer release). `stack update` to a release
that lacks a migration applied here names it before asking to proceed.
A commit that adds a migration carries an `Upgrade:` footer saying which
restart runs it and whether older releases read the result.

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

### Command rules

Every stacklet adds commands, so these keep them reading as one CLI.
Check a new command, or a changed one, against them.

1. **Name the thing or the action, as an admin sees it.** Never the
   component that does the work (`curator`, `archivist`) and never when
   it usually runs (`nightly`): those change, the admin's intent does not.
2. **A thing with more than one action is a noun with verbs.**
   `stack memory wiki update`, `stack memory wiki clean`,
   `stack messages room list`. The bare noun lists its verbs, and the
   noun leaves room for a later verb such as `show`.
3. **Flags narrow or modify, and one word means one thing everywhere.**
   `--all` everything, `--dry-run` preview and write nothing, `--yes`
   skip the confirmation, `--as <member>` act as that family member
   (on `messages` commands also `--as agent` or `--as <id>-bot`),
   `--to <member>` address that family member's private chat.
   `--json` belongs to the framework. Flags that contradict each other
   are refused in one line that names both.
4. **A command that changes something applies by default.** `--dry-run`
   is the opt-in preview, never the other way round.
5. **A command that asks a background worker waits until the work is
   done**, prints each step and each item while it waits, and on timeout
   says how far it got. The request itself survives the timeout.
6. **A command returns a dict**, `{"error": ...}` on failure, which exits
   non-zero. With `--json` that dict is all of stdout; what the command
   prints for people goes to stderr. Without it, a command that printed
   nothing has its dict printed as `key: value` lines (`ok: true` left out).

A command that records a choice in `stack.toml` calls
`config["set_cfg"](section, key, value)`, the plugin-side twin of a
hook's `ctx.cfg(key, value)`. It changes that one line in that section
and leaves the admin's comments and other sections as they are. A
`True` or `False` is written as a TOML boolean, anything else as a
string.

Use `config["set_cfgs"](section, values)` for values that belong
together, because it writes them all in one rewrite of `stack.toml`. A
write that stops between two separate calls leaves the section half
changed: for example, the address of one AI server with the key of
another. A hook has the same call as `ctx.cfgs(values)`. `stack ai
switch` uses it to write each route of `[ai]`.

A command that keeps a credential calls `config["set_secret"](name,
value)`, the plugin-side twin of a hook's `ctx.secret(name, value)`. It
writes `<stacklet>__<name>` in `.stack/secrets.toml`, in the command's own
stacklet only. An empty value reads as no secret. `stack photos setup`
uses it to keep the key of the stack's own Photos account.

A command that changes a setting a `caddy.snippet` reads calls
`config["reload_proxy"]()`. It writes the Caddyfile from the settings as
they are now and has the running proxy load it. It does nothing in port
mode or while infra is not up. `stack ai switch` uses it, because the
proxy's AI routes follow `[ai]` (see [Caddy Snippets](#caddy-snippets)).

### Work in the bot runner

The host runs `./stack` with the standard library only. A command whose
work needs a dependency of the bot runner (the AI client, page
extraction) runs that work there, as `bot/cli_entrypoint.py <command>
<args>` of its stacklet, and prints the answer:

```python
import json

from stack import bot_runner

def run(args, stacklet, config):
    reading = json.loads(bot_runner.call_stacklet(stacklet, "boil", *args))
    print(f"  {reading['celsius']} °C")
    return reading
```

- `call_stacklet(stacklet, command, *argv, input_text=None)` starts the
  stacklet's `bot/cli_entrypoint.py` in the bot runner and returns its
  stdout. A failure raises `RuntimeError` with the last line the entry
  point wrote, and the command exits non-zero with that error.
- The entry point is where core mounts the stacklet: `/stacklets/<id>/`
  for a stacklet of the repo, `/extensions/<id>/` for one in the first
  extension dir. The discovered stacklet carries that path as
  `runner_path`. An extension in a later extension dir has no
  `runner_path`, and `call_stacklet` refuses with the fix.
- The entry point gets the stacklet's rendered `.env` (`stack.env_file`)
  over the runner's own environment, which is core's. It reads its own
  `[env.defaults]` as its containers do, a service by its container
  address. A stacklet never brought up has no `.env`, and `call_stacklet`
  refuses with `stack up <id>`.
- The entry point answers JSON on stdout and the host command renders it,
  so `--json` prints the answer alone. `dispatch` streams the entry
  point's output to the terminal instead, which `--json` cannot redirect.

### Who answers as the agent

The agent's account (`[agent] name`, the `agent` stacklet's) is answered
by nanobot, from the agent stacklet's own container. Another runtime can
answer as it instead: a stacklet that ships a `bot/agent.py`, named in
`stack.toml`.

```toml
[agent]
runtime = "nanobot"                   # the default
# runtime = "kettle"                  # kettle/bot/agent.py answers as the agent
```

- The bot runner runs that stacklet's `bot/agent.py` (class `AgentBot`) as
  the agent's account, through the agent's declaration (`module`, see
  [Bot Convention](#bot-convention)). The agent's container keeps running
  and waits, so nanobot does not sign in as well.
- The account, its rooms and its login state stay the agent stacklet's,
  so everything that addresses the agent (`--as agent`, `--mention agent`,
  the archivist stepping back) works as before whichever runtime answers.
  A runtime keeps its own records in its own data dir.
- A runtime in the bot runner signs in to the account with a device of its
  own. Room keys belong to a device, so after a switch the agent cannot
  read the encrypted messages sent before it. The messages stay in the
  rooms; only the agent's view of them is gone. The bot runner logs such
  a message as undecryptable when a read comes across it (after a start,
  or new activity in its room), not on every sync.
- The stacklet is only code: it does not have to be up. The bot runner
  sees the shipped stacklets and the first extension folder, so an
  extension runtime lives there. `stack up agent` stops when the runtime
  names no stacklet with a `bot/agent.py` it can see, and says what to set.
- A change applies with `./stack restart agent core`: the agent's
  container switches, and the bot runner reads the module from core's
  environment.

### What the agent may run

The agent (the `agent` stacklet) runs `stack` commands through the core
API, never the CLI itself. The API runs a fixed set of `memory` commands
and `ai say`, plus the stacklet commands the admin lists in `stack.toml`:

```toml
[agent]
allow = ["kettle"]                    # every command of the kettle stacklet
# allow = ["kettle status", "kettle boil"]   # or single commands
```

- An entry is a stacklet id, or a stacklet id and one of its commands.
  It matches by leading words, so `"kettle"` allows `stack kettle status`
  and `stack kettle boil 3`. A bare string is read as a one-entry list.
- Only stacklet commands qualify. An entry whose first word is not a
  discovered stacklet with CLI commands, or whose second word is not
  one of its commands, is ignored. So no entry can hand the agent a
  framework command such as `up`, `destroy` or `config`.
  `stack up agent` warns about every ignored entry.
- The API reads the list on every request, so a change applies without
  a restart. The agent learns about it on the next `stack up agent`.
- Anything allowed runs, including commands that change something. That
  is the admin's decision. Limits ("never above 80 degrees") belong in
  the command as checks, not in the agent's prompt.
- One exception: a `memory` or `docs` command runs only when
  `stack.agent_audience` names it. `AUDIENCE_COMMANDS` read for the people
  in the agent's room. `NO_PRIVATE_READS` read no person's own card. The
  API refuses every other `memory` or `docs` command in every room, also
  when `allow` names it. An example is `memory ask`, because it answers
  from every person's own notes.
- A reminder runs no command of `AUDIENCE_COMMANDS`, because the scheduler
  runs it later without the people in the room.
- A `messages` command that names its sender with `--as`
  runs only as `--as agent`. Allowing `messages` lets the agent send as
  itself or as the system account, never as a person; writing in a
  person's name stays a tool for the terminal.
- No command from the agent may name a path on the host, allowed or
  not: `--vault`, `--file`, `memory write --from`, `photos import
  --source` and `messages upload` are refused before they run, because
  the API runs on the host. A shortened option (`--vau`) counts as the
  option, because the commands' parsers accept it. The same holds for the
  command of a reminder, because the scheduler runs it on the host. The
  `stack core schedule add` of an admin has no such check.
- No command from the agent may have an option between the stacklet and
  its command, except `--help`, because the CLI ignores an option it does
  not know and the checks above read a command by its position.

`stack up agent` writes the allowed commands into the agent's workspace
as an always-loaded skill, one line per command with its `HELP`, so the
agent can call one without asking for `--help` first. **`HELP` is
prompt text then**, so it names the arguments:
`HELP = "Boil the water: boil <minutes>"`. With nothing allowed there
is no such skill, and the agent's prompt is the same as without the
feature. For a runtime other than nanobot (`[agent] runtime`) it writes the
same lines as JSON pairs to `<data_dir>/agent/bot/allowed-commands.json`,
next to the account's login state.

### Scheduled and waiting commands

Core's scheduler runs a stacklet command at a time (`stack core schedule`), and core's devices commands answer a
waiting browser. Both are in [core-reference.md](core-reference.md#how-to-schedule-a-command).

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

A port the LAN needs in domain mode too is bound to `0.0.0.0` literally
instead: infra's DNS (53) and proxy (80, 443), and Forgejo's SSH, which
does not go through Caddy and is encrypted and authenticated by itself.

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

Watchtower pulls the tag the compose file names, so the tag decides how far
an image can move. Pin the minor line where upstream publishes one
(`gotenberg:8.37`), otherwise the exact version (`synapse:v1.161.0`), or a
digest when upstream tags no release at all. Watchtower then delivers patch
releases, and a minor or major version changes only with a commit. Immich
keeps to semantic versioning and tags only its major line, so it is pinned
to that (`immich-server:v3`). For PostgreSQL the major tag (`postgres:16-alpine`) is already the patch line:
its minor releases are bug and security fixes. Named tags such as `latest`,
`main` or `release` fail `tests/unit/framework/test_compose_pins.py`.

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
    image: ghcr.io/paperless-ngx/paperless-ngx:3.5
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
- URLs are `http://photos.home.internal`, or `https://` once
  `dns_provider` is set (see TLS below)
- The runtime assembles the Caddyfile from `caddy.snippet` files
- Needs two DNS records to the server's LAN IP, `*.<domain>` and
  `<domain>`, because a wildcard does not cover the bare name

### Caddy Snippets

A stacklet that serves something in a browser ships its routes in a
`caddy.snippet`:

```
# stacklets/docs/caddy.snippet
docs.{$STACK_DOMAIN} {
    reverse_proxy stack-docs-paperless:8000
}
```

The domain stays the `{$STACK_DOMAIN}` placeholder. Caddy substitutes it
from its own environment when it loads the file; the runtime never does.
Backends are container names on the `stack` network.

A snippet may use the [template variables](#environment) for a route that
depends on a setting. The ai stacklet's routes go to the server `[ai]`
names:

```
# stacklets/ai/caddy.snippet
openai.{$STACK_DOMAIN} {
    reverse_proxy {ai_openai_upstream} {
        header_up Host {upstream_hostport}
    }
}
```

The runtime replaces a `{name}` only when `name` is a template variable
and the line is not a comment. Caddy's own placeholders (`{host}`,
`{upstream_hostport}`, `{env.X}`) stay as they are. A snippet that cannot
be filled is left out of the Caddyfile and the command prints a warning,
because Caddy rejects the whole file for one bad site. A snippet cannot
be filled when a `reverse_proxy` gets a value that is no usable http or
https address, or names one of the snippet's own sites, which would loop.
A snippet that uses an empty variable is left out without a warning,
because the setting it routes to is not made (no `[ai] openai_url`).

In domain mode the runtime assembles the snippets of every stacklet that
is up into `{data_dir}/infra/Caddyfile`, in stacklet order, and the infra
stacklet's Caddy container mounts that file. "Up" is read from Docker like
every other state: the stacklet has containers running, starting, or
restarting. A crash-looping stacklet keeps its routes, because its other
containers are usually still serving.

`stack up`, `stack down` and `stack destroy` write the file, each counting
the stacklet it acts on as already in its new state, and have a running
Caddy load it with `caddy reload` once the containers have changed. The
file is written before a starting stacklet's containers exist, because
infra's own Caddy reads it the moment it starts. A reload Caddy rejects
leaves it serving its previous routes, and the command prints a warning.
Nothing is written in port mode, or while infra is not up.
`stack ai switch` writes the file and reloads Caddy the same way, because
it changes the address the ai routes go to. A CLI plugin that changes such
a setting calls `config["reload_proxy"]()`.

Around the snippets the assembler (`lib/stack/caddy.py`) emits:

- A global options block: how certificates are obtained, or, without TLS,
  every site served as plain HTTP on port 80.
- A `*.{$STACK_DOMAIN}` site that answers 404 for any host no running
  stacklet claims, a stopped stacklet's included.

Snippets describe routes only. Anything that applies to every site belongs
in the assembler, not in a snippet.

Sites for services that are not stacklets go in
`{data_dir}/infra/Caddyfile.local`. The admin writes it and the runtime
only reads it, appending it after the snippets every time it writes the
Caddyfile; `stack up infra` applies an edit. Only one proxy can own ports
80 and 443, so a hand-written Caddyfile cannot run beside this one. A local
site gets the wildcard certificate like any other subdomain. A host that a
running stacklet also serves makes Caddy reject the reload, and the
command prints the warning. `stack destroy infra` deletes the file with
the rest of infra's data.

### TLS

HTTPS for the whole stack is core's configuration (`[core] dns_provider`). It is in
[core-reference.md](core-reference.md#how-to-turn-on-https).

---

## Secrets

### Generated Secrets

Declared in `[env].generate`. Stored in `.stack/secrets.toml`,
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
- Written in one rename of a new file with mode 0600, so the file is never
  half written and never readable by other users
- A file that exists and cannot be read stops every command that reads or
  writes a secret. The command names the file and the parse error, and
  changes nothing, because a write reads the whole file and writes it back.
  Fix or restore the file, then run the command again.

### Admin Password

A single admin password is generated once and shared across all stacklets.
Stored as `global__ADMIN_PASSWORD` in secrets.toml. Available to templates
as `{admin_password}`.

Identity (who the admin is) lives in `users.toml`. Credentials (the
password) live in `secrets.toml`. Never in the same file.

---

## Users, `stack.toml` and runtime state

The family file (`users.toml`, `stack family`), the global configuration (`stack.toml`), the runtime state
(`.stack/`) and several instances on one Mac (`STACK_DIR`) are core's. They are in
[core-reference.md](core-reference.md#who-is-in-the-family).
