# AGENT - Engineer role

Goal: change famstack code safely. Stacklets, framework, CLI, tests, commits.

For full prose: [../stack-reference.md](../stack-reference.md) (framework reference) and [../creating-stacklets.md](../creating-stacklets.md) (stacklet authoring). This file is the compact decision layer.

For ops/lifecycle commands, see [ops.md](ops.md).

## Framing

This repository is **the stack reference implementation**. The stack runtime is a generic "stacklet" runtime; famstack is *one instance* of the stack. Inside the codebase, call things "stack" or "stacklet" - never "famstack". Reserve "famstack" for product-facing surfaces (README, user guide, marketing).

The canonical framework spec is [../stack-reference.md](../stack-reference.md). Keep it up-to-date whenever framework behaviour changes - same commit as the code change, not a follow-up.

Pre-1.0 stance: we keep backwards compatibility only for critical parts. Everywhere else, free to extend or change patterns when it leads to cleaner, more maintainable code. **Conceptual problems get fixed right away** - deferring them only makes them more expensive.

## Before you change code

1. **Find the ROOT cause, not the symptom.** A surface-level fix that papers over a deeper issue is technical debt with a friendly face.
2. **Reuse framework patterns. Always check first.** Before adding code, look in `lib/stack/` and existing stacklets for a concept or helper that already does the job. Duplicating framework logic in a script is a smell.
3. **No duct tape.** Think the change through conceptually first; apply it second.
4. **Discuss the approach with the user before non-trivial changes** - especially when it touches a framework invariant or introduces a new pattern.
5. **Missing a primitive? Propose adding it.** Don't work around the framework, extend it. Think like a pragmatic veteran: is this extension load-bearing or speculative?
6. **Keep `stack-reference.md` current.** Any change to framework behaviour (manifest fields, hook contract, lifecycle, env templates) updates the doc in the same commit.

## Repo layout

```
famstack/
├── stack                  4-line bash wrapper → lib/stack
├── lib/stack/             CLI core (Python, stdlib only)
├── stacklets/             one directory per stacklet (id == dirname)
│   ├── core/              always-on (Caddy, Watchtower, bot-runner)
│   ├── messages/          Matrix + Element
│   ├── photos/            Immich
│   ├── docs/              Paperless-ngx + archivist bot
│   ├── memory/            git-versioned vault for filed content
│   ├── ai/                oMLX + Whisper + Piper (host stacklet)
│   ├── chatai/            Open WebUI
│   ├── code/              Forgejo
│   └── infra/             support services
├── tests/
│   ├── framework/         fast (~3s), no Docker
│   ├── stacklets/         per-stacklet unit tests
│   └── integration/       real stacklets + Docker, opt-in
├── docs/                  user docs + ADRs + this file
└── stack.example.toml     template; users copy to stack.toml
```

`docs/design/` is **gitignored** (work-in-progress design notes). Never commit content there.

## Five framework invariants

1. **`.env` is a derived artifact.** Generated on every `stack up` from `stack.toml` + `[env.defaults]` + secrets. Never edit, never commit, never read as source of truth. (See [adr-006](../adr/adr-006-env-as-derived-artifact.md).)
2. **The CLI is stdlib-only.** `lib/stack/` imports nothing from pip. Container code carries its own deps; host-side tooling (tests, hooks) gets deps via `pyproject.toml [project.optional-dependencies] test`.
3. **State is derived.** No "enabled stacklets" registry. `docker ps -a` + `~/famstack-data/<id>/` decide state.
4. **Convention over configuration.** If a file exists with the documented name (`hooks/on_install.py`, `cli/foo.py`, `caddy.snippet`, `bot/bot.toml`), it is picked up. No registration step.
5. **The `stack` CLI is the sanctioned agent interface.** Bots, hooks, and external automations call `./stack <id> <cmd>` - they do not import from `lib/`. Commands have stable exit codes, JSON output, and idempotent semantics.

## Reprocessing replays the source

Reprocessing re-derives from the source, never from the vault file. For a chat filing the source is the **whole thread** - the original message **plus its reply chain** (corrections) - folded in timeline order. Machine-derived vault state must stay reproducible this way; only user hand-edits are irreducible. See [adr-010](../adr/adr-010-event-pipeline.md).

## Stacklet anatomy

Required:
```
stacklets/<id>/
  stacklet.toml         # manifest
  docker-compose.yml    # services (for docker stacklets)
```

Optional, all convention-named:
```
  hooks/
    on_configure.py     # once, first up - interactive config prompts (gate)
    on_install.py       # once, first up - dirs, native deps, builds
    on_install_success.py # once, after first health pass - tokens, seeds
    on_start.py         # every up, BEFORE containers - validate config, start native svc
    on_start_ready.py   # every up, AFTER health - seed data, sync accounts
    on_stop.py          # every down - stop native services
    on_destroy.py       # destroy - unload plists, uninstall native
  cli/<cmd>.py          # `./stack <id> <cmd>` - leading `_` = private helper
  bot/bot.toml          # bot manifest if shipping a chat bot
  bot/<name>.py         # MicroBot subclass
  caddy.snippet         # reverse-proxy route (domain mode only)
  taxonomy.toml         # stacklet-specific seed data
```

### Stacklet rules

- **`id` == directory name.** Lowercase, no hyphens, no spaces. Used in container names, env namespacing, secret keys.
- **Container name:** `stack-<id>` (single-service) or `stack-<id>-<service>` (multi). Set both `services.<key>` and `container_name`.
- **Network:** every container joins the `stack` network, declared `external: true`. Cross-stacklet refs go by container name (`http://stack-docs-paperless:8000`).
- **Ports:** declared in `stacklet.toml` (`port = 420xx`), bound in compose as `${PORT_BIND_IP:-0.0.0.0}:<host>:<container>`. Framework sets `PORT_BIND_IP` (0.0.0.0 in port mode, 127.0.0.1 in domain mode).
- **Volumes:** bind mounts only, paths from env vars rendered by `[env.defaults]`. No named Docker volumes.
- **Restart:** `unless-stopped` everywhere.
- **Watchtower label:** every container gets `com.centurylinklabs.watchtower.enable=${WATCHTOWER_ENABLE:-true}`.
- **No `build:` in compose** unless `stacklet.toml` sets `build = true`.

A worked example lives in [../creating-stacklets.md](../creating-stacklets.md).

## Hook contract

Python hooks (preferred) define `def run(ctx):`. `ctx` is the framework hook context:

| Key / method | What it does |
|---|---|
| `ctx.env` | rendered env vars (templates resolved) |
| `ctx.secret(name)` | read a secret; `ctx.secret(name, value)` writes one |
| `ctx.step(msg)` | progress line to the user |
| `ctx.shell(cmd)` | streaming shell with error handling |
| `ctx.http_get(url, headers=...)` | parsed-JSON GET |
| `ctx.http_post(url, body, content_type=..., headers=...)` | parsed-JSON POST |
| `ctx.stack` | full Stack instance for `run_cli_command(<id>, <cmd>, ...)` |

Hook rules:

- **All hooks must be idempotent.** They run on retry after partial failure.
- **`on_install` is gated by `.stack/<id>.setup-done`.** Don't create that marker by hand.
- **One file per hook - `.py` or `.sh`, never both.** Python wins if both exist.
- **`on_start` runs BEFORE containers.** Validation and host services only. Raise to abort.
- **`on_start_ready` runs AFTER health.** Needs a live service. Must be idempotent - runs every up.
- **`on_destroy` failures must not block destroy.** Log and continue.

Shell hooks (`.sh`) receive env vars: all rendered vars plus `FAMSTACK_DATA_DIR` and `FAMSTACK_DOMAIN`.

## CLI plugin contract

Files under `stacklets/<id>/cli/<cmd>.py` (excluding `_*.py` and `post_setup.py`) become `./stack <id> <cmd>`.

```python
HELP = "One-line description for --help"

def run(args, stacklet, config):
    if not config["is_healthy"]():
        return {"error": "Stacklet not running - start it with 'stack up <id>'"}
    # ... do work; use sys.argv[3:] for extra args
    return {"result": "..."}     # dict → JSON when piped, pretty otherwise
```

Rules:
- Return a dict. The framework decides JSON vs pretty.
- On error, return `{"error": "..."}` - framework translates to non-zero exit.
- **Never bypass the CLI from another stacklet.** Use `ctx.stack.run_cli_command(<id>, <cmd>, ...)`.

## Bot contract

Bots live at `stacklets/<id>/bot/`. `bot.toml` declares; `id` ends with `-bot`. Module convention: strip suffix → `archivist-bot` → `archivist.py` → class `ArchivistBot` (subclass of `MicroBot`).

Bot rules:

- **Matrix is the canonical event ledger.** Replaying the timeline must reconstruct system state. Emit `dev.famstack.event` envelopes for anything cross-bot.
- Envelope schema: `{source, type, summary, data, actor, ts}`.
- Visible event: `m.room.message` with `dev.famstack.event` content key (humans see it, bots read structured).
- Silent event: `dev.famstack.event`-typed message (Element ignores it).
- **Transport failures are logged, never raised.** A downstream bot offline must not break the emitter.
- Bot passwords go in `[env].generate` (e.g. `ARCHIVIST_BOT_PASSWORD`). The bot runner reads them from `secrets.toml`.

## Env templates

Use only the documented template variables in `[env.defaults]`. Adding a new one requires a `lib/stack/` change. The canonical list lives in [../stack-reference.md § Environment](../stack-reference.md#environment) - do not duplicate here, it drifts.

Most-used: `{data_dir}`, `{domain}`, `{ip}`, `{language}`, `{timezone}`, `{shared_bucket}`, `{stacklet_id}`, `{admin_username}`, `{admin_email}`, `{admin_password}`, `{ai_openai_url}`, `{ai_openai_url_docker}`, `{ai_default_model}`, `{messages_server_name}`.

## Testing

Test runner: **`uv run --extra test pytest`**. The `test` extra in `pyproject.toml` declares every dep. Do NOT re-spell with `uvx --with`.

```bash
make test-unit       # fast offline framework + stacklet tests
make test-demo       # live Simpsons demo instance tests
make test-lifecycle  # Docker lifecycle/container environment tests
make test-smoke      # quick managed-rig e2e subset
make test-e2e        # full managed-rig e2e suite
```

Profile details live in [../../tests/README.md](../../tests/README.md).

Testing rules:

- **Module tests first, and they are the point.** See [AGENTS.md § 6](../../AGENTS.md). Test one coherent piece of functionality from the outside, as a client of it would, so the test states the intent and pins the expected behaviour for every later refactor. Write it to read like API documentation: name the behaviour, say why the case matters. demo-rig and e2e sit on top and prove the wiring; they do not replace this.
- **Do not write tests that mirror the implementation.** A test written next to the code it covers proves the two agree, not that either is right. Assert against something external: a spec, a real service's response, an invariant we promise. If a test could only fail when someone changes their mind, delete it.
- **Behavioural TDD: RED then GREEN.** Write the failing test that captures the behaviour you want; make it pass with the smallest change; then refactor.
- **Blackbox at the module boundary.** Test what a module promises through its public surface. Mock only external interfaces (network endpoints, the LLM), and only when truly required.
- **Prefer a real model over a stubbed one.** `tests/integration/stacktests ai local` points the rig at a self-hosted endpoint: real answers, no cost per call, only slower. A green run against a stub proves the wiring, not the behaviour.
- **Docker integration tests when warranted.** If a change crosses a container boundary or depends on a real service's behaviour, add a test under `tests/integration/`.
- **Tests run against real stacklets and real hooks.** No parallel test-only compose files.
- **Use real Synapse via the `messages` stacklet.** No handwritten Matrix mocks.
- **Allowed mocks: loggers only.** Real imports catch real errors.
- **Established deps over handwritten fakes:** `pytest-httpserver` for HTTP, real services for the rest.
- **Test helpers do one thing.** Add a parameter only when a second test needs it - not preemptively.
- **`tests/integration/eval/` is opt-in** (slow, real model). Excluded from `pytest tests/` by `norecursedirs`.
- **Write tests before fixing.** No duct tape.
- **The rig is shared, not off-limits.** This repo root is the Simpsons dev instance, not anyone's real famstack, so agents may run the rig lanes. Ports are fixed, so exactly one run at a time: check nothing else is mid-run before starting. `tests/integration/stacktests help` lists which subcommands are autonomous, shared, or destructive.

## Code style

- **Functional decomposition.** Decompose complex methods into pure, testable functions and compose them into higher-level behaviour. The top-level method should read as a sequence of named steps.
- **Divide and conquer; one core, many surfaces.** Avoid duplicating large fractions of code across modules. Pragmatic duplication (a few similar lines) is fine; large duplication is a smell. Decompose the problem into small pieces, place each piece where it belongs in the core, and let multiple surfaces (CLI, Matrix bot, hooks, tests) call into the same core. Example: `stack docs reprocess` and the archivist bot share the same pipeline - one classifier, two entry points.
- **Literate code.** Narrative docstrings, section dividers (`# ── Section ──`), prose flow over terse chains. The code IS the specification AND the implementation - keep both legible.
- **Python 3.11 floor.** `tomllib` is stdlib; no compat shims. Use modern Python (`match`, structural pattern matching, walrus when it earns its keep).
- **Comments explain WHY, not WHAT.** If a comment paraphrases the code, delete it. Keep comments that document constraints, invariants, or surprises.
- **No em dashes in user-facing text** (commit messages, rendered docstrings, blog drafts). Hyphens or sentence breaks instead.
- **No `--no-verify` or `--no-gpg-sign`** on commits unless the user explicitly asks. If a hook fails, fix the underlying issue.
- **Unchecked return values are a smell.** On the third site in a session, propose an audit instead of patching a third instance.
- **Re-read the full error line before calling a failure a duplicate.** Check sender, target, specific IDs. Two errors that look similar at a glance often differ in the load-bearing field.
- **No backwards-compatibility shims** in pre-1.0 code. Change the code, update callers, ship.

## Commit & branch rules

- **Check branch state before major work.** `git fetch origin` and see how the working branch relates to `origin/main` (`git log --oneline origin/main..HEAD` and `HEAD..origin/main`) before starting anything substantial, new branch or existing one alike. The branch may be older than you think: `main` advances, and a local `main` can itself be stale. Branch off (or rebase/merge onto) the latest `origin/main`; a branch left behind silently diverges and lands the PR in merge conflicts.
- **Feature branches only.** Never commit to `main`.
- **Semantic prefix required:** `feat:`, `fix:`, `docs:`, `refactor:`, `chore:`, `test:`, `ci:`, `style:`.
- **Message style:** short, end-user POV, present tense. *What changed and why a user cares* - not the internal refactor narrative.
- **No `Co-Authored-By:` trailers.** Project rule.
- **Commit after every non-trivial fix.** Don't batch at session end. Each commit stands alone for review/revert.
- **Cascade rule:** if fix N triggers fix N+1 triggers N+2, stop. Name the cascade and propose continue / revert / defer.
- **Scope drift:** declare it out loud. Silent expansion is forbidden.

## Pull requests

- PR title: short, semantic-prefix, under 70 chars.
- PR body: `## Summary` with 1-3 bullets. **No "Test plan" section** - project preference.
- **Never `git push` without explicit human approval.** Every push, every branch, every time.

## Releases

Pre-tag gate, in order. A published tag is never moved; anything missed here ships in the next one.

1. Working tree clean - `git status` shows nothing modified, no stale `uv.lock` (the version bump touches `pyproject.toml` AND the lock; commit them together).
2. Version bumped in `lib/stack/cli.py` (`VERSION`) and `pyproject.toml`.
3. Full test round green: framework, stacklets, integration.
4. Fresh-instance install verified.
5. Stale references updated: README version callouts, docs links, blog "Try it" instructions.
6. Tag (`vX.Y.Z` / `vX.Y.Z-beta.N`, annotated), push main + tag, publish the GitHub release with Highlights and an "Upgrading from" section.

## Pre-1.0 conventions

- Invariant changes (marker semantics, field renames, contract shifts) get coherent commits - each stands alone for revert.
- Actionable work lives on the tracker board, one card each, every card carrying a verification gate. Design notes - decisions, rejected dead ends, known-but-unresolved tensions - live at `docs/design-notes.md`; surface them when adjacent code is touched. If a note grows a "do this next", move it to a card and leave the reasoning behind.
- Don't add backwards-compatibility shims, feature flags for one-shot migrations, or renamed `_unused` vars.

## What NOT to do

- Don't add fallbacks or validation for scenarios that can't happen.
- Don't introduce a second pattern for an existing concern. If the framework already has one surface (the CLI for agent interaction, hooks for lifecycle, Matrix for events) and you need to add another, **flag it first**.
- Don't cache `stack.toml`. Always read fresh from disk.
- Don't write business strategy, paid-tier details, or personal identifiers into files in this public repo.
- Don't paraphrase a user's content through cloud LLMs - local only. The product exists to prevent that.
- Don't mock libraries in tests beyond loggers.
- Don't read or copy a user's production family vault. Reference paths only; fabricate test data.

## ADR index

Read the relevant ADR before touching the pillar it describes.

| ADR | Topic |
|---|---|
| [adr-001](../adr/adr-001-user-seeding.md) | User seeding strategy |
| [adr-002](../adr/adr-002-port-mode-first.md) | Port mode as the default |
| [adr-003](../adr/adr-003-managed-dns.md) | Managed DNS approach |
| [adr-004](../adr/adr-004-messaging-backend-and-abstraction.md) | Matrix as the messaging backend |
| [adr-005](../adr/adr-005-bonjour-discovery.md) | Bonjour discovery |
| [adr-006](../adr/adr-006-env-as-derived-artifact.md) | `.env` as a derived artifact |
| [adr-007](../adr/adr-007-port-convention.md) | 42xxx port convention |
| [adr-008](../adr/adr-008-convention-based-bot-runner.md) | Convention-based bot runner |
| [adr-009](../adr/adr-009-managed-ai-provider.md) | Managed AI provider |
