# Knowledge Structure: Concepts, Layers, and Wiki Format

> Status: Design distillation
> Created: 2026-04-21
> Author: Homer + Claude
> Companion to: [knowledge-architecture.md](knowledge-architecture.md), [knowledge-implementation.md](knowledge-implementation.md), [ontology-design.md](ontology-design.md)

## Why this document

The architecture doc says *what we're building* (Family Brain). The implementation doc says *in what order* (7 layers). The ontology doc says *what vocabulary we use* (categories, persons, organizations).

This doc says *what shape the bytes take* — the concepts as a storage-agnostic data model, the five layers of derived state, the markdown page templates, and the explicit Obsidian + Karpathy compatibility contract. It pins down structure so the *backend* (plain markdown, SQLite, engram, vector store) becomes a swappable detail.

## Where we invest, where we borrow

**Capture is where we invest.** Every piece of structure below (buckets, entities, facts, citations) is only as good as the moment new content lands and gets classified. Get capture right — source-aware, multi-input, entity-creating — and the structure self-organizes. Get capture wrong and even the smartest retriever returns garbage.

**Retrieval is borrowed wisdom.** Obsidian Smart Connections, Khoj, LlamaIndex, txtai, dozens of others have solved markdown retrieval. We swap in proven patterns when the time comes; we don't reinvent.

The novel work — and the moat — is the **capture pipeline + Matrix-aware behavior + accumulating household memory**. Everything in this document orbits that.

## Compatibility targets

Three contracts every concept and file in this system honors:

1. **Karpathy's LLM Wiki pattern.** Three layers (raw sources, wiki, schema), three operations (ingest, query, lint), `index.md` as the LLM's map, `log.md` for chronology. We adopt the operations and the layered separation.

2. **Obsidian conventions.** YAML frontmatter, `[[wiki links]]`, `aliases:` field, plain markdown on disk, no proprietary format. Anything we write should open as an Obsidian vault with no plugins.

3. **Backend-agnostic concepts.** The conceptual layer (Topic, Fact, WikiPage, Document) is defined without storage assumptions. A markdown-in-git backend is the default. SQLite + FTS5 or engram or a vector store can swap in without touching the concept layer or the bots that use it.

The system's job is to keep these three contracts simultaneously satisfied. If a design choice breaks one, it doesn't ship.

---

## Concepts (storage-agnostic)

These are the only kinds of things that exist in the knowledge system. Every backend stores them, every consumer reads them, every bot produces them.

### Vocabulary — the static schema

The ontology defines what words we use. Lives in `<stack_root>/ontology.toml`. Loaded once, referenced everywhere.

```
Topic
  id          stable snake_case identifier
  names       per-language display names
  synonyms    natural-language phrasings for query expansion
  keywords    per-language terms expected inside matching documents
  types       document type ids commonly carrying this topic

Type
  id          stable snake_case identifier
  names       per-language display names
  (no synonyms, no signals — types are universal document shapes)

Person
  name        canonical first name (also their users.toml entry)
  aliases     nicknames not present in documents ("Dad", "Vati")
  language    optional per-user language override
  (relationships live in facts, not here — see "Layer 2")

KnowledgeKind
  id          one of: rule, fact, habit, goal, preference, context, event
  decay_days  retention window before archival (0 = never)

QueryPlan
  raw_query   the user's original question
  persons     resolved canonical names
  topics      resolved topic ids
  types       associated type ids (from topics' cross-refs)
  keywords    flattened keyword list across resolved topics
```

### Instances — the dynamic state

Each layer below produces and consumes instances of these types.

```
Document
  id              source identifier (Paperless doc id)
  source          where it came from (Paperless, manual upload, URL archive)
  language        detected/inferred document language
  title           human-readable title
  date            document date (when the doc was issued)
  topics          resolved topic ids
  type            resolved type id (single)
  persons         resolved canonical names
  correspondent   sender/issuer name (free-text or resolved entity id)
  story           story id this document belongs to (optional)
  facts           inline facts extracted at classification
  action_items    inline action items extracted at classification
  body            the document's text content (markdown if available, OCR otherwise)
  source_link     URL to the original (Paperless URL)

Fact
  id              stable identifier (hash of content + source + time)
  kind            KnowledgeKind id (rule, fact, habit, ...)
  text            one-sentence claim, suitable for LLM context
  persons         resolved canonical names (optional)
  topics          resolved topic ids (optional)
  story           story id this fact belongs to (optional)
  source          {kind, doc_id|event_id|room_id|...}    (null for hand-authored)
  actor           who caused this fact to be recorded
  extracted_at    timestamp
  expires_at      computed from kind.decay_days
  confidence      0.0–1.0 (1.0 for hand-authored)
  superseded_by   fact id of a newer version, if any

WikiEntity
  id              stable snake_case identifier
  kind            "correspondent" | "person" | "topic" | "asset" | "story"
  names           per-language canonical names
  aliases         alternative names that should resolve to this entity
  bucket          which bucket this entity belongs to (family, homer, marge, ...)

  # story-specific fields (only when kind == "story")
  noun            user-facing word ("trip", "renovation", "birthday", "side project")
  status          "planning" | "active" | "completed" | "archived"
  starts          start date (ISO)
  ends            end date (ISO, optional — open-ended stories have none yet)
  participants   canonical person names involved in the story

WikiPage
  entity          WikiEntity this page is about
  frontmatter     YAML metadata (types, dates, counts, aliases, related entities)
  sections        narrative body (overview, current state, timeline, related, sources)
  citations       list of (fact_id | doc_id) referenced in the body
  updated_at      last regeneration time

MasterIndex
  domains         list of (domain, entry_count, last_updated, SHA)
  entities        compact list of (entity_id, kind, one_line_summary, page_path, SHA)
  recent_facts    last N facts across all domains (with kind, topics, persons)
```

**Invariant:** every concept above is defined without saying *how* it's stored. A markdown backend writes a `WikiPage` as one `.md` file with YAML frontmatter. A SQLite backend writes it as rows across `wiki_pages` and `wiki_citations` tables. The contract holds either way.

---

## The five layers

State flows in one direction. Each layer is derived from the layer(s) above it. Each layer is independently human-readable.

```
  L0  Source documents
        |
        v
  L1  Document mirror             one .md per Document
        |   |
        |   +--> L2  Facts        claims extracted from L1 (+ Matrix in future)
        |   |
        v   v
  L3  Entity wiki                  one .md per WikiEntity (synthesized from L1 + L2)
        |
        v
  L4  Master index                 one .md, one-line summaries with pointers to L3
```

Each layer answers a different reader's question:

| Layer | Reader | Question it answers |
|---|---|---|
| L0 | Paperless UI, audit | "What's the original artifact?" |
| L1 | Obsidian browser, search engine | "What did this single document say?" |
| L2 | Retriever, scripts | "What claims have we accumulated about X?" |
| L3 | Family member, Kit Bot | "What do we know about Duff Insurance / Homer / insurance?" |
| L4 | LLM in its first read | "Where is everything?" |

### L0: Source documents

The original artifacts. Paperless stores them. Photos, PDFs, scans, attachments. We never edit L0. We do not encode L0 in our concept model — Paperless is its system of record.

### L1: Document mirror

One markdown file per Document, written by the Archivist. This layer exists today.

**Path convention:** `<knowledge>/documents/YYYY/MM/YYYY-MM-DD-<slug>.md`

**Page shape:**

```markdown
---
type: document
paperless_id: 247
title: Duff Insurance - Kfz-Versicherung 2026 EUR 340
date: 2026-03-15
language: de
doc_type: policy
topics: [insurance, vehicle]
persons: [Homer]
correspondent: Duff Insurance
aliases: []
tags: [Insurance, Vehicle, "Person: Homer", Policy]
facts:
  - "Policy KFZ-2024-XXXXX, Vollkasko + Haftpflicht, EUR 340/year"
  - "Annual renewal, direct debit January"
  - "Coverage period: 2026-01-01 to 2026-12-31"
action_items:
  - { action: "Compare prices before renewal", due: "2026-11-30" }
source_link: https://paperless.merles.eu/documents/247/details
processing: ai_formatted
model: qwen3-32b
---

# Duff Insurance - Kfz-Versicherung 2026 EUR 340

(clean markdown body — either LLM-reformatted OCR or raw OCR fallback)

...

## Related

- [[family/duff-insurance]]
- [[family/homer]]
- [[family/insurance]]
- [[family/vehicle]]
```

**Invariants:**
- One file per Paperless document. Filename is stable across reprocesses.
- `[[wiki links]]` to entity pages are added when those entity pages exist. Don't fabricate links to non-existent pages.
- Body can be LLM-reformatted markdown (preferred) or raw OCR (fallback).
- This file is a *mirror*, not a source. Edits made directly here get overwritten on next reprocess.

### L2: Facts

Append-only typed claims with provenance. Two files, same `Fact` shape:

```
<stack_root>/facts.toml         hand-authored seeds
<data_dir>/knowledge/facts.jsonl  machine-appended
```

**Hand-authored example** (`facts.toml`):

```toml
[[fact]]
kind = "rule"
text = "Maggie is the daughter of Homer and Marge."
persons = ["Homer", "Marge", "Maggie"]
topics = ["family"]

[[fact]]
kind = "rule"
text = "Bart has a peanut allergy."
persons = ["Bart"]
topics = ["medical"]
```

**Machine-appended example** (`facts.jsonl`):

```json
{"id":"f_2026-04-20T14:22Z_a7b3","kind":"fact","text":"Car insurance premium is EUR 340/year","persons":["Homer"],"topics":["insurance","vehicle"],"source":{"kind":"paperless","doc_id":247},"actor":"@homer:merles.eu","extracted_at":"2026-04-20T14:22:00Z","expires_at":"2026-07-19T00:00Z","confidence":0.85,"superseded_by":null}
```

**Invariants:**
- Hand-authored facts have no `source` and `confidence` defaults to 1.0.
- Machine facts always populate `source` and `extracted_at`.
- Facts never get deleted. Wrong/stale facts get a newer fact with `superseded_by` pointing to the old id.
- Ontology validates: unknown `topics` / `persons` / `kind` ids fail at write time.

### L3: Entity wiki

One markdown file per WikiEntity. Synthesized from L1 + L2 by a one-shot CLI initially, by the Deriver bot eventually.

**Path conventions:**

Flat buckets, one entity per file. Privacy and ownership live in *buckets* (top-level directories). Entity kind lives in frontmatter (`kind: correspondent | person | topic | asset`). No sub-folders inside a bucket.

```
<knowledge>/
├── family/                    # shared — everyone in the household
│   ├── index.md
│   ├── duff-insurance.md                # kind: correspondent
│   ├── springfield-mutual.md                 # kind: correspondent
│   ├── homer.md               # kind: person (family-visible profile)
│   ├── marge.md               # kind: person
│   ├── insurance.md           # kind: topic
│   ├── vehicle.md             # kind: topic
│   ├── recipes.md             # kind: topic (shared interest)
│   ├── tuscany-2027.md        # kind: story (family trip)
│   ├── bathroom-2026.md       # kind: story (renovation, completed)
│   ├── marge-40th.md          # kind: story (birthday, planning)
│   └── ...
├── homer/                    # homer's personal bucket
│   ├── index.md
│   ├── work.md                # kind: topic
│   ├── side-projects.md       # kind: topic
│   ├── simracing.md           # kind: topic (personal hobby)
│   ├── dr-frink.md            # kind: correspondent (personal physician)
│   ├── llm-benchmarks-2026.md # kind: story (personal side project)
│   └── ...
├── marge/                     # marge's personal bucket
│   ├── stitching.md           # kind: topic (personal hobby)
│   └── ...
└── (more buckets per family member as needed)
```

**Rules:**

1. **Two-bucket default**: `family/` for shared, `<person>/` per family member who wants a personal bucket.
2. **No sub-folders inside a bucket.** Frontmatter `kind:`, `topics:`, `tags:` do the classification work — not the filesystem tree.
3. **Default to `family/`.** Move to a personal bucket only when there's a clear reason (private hobby, personal correspondent, sensitive work).
4. **One person profile per bucket they appear in.** `family/homer.md` is Homer's family-visible profile. `homer/` (if it exists) is what Homer keeps to himself.
5. **Sub-folders are an optimization, not the design.** Earn them by file count past ~200 per bucket.

**Correspondent page template** (`family/duff-insurance.md`):

```markdown
---
type: entity
kind: correspondent
id: duff-insurance
name: Duff Insurance
aliases: ["Duff Insurance e.V.", "Allgemeiner Deutscher Automobil-Club"]
topics: [insurance, vehicle]
persons: [Homer]
documents: 14
first_seen: 2023-01-10
last_seen: 2026-04-15
updated_at: 2026-04-21T03:00:00Z
---

# Duff Insurance

German automobile club, primary car-insurance provider for the family since 2023.

## Current state

- [fact] Premium: EUR 340/year, Vollkasko + Haftpflicht [doc:#247]
- [fact] Policy: KFZ-2024-XXXXX, expires 2026-06-30 [doc:#247]
- [habit] Renews annually in January
- [rule] Roadside assistance member

## Timeline

- 2026-03-15 — Renewal notice [[documents/2026/03/2026-03-15-duff-insurance-renewal|#247]]
- 2025-01-10 — Annual renewal [[documents/2025/01/2025-01-10-duff-insurance-policy|#189]]
- 2024-03-12 — Coverage adjustment [[documents/2024/03/2024-03-12-duff-insurance-amendment|#102]]
- 2023-01-10 — Initial policy [[documents/2023/01/2023-01-10-duff-insurance-contract|#54]]

## Related

[[family/homer]] · [[family/insurance]] · [[family/vehicle]]

## Sources

f_2026-03-15_a7b3, f_2025-01-10_d4f2, doc:#247, doc:#189, doc:#102, doc:#54
```

**Person page template** (`family/homer.md`):

```markdown
---
type: entity
kind: person
id: homer
name: Homer
aliases: [Dad, Vati, Homie]
language: de
related_persons:
  spouse: Marge
  children: [Bart, Lisa, Maggie]
topics: [insurance, vehicle, employment, taxes]
correspondents: [duff-insurance, springfield-mutual, springfield-tax-office_springfield]
documents: 47
updated_at: 2026-04-21T03:00:00Z
---

# Homer

Family member. Married to [[family/marge]]. Father of [[family/bart]], [[family/lisa]], and [[family/maggie]].

## Current state

- [rule] Married to Marge (since 1989-06-30) [doc:#3]
- [fact] Employed at Springfield Nuclear Power Plant [doc:#84]
- [fact] Tax ID 22-XXXXX-XX [doc:#199]

## Areas of activity

- [[family/insurance]] — car (Duff Insurance), health (Springfield Mutual), liability
- [[family/vehicle]] — Canyonero, SPR-1234
- [[family/employment]] — Springfield Nuclear
- [[family/taxes]] — annual filing, Springfield Tax Office

## Related

[[family/duff-insurance]] · [[family/springfield-mutual]] · [[family/springfield-tax-office_springfield]] · ...

## Sources

(fact_ids and doc_ids)
```

**Topic page template** (`family/insurance.md`):

```markdown
---
type: entity
kind: topic
id: insurance
name.de: Versicherung
name.en: Insurance
correspondents: [duff-insurance, springfield-mutual, globex]
persons: [Homer, Marge, Maggie]
documents: 31
updated_at: 2026-04-21T03:00:00Z
---

# Insurance

The family carries coverage in four categories: car, health, household liability, and life.

## Overview

| Category | Provider | Insured | Policy | Premium |
|---|---|---|---|---|
| Car | [[family/duff-insurance]] | Homer | KFZ-2024-XXXXX | EUR 340/yr |
| Health | [[family/springfield-mutual]] | family plan | XX-XX | EUR 892/mo |
| Liability | [[family/globex]] | household | HFT-XXX | EUR 87/yr |
| Life | [[family/globex]] | Homer | LBV-XXX | EUR 412/yr |

## Recent activity

- 2026-03-15 — Duff Insurance renewal notice [doc:#247]
- 2026-02-10 — Springfield Mutual monthly statement [doc:#241]
- ...

## Action items

- Compare Duff Insurance prices before renewal (due 2026-11-30)

## Related

[[family/vehicle]] · [[family/medical]] · [[family/duff-insurance]] · ...
```

**Story page template** (`family/tuscany-2027.md`):

```markdown
---
type: entity
kind: story
id: tuscany-2027
name: Tuscany 2027
noun: trip                   # what Kit Bot says aloud ("trip" / "renovation" / "birthday")
aliases: ["Italy 2027", "the Italy trip"]
status: planning             # planning | active | completed | archived
starts: 2027-07-15
ends: 2027-07-29
participants: [Homer, Marge, Bart, Lisa, Maggie]
topics: [travel, italy]
related_correspondents: [springfield-air, springfield-lodge, hertz]
budget: { amount: 8000, currency: "EUR" }
documents: 6
updated_at: 2026-04-21T03:00:00Z
---

# Tuscany 2027

Family trip to central Italy, summer 2027. Two weeks. Driving tour with stops in Florence, Lucca, and Cinque Terre.

## Status

Planning. Flights booked, accommodations confirmed for week 1, week 2 still open.

## Confirmed

- [fact] Springfield Air MUC ↔ FLR, EUR 1,840 family of 5 [doc:#314]
- [fact] Springfield Lodge, Lucca, 2027-07-15 to 2027-07-22 [doc:#316]

## Wish list

- Marge wants to see the David in Florence
- Bart: gelato at Vivoli in Lucca
- At least 2 days in Cinque Terre

## Action items

- [ ] Renew Bart's passport (expires 2027-05-01) — due 2027-04-01
- [ ] Vet boarding for the dog, 14 nights
- [ ] Get EUR cash, not just card

## Timeline

- 2026-04-21 — Booked Agriturismo [[documents/2026/04/2026-04-21-agriturismo-confirmation|#316]]
- 2026-04-18 — Booked Springfield Air flights [[documents/2026/04/2026-04-18-springfield-air-booking|#314]]
- 2026-03-10 — Marge mentioned wanting to see the David [chat memo]

## Related

[[family/travel]] · [[family/springfield-air]] · [[family/springfield-lodge]]
```

**Invariants:**
- Page filename = entity id with `.md` extension.
- Frontmatter is the authoritative metadata. Body is human-readable narrative.
- Every claim in the body cites a fact id or doc id.
- `[[wiki links]]` cross-reference other entity pages and source documents in L1.
- Body sections are not mandatory but conventions: `Overview / Current state / Timeline / Related / Sources`. Stories add: `Status / Confirmed / Wish list / Action items`.
- Pages are *regenerable*. Edits made directly to the body get overwritten on the next synthesis pass — frontmatter overrides persist via a separate mechanism (see "Layer 3 edits" below).

### Story-specific mechanics

Stories are the only entity kind with a lifecycle, a temporal window, and explicit membership from documents and facts. Worth pinning down separately.

**Lifecycle.** Every story has one of four statuses:

| Status | Meaning | Set by |
|---|---|---|
| `planning` | Before `starts:` date. Captures flow in, action items accumulate. | Default at creation. |
| `active` | Between `starts:` and `ends:`. Captures during this window auto-link if no other story claims them. | Auto-flip by dream cycle. |
| `completed` | After `ends:`. Final synthesis: receipts totaled, lessons captured. Still in active retrieval set for ~90 days. | Auto-flip by dream cycle. |
| `archived` | Past 90 days completed, or manually archived. Facts persist; the story page drops out of master index but stays browsable. | Auto by dream cycle, or manual. |

**Membership.** A document or fact joins a story by carrying `story: <id>` in its frontmatter (L1) or its record (L2). The story page's Timeline is regenerated from all members on every wiki-rebuild.

**The `noun:` field.** Every story page has a `noun:` (`"trip"`, `"renovation"`, `"birthday"`, `"side project"`). User-facing surfaces (Kit Bot replies, briefings) interpolate this into templates:

| Template | With `noun: "trip"` | With `noun: "renovation"` |
|---|---|---|
| `Your {name} {noun} is in {duration}.` | "Your Tuscany 2027 trip is in 14 weeks." | "Your bathroom renovation is in 3 days." |
| `The {noun} wrapped up {duration} ago.` | "The trip wrapped up 2 weeks ago." | "The renovation wrapped up 3 months ago." |
| `What's the latest on the {name} {noun}?` | "What's the latest on the Tuscany trip?" | "What's the latest on the bathroom renovation?" |

The `kind: story` is a code-level umbrella; the `noun:` is what humans hear.

**How the classifier picks a story for a new capture.** In order of strength:

1. **Explicit mention.** The capture text says the story's name, an alias, or carries a booking number that matches a story's records. Hardest signal — link.
2. **Active story in window.** The current date is between `starts:` and `ends:` of one or more active stories whose participants overlap with the capture's `persons`. Strong soft-link candidate.
3. **Topic + participant + planning-phase match.** A capture with topic=travel + persons=[Homer, Marge, Bart, Lisa, Maggie] in 2027 matches a planning-phase Italy story for those same participants in that window. Weak — link only if no contradiction.

When the classifier isn't confident enough, it doesn't link. Better to miss a link (recoverable by hand) than to mis-attribute content.

### L4: Master index

One markdown file. The LLM's map. Always loaded into Kit Bot's context.

**Path:** `<knowledge>/index.md`

```markdown
---
type: index
updated_at: 2026-04-21T03:00:00Z
domains: [shared, homer, marge]
totals: { documents: 312, facts: 487, entities: 47 }
---

# Family Knowledge Index

## Domains

- shared (38 entities) — household, contacts, recurring patterns
- homer (12 entities) — personal docs, work, hobbies
- marge (9 entities) — personal docs

## Entities

### Correspondents (16)
- [[family/duff-insurance]] — car insurance · Homer · 14 docs · last 2026-04-15
- [[family/springfield-mutual]] — health insurance · family · 27 docs · last 2026-04-10
- [[family/springfield-tax-office_springfield]] — tax office · 8 docs · last 2026-03-22
- ...

### Persons (5)
- [[family/homer]] — 47 docs · employment, vehicle, taxes
- [[family/marge]] — 31 docs · health, household
- [[family/bart]] — 18 docs · school, medical
- ...

### Topics (24)
- [[family/insurance]] — 31 docs · car, health, liability, life
- [[family/vehicle]] — 19 docs · Canyonero, registration, service
- [[family/taxes]] — 14 docs · annual returns, assessments
- ...

## Recent facts (last 30)
- 2026-04-20 [fact] Duff Insurance premium EUR 340/yr [#247]
- 2026-04-15 [event] Springfield Mutual annual statement received [#241]
- ...
```

**Invariants:**
- ~200–500 tokens. Fits inside any LLM's system prompt comfortably.
- Auto-generated by the dream cycle (or for v1, regenerated whenever wiki-rebuild runs).
- One-line-per-entity. Names + counts + last-touched. Acts as the L3 directory.
- The LLM uses this to *decide which entity pages to read*. It never reasons from this alone.

---

## Capture: source-aware classification

The Archivist is the *capture gateway* for everything dropped into Matrix — not just Paperless-bound documents. Each input type has its own capture step, but the downstream pipeline (classify → mirror → extract facts → touch entities) is identical.

### Multi-input capture

| Input | How captured | Storage |
|---|---|---|
| Image / PDF of a real document | Paperless OCR | L1 doc mirror (Paperless-backed) |
| Web link (substantial article) | Fetch + readability extract → markdown | L1 doc mirror |
| Web link (GitHub repo, paper, reference) | Fetch metadata + README/abstract → markdown | L1 doc mirror |
| Voice memo | Whisper transcribe → markdown | L1 note mirror (audio stays in Matrix) |
| Short text note | Direct → markdown | L1 note mirror (skips Paperless) |
| Photo (memory, not document) | Immich, with metadata referenced | Stays in Immich; entity pages link |

### Source attribution rules

Matrix gives us authoritative signals about *who* dropped *what* *where*. We treat them as defaults the classifier can override:

| Signal | Default inference |
|---|---|
| **DM with one user** | Bucket = that user's personal bucket. Owner = that user. |
| **Shared family room** (`#documents`, `#assistant`) | Bucket = `family`. Sender is a strong candidate for `persons`. |
| **Personal room** (`#homer-private`, configured) | Bucket = the room's declared bucket. |
| **Sender of the message** | Strong candidate for `persons` field, regardless of room. |

These are *defaults*, not hard rules. The classifier overrides when content clearly disagrees:

- Homer drops a school letter for Bart in `#documents` → defaults: bucket=family, persons=[Homer]. Classifier reads OCR ("Springfield Elementary," "Bart Simpson") → overrides to persons=[Bart], bucket stays family.
- Marge sends Homer an electronics receipt photo in DM → defaults: bucket=homer (Homer's DM), persons=[Marge]. Classifier sees household appliance → overrides bucket=family, keeps persons=[Marge].

Override discipline:
1. Defaults stand unless content provides a clear, named contradiction.
2. Ambiguity → keep the default. "Might be Marge's" is not enough to override.
3. Override decisions are recorded in the L1 mirror's frontmatter (`bucket_default: homer`, `bucket_resolved: family`, `bucket_override_reason: "household appliance"`).

### Room configuration

Each Matrix room declares its capture defaults via room state:

```json
{
  "type": "dev.famstack.capture",
  "content": {
    "bucket": "family",
    "default_persons": [],
    "extract_knowledge": true
  }
}
```

DMs have implicit defaults (bucket = the user the DM is with, owner = that user). Shared rooms have explicit ones in state. Element ignores unknown event types — defaults are invisible to family members but authoritative to the bot.

### Eager entity creation

The first time a piece of content references a topic, correspondent, or person that doesn't yet have an entity page, the classifier *creates a stub*. It doesn't skip the reference.

```markdown
---
type: entity
kind: topic
id: stitching
name: Stitching
aliases: []
documents: 1
first_seen: 2026-04-21
last_seen: 2026-04-21
created_by: archivist
---

# Stitching

(no overview yet — the first document about this topic just arrived)

## Timeline

- 2026-04-21 — Pattern reference [[documents/2026/04/2026-04-21-stitching-pattern|#312]]
```

Future docs append to the Timeline. After ~3 docs in the topic, the wiki-rebuild CLI re-synthesizes the body with a real overview. The wiki grows organically from one stub into a real reference, without anyone ever having to "set up" a topic.

This is the missing link between capture and structure: every classification can *grow* the wiki, not just *file into* it.

## Karpathy parallels

| Karpathy concept | Our equivalent |
|---|---|
| **Layer 1: Raw sources** | L0 (Paperless) + L1 (document mirror) |
| **Layer 2: Wiki** | L2 (facts) + L3 (entity wiki) + L4 (master index) |
| **Layer 3: Schema config** | `<stack_root>/ontology.toml` |
| `index.md` (LLM's first read) | L4 master index |
| `log.md` (chronological log) | `facts.jsonl` (append-only event-flavored log) |
| **Operation: ingest** | Archivist (today) + Deriver bot (future) |
| **Operation: query** | Kit Bot / Archivist Q&A: read L4 → read relevant L3 pages → cite L2 facts + L1 docs |
| **Operation: lint** | Dream cycle (nightly): detect contradictions, orphan entities, stale facts |

The deeper parallel: Karpathy's "bookkeeping is the bottleneck, LLMs solve bookkeeping." Our L1→L3 synthesis and L4 regeneration are exactly the bookkeeping moves an LLM is good at. Humans hand-author L2 seeds; bots do the maintenance.

---

## Obsidian compatibility contract

What we do, by convention, in every markdown file we write:

| Convention | Where |
|---|---|
| YAML frontmatter | Top of every L1, L3, L4 file |
| `[[wiki links]]` | Body of L1 (to entities), L3 (between entities, to L1 docs), L4 (to entities) |
| `aliases:` in frontmatter | L3 entity pages |
| `tags:` in frontmatter | L1 document mirrors (mirrors Paperless tags) |
| Plain UTF-8 markdown on disk | Everywhere |
| Linkable headings (`# H1`, `## H2`) | Everywhere |

What we do **not** rely on:

- Block references (`^block-id`) — too granular, brittle across regeneration
- Inline `#hashtags` — frontmatter `tags:` is the canonical tag location
- Obsidian-only plugins (Dataview queries inline, etc.) — they may *work* on our vault, but we don't depend on them

Net: a family can open the entire knowledge repo in Obsidian, get backlinks and graph view for free, and edit pages if they want — without us promising any plugin-specific behavior.

---

## Backend-agnostic interface

Concepts are the contract. Backends implement it.

```python
class KnowledgeBackend(Protocol):
    # L1 — Document mirror
    def write_document(self, doc: Document) -> None: ...
    def get_document(self, doc_id: str) -> Document | None: ...
    def query_documents(
        self, *, persons=(), topics=(), types=(), since=None, limit=50
    ) -> list[Document]: ...

    # L2 — Facts
    def append_fact(self, fact: Fact) -> None: ...
    def query_facts(
        self, *, persons=(), topics=(), kinds=(), include_expired=False, limit=50
    ) -> list[Fact]: ...

    # L3 — Entity wiki
    def write_wiki_page(self, page: WikiPage) -> None: ...
    def get_wiki_page(self, entity_id: str) -> WikiPage | None: ...
    def list_wiki_entities(
        self, *, kind: str | None = None
    ) -> list[WikiEntity]: ...

    # L4 — Master index
    def write_master_index(self, index: MasterIndex) -> None: ...
    def get_master_index(self) -> MasterIndex: ...
```

Implementations:

| Backend | Storage | When to use | Status |
|---|---|---|---|
| `MarkdownGitBackend` | Files in git repo on Forgejo, plain markdown + jsonl | Default. Obsidian-openable. Hand-editable. | Build now |
| `SQLiteBackend` | SQLite database + FTS5 | When markdown grep is too slow (~10k+ facts, ~1k+ wiki pages) | Add when needed |
| `EngramBackend` | Engram HTTP API | If someone wants to plug into engram for their own reasons | Possible, not v1 |
| `VectorBackend` | LanceDB + nomic-embed via Ollama | When semantic search wins clearly over keyword for our doc volume | Paid tier, future |
| `HybridBackend` | Markdown for L1/L3, SQLite for L2 query, vector index optional | When we hit scale and want best-of-each | Future |

**Key property: the markdown is canonical.** Even with a SQLite backend, the .md files exist on disk. SQLite is an index *over* the markdown, not a replacement. This preserves Obsidian compatibility and human-readability regardless of which backend is active.

---

## Layer 3 edits (the contradiction we have to handle)

The wiki pages are *regenerated*. But humans might want to edit them too — fix a wrong synthesis, add color, tweak phrasing. Two strategies:

**Strategy A — pure regeneration.** Pages are read-only output. Edits get overwritten. Communicate this clearly with a comment header: `<!-- Auto-generated. Edit ontology.toml or facts.toml instead. -->`. Simple, predictable.

**Strategy B — frontmatter-as-source-of-truth.** The frontmatter persists human overrides (e.g. `manual_notes: "..."`); the body is regenerated. Bot reads existing frontmatter on regeneration, preserves designated user-fields, rewrites the body around them.

Recommend Strategy A for v1. If real pain emerges (Homer wants to add a personal note to Duff Insurance's page), add a single `notes:` frontmatter field that survives regeneration. YAGNI everything else.

---

## Family memory patterns

The fact layer + entity wiki together provide *household memory*. Worth naming the patterns we're building toward so the architecture supports them from day one.

### Habits and recurring patterns

A `[habit]` fact captures something that repeats. The dream cycle eventually detects these from `[event]` patterns; until then, hand-authored or extracted from explicit statements.

```toml
[[fact]]
kind = "habit"
text = "Family orders pizza on Friday evenings."
topics = ["food", "household"]
persons = ["Homer", "Marge", "Bart", "Lisa"]
```

Pattern detection (Layer 5 in implementation plan): three or more `[event]` facts with similar shape get proposed for promotion to `[habit]`. The dream cycle auto-promotes past a confidence threshold; humans can confirm/reject via a CLI.

### Gift ideas and wish accumulation

Conversational memory matters here. *"I'd love a Bernina for my birthday"* said in May should be remembered in October.

```toml
[[fact]]
kind = "preference"
text = "Marge wants a Bernina sewing machine for her birthday."
topics = ["gifts", "stitching"]
persons = ["Marge"]
# expires_at = next Marge birthday (computed from calendar)
```

Capture surface: conversation extraction in opt-in rooms (Deriver bot). Recognition patterns to teach the LLM extractor: *"I want / love / wish for / would be amazing / remind me to buy."*

Proactive surfacing belongs to Kit Bot — morning briefings or N days before relevant dates:

> Marge's birthday is in 3 weeks.
> Recent gift hints from chat: Bernina sewing machine, the green pottery set she pointed to at the market.

### Accumulating interests

Every new capture about a topic appends to that entity page's Timeline. After months, `homer/llm-benchmarks.md` has 30 timeline entries showing how Homer's reading evolved. No special logic required — the entity wiki *is* the accumulation.

Proactive opportunity: detect engagement velocity per topic. A topic with 8 new docs this month is "active" for that person; one with 0 in 6 months is "dormant." Master index surfaces active interests; the family knows what each person is into right now without asking.

### Repeating events and renewals

Insurance renewals, school terms, annual contracts, subscription cycles. Each occurrence is an `[event]` fact with a due date. After 3 occurrences with stable cadence, the dream cycle promotes to a `[habit]` with a "next expected" date. Kit Bot warns proactively:

> Duff Insurance car insurance renews annually in January. Next renewal expected 2027-01-10.
> Your house contents insurance is due for renewal in 6 weeks.

This is the killer feature of household memory: it remembers cycles so nobody has to.

### Family preferences that accumulate

*"Both of you like to cook"* is a hand-seeded preference fact. Every subsequent recipe capture or restaurant invoice reinforces it. The `family/recipes.md` and `family/cooking.md` entities grow organically. Over time the system learns the household's taste — favored cuisines, restaurants, ingredients to avoid — without anyone explicitly maintaining a profile.

The compound effect: after 12 months, asking *"what should we make this weekend?"* gets an answer grounded in the household's actual history, not a generic suggestion.

---

## What this means for the build

This document doesn't change the Ship plan. It *commits* to the structural shape Ship 2a will produce.

| Ship | Produces |
|---|---|
| 1 | Ontology (`ontology.toml`), facts.toml seed, FactStore, ontology-aware classifier prompt |
| 2a | One-shot wiki-rebuild CLI that synthesizes L3 entity pages from L1 + L2 |
| 2b | Q&A retriever using L4 → L3 → L1+L2 read-down chain, citing sources in Element |
| 3 | Archivist incrementally updates L3 entity pages on doc file |
| later | Deriver bot for Matrix conversation extraction; dream cycle for L4 + cleanup |

The five layers, the entity page templates, and the backend interface are the *invariant* — they hold even as the implementation evolves. A future SQLite backend or vector backend slots in by implementing `KnowledgeBackend`. A future Deriver bot writes the same L2/L3 shapes the one-shot CLI writes. The concepts are stable; the backends and bots are not.

---

## Open structural questions

1. ~~**Domain partitioning of L3.**~~ **Resolved** — flat buckets at the top (`family/`, `<person>/`), no sub-folders inside, frontmatter does the rest. One repo for v1; split into per-bucket Forgejo repos only when access control actually matters.

2. **Document mirror vs. entity wiki link direction.** L1 documents link to L3 entities. Should L3 entities also link back to every L1 doc, or only summarize via the Timeline section? My take: Timeline summarizes; full L1 list is in frontmatter `documents:` count, not enumerated in the body. Keeps L3 pages from bloating.

3. **Asset entities.** A car or a house feels like an entity, but it's often easier to model as a topic + correspondent pair. v1: no asset entity. Revisit when there's clear pain.

4. **Language of synthesized wiki pages.** A German family's `family/duff-insurance.md` body — German or English? Probably the user's primary language from `stack.toml`. Frontmatter fields stay English (id, kind, etc); display names follow language.

5. **Master index format.** Markdown (this doc proposes) or YAML/JSON for machine read + a separate markdown render? Markdown is dual-purpose; bots parse the table sections, humans browse. Start there; add structured sidecars only if parsing pain hits.

---

## Summary

| Concept | Lives in | Authored by | Read by |
|---|---|---|---|
| Topic, Type, Person (schema) | `<stack_root>/ontology.toml` | hand | classifier, retriever, all bots |
| Person aliases | `<stack_root>/users.toml` | hand | retriever, all bots |
| Hand-authored facts | `<stack_root>/facts.toml` | hand | retriever, all bots |
| Machine facts | `<data_dir>/knowledge/facts.jsonl` | archivist, deriver | retriever, all bots |
| L1 document mirror | `<knowledge>/documents/YYYY/MM/...md` | archivist | retriever, Obsidian, Kit |
| L3 entity wiki | `<knowledge>/<bucket>/<id>.md` (flat, frontmatter `kind:` ∈ {correspondent, person, topic, asset, story}) | wiki-rebuild CLI, archivist (eager stub + incremental updates) | retriever, Obsidian, Kit, humans |
| L4 master index | `<knowledge>/index.md` | wiki-rebuild CLI, dream cycle | Kit's system prompt, humans |

Concepts, layers, formats — pinned. Backends, bots, and CLIs — swappable. Obsidian and Karpathy both happy. Engram or SQLite or LanceDB plug in later without rewriting any of the above.
