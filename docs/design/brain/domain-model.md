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

**Household** — the instance itself. Owns its People, the shared
bucket slug, language, and the scope rules. `shared_bucket` being
configurable is the tell that the real concept is a circle of people
sharing an archive; a family is the primary instantiation, a
non-family deployment is the same aggregate with different vocabulary.

**Person** — someone the family knows, declared in one file each:
`family/people/<id>.md` (vault-format §7). Identity is the file's id
(`maggie`). The file holds the full name, the other names the family
uses for them, and the person's chat account if they have one. It is a
state document: the family writes and corrects it, by hand or through
the CLI, and `stack up memory` adds one for every account that has
none. The wiki's person page (`maggie/about.md`) is the compiled
projection of it: the declared names win, the compiled detail fills in
the rest. One entity, two sources, so the family's corrections to
what the stack learned ("Ed is Ned", "Grampa is Homer's father") are
edits to the same file, never a second format.

**Member** — a person who belongs to the household. Not a separate
entity and not a declared field: membership is derived, and the stack
makes an opinionated guess. In the first iteration a person is a
member when they have an account. For a member with an account the
triple equality `matrix localpart == bucket slug == git author` holds,
and it is what makes attribution (`--by homer`), scoping, and the
vault chronicle line up; such a member owns a personal bucket with the
same shape as the shared one. A person without an account (a baby, a
grandparent) is still declared, so the classifier, the transcription
vocabulary and the diary know them by every name, and the wiki gives
them a page; they are not listed among the members. The next iteration
suggests membership from the records when they make it likely (Maggie
lives in the house, the records say so), and asks the family before
it counts. Deriving members from folders is what put `media/` on the
wiki as a family member, which is why the guess starts from accounts
and nothing weaker.

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
doctype, persons, mentions, topics, summary, facts, action items).
`persons` are the members a record belongs to; `mentions` are the
people outside the household it names, extracted in the same model
call that classifies it, so a record can be replayed to recompute
them like every other part of its classification. Provenance.
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
  *Being built, people first.* A person entry is compiled from the
  `mentions` of every record: canonical name, aliases, the kind of
  relationship to the household (relative, friend, neighbor, teacher,
  doctor, ...) and the stated relation, how present the person is
  (records, months, first and last seen), and the records it cites.
  It is a projection; the family's corrections ("Ed is Ned", "Grampa
  is Homer's father") are the person's declared file (Person, above),
  which wins over it and survives every rebuild. Correspondents become a second kind in the
  same registry, and `pet`, `vehicle`, `place` after them, with the
  same aliases, citations and corrections rather than a mechanism
  each. The wiki's people tiers (family, broader family, close,
  other) are a projection of the members plus the registry: tier
  from the relationship kind, order within a tier from presence.
  Evidence: `tools/family-memories/spec.people.en.yaml` and the
  people-tiers prototype (2026-10-08). Relationship kind put 21 of 22
  people in the right tier; mention counts alone, 14.
- **FactStore** — `facts.toml` holds facts (rule | fact | habit) with
  no aggregate managing them. Typed facts with as-of validity are what
  turn a standing topic from a folder into knowledge: policy number,
  coverage, renewal date, each citing its source Record.
- **Reminder** — time-bound intent attached to a Member or Topic.
  Standing-topic facts with dates (renewals, expiries) feed it almost
  for free.
- **Dream cycle** — a nightly pass after the compile that checks what
  was compiled for plausibility: one person with two fathers, a
  relation that contradicts another source, near-duplicate entries,
  dates that cannot both be true. Cheap deterministic checks select
  the suspects; the model rereads only their cited records. It never
  rewrites the family's own documents: a contradiction the sources
  cannot settle becomes a question to the family in chat, and the
  answer becomes a correction. Majority voting is not enough: in the
  prototype Grampa was "Marge's father" in 3 records and "Homer's
  father" in 2, because Marge writes "Grampa" from the children's
  side. Earlier sketch: `ontology-design.md`, "Maintenance".

## Rules of thumb

1. Deletion test first: information loss means vault, rebuildable
   means brain.
2. New mutable content is a state document behind the CLI write seam,
   modeled like TodoList.
3. New containers are topics until proven otherwise; prefer a
   provenance flag over a new mechanism.
4. Facts cite Records. A fact without a source link is a rumor.
5. The vault is the truth; compiled projections are the agent's fast
   path. The agent may answer from a projection whose statements cite
   their records, and reads the records filed since that projection
   was last compiled. A slow local model cannot search every record
   for every question. (ADR-011, update 2026-10-09.)
