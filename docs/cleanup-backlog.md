# Cleanup Backlog

Pre-1.0 renames and debt items to tackle between features. Each entry
names the concrete change and why we deferred it — so picking it up
later doesn't require rediscovering context.

---

## Rename `FAMSTACK_*` env vars to `STACK_*`

Per `lib/AGENT.md` — *"Within the stack we call things 'stack' or
'stacklet'. Not famstack."* The framework is the generic stacklet
runtime; only the famstack *instance* is famstack-branded.

**Scope:**
- Framework (`lib/stack/hooks.py`) injects `FAMSTACK_DATA_DIR` and
  `FAMSTACK_DOMAIN` for shell hooks. Also set the `STACK_*` equivalents
  and deprecate `FAMSTACK_*` across at least one release.
- All shell hooks under `stacklets/*/hooks/*.sh` read
  `${FAMSTACK_DATA_DIR:-...}` — update to `${STACK_DATA_DIR:-...}`.
- `stacklets/*/caddy.snippet` references `FAMSTACK_DOMAIN` — update.
- `docs/stack-reference.md` documents `FAMSTACK_DATA_DIR` and
  `FAMSTACK_DOMAIN` — rewrite with `STACK_*` canonical, note the legacy
  alias.

**Why deferred:** touches ~10 files across stacklets; fix-the-bug
slice wanted one well-scoped change (inject the env vars the docs
claimed existed). Rename is a separate, reviewable PR.

**Shape when picked up:** framework sets both names simultaneously
during the transition; shell hooks migrate file-by-file; remove legacy
name once all hooks switched.
