# ADR-020: Live Events for Screens

## Status
Accepted (2026-10-26). Amended the same day with `subject`, the hub's
cursors and resync, and "The pieces", as built.

## Context
The dashboard's setup widget starts a stacklet and shows how it goes: each
step as it happens, then whether it worked. Today a caller of the host API
gets one answer after `stack up` has finished, up to 300 s later, and nothing
while it runs.

The screens that will show this are browsers on the family's devices, old
tablets included (RFC-001: iOS Safari 15+, no build step, "No WebSocket
dependency. SSE for real-time, polling as fallback"), the terminal, and later
the stacker bot in chat. In port mode they reach the Mac over plain HTTP.

## Decision

**1. One channel for screens: Server-Sent Events, plus plain POST.** The
server tells a screen what happens over SSE; a screen asks for something
(start a stacklet, answer a question) with an ordinary POST. A browser's
`EventSource` reconnects by itself and sends `Last-Event-ID`, it is plain
HTTP through Caddy, and `curl` can read it.

**2. One event envelope everywhere.** One JSON object per line, with
CloudEvents' field names and nothing else from it, no library:

```json
{"id": 3, "type": "step.started", "time": "2026-10-26T09:14:02.114Z", "data": {"text": "Pulling images"}}
```

- `id` orders events, so a screen resumes after the last one it saw. Within a
  run it counts up from 1. The tools server's hub gives each event a cursor
  instead, `<epoch>.<seq>`; the epoch is fresh every time the server starts,
  so a cursor from before a restart is recognised as stale.
- `type` says what happened.
  - A run (`stack up`): `step.started` and then `step.done` or `step.failed`
    for a slow step (their `data.started` names the event that started it),
    `step.done` or `step.failed` alone for a quick one, `warning`, and
    `result` last, exactly once.
  - The jobs around a run: `job.started` and `job.ended`, whose data is the
    job's snapshot.
  - The hub: `resync`, for a cursor it cannot continue from.
  - Never `error`, `message` or `open`: those are `EventSource`'s own event
    names, and a type of that name would be mistaken for one.
- `time` is ISO 8601 in UTC.
- `subject` is optional: what the event is about (`job/<id>`, later `stack`
  or `activity`). A screen subscribes by prefix. A run leaves it out; the
  jobs set it.
- `data` is the payload. The `result` event carries what a screen may see of
  the command's result, a list kept in one place (`result_for_screens`): `ok`,
  `error` or `cancelled`, the stacklet, its name and stage, `first_run`,
  `warnings`, `migrations` and `healthy`. Never the rendered env or the
  manifest's hints, which can name a password. Step texts are words for
  people and never carry a secret's value.
- **Health is a property of the run, not a failure** (Arthur, 2026-10-26).
  A health check that never passes is a failed step, and the run is still
  `ok` with `healthy: false`: `stack up` exits 0 and its job ends `done`.
  A slow first start (Immich, Paperless) must not read as a failure, and
  the terminal, the exit code and the job tell one story.

The same lines serve the terminal (`stack up <id> --events`), the host API
(`"stream": true` relays them as the CLI writes them), the tools server and
the stacker bot.

**3. State lives in the job, not in the connection.** A run is a job that
keeps its events and its state; a connection is only a view of it. A dropped
connection loses nothing a snapshot cannot give back, and it does not stop
the run: `stack up` goes on to its end whether anyone is watching or not.

**4. One primitive for every reader: `after(cursor, timeout)`.** The hub
answers what came after a cursor, waits up to `timeout` when nothing has,
and answers `[resync]` for a cursor it cannot continue from (another epoch,
one that fell out of the kept window, a malformed one). SSE, the poll
fallback, a bot and a test read it the same way. There is no queue per
subscriber: no state per reader to leak, no slow reader holding up a
publisher, and replay and live delivery are one code path.

## The pieces

Each piece is a module with a small API of its own, usable without the
others, in `lib/stack/live/` (stdlib only: the host CLI, the host API and
the tools server all import it) unless named otherwise.

```
 producer             host                     tools server (one process)           screen
 ─────────            ────                     ──────────────────────────           ──────
 stack up --events ─▶ host API ─(stream)─────▶ Jobs ──▶ Hub ──▶ SSE route ────────▶ live.js ──▶ widgets
 (EventOutput)        uses follow              uses client           └─▶ poll route (fallback)
                                                                     └─▶ job snapshot route
```

| Piece | API | Used by |
|---|---|---|
| `envelope` | `Event` (frozen: `id`, `type`, `time`, `subject`, `data`), `to_line()`, `from_line()`; the type names; `result_for_screens(result)` | every piece |
| `emitter` | `Emitter(stream).emit(type, data) -> Event`, numbering a run from 1 | `output.EventOutput` (`stack up --events`) |
| `follow` | `follow(argv, cwd=, silence_secs=)`: a command's events, always ending with one `result` | the host API |
| `client` | `call(request, timeout=)` for the one-line reply; `stream(request, silence_secs=)` for a followed run, ending with one `result` even when the API is down | the tools server; the stacker bot next |
| `hub` | `Hub(keep=)`: `publish(type, data, subject) -> Event` with a cursor id, `after(cursor, timeout=)`, `cursor` | the tools server |
| `jobs` | `Jobs(hub, keep=)`: `start(action, target, run) -> Job`, one running job per action and target; `get(id)`, `running()`; `Job.snapshot()` | the tools server |
| `live_routes.py` (core's tools server) | `GET /dashboard/api/events` (SSE), `GET /dashboard/api/poll`, `GET /dashboard/api/jobs/{id}`, `POST /dashboard/api/jobs` (open on the home network until the tools server has security) | browsers, containers |
| `dashboard/live.js` (core's tools server) | `connect() -> live`, `live.on(prefix, fn)`, `live.onResync(fn)`, `live.status` | dashboard widgets |

Silence ends a run, not its length: the host API ends a run that writes
nothing for 1260 s (longer than any step of `stack up` may take), and the
tools server gives up on the API a minute after that. Everything is in
memory: the hub keeps 1000 events and the jobs the last 20 finished ones,
and a restart begins a new epoch, so every screen resyncs.

## Options weighed

- **WebSocket.** Two-way, but a screen has nothing to say during a run that a
  POST cannot carry. It brings an upgrade handshake, keepalive and reconnect
  logic written by hand, on both ends. It becomes right when a screen sends a
  steady stream while it listens (live audio from a tablet, a shared editor),
  or when a page needs more open streams than HTTP/1.1 allows per host (six
  in most browsers), which is what plain HTTP in port mode gives. One SSE
  stream per page, carrying all its jobs, keeps that limit out of reach.
- **STOMP.** A messaging protocol over WebSocket, with destinations,
  subscriptions and acknowledgements, built for a broker. There is no broker
  and one producer per job; it adds a client library and semantics nothing
  here uses.
- **WebTransport over HTTP/3.** Needs a secure context and QUIC. Port mode is
  plain HTTP, and the old tablets RFC-001 targets do not have it.
- **Polling.** Works everywhere and stays the fallback: a screen can always
  ask for a job's state. As the main channel it either misses a step that
  takes 200 ms or asks several times a second.

## How this differs from ADR-010

ADR-010's `dev.famstack.event` is the family's ledger on the Matrix timeline:
what was filed, where its source is, read by bots and kept. These events are
live progress and state for screens. They are not stored past the job,
nothing is replayed from them, and they carry no family content. The
envelopes differ on purpose (no `source`, no `actor`), so code that reads one
cannot take it for the other. A stacklet that comes up may still announce it
in the server room; that is a chat message, not this channel.

## Consequences

- With `--events`, stdout carries event lines and nothing else. The human
  output goes to stderr, as it does today; anything else that would reach
  stdout meanwhile (a hook's print, `docker compose pull`) goes there too.
- Streaming starts with `stack up`. The host API names the commands it
  streams in one place (`STREAMED_COMMANDS`); a command added there writes
  events on stdout with `--events`.
- A streamed run is ended by silence, not by its length. The host API stops
  one that writes nothing for longer than any step of `stack up` may take.
