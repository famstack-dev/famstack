# ADR-011: Vault as database, brain as projection

## Status
Accepted

## Context
ADR-010 named the event pipeline: sources flow through capture and
classification into the vault, an audit ledger rides the Matrix
timeline, and derived projections (wiki pages, topic pages, rosters)
are rebuilt from the vault. Since then two pressures emerged:

1. `family/memory` accumulates two very different kinds of commits:
   human-meaningful ones (a member files a document, ticks a todo)
   and machine churn (page regenerations, splice updates). The git
   history is a product asset, a member-attributed chronicle of the
   family, and machine churn erodes it.
2. The family agent must answer from grounded, current truth. Derived
   pages are debounced and nightly-swept, so they are eventually
   consistent by design. "Add milk to the list" followed by "what's
   on the list?" must not depend on a curator cycle.

The `feat/brain-projection` branch prototyped a split into two repos:
`family/memory` (source) and `family/brain` (a mirror of source plus
every generated page, rendered by Quartz). The open question was where
mutable-but-derived content such as `todos.md` belongs.

## Decision
Adopt the split, with a placement rule based on write semantics.
Content falls into three classes:

- **Records**: things that happened. Captures, document mirrors,
  emails. Append-mostly, attributed to the filer. Live in the vault.
- **State documents**: mutable current truth that humans and machines
  both curate: `todos.md`, `facts.toml`. Live in the vault.
- **Projections**: pure views with no information of their own:
  `about.md`, folder indexes, wiki pages. Live in brain.

The litmus test: **does deleting it lose information?** If yes, it is
vault content. If it can be regenerated losslessly, it is brain
content. A todo tick is information that exists nowhere else, so
`todos.md` is a vault state document; the harvest step that folds new
action items into it is a source-writing curation act (done where
filings happen), not a wiki-generation step.

Consequences of the rule:

- **The vault is the database.** Records plus state, attributed
  history, one host working copy, and read-your-writes through the
  CLI: a write the CLI acknowledges is visible to the next CLI read.
  This is a stated, tested invariant, not an implementation accident.
- **Brain is a materialized view.** Disposable, machine-owned, one
  curator commit per cycle, rebuilt from memory at any time.
  Generation never writes memory.
- **The agent is only ever a client of the database.** It answers
  record and state questions from the vault. Projections may serve as
  maps (the brief points at `stack memory topic <slug>` rather than
  reciting), never as the answering substrate. Brain's staleness
  therefore never matters to conversational truth.
- **Freshness is a per-tier promise:** state documents are
  read-your-writes; records are fresh within seconds of filing;
  projections are eventual (debounce plus nightly sweep) by design.
- **Generated-ness is declared in the data.** Projection files carry
  `generated: true` frontmatter; the mirror keys on the marker rather
  than on filename conventions.
- **One honest impurity, accepted and documented:** the LLM briefing
  callouts inside record files are machine-derived content in the
  source repo. ADR-010 defines those files as the fold of source plus
  classification, reproducible by replay. The vault format spec names
  this explicitly so it is not "cleaned up" in either direction.

## Consequences
- `family/memory`'s git log becomes a clean, member-attributed family
  chronicle: the asset that compounds and can later carry features
  (per-person history, anniversary replays, yearbook generation).
- ADR-010's reproducibility guarantee becomes structural: brain can be
  wiped and rebuilt, and a rebuild-equality test enforces it.
- Memory's writers are exactly three: the archivist, the CLI, and
  humans. The curator writes only brain.
- Quartz renders brain; wiki edits round-trip through memory and flow
  back via the mirror. Readers must be routed explicitly: search and
  todos read memory, derived pages read brain, and the CLI hides the
  split.
- Cost accepted: a second repo and working copy, mirror lag between
  memory and the rendered wiki (observable via a projected-HEAD
  trailer on brain commits), and a rebase of the prototype branch onto
  the todo work. Implementation is tracked in
  `docs/todos/brain-projection-plan.md`.
