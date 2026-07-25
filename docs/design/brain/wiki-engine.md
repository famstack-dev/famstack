# Wiki Engine

The component that turns the memory vault from "filing cabinet" into
"the family's compounding knowledge artifact." Karpathy's LLM-wiki
pattern, adapted to famstack's architecture (entity buckets, ontology,
events, hooks).

This doc plans the work as a sequence of independently shippable
steps. Each step delivers user-visible value on its own; you can
stop after any step and still have a usefully better product.

## Vision

When the family takes a photo of a letter, three things happen:

1. The archivist files the document (already works).
2. The relevant concept page, the relevant entity page, and the
   family index all update to reflect the new knowledge (the wiki
   engine's job).
3. When someone later asks "what does our car insurance cover?", an
   LLM reads the wiki, drills into the relevant pages, and answers
   with citations (`stack memory ask`, already 80% wired - see the
   precondition below).

The deliverable is the wiki: a persistent, browsable, agent-readable
artifact that compounds in value as more is filed. Open it in
Obsidian or Forgejo and it reads like a hand-curated knowledge base
that always happens to be current.

## Karpathy in one line

Three layers - **raw sources → LLM-maintained wiki of markdown pages
→ schema doc** - where every ingest touches 10-15 pages so the
catalog, the entity pages, and the concept pages stay current
without human bookkeeping.

## Where famstack is today

- Solid raw-sources layer: Paperless documents + Matrix captures.
- Entity-bucketed vault (`family/`, `homer/`, `marge/`, ...) with rich
  frontmatter, mirrored to Forgejo on every ingest.
- Ontology + facts as TOML (machine-readable taxonomy).
- **Query pipeline already implemented**, bound to the Matrix `?`
  surface only. `recall.resolve_search_query` extracts keywords,
  `memory.lib.search_memory` + Paperless search produce hits,
  `nl_query.build_evidence` merges them, `Classifier.synthesize_answer`
  generates the answer with `[N]` citations. The core exists; only
  the surface count is one.
- Single-file writes only: one source produces one markdown file. No
  cross-page updates, no concept or entity pages.
- `wiki/` directory reserved but unimplemented.

The bones are right. What is missing is (a) more surfaces over the
existing query core, and (b) the orchestrator that makes pages
update each other on ingest.

## Deviations from Karpathy (and why)

| Karpathy | famstack | Why |
|---|---|---|
| Flat entity pages at the vault root | Per-entity buckets (`homer/`, `marge/`, ...) | Privacy scoping. A query for Bart shouldn't accidentally read Marge's notes. The wiki engine respects bucket boundaries on writes. |
| LLM-discovered concepts | Concept pages seeded from `ontology.toml` | Family taxonomy is small, stable, hand-curated. Better to start from the ontology than re-discover topics from prose. |
| Full LLM rewrites of pages on ingest | Bracketed regenerate regions (`<!-- begin: generated --> ... <!-- end: generated -->`) | Lets hand-edits survive deriver passes. Pattern already live in `correspondents/*.md`. Trust is structural, not LLM-behavioural. |
| Markdown-only knowledge | TOML-typed ontology + facts beside markdown | Classifier reasons over structured data; prose pages exist for humans. Two surfaces, one truth. |
| User-triggered "ingest this" flow | Event-driven deriver subscribing to `dev.famstack.event` | The family takes photos; the wiki updates itself. Better fit for ambient capture than the writer's-room flow. |
| `log.md` as primary audit trail | No `log.md`. Git history *is* the log; commit messages follow the Karpathy `[YYYY-MM-DD] kind \| summary` prefix. `stack memory log` wraps `git log --pretty=format:"%s"` for ergonomics. | The framework's invariant is "state is derived, not stored." `log.md` would be a stale mirror of git. If Obsidian transclusion later wants a markdown surface, it gets generated on demand. |
| Schema doc at the repo level | Schema doc at the vault root (rename/extend `seeds/README.md`) | The vault is its own world. Anyone cloning into Obsidian gets oriented without needing to find the repo's AGENT.md. The install hook already writes this file; no separate step. |

These are intentional improvements for a family vault. They are not
re-litigations of Karpathy's reasoning; they are adaptations to a
different operational context.

## Precondition: hoist the query core into the memory stacklet

Before any of the steps below, **relocate the query pipeline from
the docs bot to the memory stacklet**. This is not a "step" in the
user-value sense (the user sees no change yet); it is the
architectural prerequisite that lets every later step add a thin
surface instead of duplicating a core.

### Current state

The query layer lives entirely under `stacklets/docs/bot/`,
because the archivist grew it organically when adding Matrix
question mode. The memory stacklet owns only `search_memory` (the
regex engine). Every other piece of the query stack imports back
from the archivist's bot directory, which inverts the natural
dependency: memory should own query, not docs.

### What moves, what stays

| Module today | New home | Why |
|---|---|---|
| `docs/bot/nl_query.py` (`build_evidence`, `format_evidence_item`) | `memory/lib.py` (evidence + render section) | Pure data shaping over memory + Paperless hits. Belongs alongside `search_memory`. |
| `docs/bot/recall.py` (`resolve_search_query`) | `memory/lib.py` (query parser section) | Keyword extraction from a natural-language question. Memory-shaped, not docs-shaped. |
| `docs/bot/search_format.py` (`memory_doc_url`, `paperless_doc_url`) | `memory/lib.py` (link rendering section) | Citation link builders. The memory module already owns vault paths. |
| `docs/bot/pipeline.py` `Classifier.synthesize_answer` | **stays** in `docs/bot/pipeline.py` | The Classifier is the docs-bot LLM transport. Moving it would invert the dependency in the opposite direction. |
| Paperless search call site | **stays** in the archivist | Paperless is the docs stacklet's concern; memory shouldn't know about it. The archivist passes Paperless hits as a parameter into `memory.lib.build_evidence`. |

### Calling convention

`memory.lib` exposes a synthesizer-injection seam: callers pass a
callable that takes `(question, evidence) -> answer_text`. The
archivist passes its Classifier's bound `synthesize_answer`; the new
`stack memory ask` CLI runs inside bot-runner and does the same.
Future surfaces (deriver bot, MCP, web UI) get the same seam.

### Runtime invariants preserved

`memory.lib` module-level imports stay stdlib-only - the host CLI
plugins run on `python3` without pip deps. Anything that needs
aiohttp/yaml/Pillow uses deferred imports inside the function that
needs them. The existing `correspondents_prompt_section` is the
pattern.

### What this unlocks

After the move, the archivist's `?` handler is a 10-line caller
into `memory.lib`. Adding any new query surface becomes a thin
glue file. Every step below is then either "new thin surface" or
"new write-side feature," never "build a query layer."

---

## Roadmap

Seven steps. Each is a coherent release. Steps 1-3 are CLI-triggered
features (Karpathy's manual flow); Step 4 introduces the deriver bot
and shifts to event-driven; Steps 5-7 enrich.

### Step 1: `stack memory ask` (CLI query surface)

**Focus:** the existing query pipeline gets a CLI surface. First
user-visible win from the precondition.

**User sees:**

```
$ stack memory ask "what does our car insurance cover?"
Our Duff Insurance policy covers comprehensive damage on the family car
including parking dents and animal collisions, with a 500 EUR
deductible [1]. Roadside assistance is included nationwide [1, 3].

[1] family/documents/2026/03/2026-03-12-duff-insurance-policy-renewal-p247.md
[3] family/documents/2025/11/2025-11-04-duff-insurance-confirmation-p183.md
```

Same answer reachable from the Matrix surface already (`?` mode).

**Mechanism:** ~50-line CLI plugin at `stacklets/memory/cli/ask.py`.
Calls into the hoisted query core. Stdout by default, `--json` for
machine output. Exit codes: 0 (answered), 1 (no evidence found), 2
(invalid args), 3 (vault unreadable). Matches the contract of
`stack memory search`.

**Rationale:** the highest-value capability per line of new code in
the entire plan. The work is the precondition; this step is the
payoff. Also: gives agents (and future bots) a query surface they
can invoke without going through Matrix.

**Deviation:** citations resolve to vault-relative paths
(`family/documents/.../filename.md`), not page names. Lets the user
open the exact source in Forgejo. Page names follow in Steps 2-3
when concept and entity pages land.

### Step 2: Concept pages from ontology

**Focus:** make each topic in the ontology a real, browsable page.

**User sees:** `<shared_bucket>/concepts/<topic>.md` for every topic
in `ontology.toml`. Each page has: a one-paragraph definition
(hand-edited or empty), an auto-generated "Documents under this
topic" table sorted by date, an auto-generated "Correspondents
related to this topic" list. Opening Forgejo, the family can browse
"Insurance" or "Vehicle" and see everything filed about it.

**Mechanism:** `stack memory rebuild-concepts`. Reuses the
correspondents regenerate machinery (walker + bracketed regions +
`git add && commit && push`). Walks documents whose frontmatter
`topics:` includes a topic; fills the generated region. Hand-edits
outside the brackets persist.

**Rationale:** the ontology is already structured; this turns it
into prose surfaces. Concept pages give the deriver bot (Step 4)
explicit targets for cross-page updates. Also turns `stack memory
ask` into an index-first query: the LLM reads concept pages first,
which are denser per token than raw documents.

**Deviation:** concepts are seeded from `ontology.toml`, not
LLM-discovered. The ontology is a hand-curated narrow taxonomy
(few dozen topics). Karpathy's discovery flow assumes hundreds of
emergent concepts across a research domain.

### Step 3: Per-entity "about" pages

**Focus:** each family member gets a coherent overview.

**User sees:** `<entity>/about.md` for each entity (and
`family/about.md` for the shared bucket). Carries a hand-written
"Who is this?" intro plus auto-generated sections: recent activity
(most recent documents/notes/bookmarks involving them), facts about
them (from `facts.toml` filtered by `subject`), correspondents that
write to them.

**Mechanism:** `stack memory rebuild-entities`. Reads frontmatter
`persons:` across the vault, joins with `facts.toml`. Bracketed
regenerate regions. Same pattern as concepts.

**Rationale:** entity pages are the second cardinal axis after
concepts. Together they cover "what is this about" and "who is this
about" - the two questions every family answer turns on.

**Deviation:** entity pages live *inside* the entity bucket
(`marge/about.md`), not at the vault root (`marge.md`). Keeps the
bucket boundary clean - the wiki engine reads `marge/` and writes
`marge/about.md`. Karpathy's flat entity pages would dissolve the
scoping.

### Step 4: Deriver bot (event-driven cross-page updates)

**Focus:** the wiki maintains itself.

**User sees:** photographing a document updates the concept page, the
entity pages of every person mentioned, and the family index - all
within seconds of the archivist filing. No manual rebuild commands
needed.

**Mechanism:** new MicroBot (`deriver-bot`) under
`stacklets/memory/bot/`. Subscribes to `dev.famstack.event` envelopes
emitted by the archivist (`document.filed`, `note.captured`,
`bookmark.captured`). On each event:

1. Determine touched pages: concept(s) for each topic, entity page
   for each person, family index.
2. Read current page contents (each is bounded by bracketed regions).
3. Single LLM pass: "given this new source, propose minimal edits
   inside the generated regions of these N pages, preserving
   anything outside the brackets."
4. Write back, commit with a Karpathy-format message
   (`derive: [YYYY-MM-DD] kind | source title`), push.

The bot lives in the memory stacklet so it has the vault path,
ontology, and facts cleanly in scope. It uses the same Forgejo
auth pattern as the archivist (token issued at install). It
reuses the synthesizer seam established in the precondition.

**Rationale:** this is where Karpathy's "one ingest touches 10-15
pages" comes alive. Until this step, the wiki is a periodic
rebuild; after it, the wiki *is* the system's state.

**Deviation:** event-driven, not user-triggered. The family takes
photos in chat; the wiki updates in the background. Possible
because famstack already has the event envelope (Matrix ledger).
This is a strict improvement over Karpathy's manual flow for the
ambient-capture use case.

**Improvement room:** the deriver can batch events that arrive in
the same minute (multiple pages from one scan session), so one
LLM pass updates the wiki for the whole batch. Saves tokens, keeps
commits coherent.

### Step 5: `stack memory log` (chronological ingest timeline, derived)

**Focus:** a scrollable timeline of what the family has filed, with
no new state to maintain.

**User sees:**

```
$ stack memory log --since 1w
[2026-05-23] document | Duff Insurance car-insurance renewal (2026)
[2026-05-23] bookmark | Best autumn hikes around Lake Constance (marge)
[2026-05-22] note     | Bart's swim-team schedule (homer)
```

Same view via Forgejo's commit history page (already free).

**Mechanism:** the deriver and archivist both write commit messages in
the Karpathy prefix format: `derive: [YYYY-MM-DD] kind | source title`
and `file: [YYYY-MM-DD] kind | source title`. `stack memory log` is
a ~20-line wrapper around `git log --pretty=format:"%s"
--since=...`.

**Rationale:** the original plan had this as a `log.md` file written
by the deriver. The framework's "state is derived, not stored"
invariant says: shape the commit messages, expose the query, skip
the file.

**Deviation:** material - no `log.md` artifact. Karpathy uses
`log.md` because his sources are not versioned. famstack's vault is
a git repo; the log is free.

**Improvement room:** if Obsidian transclusion ever becomes
important (concept pages embedding a "recent activity" slice),
generate `log.md` on demand from git history - same pattern as
every other derived page.

### Step 6: `stack memory health` (drift, contradictions, orphans)

**Focus:** keep the wiki honest as it grows.

**User sees:** `stack memory health` reports:

- Orphan pages: markdown files with no inbound links from any
  concept or entity page.
- Missing concept pages: topics in ontology with no concept page yet
  (cheap to detect, easy to auto-create).
- Stale claims: frontmatter `date` older than threshold with no
  recent supporting source.
- Contradiction candidates (LLM pass, on-demand): pairs of pages
  whose claims appear to disagree.

The first three checks are deterministic. The fourth runs only when
asked, because it costs LLM time.

**Mechanism:** extends the existing `stack memory check` command. The
deterministic checks run on every deriver pass and surface in
`stack memory health`. The LLM contradiction pass is opt-in via
`--deep`.

**Rationale:** Karpathy says abandoned wikis die because of
bookkeeping load. famstack's pattern already pushes bookkeeping
to the deriver; `health` is the safety net for what the deriver
did not catch.

**Improvement room:** orphan check can suggest the fix ("create a
stub entry under `concepts/<topic>.md`?") and apply it on `--fix`.
Low-stakes work the deriver can ratify next pass.

### Step 7: Answer-back (synthesise into the wiki)

**Focus:** the wiki compounds with every question answered.

**User sees:** after `stack memory ask`, the CLI prompts: "Save this
answer to the wiki? [y/N]". Yes saves it as
`family/synthesis/YYYY-MM-DD-<slug>.md` with full citations
preserved. The next time someone asks a similar question, the LLM
finds the synthesis page first.

**Mechanism:** write the answer + frontmatter (question, asker,
date, cited paths) to the synthesis directory. The deriver bot
treats synthesis pages like any other source on the next pass:
they update concept pages, the index, the entity pages.

**Rationale:** closes the Karpathy loop ("Valuable answers are filed
back into the wiki to compound knowledge"). Synthesis pages
gradually replace ad-hoc LLM queries with curated answers.

**Deviation:** opt-in per answer rather than always-on. A family
vault accumulates trivial questions ("when was Bart's last dentist
visit?") that would clutter the wiki. The prompt forces a quality
gate.

## Cross-cutting design choices

**Stacklet ownership.** The wiki engine is part of the memory
stacklet. The deriver bot lives at `stacklets/memory/bot/`, sharing
the existing token, vault path, ontology and facts. The archivist
remains the source-filer; the deriver is the page-maintainer. One
write boundary per bot.

**The vault is the schema doc's home.** `seeds/README.md` (renamed
to AGENT.md when the install hook seeds the vault, or left as README
if Forgejo's auto-render is preferred) describes the layout. Every
bot and CLI reads it on boot to learn the page shape. This avoids
hard-coded layout assumptions in the bot code. Not a separate step;
done as part of the precondition + Step 4.

**Bracketed regenerate regions everywhere.** Already proven in
correspondents. Adopt for concept pages, entity pages, synthesis.
The bracket is the safety contract: hand-edits outside brackets
survive; generated content stays predictable.

**Citations resolve to vault-relative paths.** Always. Anchor links
inside pages are nice-to-have but not load-bearing. Forgejo
renders relative links; Obsidian resolves them too. The LLM never
needs to know the absolute path.

**Per-bucket scoping is structural.** The deriver reads across all
buckets to find cross-references but writes only to bucket roots
and the shared bucket. The bot's tests assert this invariant. A
query "for Bart" is run against `bart/` plus `family/`, never
against `homer/` or `marge/` unless explicitly scoped.

**Commit messages are the log.** The deriver and archivist both
write commit messages in Karpathy's `[YYYY-MM-DD] kind | source title`
prefix. This makes `git log --pretty=format:"%s"` (and any wrapper
around it, like `stack memory log`) the canonical chronological
view. No separate log file to maintain.

**LLM passes are bounded.** Every deriver pass has a token budget
(initially: the current ingest's source plus the candidate pages it
touches, no transitive crawl). If a topic page would have to grow
beyond a threshold, the deriver opens a "summary" callout and
truncates. Prevents runaway rewrites.

**Divide and conquer; one core, many surfaces.** The query core
sits in `memory.lib` after the precondition. Surfaces - CLI,
Matrix, deriver bot, future MCP/web - are thin callers. No
duplicated walkers, no parallel synthesis stacks. Repeating this
principle from `AGENT-dev.md` here because the wiki engine is its
flagship application in famstack.

## Open questions

- **Conflict resolution when hand-edits collide with regenerate
  brackets.** If a human edits inside the brackets, the next deriver
  pass overwrites them. Options: (a) detect drift and abort with a
  warning, (b) move the human's edit outside the brackets
  automatically, (c) merge. (a) is safest for v1.

- **Multilingual wiki.** Today the ontology is bilingual (en/de).
  Should concept pages exist per language, or one page with
  language sections? Probably one page, language-tagged regions.
  Lean on `[core].language` for the default render.

- **Wiki versioning and "as-of" queries.** Git already gives this for
  free, but `stack memory ask --as-of 2025-12-01` would need to
  read pages from that commit. Worth flagging in v1 but not
  implementing.

- **External sources.** Karpathy's pattern assumes URL/article
  ingestion. famstack already routes URL captures through extractors.
  The deriver should treat captures and documents identically for
  cross-page updates; the source-kind is metadata, not behaviour.

- **`stack docs overview` future.** After the precondition, `docs
  overview` is the only remaining bespoke walker+LLM+citation
  stack. Collapsing it onto `memory.lib` is clean follow-up but
  not blocking. Question: does `overview` survive as a fixed-prompt
  shortcut, or get reabsorbed as a special-case `memory ask`?

## Related

- [[family-memory]] - underlying vault structure
- [[knowledge-architecture]] - event bus and storage layout
- [[knowledge-implementation]] - the deriver's place in the runtime
- [[ontology-v1]] - the taxonomy that concept pages render
- [[phase-2-consolidation]] - earlier phase-2 plan this revises
- [[diary-journal]] - the user-facing read surfaces the wiki feeds into
