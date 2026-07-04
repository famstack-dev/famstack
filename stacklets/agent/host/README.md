# Agent host CLI runner — the `stack` bridge

Lets the containerized agent (Stacky) run an allowlisted set of `stack` commands
on the host, plaintext in and out. This is how Stacky **searches the vault**
today, and the channel it will **act** through (strike a todo) later.

## Why this shape

- The agent runs in a container; the `stack` CLI only works on the host (full
  config). A container cannot run the host binary directly.
- A unix socket file in a bind-mounted dir does NOT bridge the container↔host
  boundary on macOS (Docker Desktop / OrbStack) — the container sees the file but
  connecting never reaches the host listener. Containers *do* reach host-native
  loopback services via `host.docker.internal` (the same path famstack uses for
  oMLX/whisper and for its own stack API).
- So: a tiny host TCP listener on `127.0.0.1:42099`, reached from the container
  via `host.docker.internal:42099`. A **raw plaintext line protocol** — not HTTP,
  not MCP, not JSON. The CLI already prints human-readable text; JSON would just
  cost the LLM tokens. (famstack's built-in stack API speaks JSON for its own
  callers — we deliberately do not reuse that protocol here, only the transport.)

## Pieces

- `host/stack_runner.py` — the host listener. Reads one command line, checks it
  against an allowlist (`ALLOW`), runs `./stack <args>` here, returns stdout
  verbatim. Read-only vault queries for now.
- `client/stack` → `/usr/local/bin/stack` in the image — the container-side shim.
  Stacky runs `stack memory search "..."` via its exec tool; the shim forwards it
  to the runner and prints the reply.
- `hooks/on_start.py` — starts the runner on every `stack up` (idempotent; no-op
  if `42099` is already listening).

## Protocol

```
client -> runner :  memory search Zahnarzt --paths\n
runner -> client :  <the CLI's plaintext stdout, then the runner closes>
```

## Efficiency

Plaintext, no JSON. The agent uses `--paths` for bare file paths and `--limit N`
to cap; the default adds a dated, attributed, one-line snippet per hit.

## Security

The runner runs only commands whose leading tokens match `ALLOW` (currently
`memory search|topic|lookup|correspondents`). It binds loopback — reachable from
containers via the docker gateway, not the LAN. Adding a **mutating** command
(e.g. striking a todo) is a deliberate, reviewed edit to `ALLOW`.

## Hardening follow-ups

- Run the runner under launchd (auto-restart) rather than the `on_start`
  best-effort launch.
- Add a per-instance shared token to requests if the loopback assumption ever
  weakens.
