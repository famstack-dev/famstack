"""Live events: how a screen follows what the stack does while it does it.

ADR-020 has the decision; this package is the code, one module per piece,
each usable on its own:

    producer             host                     tools server (one process)           screen
    ─────────            ────                     ──────────────────────────           ──────
    stack up --events ─▶ host API ─(stream)─────▶ Jobs ──▶ Hub ──▶ SSE route ────────▶ live.js
    (EventOutput)        uses follow              uses client           └─▶ poll route (fallback)
                                                                        └─▶ job snapshot route

    envelope   the event (`id`, `type`, `time`, `subject`, `data`) as one line of JSON
    emitter    numbers a run's events from 1 and writes each at once (`output.EventOutput`)
    follow     runs a command that writes events and yields them (the host API)
    client     the host API's two protocols, one reply or a followed run (the tools server)

These are live progress and state for screens, not the family's ledger of
ADR-010: nothing here is stored past a run or replayed into anything.
Stdlib only, because the host CLI, the host API and the tools server
(which mounts lib/stack) all import it.
"""
