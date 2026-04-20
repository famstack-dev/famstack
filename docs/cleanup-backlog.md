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

## Paperless libmagic sniffing rejects many markdown files

Paperless validates uploads by running libmagic on the bytes, not by
trusting the HTTP Content-Type. A markdown file containing a
`def foo(...):` code block gets sniffed as `text/x-script.python`
and rejected with HTTP 400 — Paperless has no parser registered for
that MIME. Same fate for markdown that looks like shell, C, or
anything libmagic recognises outside the narrow supported list
(`text/plain`, `text/csv`, office via Tika, PDF, images).

Today the archivist renames `.md` → `.txt` + sets
`content_type=text/plain`, but libmagic overrides both. The upload
fails with the chat-user seeing `upload_failed` even though the file
is perfectly valid markdown.

**Scope when picked up:** pick one —

- **Relax via Paperless config**: `PAPERLESS_CONSUMER_RECURSIVE` /
  `PAPERLESS_CONSUMER_BARCODE_UPLOAD_ONLY` style env vars don't
  cover this; may need to patch Paperless's mime-type allow-list via
  a downstream consumer plugin. Possibly not tractable.
- **Pre-wrap the body** so libmagic sniffs plain text regardless of
  contents (e.g. prepend a few kB of prose). Hacky, distorts search.
- **Skip Paperless for text files altogether** — route markdown /
  notes to the future `brain` repo directly. Clean architecturally
  but reopens the "Paperless fit" question and needs the brain
  scaffolding first.

**Why deferred:** working around libmagic in the hot path is gnarly;
the real architectural answer (brain repo for notes) is a bigger
conversation. For now the archivist surfaces the 400 body in logs
(`_paperless_upload` logs the response), and the markdown e2e test
stays within libmagic-safe content.

---

## Bot context (ctx object for long-running bots)

Hooks get a rich `ctx` with `ctx.secret`, `ctx.users`, `ctx.stack`,
`ctx.http_*`, `ctx.shell`, `ctx.step`. Bots get env vars + a settings
dict + session_dir. The asymmetry forces every new bot need to be
plumbed as an env template in `core/stacklet.toml` or a bot-local
state file.

Concrete friction points already felt:
- Archivist mirrors Forgejo creds to `/data/docs/bot/forgejo-creds.json`
  because `.stack/secrets.toml` is mounted read-only.
- Admin usernames arrive parsed out of `STACK_ADMIN_USER_IDS`; native
  access to `users.toml` would be cleaner.
- No way for a bot to invoke another stacklet's CLI plugin without
  duplicating the operation locally.

**Scope when picked up:**
- `lib/stack/context.py` with a `BotContext` class (or a shared base
  with `HookContext`).
- `core/bot-runner/main.py` builds ctx per bot; `MicroBot.__init__`
  accepts it.
- Compose mount adds `stack.toml` + `users.toml` to `/setup-state/`
  (secrets.toml already there).
- Per-plugin `api.py` / `cli.py` split so `ctx.cli("code", "org",
  "create", "family")` can import-and-call without subprocess.

**Why deferred:** touches framework, bot-runner, compose, and every
plugin's shape — worth doing when we have a second bot that wants
cross-stacklet access (deriver, scribe-on-commit, morning briefing).
Today only the archivist would benefit.

**Dependency on this item:** the "Option 3" Forgejo-client collapse
in feat/docs-git-mirror (sync client under `stack.forgejo`, bot
wraps in `asyncio.to_thread`) is fine without ctx — but
`ctx.cli(...)` is what unlocks cleaner Phase 2 if we ever want the
bot to stop touching HTTP directly.

---

## Bot readiness marker — close the `stack up X` → bot-in-room race

`stack up X` returns as soon as X's `[health]` probe passes, but the
archivist (and any future bot) lives in the `core/bot-runner` container
and has its own async startup: log in → initial sync → join its
declared room. Today there's a ~5–15s window after `stack up` returns
where a user who immediately drops a file in `#documents` gets ignored
because the bot hasn't joined yet. The test rig surfaces the same race
whenever core's `.env` changes (e.g. a new stacklet adds an env entry
and the bot-runner restarts).

**Scope:**
- `microbot.py` — write a per-bot readiness marker to
  `{session_dir}/{name}.ready` at the end of `on_first_sync`.
- `lib/stack/stack.py` / `cli.py` — extend `wait_for_healthy` to also
  wait for each declared bot's marker when the stacklet ships a
  `bot/bot.toml`.
- `docs/stack-reference.md` — document the bot readiness contract
  alongside the existing `setup-done` marker.

**Why deferred:** real but mild in production (humans don't type that
fast); test rig mitigated with a `wait_for_room` helper in
`tests/integration/matrix.py`. The refactor touches cross-stacklet
startup orchestration — better as its own PR than folded into a
feature.

**When picking up:** the test helper becomes redundant for the common
case but is worth keeping for scenarios that restart core mid-session.

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
