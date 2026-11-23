# Core Reference

This file describes **core**: the parts that every running stack has and that a stacklet author does not write. It
holds these topics: the pages of the home address, the devices commands, the scheduler and HTTPS. It also holds the
family file, `stack.toml`, the runtime state and `STACK_DIR`. The stacklet contract (folder, manifest, hooks, lifecycle, commands, compose file) is in
[stack-reference.md](stack-reference.md). The family memory's cards and the curator are in
[memory-reference.md](memory-reference.md).

## What does the home address serve?

The **home address** is the port that core's tools server publishes for paired devices. A link that a bot posts
points there (see [Persistent links](stack-reference.md#persistent-links-and-card-ids)). The same port serves these
pages:

| Path | Page | Who can open it |
|---|---|---|
| `/card/<card_id>` | One card, read only | Every paired device |
| `/memory` and `/memory/*` | The memory's pages, read only | Every paired device |
| `/dashboard` | The dashboard | The home network, for reading. The first run is on the Mac's address only. |
| `/dashboard/person/<id>` | One person's profile | Every paired device and the Mac. What it shows depends on who looks (see below). |
| `/dashboard/me` | A redirect to one's own profile | A paired person's device. The Mac opens the person picked in that browser. |
| `/setup` | The setup page | The Mac's address only |

In domain mode, Caddy forwards `<domain>/card`, `<domain>/card/*`, `<domain>/memory` and `<domain>/memory/*` to the
same listener. Everything else on that port stays internal.

### What does a card page show?

Every card has a page at `/card/<card_id>`. Core renders each page from the
[cards API](memory-reference.md#the-memory-has-one-writer-the-curator). The page has one layout for every type:

- the card's type, title and facts line;
- its outside link and its text;
- the messages a diary entry or a note was filed from, in the order sent, below the text: a text, a photo, photos in a row, a file, a later reply;
- for a note that the AI wrote: ✦ beside the date, a line that says so, and how to correct it in the room it came from. The mark and the line name the AI as the topic page does. The page reads `[ai] provider` through the host API and waits at most 2 seconds. Without an answer in that time, the page names "AI";
- its people and tags;
- its history, read from the card's versions at `/card/<card_id>/history` only when the history is opened.

The page shows no links between cards. It has no control to change a card.

These requests do not show a card:

| Request | Answer |
|---|---|
| `/card` (no card named) | 404, with the page of a missing card |
| A former id or a card's file path | A redirect to the card's current id |
| An id that no card has | 404 |
| The old address `/cards#card=<id>` on that port | A redirect to `/card/<id>` |

Every card page sits in the frame of the [memory's pages](#what-do-the-memorys-pages-show). The header holds the
famstack mark, the one search and the device's person. The side bar holds the menu that the device's `/memory` shows.
These pages have the same frame:

- the page of a missing card;
- the page of a card that the device may not read;
- the page of a removed card;
- the page of a memory that does not answer.

Core reads the frame for the device and never for the card. Thus a card that the device may not read and a missing
card answer the same page.

The pages render on the server. A browser reads only their media at `/card/api/media/<path>` and their fonts at
`/card/api/fonts/<file>`. Every other route under `/card/api` answers 404.

No route under `/card` takes a write, from any device. A change to a card goes through `127.0.0.1:42002` on this Mac
(see [A card changes through one endpoint](memory-reference.md#how-does-a-card-change)).

### What do the memory's pages show?

The memory's pages are read only. Each address shows one page:

| Address | Page |
|---|---|
| `/memory` | The page of the person that the device is paired to: what comes up, their lists, topics, bookmarks and documents, and what changed. A household device gets the family's page. The Mac's address gets the admin's page. |
| `/memory/family` | The family's page, for every device |
| `/memory/person/<id>` | One person |
| `/memory/topic/<card_id>` | One topic: its head, two chips that open its bookmarks and documents, a rail with its lists and what changed in the last two weeks (beside the sections from 1100 px, after the chips on a phone), its newest 10 notes in full on the first part (each with the AI mark when the AI wrote its text, its messages folded under "show the original messages") and its other cards. Older notes are rows. A topic of mostly bookmarks or documents opens on that list. The AI mark names the AI that `[ai] provider` selects: "famstack's AI" for `managed`, "your family's AI server" for `external`, and "AI" for all other values. |
| `/memory/sender/<card_id>` | One sender |
| `/memory/documents`, `/memory/documents/<kind>` | The documents, by kind |
| `/memory/kind/<type>` | The cards of one card type, newest first |
| `/memory/kind/document` | A redirect to `/memory/documents` |
| `/memory/senders` | Every sender, with the number of its documents |
| `/memory/search?q=<words>` | The search results |
| `/memory/preview/<card id>` | A card link's preview, as JSON |

**Long lists.** The sender, documents and kind pages show 40 cards at a time. "Older" and "Newer" link to the other
parts of the list. Each part has its own address, for example `/memory/kind/note?part=2`. Because of this, Back works.
A part outside the list is "not found". On a topic that the device can read, a part after the last part says that the topic has no more entries.

**The menu.** The menu of every memory page has the group "Everything". It lists the senders and each card type that
the device has cards of, with its count. A card type that the device has no card of is not in the menu.

**Who may read what.** Core renders each page from the cards API's `/overview/<view>`. That endpoint answers with only
the cards that the device's audience may read. These requests get the same "not found" page with `404`:

- a person, topic, sender or kind that the device may not read;
- a person, topic, sender or kind that does not exist.

When the memory does not answer, the page says so.

**Search.** `/memory/search?q=<words>` is the search in the header of every memory page. It asks the cards API's
`/find` for the device's audience. It shows the results grouped by card type: people, topics and senders first, then
the cards. The search finds these things:

- titles and summaries;
- senders and kinds of document;
- bookmarked sites;
- topics and names.

Words of two letters or fewer find nothing.

**Previews.** `/memory/preview/<card id>` answers a card link's preview as JSON. The JSON holds the card's id, type and
title, and the description that its card page gives. The preview reads the card for the device's audience. Thus a
card that the device may not read is a 404, the same as a card that does not exist. The pages' script shows the
preview when a pointer rests on a card link or when the link gets the focus.

### What does the dashboard show?

The **dashboard** at `/dashboard` is a page whose widgets arrive rendered and then enhance themselves
(`stacklets/core/tools-server/dashboard/widgets/README.md`). Its first widget is the memory's activity, read from the
cards API's `/history`.

**The family.** Every dashboard page opens with the family as pills. The page reads the family through the host API's
`family` on every request. Thus a change shows on the next page, without a restart of core. The page greets the
household. Its script labels each pill as the person that the device remembers would say it, and greets that person by
time of day.

Each pill is a link to the person's profile, `/dashboard/person/<id>`. A click opens a short panel of the profile
under the pills instead. The panel is read-only: the name, the relation and the memory link; the person's devices;
how many devices wait to join (an admin's device and the Mac); and a link **Settings and devices** to the profile when
it shows more to this viewer. The name at the top right of a person's own device links `/dashboard/me`. A click on it
opens one's own panel. On the Mac's address a person picks their name with **This is me** on their profile, never
with a click on a pill.

**Your stack.** "Your stack" has one status pill for each service that is set up. Each pill shows its status word:
Running, Starting, Not answering or Stopped. A stacklet whose manifest says `dashboard = false` has no pill. Core has
no pill.

A start shows as Starting on its pill while it goes on, wherever it was started. The pills read
`GET /dashboard/api/stack`.

**The service card.** A pill opens the service's service card in a pane at the side. The service card shows four things:
the status, Open, how to sign in, and the app for the device. On the Mac that runs famstack, the service card names the
admin's accounts and shows the admin's stored password. On another device it shows no sign-in.

**What is left.** One link says what is left and leads to `/setup`:

| State | Link |
|---|---|
| A step is open | "Continue setup: <step>" |
| All steps are done and a stacklet can still be added | "Set up <n> more" |
| Nothing is left | No link |

**The first run.** While the family is empty, `/dashboard` is the first run. Three moments and "Who else is at home?"
set up the family in the browser. The page posts the family to `POST /dashboard/api/family`. That route writes it
through the host API's `family` set, as `stack family set` does. It answers with the family (200), or with every
problem by its field (422).

`GET /dashboard/api/family` is what the pills read. The first run is on the Mac's address only. "Start your journey" on the first run's last moment opens the setup page.

**Health.** `GET /dashboard/api/health` answers `{"ok": true}`. It asks the host API and the memory nothing, because
it is core's health check. `stack up core` counts core as up only once the dashboard answers. The installer does the
same before it gives the dashboard's address or opens a browser.

> ⚠️ **Send the dashboard's writes as JSON.** The dashboard's writes take a body only as `application/json` or as a
> `+json` type, and refuse anything else with 415. The reason: a page from another site, opened in the Mac's browser,
> can post text or a form without asking the browser first. That page cannot reach the writes.

### What does a person's profile show?

Each person of the family has a profile at `/dashboard/person/<id>`, for example `/dashboard/person/bart`. The
profile is the one place for a person's devices and password. The page has these parts, in this order. Each part
has an anchor, so a link can open the page at that part.

| Part | Anchor | Who sees it |
|---|---|---|
| The name, the relation, "Looks after famstack" for an admin, and a link to the person's memory page | | Everybody |
| Devices waiting to join, each with **Allow as** the person or Shared (a choice, then **Allow**) and **Dismiss** | `#waiting` | An admin's device and the Mac, when a browser waits |
| Settings: the email, the password, the word for sister or brother | `#settings`, `#password` | The person and an admin. The password shows only on the person's own device. |
| The person's devices, with **Remove**, or **Not me** on the device that looks | `#devices` | The person, an admin's device and the Mac |
| On this Mac: who the Mac's browser greets | `#this-mac` | The Mac only |

Another person's device sees one line instead of the devices. The server decides what the page shows from the
device that asks, never from the address. `/dashboard/devices` redirects to `/dashboard/me#devices`. The Server
room's notice of a waiting browser links to `/dashboard/me#waiting`.

`GET /dashboard/api/person/<id>` answers the same profile as JSON: `id`, `name`, `relation`, `admin`, `memory`,
`profile`, `own`, `devices` (a list, or `null` when the viewer may not see them) and `waiting` (a count, or `null`).
It never holds a password. An unknown id answers 404. The dashboard's panel draws only this answer. Thus the answer
also holds the panel's words for this viewer in `said` (`null` for a part the panel does not show), and each listed
device holds `label` and `when`, its name and its seen line as the page says them.

### What does the setup page do?

The **setup page** at `/setup` takes the admin through the install, one step at a time. The setup page is on the Mac's
address only. While the family is empty, `/setup` sends the browser to `/dashboard`.

A rail shows its seven steps as done, current or still to come:

1. the family;
2. the family chat;
3. the chat on the phone, with the browser and the app on the Mac as its fallback;
4. the AI;
5. Photos and Documents, with the family memory;
6. the first letter;
7. ready.

The main column shows the step that the address asks for (`/setup?step=ai`). Without one, it shows the current step,
the first that is not done.

**Starting a stacklet.** The setup page's service cards and tiles start a stacklet as a job. They follow every run
from the log. The page loads the dashboard's files and events and uses `/dashboard/api/*`.

| The tile's stacklet | What the tile does |
|---|---|
| `requires` a stacklet that is not set up and that asks questions | Says what it comes after (`waits_for` in `stack list`). `stack up` refuses it until then. |
| `requires` a stacklet that asks nothing | Nothing is held back. The start sets up the requirement first. |
| Its first `up` asks questions in forms (`asks_in_forms` in `stack list`) | Start opens the forms in a sheet. |
| Its hook asks for itself | Shows its terminal command. |
| Waits for the AI while there is no AI | Offers no Start. |

**The forms.** The sheet posts every answer so far to `POST /dashboard/api/configure/<id>`. That route relays the
answers to the host API's `configure`. It answers with one of these:

- the next form;
- the same form with the reason under a field;
- ready, after which the page starts the job.

The route relays the answers as they are. This includes a secret (`writeOnly`), because the route answers only on
the Mac's address. The forms' words are the hooks' words, in English. Messages asks only for the chat server's name. Thus, once
the family has named it, the chat starts with a plain Start.

**No AI for now.** While `[ai] provider = "none"`, `stack list` marks two kinds of rows:

- the row of the stacklet with `is_the_ai` is `ai_off`;
- every stacklet with `needs_ai` that is not set up is `waits_for_ai`.

The AI's tile then links the two routes (`/setup?step=ai&ai=choose`). The AI step counts as done.

#### When is the phone step done?

The phone's sign-in is done when `stack messages users` lists a sign-in of the admin's with the phone app. That is a
device of kind `phone`, for example "Element X Android". A sign-in in a browser or in the computer app does not count.

When the chat server does not say which app a sign-in is, the admin confirms the phone (`/setup?phone=signed`). The
admin can always skip the step (`?phone=skipped`). The browser keeps the answer in the cookie `fs-setup-phone`. The
dashboard reads this cookie too, for its link to what is left.

**The phone page.** The phone's step shows a QR code of the phone page: `/go/app` at core's link base
(`LINK_BASE_URL`), never a store link. The phone page offers the chat app for the device that opened it:

| Device | App |
|---|---|
| Android | Element X on Google Play |
| iPhone or iPad | Element X on the App Store |
| Computer | Element |

The phone page gives the chat server's address and the admin's username, and no password. It works without
JavaScript.

**"Your phone reaches famstack".** This part of the step is done when the phone page is opened through the home
address (port 42000), not through the Mac's address (42002). Then these things happen:

- The tools server keeps the time while it runs.
- It publishes `phone.reached` (subject `setup/phone`) on the live stream. The setup page then reads itself again.
- The setup page keeps the result in the cookie `fs-setup-phone-reached`, which outlives a restart of core.

While no device has opened the phone page, the step names the likely causes after a while.

**The firewall.** When the Mac's firewall keeps phones out, the step warns at once. The warning gives the fix.

The tools server asks the host API (`{"cmd": "firewall"}`). The host API runs `stack core firewall --json` on the Mac, because a container
cannot see the Mac's firewall. The answer's `blocks` names what blocks: `block-all`, `orbstack` or `signed`. A
firewall that cannot be read shows no warning.

**The sign-in guide.** The phone page leads on to the sign-in guide, `/go/app/sign-in`. The guide gives the steps for
the device's app: Element X on a phone, Element on a computer. It gives the chat server's address and the admin's
username to copy, and no password. On a phone, a last step turns on threads.

The guide reads complete without
JavaScript. Its one script only shows Copy.

#### How does the AI step work?

The AI step offers two routes until `[ai]` names a server, and never again after:

| Route | Address | What it does |
|---|---|---|
| famstack installs oMLX | `/setup?step=ai&ai=install` | Shows the command `./stack ai switch managed` for the Mac's terminal, and the AI's tile that follows the run |
| The admin's own AI server | `/setup?step=ai&ai=other` | Shows a form for the server's address and key |

The page computes the step on every read from three sources:

- `[ai]`: the listing's `remote` and the host API's `ai_recommended` (`stack ai recommended show`);
- the answer kept for it: `ai_answer` (`stack ai check --last`);
- the AI's run.

The page holds only the route that the admin picked.

**Connect.** The form's Connect posts the address and the key to `POST /dashboard/api/ai/switch`. That route runs the
host API's `ai_switch` (`stack ai switch <address> --key -`, with the key on stdin). It answers as follows:

| Case | Answer |
|---|---|
| A device that is not this Mac | 403 |
| The command wrote nothing | 422 with the command's `reason`: `key`, `unreachable`, or `model` with the server's `models` |

**Check again.** For a server that the stack does not run, Check again posts to `POST /dashboard/api/ai/check`. That
route runs the host API's `ai_check` (`stack ai check`), on this Mac only. It answers 200 with the answer, failed or
not. The plan's `other` then names three things: that server, its chat model and whether voice messages have somewhere
to go. "Your AI is ready" says so, without the first words.

**The first words.** With speech on (`speech` of the plan), the AI's first words follow "Your AI is ready":

| Button | Route | Host API | Command |
|---|---|---|---|
| Play | `POST /dashboard/api/ai/first-words/welcome` | `ai_welcome` | `stack ai hello --audio` |
| Yes | `POST /dashboard/api/ai/first-words/intro` | `ai_intro` | `stack ai intro --audio` |

Each answers `{"text", "audio"}`: the words, and their MP3 as base64. The speech server that `stack ai say` uses makes
the audio. The page plays the audio in its browser and shows the introduction's words while it speaks. It never uses
the browser's speech synthesis.

---

## How to answer a waiting browser

A browser that the home address does not know shows a code and waits
([ADR-023](adr/adr-023-paired-devices-and-the-admin-key.md) D2). Two host commands answer it.

To let the browser in, run:

```
stack devices allow <code> [--as <person> | --shared <name>]
```

To end the request, run:

```
stack devices dismiss <code>
```

| Command | Effect |
|---|---|
| `allow <code>` | The browser becomes a paired device of the family's first admin. |
| `allow <code> --as <person>` | The browser becomes that person's device. |
| `allow <code> --shared <name>` | The browser becomes a shared device with that name. |
| `dismiss <code>` | The request ends, as **Dismiss** on the Devices page does. |

The code counts with or without its dash, in any case.

**How the commands work.** Each command is a client of core's tools server
([ADR-019](adr/adr-019-where-code-runs.md)). It sends one request to the tools server's allow or decline on the Mac's
address, with the admin key's cookie. The tools server keeps the waiting requests. It also keeps the limit of ten wrong
codes in a minute.

**The answer.** With `--json`, these are the answers:

| Command | Answer |
|---|---|
| `allow` | `{"ok", "person", "device", "line"}` |
| `dismiss` | `{"ok", "line"}` |
| A refusal | `{"error"}`, with exit 1 |

**From the chat.** The host API runs the commands as `devices_allow` (`code`, `as`, `shared`) and `devices_dismiss`
(`code`). The stacker bot sends these for an admin, in the Server room only. An admin replies to the waiting notice
with `allow`, `allow as <person>`, `allow shared <name>` or `dismiss`. The reply uses the code in the notice's envelope
(type `device.waits`, see `stack messages send --event`).

---

## How to schedule a command

`stack core schedule` runs a stacklet command at a time. Core owns the scheduler (`stacklets/core/scheduler.py`). The
scheduler runs inside core's API service on the host. Thus it runs while core runs. It notes every check in a
heartbeat that `stack doctor` reports on.

Examples:

```bash
./stack core schedule add dates "daily 07:00" -- memory list show dates
./stack core schedule add tea "in 10m" -- ai say "The tea is ready"
./stack core schedule add dentist "at 2026-11-04 18:00" --title "Lisa's dentist tomorrow" \
    --by homer --for lisa -- memory list show dates
./stack core schedule list [--by <person>] [--for <person>] | run <name> | remove <name> | log [<name>]
```

### What does an entry hold?

| Option | Meaning | Default |
|---|---|---|
| `--title` | What the entry does, in words | The command |
| `--by` | Who created the entry | `admin` |
| `--for` | Who the entry is for | None |

`list` takes `--by` and `--for` too, alone or together.

**When.** These forms are allowed. All times are the household's wall clock, `[core] timezone`.

| Form | Meaning |
|---|---|
| `daily HH:MM` | Every day |
| `weekly <day> HH:MM` | Every week |
| `at YYYY-MM-DD HH:MM` | Once |
| `in <duration>` | Once. It is stored as the `at` that it means. |

**What.** The command is a stacklet command, as typed after `stack`. The rule is the same as for `[agent] allow`. The
scheduler refuses framework commands (`up`, `down`, `destroy`), when the entry is added and again when it runs. A
schedule does not change schedules.

### What happens after a restart or a sleep?

The entries, the last due time of each entry and the run log are in `<data_dir>/core/schedule/`. Nothing runs twice.

- A due time that passed while the scheduler was down runs once within the entry's grace (`--grace`, default 15
  minutes).
- After the grace, the due time is logged as `missed`.
- Of several due times that passed, only the latest can run.
- A one-off entry is removed once it ran.

### How does a run go?

A run is the CLI in text mode, with these properties:

- `STACK_SCHEDULE_NAME` and `STACK_SCHEDULE_FOR` (`--for <person>`) are set.
- It has a time limit (`--timeout`, default 5 minutes).
- It never overlaps its own previous run.

The log keeps the status (`ok`, `failed`, `timeout`, `refused`, `missed`, `skipped`) and the first lines of output.

---

## How do bots and commands keep their Matrix devices?

A Matrix login is a **device**, with a key store of its own (`stack.matrix.device`).

### Where does a bot keep its device?

A bot keeps its device across restarts in its stacklet's data dir, `~/famstack-data/<id>/bot/`:

```
archivist-bot.session.json        the login: token, user id, device id
archivist-bot_crypto/             the device's key store
archivist-bot.cross-signing.json  the account's cross-signing keys
```

The cross-signing keys belong to the account, not to the device. Thus the file is named after the account.

### Who makes the cross-signing keys?

The bot's device owns the cross-signing keys. It makes them once, publishes them and signs itself with them. Thus apps
show the bot as verified.

Any other device of the account only signs itself with the same file. It never makes keys. It stays unsigned in two
cases:

- the file is missing;
- the server holds other keys.

The reason: new keys would replace the account's identity and leave the bot's own device unsigned.

### How does `stack messages send` reach an encrypted room?

`stack messages send` into an encrypted room uses a second device of the account. The host CLI cannot encrypt. Thus it
runs `python -m stack.matrix.send` in the bot runner, with the request (password included) on stdin.

- The device is `<account>-cli`, next to the bot's files. Apps show it as `<account> (command line)`.
- It is never the running bot's device, because two processes on one key store break each other's encryption sessions.
- Two sends as one account take turns on a lock.
- A person's command-line device keeps its state in `~/famstack-data/messages/cli/`. It stays unverified, because the
  stack does not hold a person's keys.

| Room | What happens |
|---|---|
| Encrypted | The message goes through the command-line device in the bot runner. |
| Not encrypted | The host sends the message over plain HTTP, as before. No device of this kind is created. |
| The server cannot say whether it is encrypted | `send` and `upload` both refuse and send nothing. |

### How does a file reach an encrypted room?

`stack messages upload` into an encrypted room takes the same path. The file goes to the command-line device with the
message, over stdin. The device then does three things:

1. It encrypts the file.
2. It uploads only the ciphertext.
3. It sends the event with the file in `file` (`url`, `key`, `iv`, `hashes`, `v`) instead of `url`.

A bot's `send_file` does the same in an encrypted room. The reason: nio parses a file event with a plain `url` there as
a `BadEvent`, and no bot handler sees it.

---

## How to turn on HTTPS

To turn on HTTPS, set `[core] dns_provider` to `"hetzner"` or `"cloudflare"`. The global block then makes Let's
Encrypt the issuer for every certificate, through a DNS-01 challenge at that provider:

```
{
	cert_issuer acme {
		dns hetzner {env.DNS_API_TOKEN}
		propagation_delay 30s
		propagation_timeout 5m
	}
}
```

Cloudflare's block has the `dns` line only.

**Why DNS-01.** DNS-01 needs nothing on the LAN to be reachable from the internet. It is also the only challenge that
issues a wildcard. Caddy obtains two certificates:

- `*.<domain>`, for the catch-all site, which every subdomain site then uses;
- `<domain>` itself, because a wildcard does not cover it.

**The token.** The token is the infra stacklet's secret `DNS_API_TOKEN`. `stack infra dns-token` stores or replaces
it. The first `stack up infra` asks for it in a terminal.

The token reaches Caddy as an environment variable, and Caddy
reads it when it requests a certificate. Thus the token appears neither in the Caddyfile nor in the config that Caddy
serves on its admin endpoint. infra's `on_start` refuses two cases:

- a provider that the image has no plugin for;
- a missing token.

**The URLs.** With `dns_provider` set, the URLs that the runtime renders (`{url}`, `{<id>_url}`, `{home_url}`) are
`https://`. `[core] https = true` does the same for a reverse proxy in front of the stack that is not this one.

---

## Who is in the family?

`users.toml` defines the family members. It holds identity only, and no passwords.

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
| `id` | Username. Used in CLI commands and as the default login name. |
| `name` | Display name. |
| `email` | Email address. Used as the login for services that require one. |
| `role` | `admin` (created on every stacklet), `member`, or `restricted`. On screen, an admin "looks after the server". |
| `stacklets` | The services that this user gets an account on. Admins ignore this field, because they are created everywhere. |
| `relation` | Optional: `parent`, `child`, `grandparent` or `other`. Missing: no relation. |
| `words` | Optional: `she` or `he`, the words to use for the person. Missing: the name only. |

### How to write the family from one file: `stack family`

`stack family` shows the household as one object:

- its name (`[core] stack_owner`);
- its own web address (`[core] family_domain`);
- its language (`[core] language`);
- each person, with `id`, `name`, `relation`, `words`, `role` and `email`.

`--json` prints it as JSON. The command writes nothing. It reads a users.toml without `relation` or `words` as it is.

To write the whole household from a TOML or JSON file, run `stack family set --from <file>`. To read the file from
stdin, use `--from -`. An example file:

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

The schema is `SCHEMA` in `lib/stack/family.py`, checked with the standard library. Every writer of the family goes
through this command: the install from a file, the dashboard through the host API, and tests.

The host API's JSON surface has two requests for the family:

| Request | Answer |
|---|---|
| `{"cmd": "family"}` | What `stack family --json` prints |
| `{"cmd": "family", "action": "set", "family": {...}}` | What `stack family set` returns |

The host API hands the object to the CLI on stdin, never on its command line.

#### What does `stack family set` write?

- **Defaults.** The first two people are parents, and the rest are children. The first person looks after the server,
  unless the file sets `looks_after` on someone. In that case, exactly the people set to `true` look after it.
- **Usernames** come from the first name, lowercased and spelled in a to z (`Jürgen` is `juergen`). The command refuses
  two people with one name or one username.
- **What is there stays.** The command matches a person already in users.toml by name, then by username. That person
  keeps their id, email, `stacklets` and any other key. A new person gets the email `<id>@<family_domain>`. Without a
  family domain, a new person gets `<id>@home.local` and the installer's `stacklets`. Comments in users.toml are not
  kept, because the command writes the file whole.
- **A new user's first password is four words.** A user without a password in `.stack/secrets.toml`
  (`global__USER_<ID>_PASSWORD`) gets four random words from EFF's short word list (`lib/stack/words/`). The words are
  joined by "-": `otter-maple-cloud-lamp`. The password is stored before any service makes an account with it.
- **A stored password is never changed.** This includes an older password that is the username.
- **The chat's server name is permanent.** The command writes `[messages] server_name` only while it is empty and
  messages has never been set up. It writes the web address if there is one, and else the name (`simpson`). It never
  touches `[core] domain` (domain mode).
- **The language** is `[core] language`. The bots answer in it. Documents are tagged in it. Voice messages are
  transcribed in it. Without the key, the language stays as it is.
- **The spoken language** is `[ai] language`. It follows `[core] language` while it is unset or the same as the old
  one.

#### When does `stack family set` refuse?

| Case | Reason |
|---|---|
| Documents is set up, and the file has a different language | Paperless's document types are seeded in the first language. |
| Messages is set up, and the file adds, renames or removes a person | The chat accounts cannot follow yet. The refusal says what to do by hand. |
| Two people have one name or one username | See **Usernames** above. |

A refusal writes nothing and names every problem. With `--json`, a refusal looks like this:

```json
{"error": "...", "problems": [{"field": "people.2.relation", "message": "..."}]}
```

`field` is a dotted path into the file, with list positions from 0.

The same file a second time writes nothing. `changed` lists the files that a run wrote.

---

## How to configure the stack: `stack.toml`

`stack.toml` is one file, committed to the repo. The user edits it directly.

A `stack.toml` that does not parse stops every command. The command names the file, the line and the parser's message. It starts and changes nothing, because a file read as empty would point the stack at the default data folder. A missing `stack.toml` is not an error.

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

The agent's keys are described in [What the agent may run](stack-reference.md#what-the-agent-may-run) and
[Who answers as the agent](stack-reference.md#who-answers-as-the-agent).

**Backup targets.** `[backup.targets.<name>]` defines one destination. `<name>` is the user's label for that
destination (any string). The required `engine` field picks the implementation under `stacklets/backup/engines/`.
Today only `external-disk` ships. Engine-specific fields (`disk`, `schedule`, future `repository`, `password`) are in
the same block as `engine`.

Stacklets never read `stack.toml` directly. The runtime resolves the template variables and passes everything through
the rendered `.env`.

---

## What is in `.stack/`?

`.stack/` is ignored by git. `stack init` creates it. It contains these files:

| File | Purpose |
|---|---|
| `secrets.toml` | Auto-generated credentials (passwords, API tokens). |
| `*.setup-done` | Marker files. They gate the once-only hooks (`on_install`, `on_install_success`). |
| `*.up` | A stacklet without containers that is up, and the commit it started from. See [States](stack-reference.md#states). |

There is no `enabled` file. The state of a stacklet is derived from its Docker containers and the filesystem. See
[States](stack-reference.md#states).

The record of which migrations ran is not here. It is with the data, in `<data_dir>/<id>/.migrations.json`, because
then it travels with a backup. See [Migrations](stack-reference.md#migrations).

`stack uninstall` deletes `.stack/` entirely.

---

## How to run several instances: `STACK_DIR`

One repo can power more than one stack instance. The stacklet definitions (code, compose file, hooks) are shared. The
**instance** (config, secrets, state, data) is swappable.

| Directory | What it holds | How the CLI finds it |
|---|---|---|
| Repo root | `stacklets/` and `lib/` | It walks up from the current working directory. Outside any checkout, it uses the checkout that the CLI runs from. |
| Instance dir | `stack.toml`, `users.toml`, `.stack/`, and the data referenced by `[core].data_dir` | `STACK_DIR`. The default is the repo root. |

The installer's global `famstack` command (in Homebrew's `bin`) works from any directory, because the CLI falls back to
its own checkout. The command names only the checkout. Thus `STACK_DIR` selects the instance through it, as it does
through `./stack`.

To point the CLI at a different instance, set `STACK_DIR`:

```
STACK_DIR=tests/e2e/instance stack up docs
```

The same `stacklets/` definitions apply. The config, secrets, setup markers and data are all under that directory
instead. If `STACK_DIR` names a path that does not exist, the CLI fails at once. It does not fall back to another
instance.

> ⚠️ **Run one instance at a time on one machine.** Two instances cannot run at the same time, because instances
> share Docker container names (`stack-docs`, `stack-messages`). `stack down` the active instance before you bring up
> another.

A second instance is useful for dedicated test environments and sandboxes. It is also useful to experiment with a clean
state, without a change to your real household install.
