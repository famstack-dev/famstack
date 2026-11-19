# Core Reference

This file describes core: the parts that every running stack has and that a stacklet author does not write. It
holds the pages of the home address, the devices commands, the scheduler, HTTPS, the family file, `stack.toml`, the
runtime state and `STACK_DIR`. The stacklet contract (folder, manifest, hooks, lifecycle, commands, compose file) is in
[stack-reference.md](stack-reference.md). The family memory's cards and the curator are in
[memory-reference.md](memory-reference.md).

## The home address: links, dashboard and pages

The same published port serves the dashboard at `/dashboard`, open to the
home network for reading: a page whose widgets arrive rendered
and enhance themselves (`stacklets/core/tools-server/dashboard/widgets/README.md`).
Its first widget is the memory's activity, read from the cards API's
`/history`.

The same port serves the card pages, read only: every card at
`/card/<card_id>`. `/card` itself names no card and answers 404, with
the page of a missing card. A former id or a card's
file path answers with a redirect to the card's current id, and an id that
no card has answers 404. Core renders each page from the cards API: a
card's page shows one layout for every type (its type, title, facts line,
outside link, text, people and tags, and its history, read from the
card's versions at `/card/<card_id>/history` only when it is opened).
It shows no links between cards and no control to change a card. Every
card page sits in the frame of the memory's pages (`/memory`): the header
holds the famstack mark, the one search and the device's person, and the
side bar holds the menu that the device's `/memory` shows. The page of a
missing card, of a card the device may not read, of a removed card and of
a memory that does not answer have the same frame. The frame is read for
the device and never for the card, so a card the device may not read and
a missing card answer the same page. The
pages render on the server, so
a browser reads only their media and fonts, at `/card/api/media/<path>`
and `/card/api/fonts/<file>`. Every other route under `/card/api`
answers 404. No route under `/card`
takes a write, from any device. The changes to a card go through `127.0.0.1:42002` on this Mac
(see "A card changes through one endpoint"). The old address
`/cards#card=<id>` on that port forwards to `/card/<id>`. In domain mode,
Caddy forwards `<domain>/card` and `<domain>/card/*` to the same listener.

The same port serves the memory's pages, read only. `/memory` is the
page of the person the device is paired to: what comes up, their lists,
topics, bookmarks and documents, and what changed. A household device gets
the family's page there, and the Mac's address gets the admin's page.
`/memory/family` is the family's page for every device.

`/memory/person/<id>` is the page of one person.
`/memory/topic/<card_id>` is the page of one topic.
`/memory/sender/<card_id>` is the page of one sender.
`/memory/documents` and `/memory/documents/<kind>` show the documents by kind.
`/memory/kind/<type>` shows the cards of one card type, newest first.
`/memory/kind/document` redirects to `/memory/documents`.
`/memory/senders` shows every sender with the number of its documents.

The sender, documents and kind pages show 40 cards at a time.
"Older" and "Newer" link to the other parts of the list.
Each part has its own address, for example `/memory/kind/note?part=2`, so Back works.
A part outside the list is "not found".

The menu of every memory page has the group "Everything". It lists each
card type that the device has cards of, with its count, and the senders.
A card type that the device has no card of is not in the menu.

Core renders each page from the cards API's `/overview/<view>`, which
answers with only the cards the device's audience may read. A person,
topic, sender or kind that the device may not read, or that does not
exist, gets the same "not found" page with `404`. A memory that does not
answer is said on the page.

`/memory/search?q=<words>` is the search in the header of every memory
page. It asks the cards API's `/find` for the device's audience and shows
the results grouped by card type: people, topics and senders first, then
the cards. The search finds titles, summaries, senders, kinds of
document, bookmarked sites, topics and names. Words of two letters or
fewer find nothing. `/memory/preview/<card id>` answers a card link's
preview as JSON: the card's id, type, title and the description its card
page gives. It reads the card for the device's audience, so a card the
device may not read is a 404, the same as a card that does not exist. The
pages' script shows the preview when a pointer rests on a card link or the
link gets the focus.

In domain mode, Caddy forwards `<domain>/memory` and
`<domain>/memory/*` to the same listener. Everything else on that port
stays internal.

`GET /dashboard/api/health` answers `{"ok": true}` and asks the host API
and the memory nothing: it is core's health check, so `stack up core`, and
the installer before it gives the dashboard's address or opens a browser,
count core as up only once the dashboard answers.

Every dashboard page opens with the family as pills, read through the
host API's `family` on every request (so a change shows on the next page,
without restarting core), and greets the household; its script labels
each pill as whoever the device remembers would say it and greets that
person by time of day. "Your stack" is one status pill per set-up
service, with its status word (Running, Starting, Not answering,
Stopped); a stacklet whose manifest says `dashboard = false` has none, and
neither has core. A start shows as Starting on its pill while it goes on,
wherever it was started. A pill opens the service's service card in a pane
at the side: its status, Open, how to sign in, the app for the device. On
the Mac that runs famstack the service card names the admin's accounts and
shows the admin's stored password; on another device it shows no sign-in. One link
says what is left, and leads to `/setup`: "Continue setup: <step>" while a
step is open, "Set up <n> more" once all are done and a stacklet can still
be added, nothing after. The pills read `GET /dashboard/api/stack`.
While the family is empty, `/dashboard` is the
first run: three moments and "Who else is at home?" set up the family in
the browser and post it to `POST /dashboard/api/family`, which writes it
through the host API's `family` set as `stack family set` does and
answers with the family (200) or every problem by its field (422).
`GET /dashboard/api/family` is what the pills read; the first run is on
the Mac's address only. The dashboard's
writes take a body only when it is sent as JSON (`application/json` or a
`+json` type) and refuse anything else with 415, so a page from another
site, opened in the Mac's browser, which can post text or a form without
asking the browser first, cannot reach them. "Start your journey" on the first run's last moment opens the setup page.

The same port serves the setup page at `/setup`: the install, one step
at a time, for the admin. A rail shows its seven steps as done, current or
still to come: the family, the family chat, the chat on the phone (with the
browser and the app on the Mac as its fallback), the AI, Photos and Documents (with the family memory), the
first letter, and ready. The main column shows the step asked for
(`/setup?step=ai`) or the current one, the first that is not done. Its
service cards and tiles start a stacklet as a job and follow every run
from the log; it loads the dashboard's files and events and uses
`/dashboard/api/*`. A tile whose stacklet
`requires` one not set up that asks questions says what it comes after
(`waits_for` in `stack list`; `stack up` refuses it until then). While
`[ai] provider = "none"` (no AI for now), `stack list` marks the row of the
stacklet with `is_the_ai` as `ai_off` and every stacklet with `needs_ai` that is not set up
`waits_for_ai`: the AI's tile links the two routes
(`/setup?step=ai&ai=choose`), the AI step counts as done, and a tile that
waits for the AI offers no Start. A
requirement that asks nothing holds back no tile: the start sets it up
first. One whose first `up` still asks questions in forms
(`asks_in_forms` in `stack list`) is started from the page: its Start opens
the forms in a sheet and posts every answer so far to
`POST /dashboard/api/configure/<id>`, which relays them to the host API's
`configure` and answers with the next form, the same form with the reason
under a field, or ready, after which the page starts the job. The answers,
a secret (`writeOnly`) included, are relayed as they are, because the
route answers only on the Mac's address. A stacklet whose hook asks for itself shows its terminal command.
Messages asks only for the chat server's name, so once the family has
named it, the chat is started with a plain Start. The forms' words are the
hooks', in English. The phone's sign-in is done when `stack messages users`
lists a sign-in of the admin's with the phone app (a device of kind
`phone`, for example "Element X Android"). A sign-in in a browser or in
the computer app does not count. When the chat server does not say which
app a sign-in is, the admin confirms the phone (`/setup?phone=signed`).
The admin can always skip it (`?phone=skipped`). The browser keeps the
answer in the cookie `fs-setup-phone`, which the dashboard reads too for
its link to what is left.
The phone's step shows a QR code of the phone page, `/go/app` at core's
link base (`LINK_BASE_URL`), never a store link. That page offers the chat
app for the device that opened it (Element X on Google Play for Android,
on the App Store for an iPhone or iPad, Element for a computer), with the
chat server's address and the admin's username and no password, and works
without JavaScript. When it is opened through the home address (port
42000, not the Mac's address 42002), the step's part "Your phone reaches
famstack" is done: the tools server keeps the time while it runs,
publishes `phone.reached` (subject `setup/phone`) on the live stream so the
setup page reads itself again, and the setup page keeps it in the cookie
`fs-setup-phone-reached`, which outlives a restart of core. While no
device has opened it, the step names the likely causes after a while.
When the Mac's firewall keeps phones out, the step warns at once, with the
fix: the tools server asks the host API (`{"cmd": "firewall"}`, which runs
`stack core firewall --json` on the Mac, because a container cannot see the
Mac's firewall), and the answer's `blocks` names what blocks (`block-all`,
`orbstack`, `signed`). A firewall that cannot be read shows no warning.
The phone page leads on to the sign-in guide, `/go/app/sign-in`: the
steps for that device's app (Element X on a phone, Element on a computer),
with the chat server's address and the admin's username to copy, no
password, and on a phone a last step that turns on threads. It reads
complete without JavaScript; its one script only shows Copy.
The AI step offers two routes until `[ai]` names a server, and never
again after: famstack installs oMLX (`/setup?step=ai&ai=install`, the
command `./stack ai switch managed` for the Mac's terminal, and the AI's
tile that follows the run), or the admin's own AI server
(`/setup?step=ai&ai=other`). The step is computed on every read from
`[ai]` (the listing's `remote` and the host API's `ai_recommended`, `stack
ai recommended show`), the answer kept for it (`ai_answer`, `stack ai
check --last`) and the AI's run; the page holds only the route the admin
picked. The form's Connect posts the address and the key to `POST
/dashboard/api/ai/switch`, which runs the host API's `ai_switch` (`stack ai
switch <address> --key -`, the key on stdin). The route answers 403 to any
device but this Mac, and 422 with the command's `reason` (`key`,
`unreachable`, or `model` with the server's `models`) when it wrote nothing.
For a server the stack does not run, Check again posts to `POST
/dashboard/api/ai/check`, which runs the host API's `ai_check` (`stack ai
check`), on this Mac only, and answers 200 with the answer, failed or not.
The plan's `other` then names that server, its chat model and whether voice
messages have somewhere to go, and "Your AI is ready" says so, without the
first words. With speech on (`speech` of the plan), "Your AI is ready"
is followed by the AI's first words: Play posts to `POST
/dashboard/api/ai/first-words/welcome`, Yes to `.../intro`. Each runs the
host API's `ai_welcome` or `ai_intro` (`stack ai hello --audio`, `stack
ai intro --audio`), which answers `{"text", "audio"}`: the words and their
MP3 as base64, made by the speech server `stack ai say` uses. The page
plays the audio in its browser and shows the introduction's words as it
speaks. It never uses the browser's speech synthesis. While the family is empty, `/setup` sends the browser to
`/dashboard`. The setup page is on the Mac's address only.

---

## Answering a waiting browser

A browser that the home address does not know shows a code and waits
([ADR-023](adr/adr-023-paired-devices-and-the-admin-key.md) D2). Two
host commands answer it:

```
stack devices allow <code> [--as <person> | --shared <name>]
stack devices dismiss <code>
```

`allow` makes the browser a paired device: the family's first admin's,
the device of `--as <person>`, or a shared device with the name of
`--shared`. `dismiss` ends the request, as "Not now" on the Devices page
does. The code counts with or without its dash, in any case.

Each command is a client of core's tools server (ADR-019). It sends one
request to the tools server's allow or decline on the Mac's address, with
the admin key's cookie. The tools server keeps the waiting requests and
the limit of ten wrong codes in a minute. With `--json` the answer of
`allow` is `{"ok", "person", "device", "line"}`, and the answer of
`dismiss` is `{"ok", "line"}`. A refusal is `{"error"}` with exit 1.

The host API runs them as `devices_allow` (`code`, `as`, `shared`) and
`devices_dismiss` (`code`). The stacker bot sends these for an admin in
the Server room only. A reply `allow`, `allow as <person>`,
`allow shared <name>` or `dismiss` to the waiting notice uses the code in
the notice's envelope (type `device.waits`, see `stack messages send
--event`).

---

## Scheduled commands

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

---

## TLS

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
  `<id>@home.local` without one and the installer's `stacklets`.
  users.toml is written whole, so comments in it are not kept.
- **A new user's first password is four words.** A user without a
  password in `.stack/secrets.toml` (`global__USER_<ID>_PASSWORD`) gets
  four random words from EFF's short word list
  (`lib/stack/words/`), joined by "-": `otter-maple-cloud-lamp`. It is
  stored before any service makes an account with it. A stored password
  is never changed, an older one that is the username included.
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
| `*.up` | A stacklet without containers that is up, and the commit it started from. See [States](stack-reference.md#states). |

No `enabled` file — stacklet state is derived from Docker containers
and the filesystem. See [States](stack-reference.md#states).

Which migrations ran is not here: it lives with the data, in
`<data_dir>/<id>/.migrations.json`, so it travels with a backup. See
[Migrations](stack-reference.md#migrations).

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
