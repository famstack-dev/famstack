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

---

## Silent-failure audit across hooks and CLI plugins

Three silent-failure bugs fixed in the e2e session: `on_install_success`
ignored `setup.py`'s return value, `CLI.up` ignored
`run_on_install_success`, `setup.py` used a marker check where a
reachability probe belonged. Pattern: callers call a thing that returns
truthy/falsy and discard the result.

**Scope:** grep for `run_cli_command(`, `run_on_install_success(`, and
`resolver.run("on_` across `lib/` and `stacklets/`. For each call site,
confirm the return is checked OR document why it's intentionally
ignored.

**Why:** silent failures compound. A half-installed stacklet looks
installed. The next test or the next user report is the first signal.

---

## `repo_root` vs `instance_dir` in plugin config

The e2e session added `instance_dir` to the CLI-plugin config dict
alongside the legacy `repo_root`. All five messages plugins now prefer
the new key. Other stacklets' plugins may still read `repo_root` and
treat it as "where .stack/ lives" — which is wrong for non-default
instances.

**Scope:** grep `config.get("repo_root"` in `stacklets/*/cli/*.py`. Each
use is either (a) reading repo structure — correct as repo_root, or
(b) reading `.stack/` — should migrate to `instance_dir`.

**Why:** a second stacklet adopting the `stacktests`-style multi-instance
pattern will break today unless its plugins use `instance_dir`.

---

## Document `stacklet["enabled"]` semantics

`enabled` means "the setup-done marker exists," which (post the
marker-gating fix) means "on_install_success ran successfully." Setup.py
used it as a proxy for "the service is reachable" — and the proxy
broke when setup.py itself became on_install_success. Other plugins
might do the same.

**Scope:** grep `stacklet.get("enabled")` / `stacklet["enabled"]` in
`stacklets/*/cli/*.py`. For each check, ask: "do I mean *installed* or
*reachable*?" Migrate reachability checks to explicit socket/HTTP
probes.

**Why:** the two concepts diverged quietly. Future lifecycle changes
will re-break any implicit coupling.

---

## Admin-role bypass pattern is stacklet-local

`stacklets/messages/cli/setup.py` used to skip users without a
`stacklets = [...]` list — including admins. Docs said admins bypass.
Other stacklets may grow the same pattern (or already have it) without
the admin carve-out. Today only messages filters by `stacklets`; other
stacklets' on_install_success files create accounts for everyone.

**Scope:** either lift the "who gets an account on this stacklet"
decision into a shared helper (`stack.users.get_stacklet_users(stacklet_id)`)
that returns the right set, or document the convention with a lint
check.

**Why:** we'll repeat the divergence the next time a stacklet adopts
per-stacklet user filtering.

---

## Destroy flow sometimes leaves containers

`stacktests cleanup` has a belt-and-suspenders `docker rm -f` sweep
after `stack destroy` because, in practice, destroy occasionally left
`stack-<id>-*` containers running. Root cause probably `compose_down`
exit handling or race with volume unmount.

**Scope:** reproduce the leftover-container scenario (destroy while a
container is unhealthy/restarting is one known path). Add an integration
test. Fix `CLI.destroy` to guarantee container removal or return a clear
error.

**Why:** the `docker rm -f` workaround hides the underlying bug and
teaches users "if destroy seems to succeed but things break, clean up
manually." Not a shipping-quality behaviour.

---

## `on_install.sh` hardcoded-default pattern

Shell hooks read `${FAMSTACK_DATA_DIR:-$HOME/famstack-data}`. The
fallback hid a framework bug for over a year (the framework wasn't
setting the var; fallback matched production by luck). Similar
patterns may hide similar bugs.

**Scope:** grep shell hooks for `${...:-...}` fallbacks to env vars
the framework *should* always provide. Decide: remove the fallback
(fail loudly if framework didn't set it) OR have the framework set
the var unconditionally (current fix for DATA_DIR / DOMAIN). No
middle ground — a fallback silently diverges.

**Why:** "defence in depth" becomes "bug concealment" when the
framework is supposed to be the source of truth.
