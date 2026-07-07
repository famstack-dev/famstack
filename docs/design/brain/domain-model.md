# famstack domain model

What we actually model, in one vocabulary. This is the ubiquitous
language for the knowledge core: the names code, docs, prompts, and
chat replies should agree on. It builds on ADR-010 (event pipeline)
and ADR-011 (vault as database, brain as projection).

The one-line frame: famstack started as document management and grew
into knowledge management for a household. The domain model below is
what that growth converged on.

## Three content classes are the write/read split

ADR-011's classes map directly onto classic domain modeling:

| Class | Modeling meaning | Consistency |
|---|---|---|
| Records | immutable domain facts | append, bounded correction window, then frozen |
| State documents | mutable aggregates | read-your-writes through one CLI, attributed |
| Projections | read models | eventual, disposable, rebuildable |

Everything below sorts into one of these three. The litmus test for
placement stays the deletion test: does deleting it lose information?

## Bounded contexts

1. **Knowledge** (the core): the vault and what lives in it.
2. **Ingestion** (supporting): sources, classification, filing.
3. **Conversation** (supporting): rooms, membership, bindings. Matrix
   is both the family's surface and the ledger transport.
4. **Projection** (supporting): brain, wiki pages, citations.
5. **Platform** (generic): stacklets, lifecycle, secrets. Deliberately
   not part of the domain model. It hosts the domain; it is not the
   domain.

## Aggregates in the knowledge core

**Household** — the instance itself. Owns Members, the shared bucket
slug, language, and the scope rules. `shared_bucket` being
configurable is the tell that the real concept is a circle of people
sharing an archive; a family is the primary instantiation, a
non-family deployment is the same aggregate with different vocabulary.

**Member** — identity is the triple equality
`matrix localpart == bucket slug == git author`. That equality is
load-bearing: it is what makes attribution (`--by homer`), scoping,
and the vault chronicle line up. A Member owns a personal bucket with
the same shape as the shared one.

**Record** — the generalization that ate "document". A capture, a
document mirror, an email thread, a voice memo. Identity is
content-addressed (capture hash, paperless id, thread root), which is
why filing is idempotent. The aggregate includes its correction chain,
folded in timeline order. Invariant: a Record is reproducible by
replaying its source, never by patching the vault file (ADR-010).

**Topic** — identity: slug + scope. Owns its capture folders and its
TodoList. Topics have two provenances and the distinction matters:

- *Emergent* topics are born from conversation (a `Topic:` room earned
  a folder): episodic, project-shaped, eventually dormant. Example:
  a camping trip.
- *Standing* topics are born from the ontology, not from chat:
  insurance, finance, health, home, vehicles. They exist before any
  conversation, never end, accumulate few but high-value records, and
  carry cyclical time (renewals, tax years, expiries).

One aggregate, one mechanism (folder, about page, scoped search,
todos); provenance is a property, not a second container type.
Standing topics materialize on first record, never preemptively: no
empty scaffolding, no admin queue.

**TodoList** — the first genuinely mutable aggregate; identity is
scope + slug. Invariants: a ticked box is never resurrected; every
mutation is attributed. Todos are the prototype for every future
state document (lists, plans, schedules): the write seam built for
them is meant to be reused, not re-invented.

**Ontology** — the controlled vocabulary the classifier speaks
(topics, doctypes, synonyms). Schema as data; evolving it is a hand
edit and part of the irreducible human delta.

**Correspondent** — an external party with a canonical name and
aliases, reconciled at classification time. Today an entity discovered
from frontmatter; it wants to live in a registry (below).

## Value objects

Scope (the privacy rule as a type: shared bucket or one member's
bucket, derived from room membership). SourceContent (text + title +
origin URI, the ingestion contract). Classification (correspondent,
doctype, persons, topics, summary, facts, action items). Provenance.
Attribution. CaptureHash / PaperlessId / ThreadRoot. VaultPath (the
deterministic path builders are its factories). SummaryCallout with
Facts and ActionItems. PermaLink (`/go/docs|topic|person`, the
identity map as a URL). The `generated: true` marker. Frontmatter
itself, once the format spec pins it.

## Domain events

`dev.famstack.event` envelopes on the Matrix timeline are the audit
ledger: thin, append-only, saying what was filed and where the source
is. `dev.famstack.capture` room bindings are the value object that
connects Conversation to Knowledge (this room feeds that topic).

## Deliberately missing, named so we stop rediscovering them

- **EntityRegistry** — Person/Correspondent/Topic identity is
  currently heuristic (longest synonym wins). Entities deserve an
  identity authority. This is also the foundation for typed entities
  inside standing topics: a Policy, an Account, a Vehicle, each linked
  to a Correspondent and to source Records.
- **FactStore** — `facts.toml` holds facts (rule | fact | habit) with
  no aggregate managing them. Typed facts with as-of validity are what
  turn a standing topic from a folder into knowledge: policy number,
  coverage, renewal date, each citing its source Record.
- **Reminder** — time-bound intent attached to a Member or Topic.
  Standing-topic facts with dates (renewals, expiries) feed it almost
  for free.

## Rules of thumb

1. Deletion test first: information loss means vault, rebuildable
   means brain.
2. New mutable content is a state document behind the CLI write seam,
   modeled like TodoList.
3. New containers are topics until proven otherwise; prefer a
   provenance flag over a new mechanism.
4. Facts cite Records. A fact without a source link is a rumor.
5. The agent is a client of the database, never of the projections.
