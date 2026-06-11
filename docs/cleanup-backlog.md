# Cleanup backlog

Items land here with a reason. Surface them when adjacent code is touched
(see `docs/agent/dev.md`, Pre-1.0 conventions).

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
