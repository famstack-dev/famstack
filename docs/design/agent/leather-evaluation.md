# Leather — Dependency Evaluation

> Subject: [github.com/TGPSKI/leather](https://github.com/TGPSKI/leather)
> Date evaluated: 2026-06-16
> Verdict: **Don't adopt as a dependency. Mine the architecture. Optionally spike against nanobot.**
> Relates to: [plan.md](plan.md) — the Family Agent slot leather would compete for

## What it is

Local agent infrastructure as a **single stdlib-only Go binary** (`go.mod` has zero
external requires). Agents are markdown + YAML front-matter files run against any
OpenAI-compatible endpoint. License GPL-3.0.

Core pieces:

- **Curings** — a named N-agent workflow that "transforms hides into artifacts."
  Agents bind to input queues; chaining one curing's output queue into another's
  input queue gives serial and fan-out pipelines with no external orchestrator.
- **`leather serve`** — one long-running process: scheduler, queue workers, optional
  HTTP API. Built-in FIFO queues with backpressure, retries, dead-letter routing.
  Process lock per state dir.
- **Tool surfaces** — skills (`*.skill.yaml`), toolsets (`*.toolset.yaml`), stdio MCP
  servers (`mcp-servers.yaml`), and `shell-mcp` (JSON manifest → local tool surface).
- **Intake** — webhook-driven workflows with HMAC validation.
- **Audit** — JSONL run history, deterministic replay, artifact lineage.
- **Posture** — loopback-bound HTTP by default, no phone-home telemetry.

## Maturity (the deciding factor)

| Signal | Value |
|---|---|
| Created | 2026-05-21 (under a month old at eval) |
| Last push | 2026-06-08 |
| Release | v0.3.0 |
| Stars / forks | 10 / 0 |
| Contributors | 1 (solo) |

Bus factor of 1 on a project not yet a month old. The design is mature for its age;
the *project* is not something to put under our critical path.

## Where it would land in famstack

Squarely on the **Family Agent** layer ([plan.md](plan.md)), the same slot as the
pi/nanobot Phase 0 spike. Per the "one agent surface" principle we must not end up
with leather *and* nanobot *and* a homegrown runtime as parallel surfaces. Pick a lane.

## What's genuinely attractive

1. **Single self-contained binary, zero deps.** The opposite of our Python-env pain.
   For a host process living next to the Matrix bot, "drop the binary in, no venv" is
   a clean distribution story.
2. **Curing pipelines (queue + dead-letter + replay).** Maps almost perfectly onto
   Family Brain document flows: Paperless ingest → classify → file → notify, with
   auditable JSONL lineage. The HMAC webhook intake fits the event bus too.
3. **MCP + skills as the tool surface against an OpenAI-compatible endpoint.** Plugs
   straight into oMLX / Ollama. Privacy posture (loopback, no telemetry) matches ours.

## Why not adopt it

1. **A sub-month-old solo project as core infra is a real risk.** If the author moves
   on at v0.3, we own a Go codebase embedded in a Python stack.
2. **Language mismatch cuts against the single-CLI ethos.** A self-contained binary
   removes the *runtime* cost (no Go toolchain for users), but every extension is Go,
   not Python — against the "port to the framework's language" rule.
3. **License is fine, not a blocker.** GPL-3.0 invoked as a separate process over a
   socket is no linking concern for AGPL-3.0 famstack; bundling the binary is clean.

## Recommendation

- **Steal the curing / queue / replay model** as a design input for Family Brain
  pipelines regardless of whether we ever run leather. The hides→artifacts +
  dead-letter + JSONL-replay pattern is the best part and it's free to borrow.
- **Optional Phase-0 bake-off:** run leather head-to-head with the nanobot spike on
  one concrete flow (Paperless document → classify → file via Matrix). ~2-3 hrs.
  Decide on evidence, not the README.
- **Watch, don't wire.** Revisit at v0.5+ / multiple contributors. If it survives and
  grows, the calculus changes.
