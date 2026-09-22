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
├── lib/stack/             CLI core (Python; top level stdlib only)
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
2. **The host CLI is stdlib-only.** `lib/stack/*.py` imports nothing from pip, because `./stack` runs on the host's system Python with no venv. A subpackage that only ever loads inside a container may use pip deps declared by the images that mount it: `lib/stack/ai/` imports `openai` and `loguru`. Container code carries its own deps; host-side tooling (tests, hooks) gets deps via `pyproject.toml [project.optional-dependencies] test`.
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

- **Location.** `stacklets/<id>/` is for what the release ships. A private, incubating or third-party stacklet goes in `~/<product>-extensions/` (`[core] extension_dirs` adds more search paths) and is discovered the same way, bot included. Searched in order, first claim on an id wins, and only the first dir is mounted into the bot runner.
- **`stage`.** Anything not finished declares `stage = "beta"` or `"incubating"`. `stack list` marks it, `stack up` warns.
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

## Static checks

```bash
uvx ruff check .                 # undefined names, unused imports
make typecheck                   # types across lib, stacklets, tools, hooks
uvx basedpyright <paths>         # types in just what you touched
```

A language server (basedpyright) is wired up for this repo. **Check your own tool list before relying on it:** the plugin is read by the Claude Code CLI, and harnesses built on the Agent SDK do not start it. With no LSP tools in hand, use `uvx basedpyright <paths>` for diagnostics (same config, so it says what the server would) and grep for navigation. There is no MCP fallback and none is planned: basedpyright ships a CLI and a language server, nothing else, so the CLI is the whole story for an agent without LSP. With them, prefer asking for a definition or the references to a symbol over grepping for the name. Setup and the reasoning behind the type config live in [../../CONTRIBUTING.md](../../CONTRIBUTING.md) - do not duplicate here, it drifts.

Two rules when reading its output:

- **It is not a gate.** The tree is not clean and getting it to zero is not the job. Read what it says about the files you changed.
- **If you add a stacklet, add its execution environment** to `[tool.basedpyright]`. Otherwise its imports go dark and nothing in it is checked. Container-only deps (`nanobot`, `fastapi`) stay unresolved on purpose.

And three ways the server answers wrongly rather than saying it cannot answer. All three were hit in one session:

- **A cold server under-reports.** It answers while still indexing instead of waiting, so the first find-references of a session can come back with just the definition. One symbol went 1, then 3, then 9, then 12 for the same query as more of the tree got parsed. An empty result is not evidence of no callers.
- **Ask from both ends when the answer decides an edit.** For an attribute reached through `self`, asking at the definition missed every same-file use (5 results where the truth was 40); asking at a call site returned a superset that swept in unrelated symbols of the same name (106). Neither number was right. Two queries that agree are worth more than one that looks tidy.
- **It reads `pyproject.toml` once, at startup.** Change the type config and the server keeps answering from the old view while the CLI already has the new one, which is the one way those two can disagree. `/reload-plugins` resyncs it.

`tests/` is indexed but silent by design: navigation reaches test callers, and the CLI never reports test diagnostics. To type check a test file, comment out `ignore` in `[tool.basedpyright]` - naming the file on the command line does not override it.

### Agents without LSP tools

Navigation stays with grep, and none of the caveats above apply to you. But the CLI has one thing the language server does not, and it is the one that matters most here: a baseline, so you read the error you just wrote instead of the couple of hundred that were already there.

```bash
# once, before you touch anything
uvx basedpyright --writebaseline --baselinefile /tmp/famstack-baseline.json

# after each change: only what you introduced
uvx basedpyright --baselinefile /tmp/famstack-baseline.json
```

Exit codes are the contract, so this works as a check and not just as reading material: **0** when you added nothing new, **1** when you did. A bare run without the baseline is always 1 while the tree is unclean, which is why it cannot tell you anything on its own. Add `--outputjson` to parse rather than read.

Keep that file outside the repo. A committed baseline would quietly turn the type checker into the merge gate this repo says it is not, and that is a decision to take deliberately, not a side effect of where a file landed.

## Testing

Test runner: **`uv run --extra test pytest`**. The `test` extra in `pyproject.toml` declares every dep. Do NOT re-spell with `uvx --with`.

```bash
make                 # the lane table: cost, what each needs, when to run it
make lint            # <1s, the whole tree. The hook already covers staged files.
make test-unit       # ~50s, offline, no Docker. Before a push, or when a
                     #   piece of work is done. Not per commit.
make test-integration # ~6m, Docker, APFS. Lifecycle, env rendering, compose,
                     #   health, and the backup engine on real disk images.
make test-demo       # against the running demo instance.
make test-smoke      # quick managed-rig e2e subset.
make test-e2e        # full managed-rig e2e suite.
```

Run the cheapest lane that proves what you changed. Every lane but
`test-unit` owns fixed container names and ports, so exactly one runs at a
time on a Mac: check nothing else is mid-run first. One name per lane, no
aliases. Details, and the timings nobody has measured yet, live in
[../../tests/README.md](../../tests/README.md).

Testing rules:

- **Module tests first, and they are the point.** See [AGENTS.md § 6](../../AGENTS.md). Test one coherent piece of functionality from the outside, as a client of it would, so the test states the intent and pins the expected behaviour for every later refactor. Write it to read like API documentation: name the behaviour, say why the case matters. demo-rig and e2e sit on top and prove the wiring; they do not replace this.
- **Do not write tests that mirror the implementation.** A test written next to the code it covers proves the two agree, not that either is right. Assert against something external: a spec, a real service's response, an invariant we promise. If a test could only fail when someone changes their mind, delete it.
- **Behavioural TDD: RED then GREEN.** Write the failing test that captures the behaviour you want; make it pass with the smallest change; then refactor.
- **Blackbox at the module boundary.** Test what a module promises through its public surface. Mock only external interfaces (network endpoints, the LLM), and only when truly required.
- **Prefer a real model over a stubbed one.** `tests/e2e/stacktests ai local` points the rig at a self-hosted endpoint: real answers, no cost per call, only slower. A green run against a stub proves the wiring, not the behaviour.
- **Seed through the front door.** See [AGENTS.md non-negotiable 11](../../AGENTS.md). To populate an instance with documents, run `tools/family-docs/ingest.py`: it posts each rendered demo document into the Matrix `documents` room as the family member who would plausibly have sent it, so the archivist picks it up and the whole pipeline runs. Uploading to `POST /api/documents/post_document/` instead is faster and produces the wrong corpus - bare OCR text with a filename for a title, no tags, correspondent, document type, summary note, or vault entry. Search, classification, and mirror behaviour all read differently against that, so measurements taken on it are worthless. The same rule holds for every stacklet: drive the user-facing surface, not the service behind it.
- **Docker integration tests when warranted.** If a change crosses a container boundary or depends on a real service's behaviour, add a test under `tests/e2e/`.
- **Tests run against real stacklets and real hooks.** No parallel test-only compose files.
- **Use real Synapse via the `messages` stacklet.** No handwritten Matrix mocks.
- **Allowed mocks: loggers only.** Real imports catch real errors.
- **Established deps over handwritten fakes:** `pytest-httpserver` for HTTP, real services for the rest.
- **Test helpers do one thing.** Add a parameter only when a second test needs it - not preemptively.
- **`tests/e2e/eval/` is opt-in** (slow, real model). Excluded from `pytest tests/` by `norecursedirs`.
- **Write tests before fixing.** No duct tape.
- **The rig is shared, not off-limits.** This repo root is the Simpsons dev instance, not anyone's real famstack, so agents may run the rig lanes. Ports are fixed, so exactly one run at a time: check nothing else is mid-run before starting. `tests/e2e/stacktests help` lists which subcommands are autonomous, shared, or destructive.

## Code style

- **Functional decomposition.** Decompose complex methods into pure, testable functions and compose them into higher-level behaviour. The top-level method should read as a sequence of named steps.
- **Divide and conquer; one core, many surfaces.** Avoid duplicating large fractions of code across modules. Pragmatic duplication (a few similar lines) is fine; large duplication is a smell. Decompose the problem into small pieces, place each piece where it belongs in the core, and let multiple surfaces (CLI, Matrix bot, hooks, tests) call into the same core. Example: `stack docs reprocess` and the archivist bot share the same pipeline - one classifier, two entry points.
- **Literate code.** Narrative docstrings, section dividers (`# ── Section ──`), prose flow over terse chains. The code IS the specification AND the implementation - keep both legible.
- **Python 3.11 floor.** `tomllib` is stdlib; no compat shims. Use modern Python (`match`, structural pattern matching, walrus when it earns its keep).
- **Comments explain WHY, not WHAT.** If a comment paraphrases the code, delete it. Keep comments that document constraints, invariants, or surprises.
- **No em dashes in user-facing text** (commit messages, rendered docstrings, blog drafts). Use a comma, colon, period or parentheses instead.
- **No `--no-verify` or `--no-gpg-sign`** on commits unless the user explicitly asks. If a hook fails, fix the underlying issue.
- **Unchecked return values are a smell.** On the third site in a session, propose an audit instead of patching a third instance.
- **Re-read the full error line before calling a failure a duplicate.** Check sender, target, specific IDs. Two errors that look similar at a glance often differ in the load-bearing field.
- **No backwards-compatibility shims** in pre-1.0 code. Change the code, update callers, ship.

## Commits: the subject is the changelog

The release log is the changelog. `stack update` prints it, the GitHub release
quotes it, famstack.dev renders it. A subject that does not parse is missing
from the release notes.

Messages follow Conventional Commits and the Git and Linux kernel
guidelines, in plain technical English.

```
<type>(<scope>)!: <subject>

<body>

<footer>
```

`!` marks a change that needs the admin to do something.

| Type | Section | Shown |
|---|---|---|
| `feat` | Added | yes |
| `fix` | Fixed | yes |
| `security` | Security | yes, near the top |
| `perf` | Performance | yes |
| `docs` | Documentation | yes, last |
| `refactor` `test` `chore` `ci` `style` `build` | | no |

Nothing outside that list. A change that fits none of them is usually two changes.

The scope is what a reader recognises, never a file or a module:

| Scope | Renders as |
|---|---|
| `agent` | Agent Stacklet |
| `ai` | AI Stacklet |
| `backup` | Backup Stacklet |
| `chatai` | ChatAI Stacklet |
| `code` | Code Stacklet |
| `core` | Core Stacklet |
| `docs` | Docs Stacklet |
| `infra` | Infra Stacklet |
| `memory` | Memory Stacklet |
| `messages` | Messages Stacklet |
| `photos` | Photos Stacklet |
| `archivist` | Archivist Bot |
| `mail` | Mail Bot |
| `scribe` | Scribe Bot |
| `stacker` | Stacker Bot |
| `curator`, `wiki`, `diary` | Memory Stacklet |
| `capture` | Archivist Bot |
| `voice` | AI Stacklet |
| `stack`, `cli`, `doctor`, `update`, `install` | stack CLI |
| `web` | web fetch |
| *(none)* | General |

The scope is a stacklet id or a bot name, written the way the code writes
it, so a reader can go from a changelog line to the directory it came from.
A stacklet renders as "<id> Stacklet" and a bot as "<name> Bot": one release
line saying "Archivist Bot" and the next saying "Docs Stacklet" tells you
which one changed without opening anything.

Anything that is neither, a component like the wiki, the curator or capture, renders
as the stacklet that owns it.

`docs` is both a type and a scope and they mean different things. Type `docs`
is documentation; scope `docs` is the Documents stacklet. `fix(docs):` changes
Paperless behaviour; a README fix is `docs(readme):`. A scope outside the
table still commits and lands under General, so drift surfaces at release time
rather than on the website.

**Subject rules.** Describe the**Subject.** Imperative mood: it completes "If applied, this commit will
...". Lowercase after the colon, no full stop, at most 72 characters
including the prefix. Describe the change as an admin sees it: no class,
file or function names unless an admin types them. A subject that needs
"and" is two commits.

**Body.** Blank line after the subject, wrapped at 72. Why the change is
needed and what it changes; the diff shows how. It must make sense without
the PR. Bullets for sets of changes. No debugging history, test narrative
or rhetoric; those go in the PR.

**No em dashes**, in the subject, the body, or the PR title and
description. All of them are published as release notes. Use a comma,
colon, period or parentheses. An en dash in a range (`1–2`) and a hyphen
are fine. `tools/commit-lint` rejects the em dash in all four places.

| Type | Body states |
|---|---|
| `feat` | what is now possible, how to use it, limits |
| `fix` | symptom and trigger, cause, fix; `Fixes: <sha> ("<subject>")` if a commit caused it |
| `perf` | cause, change, before/after numbers and how measured (the only type with measurements) |
| `refactor` | why, and "No behaviour change." |
| `docs` `test` `ci` `build` `chore` `style` | what changes and why |

```
fix(doctor): compare container env with the compose config

`stack doctor` compared a container's environment with the stacklet's
rendered `.env`, so services that set a different value in compose were
reported as drifted after every recreate.

Compare with the environment `docker compose config` resolves for the
service.
```

he 54 commits in `v0.3.0-beta.2..v0.3.0-beta.3`
already parse. Both that do not are instructive.

```
Move the docs stacklet to Paperless-ngx 3.0.4
```

No type, so it cannot be classified, and it was the most important entry in
the release: it needed a backup before restarting, and 2.x cannot read the
database once 3.x has migrated it. Written properly it cannot be lost:

```
feat(docs)!: move Paperless to 3.0.4

Upgrade: back up `~/famstack-data/docs` before restarting the stacklet
with `./stack restart docs`. Paperless migrates the database on first
start and 2.x will not read it afterwards. There is no downgrade.
```

**The body is markdown.** It is quoted verbatim into the GitHub release
and rendered on famstack.dev, so commands and paths in it are written as
code: `` `./stack restart docs` ``, `` `~/famstack-data/docs` ``. The
subject is not: it appears in plain-text contexts too, including
`./stack update` output, where backticks would be literal clutter.

| Footer | What it does |
|---|---|
| `Upgrade: <what the admin must do>` | Renders as **Action required** at the top of the release. Any update needing a human step: a backup, a config edit, a one-way migration. |
| `BREAKING CHANGE: <what breaks>` | Same section, for something that breaks an existing setup rather than asking for a step. |
| `Refs: FAM-12` | Links the tracker card. |
| `Co-Authored-By:` | Never. Project rule, enforced: `tools/commit-lint` rejects the trailer, a "Generated with Claude Code" line, its link and an Anthropic noreply address, in commit messages and in PR descriptions. |

`tools/commit-lint` enforces the header, and is the same check in every
place that matters: the `commit-msg` hook rejects it before the commit
exists, CI rejects the PR title and every commit in the PR, and the
release gate runs it over the range being tagged. `script/setup`
turns the hooks on, as do `make test-unit` and `make typecheck`; git
cannot do it on clone, by design.

A generator reads the header as
`^(type)(\((scope)\))?(!)?: (subject)( \(#(pr)\))?$` and groups into **Action
required** (any `!` or `Upgrade:`), Security, Added, Fixed, Performance,
Documentation, by rendered scope within each.

## Branch rules

- **Check branch state before major work.** `git fetch origin` and see how the working branch relates to `origin/main` (`git log --oneline origin/main..HEAD` and `HEAD..origin/main`) before starting anything substantial, new branch or existing one alike. The branch may be older than you think: `main` advances, and a local `main` can itself be stale. Branch off (or rebase/merge onto) the latest `origin/main`; a branch left behind silently diverges and lands the PR in merge conflicts.
- **Feature branches only.** Never commit to `main`.
- **Commit after every non-trivial fix.** Don't batch at session end. Each commit stands alone for review/revert.
- **Cascade rule:** if fix N triggers fix N+1 triggers N+2, stop. Name the cascade and propose continue / revert / defer.
- **Scope drift:** declare it out loud. Silent expansion is forbidden.

## Pull requests

- **One PR is one changelog entry.** Squash merge; the PR title is the commit subject, in the format above, under 70 chars. A PR that needs two changelog lines is two PRs.
- PR body: `## Summary` with 1-3 bullets, plus any `Upgrade:` / `BREAKING CHANGE:` footer, which lands in the squashed commit where a reviewer can argue with it. **No "Test plan" section** - project preference.
- **Never `git push` without explicit human approval.** Every push, every branch, every time.

## Releases

Pre-tag gate, in order. A published tag is never moved; anything missed here ships in the next one.

1. Working tree clean - `git status` shows nothing modified, no stale `uv.lock` (the version bump touches `pyproject.toml` AND the lock; commit them together).
2. Version bumped in `lib/stack/cli.py` (`VERSION`) and `pyproject.toml`.
3. Full test round green: framework, stacklets, integration.
3b. Every commit since the previous tag parses as a changelog entry. An unclassified subject blocks the tag: it would be missing from the release notes and from the website. Reword it if it has not shipped, add the entry by hand if it has.
4. Fresh-instance install verified.
5. Stale references updated: README version callouts, docs links, blog "Try it" instructions.
6. Tag (`vX.Y.Z` / `vX.Y.Z-beta.N`, annotated), push main + tag, publish the GitHub release with Highlights and an "Upgrading from" section.

**The tag format is load-bearing.** `stack update` parses `v?MAJOR.MINOR.PATCH[-label.N]` and ignores anything else, so a tag spelled `v0.3.0.beta1` is invisible: `stack update` would keep offering the previous release and never mention it. Hyphen before the label, dot before its number, as SemVer has it. The prerelease sorts below the release it leads to, and label numbers compare as numbers, so `beta.10` is newer than `beta.9`.

Two spellings of the same version exist by necessity: the tag and `lib/stack/cli.py VERSION` are SemVer (`0.3.0-beta.3`), while `pyproject.toml` is PEP 440 (`0.3.0b3`), because Python packaging requires it. Step 2 bumps both.

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
| [adr-013](../adr/adr-013-stacklet-locations-and-stages.md) | Stacklet locations and stages |
