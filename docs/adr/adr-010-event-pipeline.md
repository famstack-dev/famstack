# ADR-010: Event Pipeline

## Status
Accepted

## Context
famstack keeps growing inputs that follow the same shape: a document arrives in
Paperless, a URL or note is pasted in Matrix, a voice memo is recorded, and
(next) email is fetched over IMAP. Each one is captured, surfaced in a Matrix
room, filed into the vault, and later consumed — by the wiki deriver, the
search/Q&A loop, reminders, and the planned Family Agent.

Looking at this from a birds-eye view, four boxes recur:

1. **Sources** — an inbound channel produces a `SourceContent` (text + title +
   origin URI). Documents, captures, soon email.
2. **Ledger** — every state-changing filing emits a `dev.famstack.event`
   envelope on the Matrix timeline (`build_document_event` / `build_capture_event`).
3. **Surface / routing** — rooms bind to intent via `dev.famstack.capture`
   room state carrying a `kind` (topic, documents, soon mailbox); filings post
   to the bound room.
4. **Projections / processors** — consumers fold the stream into derived state
   and actions: the wiki deriver, the tasks rollup, the agent, reminders.

This looks like event sourcing, which raised the question of whether to (a)
adopt event sourcing as a formal architecture and (b) build a generic
Channel/Processor framework now that we have nearly three sources.

The risk on both: the repo's own guidance (`agent/plan.md`, `dev.md`) warns
that generalising across consumers before a second/third concrete caller forces
the abstraction is the canonical premature-framework failure mode. The
archivist's tool loop was deliberately left un-generalised for exactly this
reason.

## Decision
Name the architecture **the event pipeline** and treat it as a *content
pipeline with an audit ledger and derived projections* — event-sourcing-
flavored, but not textbook event sourcing.

- **The vault (Forgejo git) is the working source of truth** for reads and
  hand-edits; its git history is the durable record. The `dev.famstack.event`
  stream is the audit/notify ledger, not the system of record. We do not adopt
  a formal event store or CQRS — that ceremony does not pay off for a
  single-Mac, single-family system.
- **The machine-derived vault is reproducible from its sources, indexed by the
  ledger.** Everything the machine produced — document mirrors, capture
  entries, derived wiki/topic/tasks pages — can be rebuilt by replaying filings
  against their sources. The ledger says *what* was filed and *where its source
  is*; the source holds the content. **Reprocessing replays the source, never
  the vault file** — and the source of a chat-originated filing is the *whole
  thread*: a URL/note capture reprocesses **its originating Matrix message plus
  the reply chain rooted on it** (the corrections), folded in timeline order; a
  document re-fetches Paperless by `paperless_id`, an email re-fetches IMAP by
  `message-id`. The vault entry is the fold of original + corrections
  (`_collect_correction_chain` already walks this thread for live corrections;
  rebuild reuses the same walk). The only content not reproducible this way is
  **user hand-edits** (plus `facts.toml`, ontology evolution) — preserved in
  git, the irreducible human delta. This is "from sources, indexed by the
  ledger" — *not* "from Matrix alone"; events stay thin, and full-content
  disaster recovery is the backup stacklet's job, not the ledger's.
- **Extract only the thin seams that already repeat**, nothing more:
  1. the `SourceContent` ingestion contract,
  2. the `dev.famstack.event` envelope,
  3. the `dev.famstack.capture` room-binding-by-`kind`.
  A new source should need only: an extractor that yields `SourceContent`, a
  `kind` for routing, and (if it acts outward) a tool.
- **Defer the framework.** Do not build a generic Channel/Processor abstraction
  layer yet. The next concrete source (email — see
  [../design/agent/email-tools.md](../design/agent/email-tools.md)) is the
  third instance; build it against the three seams above and let it prove or
  strain them. Formalise the abstraction only if a real second consumer or a
  second decision branch forces it — on evidence, not aesthetics.

## Consequences
- There is one named mental model for how inputs flow through famstack; new
  features describe themselves as "a source" / "a processor" / "a binding".
- Adding a source is cheap and uniform: extractor → `SourceContent` → classify
  → vault + event, plus a `kind` for room routing.
- We keep the useful parts of event sourcing (append-only ledger, replayable
  deriver, idempotent keyed writes) without its operational cost.
- The framework is intentionally absent. Until a third instance earns it, some
  per-source divergence (Paperless write-back, sender routing, email account
  binding + outbound) stays as concrete code rather than forced into a shared
  interface — accepted as the cheaper risk than a premature abstraction.
- **Reprocessing is defined as replaying the source, not patching the vault.**
  This closes a current gap: capture reprocess reads the vault file today; the
  reproducibility guarantee requires it to re-derive from the originating Matrix
  message instead. Documents (Paperless re-fetch) and email (IMAP re-fetch)
  already work this way.
- The reproducibility guarantee is kept honest by **one rebuild test**:
  fabricated sources → ingest → vault; wipe the machine-derived files; rebuild;
  assert equality modulo timestamps. Every new source must pass it — if you
  can't replay it from its source, you're not done.
- We deliberately stop short of *full* event sourcing (rebuild-from-log alone,
  time-travel, event-store infra). If those are ever needed, this ADR is the
  place that records we scoped reproducibility to "from sources + ledger" on
  purpose, and the decision would be revisited explicitly.
