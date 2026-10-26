# ADR-020: Live Events for Screens

## Status
Accepted (2026-10-26). The CLI and host API half ships first; the tools
server's jobs and its SSE endpoint follow.

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

- `id` counts up from 1 within a run, so a screen orders events and resumes
  after the last one it saw.
- `type` says what happened. For `stack up`: `step.started` and then
  `step.done` or `step.failed` for a slow step (their `data.started` names the
  event that started it), `step.done` or `step.failed` alone for a quick one,
  `warning`, and `result` last. No type is called `error`, because that name
  is `EventSource`'s own event for a lost connection.
- `time` is ISO 8601 in UTC.
- `data` is the payload. The `result` event carries the whole result the
  command returns: `ok`, or `error`, or `cancelled`, and what goes with them.

The same lines serve the terminal (`stack up <id> --events`), the host API
(`"stream": true` relays them as the CLI writes them), the tools server and
the stacker bot.

**3. State lives in the job, not in the connection.** A run is a job that
keeps its events and its state; a connection is only a view of it. A dropped
connection loses nothing a snapshot cannot give back, and it does not stop
the run: `stack up` goes on to its end whether anyone is watching or not.

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
