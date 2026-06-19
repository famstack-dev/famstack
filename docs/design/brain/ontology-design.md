# Smart Ontological Tag Management System

> Status: Design document
> Created: 2026-04-14
> Author: Homer + Claude
> Used by: All famstack stacklets, Paperless-ngx, knowledge wiki, Kit Bot

## The Problem

Every service in the stack has its own tagging:
- Paperless: tags, correspondents, document types (flat, string-based, manual + LLM)
- Immich: albums, faces, locations (auto-detected + manual)
- Matrix: rooms, topics (static)
- Calendar: calendars, event categories
- Knowledge wiki: ontology types, domain tags, wiki links

These don't talk to each other. "Duff Insurance" is a Paperless correspondent, a wiki entry, and maybe a photo album from the roadside assistance visit. The person "Marge" is a Paperless person tag, an Immich face, a Matrix user, and a wiki domain. But nothing connects them.

## The Design

A **shared ontology** that all stacklets reference. Not a database, not Markdown prose -- a single YAML file (`ontology.yaml`) in the `knowledge/meta` repo. YAML because the ontology is structured, relational data (a graph of entities, categories, aliases, and relationships). Markdown is for knowledge content (prose). Using Markdown for graph structure means parsing prose to do dict lookups -- the wrong tool for the job.

At famstack's scale (~20 categories, ~50 entities, ~8 knowledge types), the entire ontology fits in one file, loads in microseconds, and is queryable as a Python dict in memory. No graph database needed.

**The split:** YAML for the graph structure (ontology.yaml), Markdown for the knowledge content (wiki files with YAML frontmatter referencing ontology keys).

### The Ontology File

One YAML file. The entire graph.

```yaml
# knowledge/meta/ontology.yaml
# The shared vocabulary for all famstack services.
# Loaded into memory at runtime. ~100 lines. Queryable as Python dict.

categories:
  insurance:
    aliases: [Versicherung, policy, coverage, Vollkasko, Haftpflicht]
    related: [finance, vehicle, health]
    paperless_tag: Insurance
    paperless_color: "#2196f3"

  finance:
    aliases: [Finanzen, banking, tax, Steuer, Konto]
    related: [insurance]
    paperless_tag: Finance
    paperless_color: "#4caf50"

  medical:
    aliases: [Gesundheit, health, doctor, Arzt, Krankenhaus]
    related: [insurance]
    paperless_tag: Medical
    paperless_color: "#f44336"

  school:
    aliases: [Schule, education, Unterricht]
    persons: [marge]
    paperless_tag: School
    paperless_color: "#ff9800"

  vehicle:
    aliases: [Auto, car, KFZ, Werkstatt]
    related: [insurance]
    paperless_tag: Vehicle
    paperless_color: "#607d8b"

  home:
    aliases: [Haus, Wohnung, household, maintenance, Reparatur]
    paperless_tag: Home
    paperless_color: "#795548"

  legal:
    aliases: [Recht, Vertrag, contract, Notar]
    related: [finance]
    paperless_tag: Legal
    paperless_color: "#9c27b0"

  travel:
    aliases: [Reise, vacation, Urlaub, Flug, Hotel]
    paperless_tag: Travel
    paperless_color: "#00bcd4"

persons:
  homer:
    aliases: [Homer, Papa]
    services:
      matrix: "@homer:merles.eu"
      paperless: "Person: Homer"
      immich: face-abc123
      forgejo: homer
      calendar: homer@merles.eu

  marge:
    aliases: [Marge, Mama]
    services:
      matrix: "@marge:merles.eu"
      paperless: "Person: Marge"
      immich: face-def456
      calendar: marge@merles.eu

organizations:
  duff-insurance:
    name: Duff Insurance
    aliases: ["Duff Insurance e.V.", "Duff Insurance Autoversicherung"]
    categories: [insurance, vehicle]
    persons: [homer]
    paperless_correspondent: Duff Insurance

  tk:
    name: Techniker Krankenkasse
    aliases: [TK, "Techniker Krankenkasse"]
    categories: [insurance, medical]
    persons: [homer, marge]
    paperless_correspondent: TK

  springfield-tax-office:
    name: Springfield Tax Office
    aliases: [Springfield Tax Office, "Springfield Tax Office", FA]
    categories: [finance]
    paperless_correspondent: Springfield Tax Office

knowledge_types:
  rule:
    decay: null
    description: Permanent, safety-critical facts
    examples: ["Marge is allergic to peanuts", "Emergency number: 112"]
    paperless_action: "Action: Critical"
  habit:
    decay: 365
    description: Recurring pattern, auto-promoted from repeated events
    examples: ["Duff Insurance invoice arrives in March", "Pizza night every Friday"]
    promotion_threshold: 3  # events before promotion to habit
  goal:
    decay: 365
    description: Aspiration with time horizon
    examples: ["Save for Italy trip summer 2027"]
  preference:
    decay: 180
    description: Personal choice or taste
    examples: ["Homer prefers dark roast coffee"]
  fact:
    decay: 90
    description: Verifiable information with limited shelf life
    examples: ["Car insurance premium: EUR 340/year"]
  context:
    decay: 30
    description: Temporary situation awareness
    examples: ["Bathroom renovation ongoing"]
  event:
    decay: 14
    description: Something that happened at a specific time
    examples: ["Marge had dentist appointment Apr 17"]
  reference:
    decay: null
    description: Pointer to external resource
    examples: ["Duff Insurance phone: 089-XXX", "Insurance policy in Paperless #247"]
```

### Projectional Views

The YAML is the source of truth. Derived views are generated from it:

**graph.md** -- Obsidian-compatible view of the ontology as wiki links. Generated by the dream cycle or on `fk ontology render`. Lets Obsidian's graph view render the full relationship structure.

```markdown
# Ontology Graph
<!-- Auto-generated from ontology.yaml. Do not edit. -->

## Categories
- [[Insurance]] -- related: [[Finance]], [[Vehicle]], [[Health]]
- [[Finance]] -- related: [[Insurance]]
- [[Medical]] -- related: [[Insurance]]
- ...

## Organizations
- [[Duff Insurance]] -- categories: [[Insurance]], [[Vehicle]] -- persons: [[Homer]]
- [[TK]] -- categories: [[Insurance]], [[Medical]] -- persons: [[Homer]], [[Marge]]
- ...

## Persons
- [[Homer]] -- organizations: [[Duff Insurance]], [[TK]], [[Springfield Tax Office]]
- [[Marge]] -- organizations: [[TK]]
```

**Paperless tag report** -- generated view showing how ontology maps to current Paperless state:

```
fk ontology paperless-sync
  Insurance     → Paperless tag "Insurance" (exists, 23 documents)
  Finance       → Paperless tag "Finance" (exists, 15 documents)
  Vehicle       → Paperless tag "Vehicle" (MISSING — will be created on next classify)
  Duff Insurance          → Paperless correspondent "Duff Insurance" (exists, 8 documents)
```

The projectional views are read-only artifacts. Change the YAML, regenerate the views.

### The Tag Resolution Chain

When the Archivist classifies a document, it doesn't just pick a tag string. It resolves through the ontology:

```
OCR text mentions "Duff Insurance" and "Rechnung"
  → Ontology lookup: Duff Insurance is a known correspondent in Insurance category
  → Category: Insurance (resolved, not guessed)
  → Person: Homer (Duff Insurance is associated with Homer's car)
  → Knowledge type: fact (it's an invoice with amounts and dates)
  → Related: [[insurance.md#Duff Insurance]], [[vehicle.md]]
  → Paperless tags: Insurance, Person: Homer, Invoice, Duff Insurance
  → Wiki entry: update shared/household/insurance.md#Duff Insurance
```

The LLM gets the ontology as context in its classification prompt. Instead of guessing tags from scratch every time, it picks from a known vocabulary with relationships already defined.

### How This Improves Classification

**Current prompt (Layer 0):**
```
Existing categories: ["Insurance", "Finance", "Versicherung", "insurance"]
```
Problem: duplicates, no hierarchy, no relationships. LLM picks randomly among variants.

**With ontology (Layer 1+):**

The ontology is serialized into the classification prompt (~1-2K tokens):

```
Categories (use canonical key, not aliases):
  insurance: [Versicherung, policy, coverage, Vollkasko]
    related: finance, vehicle, health
  finance: [Finanzen, banking, tax, Steuer]
  medical: [Gesundheit, health, doctor, Arzt]
  ...

Persons:
  homer: [Homer, Papa]
  marge: [Marge, Mama]

Organizations (known correspondents):
  duff-insurance: [Duff Insurance, "Duff Insurance e.V."] → categories: insurance, vehicle → persons: homer
  tk: [TK, "Techniker Krankenkasse"] → categories: insurance, medical → persons: homer, marge
  springfield-tax-office: [Springfield Tax Office, FA] → categories: finance
```

The LLM now has relationships. It doesn't just see tag strings -- it sees a graph. "This is from Duff Insurance" immediately implies insurance + vehicle + homer without the LLM having to figure that out from OCR text alone.

### Cross-Service Entity Resolution

The ontology connects entities across services. When the Deriver processes events:

```
Document event: correspondent=Duff Insurance, person=Homer
Photo event: faces=[Homer], location=Highway A8
Calendar event: "Duff Insurance Pannenservice", date=2026-03-15

→ Ontology resolves: all three relate to entity "Duff Insurance" + person "Homer"
→ Wiki: shared/household/vehicle.md gets updated with the roadside assistance event
→ Cross-references: [[insurance.md#Duff Insurance]] + [[calendar/2026-03.md]]
```

Without the ontology, these three events are unrelated -- different services, different data formats, different tag systems. With the ontology, they're one story.

### Person Association Model

Paperless's person tagging is good. Extend it across the stack:

**Every piece of knowledge can be associated with one or more persons.**

- Documents: `Person: Homer` tag in Paperless + `person` field in classification
- Photos: face detection in Immich maps to person entities
- Calendar events: attendees map to persons
- Wiki entries: `persons` field in frontmatter
- Action items: `assigned` field

The person entity in the ontology is the join key:

```markdown
## Homer
- matrix: @homer:merles.eu
- paperless: "Person: Homer"
- immich: face-id-abc123
- calendar: homer@merles.eu
```

When Kit Bot serves Homer, it can query "everything associated with Homer" across all services by resolving through the ontology. When Marge asks Kit something, the person filter scopes results to what's relevant to her.

### Ontology Lifecycle

**Bootstrap (manual):** Homer seeds the ontology files by hand. Categories based on Paperless tags that already exist. Persons from the user list. Correspondents from Paperless.

**Growth (Archivist + Deriver):** When the Archivist encounters a truly new correspondent or category that doesn't match anything in the ontology, it:
1. Creates the Paperless tag (as today)
2. Emits a `tag.created` event
3. The Deriver adds the new entry to the ontology files
4. Commits to `knowledge/meta`

**Maintenance (dream cycle):** Nightly review:
- Detect near-duplicate entries (fuzzy matching on names + aliases)
- Suggest merges ("Springfield Tax Office" and "Springfield Tax Office" should be one entry)
- Flag orphans (entities referenced nowhere)
- Update alias lists from observed usage
- Count usage per tag to identify the most/least used

**Cleanup CLI:**
```
fk ontology list                    show all entities/categories
fk ontology list persons            show person registry
fk ontology list categories         show category tree
fk ontology check                   find duplicates, orphans, inconsistencies
fk ontology suggest-merge           propose merges for near-duplicates
fk ontology stats                   usage counts per tag across services
```

### File Structure

```
knowledge/meta/
  ontology.yaml         THE source of truth. All graph data in one file.
  graph.md              Projectional view for Obsidian (auto-generated)
  master-index.md       Pointer brain (summaries + SHA refs for bot context)
```

Tag mappings (ontology key to Paperless tag name + color) live inside `ontology.yaml` on each category/person entry. One file, no separate mapping table.

`graph.md` is a projectional view -- auto-generated from the YAML by the dream cycle or `fk ontology render`. It renders the ontology as `[[wiki links]]` so Obsidian's graph view can visualize the relationships. Read-only artifact. Edit the YAML, regenerate the view.

### How the Archivist Uses the Ontology

Updated classification flow:

```
1. Load ontology.yaml (cached in memory, refreshed on git change)
   → Python dict with categories, persons, organizations, knowledge_types

2. Build classification prompt with ontology context (~1-2K tokens):
   → Serialize relevant ontology sections into the prompt

3. LLM returns classification using ontology vocabulary:
   - category: canonical key from ontology (not a free-text guess)
   - correspondent: matched to known organization or flagged as new
   - person: resolved from ontology person registry
   - knowledge_type: one of the 8 types
   - facts: extracted with ontology-aware context

4. Resolve to Paperless tags via ontology dict lookups:
   category "insurance" → ontology["categories"]["insurance"]["paperless_tag"] → "Insurance"
   person "homer" → ontology["persons"]["homer"]["services"]["paperless"] → "Person: Homer"
   org "duff-insurance" → ontology["organizations"]["duff-insurance"]["paperless_correspondent"] → "Duff Insurance"

5. If new entity: emit tag.created event, Deriver adds to ontology.yaml, commits
```

### Search: How Ontology Enables Fast Queries

The ontology makes queries intelligent:

**Without ontology:**
```
fk knowledge search "car insurance"
→ grep for "car insurance" across all files
→ misses: "Duff Insurance", "Vollkasko", "KFZ", "Autoversicherung"
```

**With ontology:**
```
fk knowledge search "car insurance"
→ Ontology lookup: "car insurance" matches category Insurance + subcategory Vehicle
→ Aliases: Autoversicherung, KFZ, Vollkasko
→ Known correspondents: Duff Insurance, HUK, Globex
→ Expanded search: grep for all aliases + correspondent names
→ Finds everything related, regardless of language or terminology
```

This is the "super fast queries" insight. The ontology is a search expansion layer. You type one term, it knows the 10 related terms to also search for. No vector embeddings needed -- just a well-maintained alias graph.

**Kit Bot uses the same expansion:**
```
Homer: "What do we know about car insurance?"
Kit: loads ontology → Insurance+Vehicle → aliases + correspondents + persons
Kit: fk knowledge search with expanded terms
Kit: finds insurance.md, Duff Insurance correspondent entry, recent invoice, action item
Kit: synthesizes answer from all sources
```

### Integration with Future Stacklets

The ontology is designed for famstack as a whole. When new stacklets come online:

**Photos (Immich):**
- Face detection maps to `entities/persons.md` face IDs
- Location tags map to `entities/places.md`
- Album names can follow category conventions

**Calendar:**
- Event attendees map to persons
- Event categories map to ontology categories
- Recurring events detected by the Deriver become `[habit]` entries

**Code (Forgejo):**
- Repo topics could map to categories
- Commit authors map to persons

Each stacklet reads the ontology for context and emits events that the Deriver uses to keep the ontology current. The ontology is the shared vocabulary -- services speak their own language internally but translate through the ontology when communicating knowledge.

---

## Implementation Notes

### Where the ontology lives
`knowledge/meta` repo on Forgejo. Single `ontology.yaml` file + generated `graph.md` view. Git-tracked. Editable by hand, by bots, or via `fk ontology` CLI.

### Who reads it
- Archivist: at classification time (loads YAML, builds prompt context)
- Deriver: when extracting knowledge from events (full graph for resolution)
- Kit Bot / family assistant: when answering questions (alias expansion for search)
- Dream cycle: when maintaining and cleaning the ontology
- fk CLI: `fk ontology` commands

### Who writes it
- Homer: manual bootstrap and corrections (edit YAML directly or via CLI)
- Deriver: adds new entities discovered from events (appends to YAML, commits)
- Dream cycle: merges, cleanup, alias updates
- Family assistant: conversational writes ("remember that Dr. Hibbert is our pediatrician")

### Runtime loading
Load `ontology.yaml` once into a Python dict at startup. Refresh on git webhook or periodic poll (every 5 min). The file is ~5-10 KB. All lookups are O(1) dict access. No parsing on every query.

### Ontology as classification context
The ontology serialized into the Archivist's classify prompt adds ~1-2K tokens. At 50K context with a 3K document, this is well within budget and dramatically improves classification accuracy by giving the LLM a vocabulary to pick from rather than guessing.
