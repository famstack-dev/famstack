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
| `requires` | list | `[]` | Stacklet IDs that must be enabled before this one. The runtime enforces ordering on `stack up` and prevents destroying dependencies. |
| `build` | bool | false | If true, the stacklet has a local Dockerfile. `stack up` rebuilds the image on every run instead of pulling from a registry. Use for stacklets with custom code (bots, agents). |
| `stage` | string | `"stable"` | How finished the stacklet is. `"stable"` is silent. Any other value (`"beta"`, `"incubating"`, `"deprecated"`, or your own) is shown in `stack list` and warned about on every `stack up`. `"deprecated"` marks a stacklet that still works but will be removed in a future release. |
| `secure_context` | bool | false | Its web app runs only in a secure context, HTTPS or localhost, as Element does for WebCrypto. In port mode only a browser on this Mac opens it, at `{browser_url}`; screens offer other devices no link to it. |
| `attended_setup` | bool | false | Its setup needs a person at this Mac: macOS asks for permission along the way (a dialog, a grant in System Settings). `stack list` reports it, and a screen offers its terminal command for this Mac, never a button, whatever its questions are. The backup sets it. |
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
| `{ai_openai_url}` | Derived from `stack.toml` → `[ai].openai_url` |
| `{ai_openai_url_docker}` | Same, rewritten for container access via `host.docker.internal` |
| `{ai_openai_key}` | `stack.toml` → `[ai].openai_key` |
| `{ai_whisper_url_docker}` | Derived from `stack.toml` → `[ai].whisper_url`, or the AI server when unset |
| `{ai_speech_url}`, `{ai_speech_url_docker}` | The server that speaks, as its root (requests go to `/v1/audio/speech`): `stack.toml` → `[ai].speech_url`, or the AI server when unset |
| `{ai_speech_key}` | `stack.toml` → `[ai].speech_key` with a speech server of its own, otherwise the AI key |
| `{ai_speech_voices_json}` | The voice of each language as JSON: `stack.toml` → `[ai.voices.<lang>]` (`model`, `voice`, `clip`) over the stack's own voices; read back with `stack.ai.voice.voices` |
| `{ai_whisper_key}` | `stack.toml` → `[ai].whisper_key` with a dedicated speech server, otherwise the AI key |
| `{ai_whisper_model}` | `stack.toml` → `[ai].whisper_model`, default `whisper-1` |
| `{ai_default_model}` | `stack.toml` → `[ai].default` |
| `{ai_tts_voice}` | Derived from `[ai].language` |
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
name          = "TTS"
url           = "http://localhost:42063/"
hint          = "TTS not running — check 'docker logs stack-ai-speech'"
skip_when_env = "STACK_AI_NO_VOICE"
```

`skip_when_env` names an environment variable that, when set to `"1"`,
drops the check entirely. Use it for a part of a stacklet the user can
choose not to start: `stack up ai --no-voice` leaves the speech containers
out, and without this the start sequence waits a full timeout for each of
them and then reports containers it was told to skip as broken. Any other
value, including `"0"` or empty, leaves the check in place, so an
accidentally exported variable cannot quietly disable monitoring.

A stacklet that can be served by another machine declares a top-level
`remote = "{template}"` in its manifest. When it renders to an address,
`stack list` shows the row as `remote` with that host, or `localhost` when
the address is this machine, meaning something on it that the stack does
not manage serves it. Only the checks marked `remote = true` probe that
server; the others probe this Mac, so they run only while the stacklet's
own containers run here as well.

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
| `name` | Display name. |
| `check_url` | Where the service answers. Supports `{template}` variables from `stack.toml` (e.g. `{ai_openai_url}`). |
| `start` | The command that starts the service. Optional. |
| `stop` | The command that stops the service. Optional. |

### Bot Convention

A stacklet ships a bot by adding a `bot/` directory with a `bot.toml`
manifest and a Python file. The bot runner (in core) discovers bots
across all enabled stacklets and runs them in one async process.

Bot IDs always end with `-bot` (e.g. `archivist-bot`, `scribe-bot`).

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

### Devices and keys

A Matrix login is a device with a key store of its own
(`stack.matrix.device`). A bot keeps its device across restarts in its
stacklet's data dir, `~/famstack-data/<id>/bot/`:

```
archivist-bot.session.json        the login: token, user id, device id
archivist-bot_crypto/             the device's key store
archivist-bot.cross-signing.json  the account's cross-signing keys
```

The cross-signing keys belong to the account, not the device, so the file
is named after the account. The bot's device owns them: it makes them
once, publishes them and signs itself with them, so apps show the bot as
verified. Any other device of the account only signs itself with the same
file. It never makes keys, and it stays unsigned when the file is missing
or the server holds other keys, because new keys would replace the
account's identity and leave the bot's own device unsigned.

`stack messages send` into an encrypted room uses such a second device.
The host CLI cannot encrypt, so it runs `python -m stack.matrix.send` in
the bot runner, the request (password included) on stdin. That device is
`<account>-cli` next to the bot's files, shown in apps as
`<account> (command line)`. It is never the running bot's device: two
processes on one key store break each other's encryption sessions. Two
sends as one account take turns on a lock. A person's command-line device
keeps its state in `~/famstack-data/messages/cli/` and stays unverified,
since the stack does not hold a person's keys. An unencrypted room gets
the message over plain HTTP from the host, as before, and no device of
this kind is created.

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

To send a silent event from a bot, call `MicroBot.emit_event`:

```python
await self.emit_event(
    room_id,
    "dev.famstack.event",
    {"source": "docs", "type": "service.started", "summary": "…",
     "data": {…}, "actor": "archivist-bot", "ts": "…"},
)
```

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

Both paths are best-effort; transport failures are logged but never
raised. A downstream bot being offline must not take the emitter's
main path with it.

Subscribing to custom events on the bot side isn't wired yet — that
comes with the first consumer bot. The emit contract is stable and
tested end-to-end (see `tests/e2e/test_archivist_e2e.py`).

### Persistent links and card ids

A link a bot posts into a room stays in the timeline for good, so it
never points at a service or a file path. It points at
`<home>/go/<card_id>` for a card, or `<home>/go/<kind>/<name or id>`, and
core's tools server resolves it when someone opens it. `stack.links` builds
these paths (`go_card` for a card, `go_docs`, `go_topic`, `go_person`,
`go_list`, `go_capture`, `go_diary`, and `go_page` for a page an edit
touched); `stacklets/core/tools-server/resolver.py` is the only place that
knows what they resolve to.

The same published port serves the dashboard at `/dashboard`, open to the
home network until there is a login: a page whose widgets arrive rendered
and enhance themselves (`stacklets/core/tools-server/dashboard/widgets/README.md`).
Its first widget is the memory's activity, read from the cards API's
`/history`. Everything else on that port stays internal.

`GET /dashboard/api/health` answers `{"ok": true}` and asks the host API
and the memory nothing: it is core's health check, so `stack up core`, and
the installer before it gives the dashboard's address or opens a browser,
count core as up only once the dashboard answers.

Every dashboard page opens with the family as pills, read through the
host API's `family` on every request (so a change shows on the next page,
without restarting core), and greets the household; its script labels
each pill as whoever the device remembers would say it and greets that
person by time of day. Until it is set up, a "Setting up" widget leads
through six steps, chat first, each Add a job like the stack widget's.
Its steps offer the core (chat, Photos, Documents, AI, the family memory)
and what the core `requires`; "Your stack" lists what runs and offers the
rest under "Also available", a beta stacklet with its mark. The setup
reads who is in the chat through the host API's `chat_users`
(`stack messages users --json`, read-only). A tile whose stacklet
`requires` one not set up says what it comes after (`stack up` refuses it
until then). One whose first `up` still asks questions in forms
(`asks_in_forms` in `stack list`) is added from the page: its Add opens
the forms in a sheet and posts every answer so far to
`POST /dashboard/api/configure/<id>`, which relays them to the host API's
`configure` and answers with the next form, the same form with the reason
under a field, or ready, after which the page starts the job. A secret
(`writeOnly`) is answered only from a page opened on this Mac: from any
other device the route walks the forms itself, forwards only answers to
fields of forms without a secret, and at a form with a secret answers
with the terminal command; a secret posted anyway never reaches the host
API. A stacklet whose hook asks for itself shows its terminal command.
Messages asks only for the chat server's name, so once the family has
named it, the chat is added with a plain Add. The forms' words are the
hooks', in English. While the family is empty, `/dashboard` is the
first run: three moments and "Who else is at home?" set up the family in
the browser and post it to `POST /dashboard/api/family`, which writes it
through the host API's `family` set as `stack family set` does and
answers with the family (200) or every problem by its field (422).
`GET /dashboard/api/family` is what the pills read. Like the job route,
both are open to the home network until there is a login. The dashboard's
writes take a body only when it is sent as JSON (`application/json` or a
`+json` type) and refuse anything else with 415, so a page from another
site, which can post text or a form without asking the browser first,
cannot reach them.

**The memory format has a document of its own:
[memory-format.md](memory-format.md).** What a card is, the card types,
their fields, a note's sources and sidecar, media, views and the folder
layout are described there for anyone reading a memory without the stack,
and defined by the JSON Schemas in `stacklets/memory/schemas/`. This
section is what the stack provides around that format: ids, links,
lookups, the migrations that brought older files to it, and the
interfaces that read and change cards.

A card's path is not a stable name, so anything that refers to a card from
outside it (a `/go/` link, a correction, the agent) finds the card by an
id. A stored path is only a hint. Before every card carried a `card_id`,
each type was found by an id of its own, and those links still resolve:

| Type | Card id | Link |
|---|---|---|
| document | `paperless_id` | `/go/docs/<paperless_id>`, opens Paperless |
| note, bookmark | `capture_id`: the event id of the message it was filed from | `/go/capture/<capture_id>` |
| email thread | `resource`: the `mid:` URI of the thread's first message | none yet |
| diary card | `entry_id` | `/go/diary/<entry_id>`, opens its month page at the card |
| person | the file name, `<bucket>/people/<id>.md` | `/go/person/<slug>` |
| topic | its folder, `<bucket>/<slug>/`, whose card is `topic.md` | `/go/topic/<slug>` |
| list | none yet: its file name is its name | `/go/list/<slug>` |
| list archive | `<list>/<YYYY-MM>`, from `<bucket>/lists/archive/<list>/<YYYY-MM>.md`; part of its list | none |
| correspondent | `slug`, else its file name, `<shared>/correspondents/<slug>.md` | none yet |
| note, bookmark written by hand | its file name (no message to name it by) | none |
| a page the wiki generates to navigate (a diary month, an index) | `index/<path>` | the wiki's page |

The card graph behind `stack memory find`, `card` and `links`, and the
cards API, reads its cards from the vault only (ADR-016); navigation pages
and the views the wiki compiles into the brain are never found or opened
as cards. A card comes with the views that name it, the one about it
first. A card is named by its `card_id` when it has one
(`stack.cards.card_ref`); one without is named by the rule in the table
above (`card_name`), and that name still opens a card that has an id
since. Every link the graph draws names the card it reaches by its id. A
topic is a folder of one bucket, so a card belongs to the topic of the
folder it is filed in, in its own bucket, and a list's archive to the
list beside it; a slug in a card's `topics` is looked for in its own
bucket first. Where buckets share a name from before card ids (the
family's camping folder and Homer's were both `topic/camping`), the name
opens the card whose id it spells, `topic-camping`.
`card_kind` tells a list archive and a navigation page from the rest.

**Views name their cards and follow them** (ADR-016 §3 and §5, ADR-017
§7). `stack.cards.is_view` is the one rule every reader uses to tell a
view from a card, and still knows the older `generated: true`. A view's
versions are `stack.cards.card_version`, the file's git blob id. The card
graph attaches to a card every view whose `about` or `derived_from` names
its `card_id`. A view whose `compiled_from` holds another version of the
card than the vault does is behind it: `stack memory card` says so on the
view's line, the cards API gives the view `behind: true`, and the card's
own text counts.

When a card is pushed, the curator compiles again exactly the views
whose `derived_from` names it before or after the change, and the ones a
new card would now be in (the people, topic and correspondent it names,
its diary month), in two speeds. At once, with the mirror, without the
model (`stack memory wiki update ... --fast`, `diary pages --fast`):
which cards belong, list items, dates and links, with the model's last
wording renumbered to the cards as they are. After the quiet window
(`wiki_rebuild_quiet_secs`), once per burst, with the model. A change
to a list or its archive is compiled at once only: the person and topic
pages read a list's open items from the list file and the home page
shows no list, so the model would write the same text again. A topic
page's text about its to-dos follows with its next model compile (a
filing that touches it, or the nightly sweep). The model's
compile runs in the background, one at a time, so a push during the
model's turn is compiled at once all the same and joins the next model
compile. The model is asked from the cards as they were, and its answer
is rendered over each view as it is when written, the cards read again
under the brain's writer lock (`memory/bot/brain_lock.py`), which every
writer of the brain takes, the commit included.

A compile with the model asks the model only for a view whose request
changed. Each view keeps a fingerprint of its request next to the model's
answer. The fingerprint covers the AI server, the model and the request
settings. It also covers the prompt and the sources that the answer
cites.

The fingerprint of a person page uses the prompt without the previous
page, because that text is the model's own last answer. It also contains
the page layout stamp.

When the fingerprint is the same, the view uses its kept answer. This is
what `--fast` does. The compile then prints `<page>: unchanged since the
model wrote it, not asked again`. The compile decides this under the
brain's writer lock, because no other writer may change the view in
between.

The model is asked again when one of these changes:

- What the view reads of a card: its title, date, summary, people, who
  filed it, or its link. Not every view reads all of these.
- The prompt itself: a card added or removed, a new prompt file, the
  date on the home page.
- The model's name, or the AI endpoint (`stack ai switch`).
- The layout stamp of a person page.

The model is also asked again in these cases:

- The kept answer cited a card that is gone.
- The kept answer does not pass the current checks of the page.
- The view was compiled before views kept a fingerprint.

The rest of a card does not ask the model again. This includes its body
text and the fields that the view does not read. A new model served
under the same name rewrites nothing until one of the changes above
occurs.

The nightly sweep asks the model for the home page every night, because
the home page's prompt contains the date. On a night with nothing filed, the
person and topic pages do not ask the model.

Forgejo
tells the curator of each push to `family/memory` through a webhook to
`http://stack-memory-curator:8090/push` (the cards API), which `stack up
memory` adds once; the code stacklet allows webhooks to `stack-*` hosts
(`FORGEJO__webhook__ALLOWED_HOST_LIST`). The poll stays as the fallback,
and the nightly sweep as the catch-all.

**A record links the cards it names** (ADR-017 §3, §4, §10).
`stack.cards.links` holds the rules: `persons` as declared person ids, the
meta block's links derived from them, and the correspondent's and the
topic's card written the first time a record names them, never rewritten.
The archivist writes a correspondent's card with the classifier's
`correspondent_aliases`. Paperless keeps its own person tags. The same
rules brought the files written before to the same shape: `memory/0003`
(people by id, `Person:` tags dropped, and links to a document that moved
since, found by its Paperless id), `memory/0004` (the cards of
correspondents and topics) and `memory/0009` (a correspondent written by
hand, which named itself in `canonical`, gets that as its `title`). The
graph links a record to the people its `persons` names, by id or by any
name they go by, and the wiki sends a link to a person's, a topic's or a
correspondent's card to that card's view (`quartz/plugins/vaultLinks.ts`). The diary
compile resolves the names the model read to declared people
(`stack.cards.links.About`) and leaves a name nobody goes by off the card,
logged. `memory/0007` brought the diary cards the compile still owned to
that shape and signed them again (`stack.cards.links.persons_by_id`); a
diary card a person changed kept its names.

**A card tags a topic by its id** (ADR-017 §4). `stack.tags` holds the
rule. A topic's id or its label in any of the ontology's languages, in any
case and spacing, is written as the id; anything else is lowercased with
hyphens between its words. The archivist writes every record's tags
through it, slugging a tag it cannot read (`Moe's` becomes `moe-s`), and
so does the diary compile; `memory/0005` brought the files written before
to the same shape, leaving a tag it cannot read as it is and naming it,
and `memory/0010` the diary cards, which had held the topic's label:
signed again where the compile still owned them, a person's left
unsigned. The label a reader
sees comes from the ontology in the instance's language: the card browser
shows it, and `stack memory find`, memory search and the wiki find a topic
by its id and by its label in every language.

**A card's `kind` is one of its type's** (ADR-017 §6,
`stack.cards.CARD_KINDS`). The nightly move writes `kind: archive` on
every list archive it adds to (`stack.lists.as_archive`), the diary
compile writes a diary card's `kind`, and `memory/0006` gave the files
written before theirs. The renderer shows an archive's lines without a
checkbox. A diary card is signed with a digest of its text
(`stack.digest`), and a migration that changes a card the compile owns
signs it again.

`stack memory check-format` (and the cards API's `/report`, which the
card browser shows) lists where the vault does not meet the format yet:
cards without a `card_id`, per type; a `card_id` two files carry; links to
a file the vault does not have; files with no frontmatter or a `type` that
is no card type; tags that are neither a topic id nor a keyword; a topic
card whose folder is a topic id of the ontology, which wins; a `kind`
outside its type's set, a list archive without `kind: archive` and a diary
card that still says `medium`; a note whose sources and sidecar do not
pair up, and a sidecar whose card is gone; a sidecar value its schema does
not allow; fields a card's type's schema does not name, which a change to
the card is refused for until they are removed (`stack memory card edit
<id> --remove <field>`). It is the admin's, read over every card, and
writes nothing. A vault that meets the format gives an empty report.

**A note is its card and its sidecar** (ADR-018, `stack.cards.sources`, the
format in [memory-format.md](memory-format.md#a-notes-sources)). The
sidecar's `made.code_commit` is `STACK_COMMIT`, and its template hashes
(`prompt_template`, `correction_template`, `conversation_template`) hash a
prompt's skeleton, with a fixed placeholder for every value that varies
per call, so they change with a template's wording, never with the day or
the note. A note in its writer's own words that has corrections (replies,
or `--msg`) or a person's edits of its clean version is rebuilt in two
calls (ADR-018 §7), the capture prompt reading the rebuilt text as the
note for the title, tags and persons. With corrections alone, the
correction prompt (`note_correction`) writes it from the original's words
and every correction. A note a person also edited by hand is asked the
card conversation (`card_conversation`): the original's words, the clean
version before the first correction or edit as the model's own answer,
and one message per correction or edit in time order. The rebuilt text is
checked by the first filing's rule, less what an event replaced
(`clean_words.kept_rebuilt`). Filing, a correction in the chat and
`stack memory reprocess --apply` write card and sidecar in one commit,
through the curator, the memory's one writer (below); a refile reads the
sources, never the interpretation, and appends to them. A note is found by a source's id
(`stack.cards.lookup.find_by_source_id`, through `find_card("capture", …)`),
so a correction to a chat message finds its note. Migration `memory/0008`
converts every note filed before, photo notes included, in one commit and
without a model call: the old sections become quotes, the fields move into
the sidecar, and a reply's missing time and id, a photo the media store
has no record of and words that carry prompt text are kept, marked
`reconstructed` and named.

**A card type has one renderer, and it writes markdown** (ADR-016 §4,
`stack.card_render`). `render(fields, body)` gives a card's title, one
line of the facts that matter for its type, and its body: the type's own
sections, then the person's own words, quoted, then the replies. Each
field appears once: the heading, the meta block under it (`> **About** …`)
and `Person:` tags are not shown, people are shown by name (`names` maps
an id to one), and due dates keep the form the files write
(`📅 2026-10-15`). The briefing callout becomes its summary, its facts
and its to-dos, the last under one label in the household's language.

| Type | Line of facts | Body |
|---|---|---|
| `document` | date · people · correspondent · `document_type` · `[#<paperless_id>](<resource>)` | summary, facts, to-dos, the document's text |
| `note`, `bookmark` | date · people (· the site, for a bookmark) | summary, facts, to-dos, media, then the paste and the caption, then the replies; a note filed from a person's words has the clean version of them instead of a summary, facts and to-dos |
| `diary` | date · people · 🎤, 📷, 🎬 or 📎 for its `kind` | summary, facts, media, quotes, then the transcript or text, then the replies |
| `email` | date · people | each message under its heading (day and sender): summary, to-dos, its words |
| `list` | `kind` | the description, then the items as the file writes them; an archive's without a checkbox |
| `person`, `correspondent`, `topic` | other names · `@account` | what the family wrote |
| any other | none | the fields as a list, then the body |

Every surface shows that markdown: `stack memory card` prints it (and
returns it in `rendered` with `--json`), the cards API returns it in
`rendered` and the card browser shows it as the card's body, and the
archivist's filing, capture and diary replies and its search hits show
the card through it. In the chat, `stack.card_render.for_chat` turns
media into a link and a link into the memory repo into its label, since
the chat shows neither. The renderer reads the file and never changes it.

The cards API also serves the media archive (`<data>/memory/media`) at
`/media/<path>`, for the photos and recordings cards embed
(`![[/media/2026/10/<event id>.png]]`). A file goes only to an audience
that may read a card embedding or linking it; with no audience, the admin
reads every file. Paths with `..` and anything outside the archive are
refused. Images, audio, video and PDFs are sent as what they are; any
other file as a download.

**A card changes through one endpoint** (`stacklets/memory/card_edit.py`).
Every surface that changes an existing card is a client of it: `stack
memory card edit`, the card browser later, the agent through the command.
None validates or commits on its own. The memory stacklet serves it on
the cards API; core passes it on at `/cards/api/cards/<card_id>` on
`127.0.0.1:42002`, with the request's `Authorization`, `Content-Type`,
`If-Match`, `Famstack-Actor` and `Accept` headers, and holds no token of
its own (the bot runner reads core's `.env`).

| Request | What it does |
|---|---|
| `GET /cards/<card_id>` | The card as `{"card_id", "path", "fields", "body", "rendered"}`, its version as the `ETag`; the file itself with `Accept: text/markdown` |
| `PATCH /cards/<card_id>` | `application/merge-patch+json` (RFC 7396) on `{"fields": ...}`; a `null` removes a field, a list is sent whole |

A change needs `Authorization: Bearer <memory__CARDS_WRITE_TOKEN>` (a
secret memory generates; the curator checks it, the host CLI and the bot
runner send it), `If-Match` with the `ETag` it was made on, and
`Famstack-Actor` naming a declared person with an account. The `ETag` is
the file's git blob id, which Forgejo calls its `sha`. The endpoint reads
the file as the curator's clone holds it, applies the change, checks the
fields against the type's JSON Schema (`stacklets/memory/schemas/cards/`),
derives the meta block's `**About**` links from `persons` and the title
heading from `title`, and commits it through the card store, the person
as author (below). The subject is `edit:
<title>` and the body names each changed field with its new value; a
rebuild reads the change back from the card's history, as it reads a
hand edit (ADR-018 §7). A change that changes nothing makes no commit.

A person changes fields, with `PATCH`, and nothing else. A whole card is
written only by `stack memory reprocess --apply`, card and sidecar in one
commit, through the curator's API (below), not this endpoint.

Answers: `200` the card and its new `ETag`; `401` no write token; `404`
no such card; `412` the card
changed since `If-Match` (nothing written);
`415` another media type; `422` `application/problem+json` (RFC 9457)
with `errors`, one `{"pointer", "field", "message", "allowed"}` per field,
for a field outside its schema, a field no schema names, a `readOnly`
field changed, or an actor
who is no member; `428` no `If-Match`; `503` before the curator has its
clone of the memory, or without a Matrix server name to make a person's
commit address.

**The schemas are checked in two places.** The curator's card store
validates every card it writes with `jsonschema` (below), in the curator's
container only, building the `urn:famstack:vault` resource from the memory
repo for each check
(`card_schema.py`): `person_id` (the declared people), `topic_id` (the
ontology and the topic cards' folders) and `tag` (a topic id, or a keyword checked as `format:
keyword` by `stack.tags.is_tag`). The format check runs on the host as
well, which is stdlib-only, so it reads the same files with the standard
library: `card_fields.py` the field names each card type has, and
`sidecar_schema.py` `schemas/sidecar.json` with every keyword that schema
uses, raising on one it does not know. A test holds that reader to
`jsonschema`'s verdict.

`stack memory card edit <card_id> --set f=v --add f=v --remove f[=v] --as
<person>` reads the card and its `ETag` through core's door, builds the
merge patch (`--add` and `--remove` change a list, sent whole; `--remove f`
removes the field), sends it, and prints the change or each refused field
with what it may hold.

`/history` is the memory's activity, newest first, read from the repo's
log (`stack.cards.activity`): each change with its time, who made
it, whether it is the machine's own work (`machine`), its kind in a word
(`filed`, `replied`, `corrected`, `edited`, `refiled`, `migrated`, ...;
from the subject prefix and the `Migration:` trailer, by the closed
commit grammar of ADR-018 §6 in `stack.cards.commits`) and the
cards it touched, each with its
`/go` link path. A card and its sidecar count once; a change over many
cards lists the first few, a `count` and how many of each type. A change
touching no card the audience may read is left out.

**Every card carries a `card_id`** (ADR-016; its form in
[memory-format.md](memory-format.md#the-card-id)).
`stack.cards.new_card_id` makes it for every writer; a writer that
rebuilds a card from its source keeps the one the file has.
`stack.cards.with_card_id` gives one to a file that has none: migration
`memory/0002` did so for the cards already there, and the curator does it
for each file pushed without one, or with one another file carries (the
file first committed later gets a new id). Readers take the id from
`stack.cards.card_id_from`.

**`/go/<card_id>` opens any card**, on the page the link of its kind in
the table opens: a note on its wiki page, a diary card on its month at the
card, a person, a topic or a correspondent on the page compiled from their
card, a list on the list. Bots post it wherever
they link a card (a filing reply, a search hit, the reply to an edit;
`go_card`), and fall back to the link of the kind for a card that has no
id yet. The links in the table keep resolving as before. A document's
filing reply still opens Paperless (`/go/docs/<paperless_id>`).

**A card whose id changes keeps the old one.** A card that becomes another
type is a new card with a new id: it lists the ids it had in
`former_card_ids`, in the same commit (`stack.cards.card_identity`, which
every writer uses, so a rebuild keeps both fields). `/go/<former id>` opens
the card, and `stack memory card <former id>` shows it and says which id it
was asked for. The card graph opens it by a former id too (`stack memory
links`, the cards API's `/card/` and `/links/`), unless another card
carries that id as its own now. A photo filed as a bookmark and refiled as a note
(migration `docs/0001`) is the case today.

**A card that was removed answers as gone.** When no card carries an id,
`stack.cards.lookup.removal_of` reads the memory repo's history for the newest
commit that deleted a file with that `card_id`. `/go/<card_id>` then
answers 410 with who removed it, the day and the commit (not its subject,
which can carry the title), and `stack memory card` says the same to an
audience that could have read the card. An id no card ever carried is a
404. Core's tools server reads the memory repo read-only for this, and git
in its image; cards themselves are still found in the wiki's copy.
Opening a link writes nothing.

The kinds found by id in the vault are listed in `stack.cards`
(`CARD_ID_FIELDS`); the lookups are in `stack.cards.lookup`: `find_card` by
the id of a kind, `find_by_card_id` by a `card_id` or a former one.
They scan the frontmatter of a copy of the vault, with no index to go
stale, and answer None for an id no card carries rather than guessing. Each reader runs it over the copy it reads
from: core's tools server over the wiki's copy, so a `/go/` link lands on
a page the wiki serves (404 otherwise); the archivist over its own mount,
before it applies a correction to a capture, falling back to the path
the filing message recorded while that copy has not caught up.

**`stack memory reprocess <card_id>` refiles one note under today's
rules**, from its sources, never its summary (ADR-018). They are
`memory.note_card.note_sources`: the sources the card and its sidecar keep
(an older note's read the way migration `memory/0008` converts it), then
each reply in its message's thread the card does not hold yet (read in the
bot runner as the archivist: the room is the one of its rooms that has the
original's event, the thread its `m.thread` relations, bot accounts left
out), then `--msg` as a `note` source. Sources are only appended: the chat
replaces no quote. The model is given exactly this list, and the preview
returns it in `sources`, each record with its `quote`, so a surface that
shows a card's sources shows what the model read.

The preview writes nothing to the memory: it prints the card as it is and
as it would be (both through the card renderer), the diff of the card and
its sidecar and a proposal id, and keeps the proposal in
`<data>/memory/proposals/`, outside the memory's git tree. `--apply
<proposal_id>` commits exactly the previewed card and sidecar, with no
second model call, as one commit `reprocess: <title>` with `Replaces:
<sha>`, so one `git revert` undoes it. A refile is a machine rebuild: the
memory bot commits it, with `Requested-By: <person>` naming who asked,
and the sidecar's `made.requested_by` names the same person: `--by
<person>`, or `admin` (the admin at the terminal) when none is given.
`--msg "<text>" --by <person>` is a correction: the refile reads it with
the replies, the sidecar keeps it as a `note` source by that person, and
the commit is theirs, with their words in its body (ADR-018 §6).

A refile keeps what people did to the note (ADR-018 §7). The card's
history has one reader, the curator's card store (`CardStore.history`,
through `stack.cards.history.card_versions`), which the host and the
archivist in the bot runner ask for it (`GET /cards/<card_id>/versions`;
`GitMirror.card_history` in the archivist). It follows the card across
moves by its `card_id` (a person's rename or move in Forgejo or a clone;
the archivist keeps a note at the path it filed it under): a move is one
commit that removes the card at its
old path and adds it at the new one, and the old file is followed only
when it held the same `card_id`. The commit that put the card at its path
is read on its own for the files it removed, unless it is the card's
filing, so a note that never moved costs no extra read. Another card that lived at a path the
card had is never read as its past. `history_of` reads what it answers.
Whose commit counts is `stack.cards.commits.is_machine`; a person's commit that
carries a source (a filing, a reply, a refile with `--msg`) is a
correction, read from the sources. From every other person's commit it
takes the change to the clean version, a message of the card
conversation, and the change to the fields, as operations (`stack.cards.fields`):
`add` and `remove` for a list by set difference, so the model's other
suggestions still come in, `set` and `unset` for a value. The host sends
the history to the bot runner with the request (`prior`, the clean
version before the first correction or edit, `edits` and `ops`); the
operations are applied in the bot runner when the card is composed
(`GitMirror.capture_entry`), after the model, in order, each checked against the
card schemas (`schemas/cards/`, read with the standard library: the
field, `readOnly`, `required`, type, `minLength`, `pattern`, and the
instance's people, topics and tags). One that no longer fits is left out
and named: printed, and `left_out` in the JSON. A note without
corrections or edits is refiled from its words alone, one call. A card
edited by hand that shows no clean version
(one that would only repeat the words) is given one by the capture prompt
first, and the conversation starts from that (`clean_words.starting_point`).

Refused, with the reason and a `refusal` code: a card that is not a note
(`not-a-note`) or a note with no sources, written in the memory rather than
filed (`no-sources`); a note without its words, a
card whose quotes and sidecar sources do not pair up, or words that carry
prompt text from an older reprocess (`words`); no card with the id
(`no-card`); a model half that made no proposal (`no-proposal`). Preview
and apply both refuse when the curator cannot be asked (`unreachable`),
and the apply when the card or its sidecar changed after the preview
(`changed`). With
`--json` a preview returns `card_id`, `path`, `proposal_id`, `current` and
`proposed` (each `file`, `rendered` and `sidecar`), the `diff`,
`clean_rejected` (why the model's clean version was not used, "" when it
was or none was needed), `sources` and `left_out`; an apply returns `commit` and
`replaces`. The model half runs in the bot runner (`reprocess-note` in the
archivist's CLI entrypoint), through a mirror that composes the files and
writes nothing (`GitMirror.capture_entry`); the bot runner never writes to
the memory for this command.

The preview reads the card and its sidecar through the curator and keeps
their versions with the proposal. The apply sends exactly those files to
the curator as one commit, each on the version the preview read; when
either changed since, the curator refuses the whole commit and nothing is
written (`changed`).

**The memory has one writer, the curator** (ADR-019). It holds the
memory's clone (`<data>/memory/vault`) and the card store over it
(`stacklets/memory/cards/`), and every change to the memory goes through
it: the archivist's filings, declarations and list folds, the diary, the
list archive, the card editor, `stack memory write`, `list`, `person`,
`ontology` and `reprocess --apply`, the migrations, the seed of a new
memory and the person files. No other process writes the clone, and
nothing writes the memory through Forgejo's API. Its API, on the curator
and through core's door at `http://127.0.0.1:42002/cards/api` on this Mac:

| Request | What it does |
|---|---|
| `POST /commits` | one commit of the changes, with the write token |
| `POST /sync` | pull Forgejo's latest and push what waits, now, with the write token; answers the commit the clone stands at afterwards (`head`) |
| `GET /files/<path>` | a file and the version to write it on |
| `GET /tree/<dir>` | every file under a directory, likewise |
| `GET /paths[/<dir>]` | the paths alone, of a directory or of the whole memory |
| `GET /pending` | the commits Forgejo does not have yet, and how the last cycle went |
| `GET /cards/<card_id>/versions` | the card's history, followed across its moves |

A write names, for each file, the version it was read at (its git blob
id, `""` for a file that must not exist yet), the person or bot it is by
(`by`) and the bot that writes it (`via`). The store makes the message
from `kind`, `title`, `body` and `trailers` by the commit grammar
(`commit_subject`), with the person as author (ADR-015) and the bot as
committer (`<bot>@local`):

    {"kind": "chore(lists)", "title": "marge changed Camping", "by": "marge",
     "via": "memory-bot", "changes": [{"path": "family/lists/camping.md",
     "text": "...", "base": "<version>"}]}

Every write is checked before it is committed (`card_check.CardCheck`): a
markdown file with a `type` meets its type's schema, with the instance's
people and topics; a card keeps its `card_id`, or lists the one it had in
`former_card_ids`, unless another file still carries that id (a copy, which
may get an id of its own); a note's sidecar carries the note's `card_id`. A page
written by hand, without a `type`, goes in as it is. A migration's commit
(its `Migration:` trailer) is checked for identity only: migrations bring
cards into the format in steps, and the migration chain's test checks the
end. Writing the same text again commits nothing.

The curator pushes before it answers. With Forgejo down, the commit stays
in the clone, pending, and the next cycle or `POST /sync` pushes it. When
Forgejo and the clone both moved, the clone's own commits are replayed on
top of Forgejo's, each keeping its author and committer; a replay that
conflicts is put back, and the clone is reported diverged until a person
decides. Nothing is ever pushed by force. Answers: `201` with `{"commit",
"pending", "mirrored"}`, or `200` with an empty commit when nothing
changed; otherwise a problem document (RFC 7807) whose `reason` is
`malformed` (400), `unauthorised` (401), `changed` (409, with the
`paths`), `refused` or `invalid` (422; `invalid` with `errors`, one
`{"path", "field", "message"}` per field), `missing` (503, no clone yet)
or `failed` (500).

Memory's own code reaches the API through `memory_api.MemoryAPI`
(`from_host(config)` in a command, `from_hook(ctx)` in a hook or a
migration, `from_container()` in the curator's and the bot runner's
containers); the archivist through its own client
(`stacklets/docs/bot/memory_client.py`), since the docs stacklet imports
none of memory's code. The curator clones the memory itself and gives a
memory with no commit its seed as the first commit. Its API answers from
its first moment, before there is a clone: `stack up memory` waits for
`GET /pending` through core's door, and the install asks it to sync once
Forgejo has the repository.

Every commit `ForgejoClient` writes (the brain's scaffold, until the brain
moves into the curator too) names its committer: the client's `committer`
(a name and an address, no account needed) when it has one, else the
account the client signs in as, which it asks Forgejo for once (`GET
/user`). The author is the person a change is by or for, when the caller
names one (ADR-015). The memory bot has no Forgejo account: the memory
stacklet writes the scaffold with the admin's token through
`memory_forgejo`, which names the memory bot as committer.

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

**`[[backup.snapshot]]`** declares mutable state that cannot be rsynced.
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
| `git` | The other capture: `repo` is a git repository on the host (`{data_dir}` is rendered). The snapshot holds `<name>.bundle`, made with `git bundle create --all`, and the restore command is `git clone <name>.bundle <name>`. An entry sets `postgres` or `git`, never both. |
| `include` | Files that must travel with the dump for a restore to be possible. Globs allowed; a path this install never created is skipped rather than failing the snapshot. |

The capture key is namespaced so a stacklet keeping state somewhere other
than Postgres can declare a snapshot without the contract having to
pretend every database looks like this one. Driving a containerised
Postgres lives in `stack.postgres`, shared with whatever eventually
restores one, rather than inside the backup coordinator where only it
could reach it.

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
snapshot as unchanged.

Snapshots run before the sync, so a dump is never newer than the files it
references. `pg_dump` takes its own consistent view, so nothing stops and
nobody is logged out while it runs. The tarball records the image
versions and digests that produced it; nothing reads that yet, but it is
the one part of a snapshot that cannot be added afterwards, and a restore
has to be able to refuse an incompatible target.

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

Running is not healthy, with containers or without. `stack up` waits for
the `[health]` checks before `on_start_ready`, and `stack list` and `stack
status` show a running stacklet whose checks fail as degraded, with their
hints. The commit in the file is what `stack doctor` and a bare `stack
restart` compare with the checkout, as they do a container's `stack.commit`
label.

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
this Mac and says why, never Add, whatever `asks_in_forms` says.

### `stack up <id>`

Brings a stacklet to the **running** state. Idempotent — safe to run
repeatedly. Every run refreshes config so changes in stack.toml take
effect.

```
 1. Check requires — fail if dependencies not running/stopped
 2. Render .env from templates + generate missing secrets
 3. First run only:
    a. hooks/on_configure.py — setup questions, as forms
    b. hooks/on_install.sh — create dirs, install deps, build
 4. Write .env to stacklet directory
 5. Bot runner discovers bots (if stacklet has bot/bot.toml)
 6. hooks/on_start.sh — start native services (host stacklets); without
    a compose file, write `.stack/<id>.up`
 7. Write the Caddyfile, counting this stacklet as up (domain mode)
 8. Build or pull Docker images
 9. Start containers (docker compose up -d)
10. Reload Caddy (domain mode)
11. Wait for health check, with containers or without
12. First run only:
    a. hooks/on_install_success.py — obtain tokens, seed data
13. hooks/on_start_ready.py — service is healthy, seed data, sync accounts
14. Show welcome screen with URL, login, hints
```

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
  the start), `warning`, and `result` last, exactly once.
- `result` carries what of `up`'s result a screen may show, listed in
  `stack.live.envelope.result_for_screens`: `ok`, `error` or `cancelled`,
  `stacklet`, `name`, `stage`, `first_run`, `warnings`, `migrations` and
  `healthy` (whether every health check passed; a run can be `ok` without).
  Never the rendered `env` or the hints, which can hold a password; a
  caller that needs them asks the host API without `stream`.
- A step's text and a warning are words for people and never carry a
  secret's value.
- stdout carries nothing else. What people read goes to stderr, and so
  does anything a hook or a tool writes to stdout meanwhile. The exit
  code is the one `stack up` always has.

The host API relays it: a JSON request `{"cmd": "up", "stacklet": "<id>",
"stream": true}` gets each line as the CLI writes it, then the connection
closes after the `result`. Only `up` streams (`STREAMED_COMMANDS` in
`stacklets/core/famstack-api.py`). A run that writes nothing for 21
minutes, longer than any step of `up` may take, is ended, and the API
writes the `result` itself, as it does when the CLI ends without one.
Without `"stream"` the reply stays one line.

The pieces are modules of `stack.live`, each usable on its own: `envelope`
(the event), `emitter` (what `--events` writes through), `follow` (what
the host API relays with), `client` (both of the API's protocols, for
code in containers), `hub` (the latest events, each with a cursor, and
`after(cursor, timeout)` for every reader) and `jobs` (runs a screen
follows, one per action and target, folded into snapshots).

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
  itself (`run(ctx)`) is refused with `{"error": ...}`.

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

### `stack uninstall`

Destroys all stacklets, removes Docker network, deletes runtime state
(`.stack/`), and removes config files (`stack.toml`, `users.toml`).
The nuclear option — back to a fresh clone.

### Hooks

Each lifecycle transition can trigger stacklet-specific hooks.
Convention over configuration: if the file exists, it runs. If not,
the step is skipped. No registration needed.

```
First stack up:

  on_configure ──► on_install ──► on_start ──► health ──► on_install_success ──► on_start_ready ──► migrations
  (config gate)    (system)       (services)   (wait)     (first-run API work)   (every-up API work)   (pending ones)

on_configure is the gate: it collects all required settings (provider
choice, API keys, server names) and persists them to stack.toml or
secrets.toml. If it fails or is interrupted, the next stack up re-enters
on_configure and picks up where it left off. on_install reads the config
that on_configure wrote and acts on it. Both hooks should be idempotent.

Subsequent stack up:

  on_start ──► health ──► on_start_ready ──► migrations
  (services)   (wait)     (API work)          (pending ones)

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
# stacklets/core/cli/schedule.py
from ._common import dispatch        # stacklets/core/cli/_common.py
from ..scheduler import Scheduler    # stacklets/core/scheduler.py
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
and leaves the admin's comments and other sections as they are.
`stack ai switch` uses it to write `[ai]`.

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
- The stacklet is only code: it does not have to be up. The bot runner
  sees the shipped stacklets and the first extension folder, so an
  extension runtime lives there. `stack up agent` stops when the runtime
  names no stacklet with a `bot/agent.py` it can see, and says what to set.
- A change applies with `./stack restart agent core`: the agent's
  container switches, and the bot runner reads the module from core's
  environment.

### What the agent may run

The agent (the `agent` stacklet) runs `stack` commands through the core
API, never the CLI itself. The API runs a fixed set of `memory` commands,
`docs show` and `ai say`, plus the stacklet commands the admin lists in
`stack.toml`:

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
- One exception: a `messages` command that names its sender with `--as`
  runs only as `--as agent`. Allowing `messages` lets the agent send as
  itself or as the system account, never as a person; writing in a
  person's name stays a tool for the terminal.
- No command from the agent may name a path on the host, allowed or
  not: `--vault`, `--file`, `memory write --from`, `photos import
  --source` and `messages upload` are refused before they run, because
  the API runs on the host. A shortened option (`--vau`) counts as the
  option, because the commands' parsers accept it.

`stack up agent` writes the allowed commands into the agent's workspace
as an always-loaded skill, one line per command with its `HELP`, so the
agent can call one without asking for `--help` first. **`HELP` is
prompt text then**, so it names the arguments:
`HELP = "Boil the water: boil <minutes>"`. With nothing allowed there
is no such skill, and the agent's prompt is the same as without the
feature. For a runtime other than nanobot (`[agent] runtime`) it writes the
same lines as JSON pairs to `<data_dir>/agent/bot/allowed-commands.json`,
next to the account's login state.

---

### Scheduled commands

`stack core schedule` runs a stacklet command at a time. Core owns the
scheduler (`stacklets/core/scheduler.py`): it runs inside core's API
service on the host, so it runs while core runs, and notes every check in a
heartbeat that `stack doctor` reports on.

```bash
./stack core schedule add dates "daily 07:00" -- memory list show dates
./stack core schedule add tea "in 10m" -- ai say "The tea is ready"
./stack core schedule add dentist "at 2026-11-04 18:00" --title "Lisa's dentist tomorrow" \
    --by homer --for lisa -- memory list show dates
./stack core schedule list [--by <person>] [--for <person>] | run <name> | remove <name> | log [<name>]
```

- **Who and what**: `--title` says what an entry does in words (the
  command when left out), `--by` who created it (`admin` by default),
  `--for` who it is for. `list` takes `--by` and `--for` too, alone or
  together.

- **When**: `daily HH:MM`, `weekly <day> HH:MM`, `at YYYY-MM-DD HH:MM`,
  `in <duration>` (stored as the `at` it means). Household wall clock,
  `[core] timezone`.
- **What**: a stacklet command as typed after `stack`, the same rule as
  `[agent] allow`: framework commands (`up`, `down`, `destroy`) are
  refused, when the entry is added and again when it runs. A schedule does
  not change schedules.
- **Restarts and sleep**: entries, the last due time per entry and the run
  log live in `<data_dir>/core/schedule/`. Nothing runs twice. A due time
  that passed while the scheduler was down runs once within the entry's
  grace (`--grace`, default 15 minutes), later it is logged as `missed`;
  of several passed due times only the latest can run. A one-off is
  removed once it ran.
- **A run**: the CLI in text mode, with `STACK_SCHEDULE_NAME` and
  `STACK_SCHEDULE_FOR` (`--for <person>`) set, a time limit (`--timeout`,
  default 5 minutes), never overlapping its own previous run. The log keeps
  the status (`ok`, `failed`, `timeout`, `refused`, `missed`, `skipped`)
  and the first lines of output.

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
    image: ghcr.io/paperless-ngx/paperless-ngx:3.0
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

`[core] dns_provider` (`"hetzner"` or `"cloudflare"`) turns on HTTPS. The
global block then makes Let's Encrypt the issuer for every certificate,
through a DNS-01 challenge at that provider:

```
{
	cert_issuer acme {
		dns hetzner {env.DNS_API_TOKEN}
		propagation_delay 30s
		propagation_timeout 5m
	}
}
```

Cloudflare's block has the `dns` line only. DNS-01 needs nothing on the
LAN to be reachable from the internet, and it is the only challenge that
issues a wildcard. Caddy obtains two certificates: `*.<domain>`, for the
catch-all site, which every subdomain site then uses, and `<domain>`
itself, which a wildcard does not cover.

The token is the infra stacklet's secret `DNS_API_TOKEN`. `stack infra
dns-token` stores or replaces it, and the first `stack up infra` asks for
it in a terminal. It reaches Caddy as an environment variable and is read
when a certificate is requested, so it appears neither in the Caddyfile
nor in the config Caddy serves on its admin endpoint. infra's `on_start`
refuses a provider the image has no plugin for, or a missing token.

With `dns_provider` set, the URLs the runtime renders (`{url}`,
`{<id>_url}`, `{home_url}`) are `https://`. `[core] https = true` does the
same for a reverse proxy in front of the stack that is not this one.

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
id = "homer"
name = "Homer"
email = "homer@home.local"
role = "admin"
relation = "parent"
words = "he"

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
| `role` | `admin` (created on every stacklet), `member`, or `restricted`. On screen, an admin "looks after the server". |
| `stacklets` | Which services this user gets an account on. Admins ignore this — they're created everywhere. |
| `relation` | Optional: `parent`, `child`, `grandparent` or `other`. Missing: no relation. |
| `words` | Optional: `she` or `he`, the words to use for the person. Missing: the name only. |

### The family as one file: `stack family`

`stack family` shows the household as one object: its name
(`[core] stack_owner`), its own web address (`[core] family_domain`), its
language (`[core] language`) and
each person with `id`, `name`, `relation`, `words`, `role` and `email`.
`--json` prints it as JSON. It writes nothing, and reads a users.toml
without `relation` or `words` as it is.

`stack family set --from <file>` writes the whole household from a TOML
or JSON file, or from stdin with `--from -`:

```toml
name     = "Simpson"
domain   = "simpsons.com"       # optional
language = "en"                 # optional: en or de

[[people]]
name        = "Homer Simpson"
relation    = "parent"          # optional: parent, child, grandparent, other
words       = "he"              # optional: she, he
looks_after = true              # optional: looks after the server (role admin)
```

The schema is `SCHEMA` in `lib/stack/family.py`, checked with the
standard library. Every writer of the family goes through this command:
the install from a file, the dashboard through the host API, and tests.
The host API's JSON surface answers `{"cmd": "family"}` with what
`stack family --json` prints, and `{"cmd": "family", "action": "set",
"family": {...}}` with what `stack family set` returns; it hands the
object to the CLI on stdin, never on its command line.

- **Defaults.** The first two people are parents, the rest children. The
  first person looks after the server unless the file sets `looks_after`
  on someone; then exactly those set to `true` do.
- **Usernames** come from the first name, lowercased and spelled in a to z
  (`Jürgen` is `juergen`). Two people with one name or one username are
  refused.
- **What is there stays.** A person already in users.toml is matched by
  name, then by username, and keeps their id, email, `stacklets` and any
  other key. A new person gets `<id>@<family_domain>`, or
  `<id>@home.local` without one, the installer's `stacklets`, and their
  username as first password in `.stack/secrets.toml`. users.toml is
  written whole, so comments in it are not kept.
- **The chat's server name is permanent.** `[messages] server_name` is
  written only while it is empty and messages has never been set up: the
  web address if there is one, else the name (`simpson`). `[core] domain`
  (domain mode) is never touched.
- **The language** is `[core] language`: what the bots answer in, what
  documents are tagged in, what voice messages are transcribed in.
  `[ai] language`, the spoken one, follows it while it is unset or the
  same as the old one. Once Documents is set up, a different language is
  refused: Paperless's document types are seeded in the first one.
  Without the key, the language stays as it is.
- **Once messages is set up,** a file that adds, renames or removes a
  person is refused: the chat accounts cannot follow yet. The refusal says
  what to do by hand.
- **A refusal writes nothing** and names every problem. With `--json`:
  `{"error": ..., "problems": [{"field": "people.2.relation", "message": ...}]}`,
  where `field` is a dotted path into the file, list positions from 0.
- **The same file twice** writes nothing the second time; `changed` lists
  the files a run wrote.

---

## Global Configuration: `stack.toml`

One file, committed to the repo. User edits it directly.

```toml
[core]
name          = "famstack"            # product name: CLI branding and
                                      # derived paths (~/<name>-extensions).
                                      # Missing = "stack". An instance
                                      # without it whose data dir exists
                                      # as <name>-data gets it written on
                                      # the next command.
stack_owner   = "Simpson"             # the household's name, as greeted
family_domain = ""                    # its own web address, optional:
                                      # names the chat server and new
                                      # people's emails (`stack family`).
                                      # Not `domain`.
domain        = ""                    # empty = port mode
dns_provider  = ""                    # domain mode: "hetzner" or
                                      # "cloudflare" serves HTTPS with
                                      # certificates via DNS-01; empty
                                      # serves plain HTTP. The API token
                                      # is a secret (`stack infra
                                      # dns-token`), never in this file.
host          = ""                    # port-mode host override.
                                      # Empty = auto-detect LAN IP.
                                      # Set to "localhost" for
                                      # single-machine testing, or a
                                      # custom hostname for stable
                                      # access across network changes.
data_dir      = "~/famstack-data"
timezone      = "Europe/Berlin"
language      = "de"                  # document tags, UI language (de, en)
shared_bucket = "family"              # slug for the shared/institutional
                                      # bucket inside the memory vault.
                                      # Personal entities (homer, marge, …)
                                      # live at <vault>/<localpart>/. Default
                                      # "family" fits famstack; non-family
                                      # deployments override.

[updates]
schedule = "0 0 3 * * *"        # Watchtower cron (3am nightly)

[ai]
openai_url = "http://localhost:8000/v1"
openai_key = "local"
default    = "mlx-community/Qwen2.5-14B-Instruct-4bit"
whisper_url = "http://localhost:6111/v1"
language   = "en"

[agent]
name  = "Stacky"                # the agent's name in chat
allow = []                      # stacklet commands the agent may run,
                                # "<id>" or "<id> <command>". See
                                # "What the agent may run".
runtime = "nanobot"             # who answers as the agent: nanobot, or a
                                # stacklet with a bot/agent.py. See
                                # "Who answers as the agent".

[backup]
# One block per destination. Engine name selects the implementation.
[backup.targets.vault]
engine   = "external-disk"
disk     = "backup-vault"
schedule = "0 2 * * *"
```

`[backup.targets.<name>]` defines one destination. `<name>` is the user's
label for that destination (any string). The required `engine` field
picks the implementation under `stacklets/backup/engines/`. Today only
`external-disk` ships. Engine-specific fields (`disk`, `schedule`,
future `repository`, `password`) live alongside `engine` in the same
block.

Stacklets never read `stack.toml` directly. The runtime resolves
template variables and passes everything through the rendered `.env`.

---

## Runtime State: `.stack/`

Gitignored. Created by `stack init`. Contains:

| File | Purpose |
|---|---|
| `secrets.toml` | Auto-generated credentials (passwords, API tokens). |
| `*.setup-done` | Marker files. Gates once-only hooks (`on_install`, `on_install_success`). |
| `*.up` | A stacklet without containers that is up, and the commit it started from. See [States](#states). |

No `enabled` file — stacklet state is derived from Docker containers
and the filesystem. See [States](#states).

Which migrations ran is not here: it lives with the data, in
`<data_dir>/<id>/.migrations.json`, so it travels with a backup. See
[Migrations](#migrations).

Deleted entirely by `stack uninstall`.

---

## Multiple Instances: `STACK_DIR`

One repo can power more than one stack instance. A stacklet definition
(code, compose file, hooks) is shared; the *instance* (config, secrets,
state, data) is swappable.

- **Repo root** — holds `stacklets/` and `lib/`. Discovered by walking
  up from the current working directory; outside any checkout, the
  checkout the CLI runs from. That is how the installer's global
  `famstack` command (in Homebrew's `bin`) works from any directory. It
  names only the checkout, so `STACK_DIR` selects the instance through
  it as it does through `./stack`.
- **Instance dir** — holds `stack.toml`, `users.toml`, `.stack/`. Holds
  the data referenced by `[core].data_dir`. Defaults to the repo root.

Point the CLI at a different instance with `STACK_DIR`:

```
STACK_DIR=tests/e2e/instance stack up docs
```

The same `stacklets/` definitions apply, but config, secrets, setup
markers, and data all live under that directory instead. If `STACK_DIR`
is set to a non-existent path, the CLI fails fast rather than silently
falling back.

Instances share Docker container names (`stack-docs`, `stack-messages`),
so two instances cannot run concurrently on the same machine — `stack
down` the active one before bringing another up. Useful for: dedicated
test environments, sandboxes, experimenting with a clean state without
touching your real household install.
