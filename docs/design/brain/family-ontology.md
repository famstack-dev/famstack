# Family ontology: declared entities priming classification

Status: design draft, 2026-06-12. Implementation slice of
`ontology-design.md` (2026-04-14), which designs the full living ontology
(persons, organizations, relations, growth loop, dream-cycle maintenance,
search expansion). Companion to `wiki-page-anatomy.md` and
`wiki-improvements-backlog.md`.

## Relation to ontology-design.md

What shipped since April is only the FIXED generic half of that design:
topics + doctypes in `ontology.toml`, seed-synced with wholesale replace.
The living entity layer (persons/organizations/aliases/relations) never
landed. This doc is the plan for that layer, with three deltas against
the April design:

1. **Bootstrap via onboarding instead of hand-seeding.** April says
   "Homer seeds the ontology files by hand." `!onboard` replaces that
   for every household that is not Homer.
2. **Two-file ownership split.** April assumed one living file. Reality
   now has a shipped seed that clobbers on sync, so the household layer
   gets its own file the sync never touches.
3. **The accuracy claim gets measured.** April asserts the ontology
   context "dramatically improves classification accuracy." Experiment
   round 6 tests that on the Simpsons corpus before we build the wizard.

Format: April argued YAML, the first draft of this doc said TOML; the
final call is markdown entity pages with YAML frontmatter (see Design),
because the correspondents layer already shipped that pattern. Out of scope for this slice:
knowledge_types/decay, cross-service ID mapping (Immich faces, Matrix
IDs), search expansion, dream cycle. They layer on top later; the schema
below keeps `services` and `kind` fields compatible with them.

## Goal

Classification is the highest-leverage step in the pipeline: everything
downstream (filing, wiki, Q&A) inherits its quality. Today the classifier
works open-world: it sees topics and doctypes from `ontology.toml` but knows
nothing about THIS household. A declared family ontology turns classification
into entity linking against a known universe. Hypothesis: priming a weak
local model with household context closes most of the accuracy gap to a
much larger model. That hypothesis is testable (experiment round 6, below).

## Current state (verified 2026-06-12)

- `stacklets/memory/seeds/ontology.toml`: topics + doctypes, multilingual
  names/synonyms/keywords. Generic, famstack-owned. No entities.
- `stack memory ontology` push REPLACES the live vault copy wholesale;
  hand-curated household edits get clobbered (own docstring warns).
- The wiki roster is INFERRED from disk (`wiki.py` `_member_slugs`: bucket
  dirs + frontmatter names). No declared family anywhere. Consequences:
  short-name H1s, "Bartholomew J." vs "Bart" inconsistencies, the deferred
  frontmatter-alias-canonicalization idea has no canonical name to map to.
- `wiki-page-anatomy.md` designs typed identity slots per entity type
  (person/vehicle/pet/property) with no source of truth feeding them.

## Design: entity pages by kind (markdown, not TOML)

Decision 2026-06-12 (supersedes the earlier living-ontology.toml plan):
household entities are MARKDOWN PAGES with frontmatter, in plural
plain-word subfolders of the shared bucket. The correspondents layer
already shipped this exact pattern; we extend it instead of inventing a
parallel TOML registry. Wins: Obsidian graph edges for free, per-entity
git history, free-form body for priming notes and trivia, hand-editable
by normal humans, links to wiki pages are native markdown.

```
family/
  people/         homer.md, bart.md, patty.md (relatives too)
  pets/           slh.md
  vehicles/       junkerolla.md
  places/         home.md
  correspondents/ (unchanged, stays on its own machinery for v1)
```

`kind` is derived from the folder (people -> person, ...), never stored.
The word "entities" appears nowhere user-visible.

### Frontmatter schema (minimal v1)

```yaml
# family/people/homer.md
---
canonical: Homer Jay Simpson      # feeds wiki H1 + frontmatter canonicalization
aliases: [Homer, "H. Simpson", Dad]
role: parent                      # parent | child | relative
member: true                      # the Family = people with member: true
birthday: 1956-05-12              # optional
employer: springfield-nuclear     # correspondent/entity slug
---
[free-form notes: priming, trivia, corrections. High-trust input.]
Wiki: [about page](../../homer/about.md)
```

```yaml
# family/people/patty.md — relative, known but not a member
---
canonical: Patty Bouvier
aliases: [Patty, "Aunt Patty"]
role: relative
member: false
relation: "aunt (Marge's sister)"
---
```

```yaml
# family/vehicles/junkerolla.md
---
canonical: Plymouth Junkerolla
aliases: ["the pink car", "ABC-123"]   # plate is just an alias
owners: [homer]
---
```

Relatives exist so the classifier recognizes non-members (Bouvier
grandparents from birth certificates) instead of inventing phantom
household members. They get no bucket and no generated wiki page.

Rules: slugs match bucket slugs for member people; relations reference
slugs; unknown frontmatter keys are preserved (forward compat); no
secrecy logic, the assistant sees everything
([[project-local-ai-full-trust]]). Wiki identity headers merge these
declared facts with document-derived slots.

## Consumers

1. **Classifier prompt** (the point of this design): a "Household" block
   rendered next to topics/doctypes:
   - persons with aliases and roles ("Bart Simpson, child, also: Bartholomew
     JoJo Simpson; school: Springfield Elementary")
   - organizations with kind and who they relate to
   - pets, vehicles with owners
   Effects expected: correct person assignment for ambiguous docs (school
   letter -> the child attending that school, payslip -> the employee of
   that employer), fewer cross-entity conflations, better topic choice.
2. **Wiki**: declared roster replaces the inferred one; canonical `name`
   feeds deterministic H1s (kills the short-name nit in code, not prompts);
   non-person entities get their profile pages (the car) per
   wiki-page-anatomy.md.
3. **Filing**: alias canonicalization at write time (the deferred
   frontmatter idea lands here: any alias in extraction maps to the
   canonical name/slug).
4. **Q&A bot**: household block as standing context.

## Onboarding

Two entry points, one flow:
- `!onboard` in Matrix (re-runnable, additive; also the correction channel:
  "call him Bart, not Bartholomew" updates aliases)
- install-time CLI prompt (same questions, optional, skippable)

Question set v1 (about 10, each answer writes entities + relations):
1. Who lives in the household? (names, day-to-day names, who is a child)
2. Birthdays? (optional, skippable as a block)
3. Kids' schools / daycare?
4. Adults' employers?
5. Pets?
6. Vehicles?
7. Home address? (property entity)
8. Main bank(s), insurer(s), family doctor(s)? (correspondent entries)
9. Relatives who show up in your paperwork? (grandparents, aunts;
   role: relative, member: false)

Output: one commit per run touching the entity pages, message
`feat(ontology): onboarding <date>`. The wizard never deletes; corrections
edit, removals are explicit.

Phase 2 (not now): extraction proposals. Filing notices an unknown
recurring correspondent and asks in chat "Is 'Dr. Riviera' your dentist?
[yes/no]" -> confirmed entities grow the kind folders without a session.

## Write-path contract

The only part of the living machinery designed up front, because three
writers will exist and polluted entity pages are worse than none:

1. **Provenance per write.** Every change is a vault commit; the message
   names the writer: `feat(ontology): onboarding <date>`,
   `fix(ontology): correction via chat (<member>)`,
   `feat(ontology): confirmed proposal (<correspondent>)`.
2. **Trust order.** User-stated (onboarding, chat correction) beats
   extracted. An extraction proposal never overwrites a user-stated field;
   it may only ADD entities or fill empty fields, and only after explicit
   confirmation in chat.
3. **No silent deletes.** Bots never remove entities or aliases. Removal
   is a human edit or an explicit chat command, and stays a visible commit.
4. **Hand edits are first-class.** Direct edits in Forgejo/Obsidian are
   expected; loaders must tolerate unknown frontmatter keys, and bot
   edits touch single pages, never the whole folder (no clobber, the
   ontology.toml lesson). Free-form bodies are never rewritten by bots.

Everything else that makes the ontology "living" (dedup, merge
suggestions, alias harvesting, dream cycle) is deliberately NOT designed
yet: a static entity set in real use for a few weeks tells us
what actually drifts before we build maintenance machinery for it.

## Experiment round 6: does priming close the gap?

Corpus: Simpsons, ground truth exists (person + topic + doctype per doc).
Arms, all on Qwen3.5 9B (the weak model):
  A. classify without household block (status quo)
  B. classify with household block from entity pages we author for the
     Simpsons
Reference: Qwen3.6 35B without block (is priming worth more than 4x params?)
Metrics: person-assignment accuracy, topic accuracy, doctype accuracy,
cross-entity misattributions. Mechanical scoring like rounds 1-5.
Write-up: whitepaper round 6 + likely the strongest standalone post
("a family ontology made the 9B classify like the 35B", if it holds).

## Sequencing

1. Entity-page loader + tests (`lib/stack/living_ontology.py`: frontmatter
   walk over the kind folders; returns entities + prompt block) — ~3h
2. Classifier prompt gains the Household block — ~1h
3. Experiment round 6 (author Simpsons entity pages, run arms, score) — ~2h
4. `!onboard` Matrix wizard — ~half day
5. Wiki consumes declared roster (H1s, canonicalization) — ~2h
6. Extraction proposals — later

## Parked: companion ontologies per topic

Decision 2026-06-12: no authored per-topic ontologies (insurance terms,
medical specialties, ...) for famstack. Maintenance treadmill, prompt
token bloat on the small models we optimize for, serves completeness the
corpus doesn't need. Two variants stay alive:

- **Emergent topic depth** — the living loop accumulates confirmed
  correspondents, aliases, and doctype frequencies UNDER topics from the
  household's actual mail. A query over the entity pages + usage, zero
  authoring. Revisit after the extraction-proposal phase.
- **Authored vertical packs for deskstack** — law firm / tax advisor
  domain ontologies (court names, Mandant/Akte structures, deadline
  doctypes) are authored once, shared by every firm, and a paid
  differentiator. Product idea, parked under deskstack.

Round 6 error analysis can reopen this: if 9B failures cluster in one
domain, that's the data-driven case for depth in that topic.

Steps 1-3 are one session and produce data before we invest in the wizard.
If priming shows no effect, the wizard priority drops and the entity layer
still pays for itself via the wiki roster (step 5).
