# Cleanup backlog

Items land here with a reason. Surface them when adjacent code is touched
(see `docs/agent/dev.md`, Pre-1.0 conventions).

## Wiki freshness follow-ups (curator shipped 2026-06-11)

The curator sidecar ships the first two freshness tiers: debounced
incremental rebuilds (persons + home) and the nightly full sweep.
Design notes that survive it, for whoever touches this next:

- **Realtime is NOT a requirement.** The mirror is realtime; the wiki
  is a derived view. The nightly sweep makes the incremental person
  mapping merely *helpful*, never load-bearing — worst case for a
  mapping miss is "stale until tonight". Don't grow the incremental
  heuristics; grow the deriver instead.
- **famstacker `wiki` command** (Server Room chat trigger) is the
  missing third tier: "CLI commands are the primitives, the bot runs
  or offers them". Needs the famstack API to allow the command and an
  ack-then-report shape for the multi-minute run.
- `{"cmd": "notify"}` for the famstack API (containers → Server Room
  via `stack messages send`) lives in the git stash
  ("wiki auto-rebuild: curator sidecar + API notify") — platform
  piece, ship it with whichever consumer arrives first; the curator's
  completion notice is a natural one.
- Rejected runtime homes, don't re-litigate: host daemons (no launchd
  surface), quartz container (node image; "the wiki never writes"),
  bot-runner service concept (one consumer), bot-runner image reuse
  (the curator uses 2 of its 10 deps; slim image won).

## CI release gate (post-0.3)

## CI release gate (post-0.3)

The pre-tag gate in `docs/agent/dev.md` is manual; v0.3.0-beta.1 shipped
with a stale `uv.lock` and a wrong `VERSION` string because of it. Move it
to CI in tiers:

1. **Tag-triggered GitHub Action (cheap, do before the next tag):**
   version consistency (`lib/stack/cli.py` VERSION == `pyproject.toml` ==
   tag name), `uv lock --check`, ruff, framework + stacklet unit tests.
   Linux runner, no Docker needed.
2. **Integration suite in CI:** needs Docker + Synapse + Forgejo + the
   OpenAI stub, ~35 min wall clock, assumes repo-root-as-instance. Real
   work, decide after 0.3.0 final.
3. **Fresh-install + ai stacklet:** macOS/Apple Silicon only — needs a
   self-hosted runner on the Mac Studio. Own project, don't start it
   casually.
