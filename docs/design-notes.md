# Design notes

Decisions and dead ends worth remembering. Surface them when adjacent code
is touched (see `docs/agent/dev.md`, Pre-1.0 conventions).

**This file is not a task list.** Anything actionable lives on the tracker
board, where it carries a verification gate and an owner. What stays here is
the material a card can't hold: why a shape was chosen, what was tried and
rejected, and which tensions are known but not yet resolved. If an entry
below ever grows a "do this next", move it to a card and leave the reasoning.

## Wiki freshness (curator shipped 2026-06-11)

The curator sidecar ships the first two freshness tiers: debounced
incremental rebuilds (persons + home) and the nightly full sweep. The
third tier (chat-triggered rebuild) is a card.

- **Realtime is NOT a requirement.** The mirror is realtime; the wiki
  is a derived view. The nightly sweep makes the incremental person
  mapping merely *helpful*, never load-bearing - worst case for a
  mapping miss is "stale until tonight". Don't grow the incremental
  heuristics; grow the deriver instead.
- **Page update strategy - unresolved, and the tension is known
  (2026-06-11):** full regeneration resamples page quality (a good page
  can regress on the next sweep); evolving the existing page accumulates
  errors that self-cite (the "Bartley [5]" finding). Most promising
  middle: a fact-checking pass - "page + sources, fix what the sources
  don't support, touch nothing else" - anchored to ground truth while
  preserving good prose. Likely CLI shape then: `wiki` = update/check,
  `wiki rebuild` = fresh full generation.
- **Rejected runtime homes, don't re-litigate:** host daemons (no launchd
  surface), quartz container (node image; "the wiki never writes"),
  bot-runner service concept (one consumer), bot-runner image reuse
  (the curator uses 2 of its 10 deps; slim image won).

## Both halves of a Matrix relation (thread corrections, 2026-08-02)

The archivist learned to *answer* in a thread (`_answer`, `REPLY_IN_THREAD`)
but kept *reading* one `m.in_reply_to` hop, so a correction typed in a
filing's thread was routed to search. A client's reply pointer inside a
thread is a falling-back rendering aid at the newest event there, not the
message the human aimed at, so the pointer stopped naming our filing the
moment anything followed it (a todo link, a status line).

The general shape, worth checking whenever a bot gains a new relation
type: **whoever teaches the sender a relation owns teaching the reader
the same one.** Half a relation is worse than none, because the send
side looks right in the client while routing silently degrades.

Also why it survived a release: the intent spec that would have caught it
(`tests/integration/test_room_modes_e2e.py`) is marked
`xfail(strict=False)`, which is green when broken and green when fixed.
A non-strict xfail is not coverage. Making it `strict=True`, skipping it
with a reason, or deleting it are all better; the action is FAM-6.

## Surviving upstream drift: the `wait_task` pattern

When Paperless-ngx 3.0 redesigned its task API, the fix that held up was
absorbing *both* response shapes in a single parser and covering both
offline: `PaperlessAPI.wait_task` (`stacklets/docs/bot/pipeline.py`) plus
`TestWaitTask` in `test_pipeline.py`.

Worth copying whenever an upstream service changes a contract. One parser,
both shapes, proved in the `unit` lane - it turns a version migration into
a contained task instead of a rewrite, and it means the old version keeps
working while the new one is evaluated.

The corollary is the reason it was needed: **an unpinned image is a
scheduled outage.** `:latest` plus watchtower rolled Paperless from 2.x to
3.0.2 unattended and broke filing across the whole e2e suite.

## Two rot vectors in one URL, and a hook that stopped halfway (2026-08-01)

The Mac took a new DHCP lease. The vault clone's `origin` was
`http://<token>@<old-LAN-IP>:42040/family/memory.git`, so every host-side
git call hung until its timeout. `on_start_ready` was mid-run when the
`TimeoutExpired` escaped, which is why `family/brain` was never created,
which is why the curator's brain push returned 403 on every cycle from
then on. The embedded token had separately expired. The container plane
never noticed any of it: its remote is `stack-code:3000`, a service name.

What the fix encodes: host-side remotes use loopback and the published
port, remote URLs are re-derived on every start (both halves rot, and
nothing else refreshes them), git never raises a timeout at a caller,
and a wedged sync recovers by policy - source preserves local commits,
the projection may realign freely.

**Not built, worth deciding: hook steps that create durable resources
should be independently re-runnable rather than sequential-and-abort.**
`on_start_ready` is one function where step 3 creating `family/brain`
depends on step 1 finishing, so an unrelated failure upstream silently
skips it and the only symptom is a line in `stack up` output. Hooks are
already required to be idempotent, which is most of the way there; what
is missing is that a hook is one all-or-nothing block. A shape worth
weighing: let a hook declare independent steps, run each, report per
step, and fail the hook without skipping the ones that would have
succeeded. The cost is a framework concept where today there is a plain
function, so it needs to earn its place - but "a resource nobody created
and nobody noticed" is the second time this pattern has cost a debugging
session.

## Two query languages, one hop, and only one caller has it (2026-08-04)

`stack memory search <query>` matches the query as a **Python regex** against
file content. The archivist and the agent both search that vault, and only one
of them knows it.

The archivist routes a chat question through `stacklets/docs/bot/recall.py`,
which is the hop. On a message ending in `?` it asks the classifier for 2-4
keywords that would literally appear in a matching document, then OR-alternates
them (`"|".join(re.escape(k) for k in keywords)`) for the memory walker and
joins them with ` OR ` for Paperless, because Whoosh and `re` disagree about
what `|` means. Asked "What do we still need to buy for the camping trip?" it
reports `Searched for: Travel, Shopping, Trip` and answers with a citation.

The agent's `memory_search` tool sends the sentence itself. As a regex that
looks for those exact words adjacent, which no file contains, so every
natural-language question returns nothing. The tool's own parameter
description says "Natural-language question or keywords", so the contract the
model is handed is not the contract the CLI implements. What the model does
with an empty non-answer is ask again, differently, which is the shape of the
loop in [ADR-012](adr/adr-012-nanobot-fork.md) lesson 5.

**Where the hop belongs.** Not in the archivist. Own the resource, own the
concern: memory owns the vault and owns what a query means against it, the
same way the archivist owns filing because it owns Paperless. `recall.py` sits
in `stacklets/docs/bot/` for historical reasons, and the archivist already
imports `memory.lib`, so the dependency direction is established and points the
right way.

Moving it makes every caller correct at once: the agent tool, the archivist,
`stack memory search` from a terminal, and whatever asks next. Leaving it
means the next consumer re-learns this the way the agent did.

**What was decided when it moved.** The rewrite lives in `memory.lib` and
takes the caller's LLM, so memory owns the hop without owning a client. On the
CLI it sits behind `stack memory search --nl`, which reaches the model in the
bot-runner the same way `stack memory wiki` does. Opt-in rather than inferred:
this surface's default query language is a regex, in which `?` is a quantifier,
so inferring from a trailing `?` would turn `Zelt?` into a surprise model call,
and it is on an agent's hot path where the default has to stay cheap. Chat keeps
inferring, because there a rewrite per message is affordable and one character
is a rule a family can learn. Two guards keep the cost honest: a query with no
whitespace never leaves the host, and every failure (no AI, container down,
model silent) falls back to searching the query literally. Both surfaces print
`Searched for: ...`, because a rewrite that picked the wrong words has to look
different from an empty vault.

Both current callers should keep working unchanged through the move. That is
the test.

## A new compose variable and the .env that predates it (2026-09-20)

Adding `${EXTENSIONS_DIR}:/extensions:ro` to core's compose file exposed
something older than the mount. `compose_down` and `_refresh_core`'s
`compose_up` pass no env dict, so Docker resolves `${VAR}` from the `.env`
sitting beside the compose file. That file is a derived artifact
([adr-006](adr/adr-006-env-as-derived-artifact.md)), and after an upgrade
it is one release out of date until something re-renders it.

`stack up <id>` is safe: it renders first and passes the env explicitly, and
`_refresh_core` calls `refresh_env("core")` before it recreates core. `stack
down` is not: it never re-renders, so on an instance that has pulled a release
adding a new variable and has not run any `up` since, the variable resolves
empty and compose rejects the volume spec. Loud, and fixed by `stack up core`,
but the user has to know that.

This is not specific to extensions. Every bind mount built from a variable has
had it since the first one. The fix, if it is worth one, is for the down path
to re-render env the way the up path does, which is a change to every
stacklet's teardown and wants its own commit.

## Customising a shipped stacklet without fighting the updater (2026-09-20)

Writing the update docs made the gap obvious. An admin who wants a different
Paperless setting, an extra volume, or a second service beside one of ours has
exactly one move today: edit the file in `stacklets/`. Every update then stops
on that edit, and they resolve the same conflict again on the next one.
`~/famstack-extensions/` does not help, because an extension adds a stacklet
and never replaces one the repo ships. So the only paths we offer are "carry a
patch forever" or "fork", and both are worse than what the admin wanted.

**Compose already has the answer, and we turned it off.** `docker compose` has
loaded `docker-compose.override.yml` beside the main file since forever, but
only when it resolves files itself. `docker.compose()` passes `-f <file>`
explicitly, which disables that discovery. So the feature exists, is
understood by every Docker user, and is one argument away:

```
docker compose -f docker-compose.yml -f docker-compose.override.yml ...
```

A stacklet's override would live at `stacklets/<id>/docker-compose.override.yml`,
gitignored like `.env`, hand-written by the admin, and merged by compose with
its own documented rules. `stack up` picks it up, an update never touches it,
and `stack doctor` can report that a stacklet is running with one so a
surprising container is traceable.

**The same `-f` plumbing is what mounting several extension dirs needs.** That
was left open in [adr-013](adr/adr-013-stacklet-locations-and-stages.md): a
compose file cannot iterate a list, so only the first extension dir is
bind-mounted into the bot runner. A framework-generated overlay, written next
to the base file on every `stack up`, would carry one mount line per configured
directory. One mechanism, two problems, which is the argument for building it
properly rather than special-casing either.

Open, and the reason this is a note and not a card yet:

- Compose overrides cover services, volumes, env and ports. They do not cover
  `stacklet.toml`: `[env.defaults]`, ports the framework renders, health
  checks, hints. An admin who wants a different `PAPERLESS_OCR_LANGUAGE` is
  editing an env default, not a compose file. Either the override grows a
  manifest half (`stacklet.override.toml`), or `stack.toml` grows per-stacklet
  env overrides, which is arguably where it belongs.
- Two writers to one path. If the framework generates an overlay for extension
  mounts and the admin hand-writes one, they collide. Separate names
  (`docker-compose.override.yml` for the admin, a generated file the framework
  owns) or a single generated file that includes the admin's, but it has to be
  decided before either exists.
- An override is unversioned local state that changes what runs. `stack doctor`
  should say so out loud, the way it reports env drift, or the next
  "why is this container different" takes an hour.
