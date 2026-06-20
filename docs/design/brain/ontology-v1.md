# Ontology v1: A Layered, Pre-Seeded Vocabulary for Smart Document Retrieval

> Status: Design document — implementation-ready
> Created: 2026-04-21
> Author: Homer + Claude
> Companion to: [ontology-design.md](ontology-design.md), [knowledge-architecture.md](knowledge-architecture.md), [knowledge-implementation.md](knowledge-implementation.md)
> Triggered by: paperless-gpt deep dive — they have no ontology layer; that gap is exactly what blocks LLM Q&A over documents.

## Why this exists

We are building toward LLM-driven retrieval ("what do we know about car insurance?", "when does the Duff Insurance policy expire?", "which family member has medical bills outstanding?"). Those questions cannot be answered by full-text search alone:

- "car insurance" misses every German document that says *Vollkasko*, *Haftpflicht*, *Kfz-Versicherung*
- "medical" misses *Gesundheit*, *Arzt*, *Krankenkasse*
- "Homer's bills" requires joining persons + categories + custom fields
- "expires soon" requires a typed `due_date` / `expiry_date` field, not prose

The ontology is the bridge between the user's natural question and the structured data living in Paperless, the wiki, and (later) the event bus.

This doc is the **shipping spec** for v1 — what file lives where, what fields it has, how the classifier and retriever consume it, how it grows. The conceptual vision is in [ontology-design.md](ontology-design.md). This doc is what to build first.

## The paperless-gpt "ontology": none

paperless-gpt is the closest comparable project. Their design choice is worth recording, because *their absence of an ontology is itself a design.*

| Concern | paperless-gpt approach |
|---------|------------------------|
| Tag vocabulary | Flat string list pulled from Paperless: `{{.AvailableTags \| join ", "}}` |
| Correspondent vocabulary | Flat list + a single env-var `CORRESPONDENT_BLACK_LIST` of names to exclude |
| Document type vocabulary | Flat list, "respond with empty string if none fit" |
| Aliases / translations | None — language is a single `LLM_LANGUAGE` env var |
| Relationships between entities | None |
| Person / household model | None |
| Typed extraction | Custom fields with type hint (`string`, `date`, `monetary`, `integer`) — closest thing to a schema |
| Source of truth | Paperless's own database |
| Cross-document reasoning | Not supported — each doc processed in isolation |
| Retrieval | Out of scope — they end at "doc is now well-tagged in Paperless" |

**Their implicit philosophy:** Paperless's data model *is* the ontology. The LLM does ad-hoc resolution by being given the current tag list as context. New tags are either created freely (`CREATE_NEW_TAGS=true`) or rejected (`false`).

**Why this is limiting (and why we shouldn't copy it):**

1. **No alias resolution.** A German "Vollkasko" doc gets a *Vollkasko* tag; a German "KFZ-Versicherung" doc gets a *Kfz-Versicherung* tag; an English "comprehensive insurance" doc gets a *Comprehensive Insurance* tag. All three are car insurance. Search for any one misses the others.
2. **No relationships.** Knowing "Duff Insurance is a car insurance correspondent" requires the LLM to re-derive that from prose every time. Wasted tokens, inconsistent results.
3. **No persons.** Documents about Marge vs. Homer cannot be filtered without manual tagging discipline.
4. **No retrieval surface.** They don't attempt cross-doc Q&A. The product ends at "tag exists in Paperless."

The one piece worth stealing from them is the **typed custom-field schema** (`type: monetary` → `"EUR1664.58"`). We adopt it directly into v1 below.

## The layered concept

Each layer is independently shippable and adds capability over the previous one. This is how we go from "today's flat tags" to "answer arbitrary questions about the household" without a big-bang rewrite.

```
Layer 5  Vector overlay (paid tier)              embedding similarity for novel queries
            └─ uses Layer 1+3+4 as the structural backbone
Layer 4  Growth from data (Deriver)              ontology learns from new correspondents/tags
            └─ proposes merges, dedups, alias additions
Layer 3  Ontology-aware retrieval                query expansion → multi-source fetch → LLM synthesis
            └─ "what about car insurance?" works in DE+EN
Layer 2  Ontology-aware classification           Archivist returns canonical keys, not free-text strings
            └─ classification quality lifts; tag sprawl stops
Layer 1  Pre-seeded ontology.yaml (V1 SHIPPING)  graph of categories/types/persons/orgs/custom_fields
            └─ single file, ~200 lines, hand-editable
Layer 0  Implicit ontology (today)               flat taxonomy.toml + users.toml + Paperless tags
            └─ where we are now
```

**Read upward** to see how each layer enables the next. Layer 1 is the v1 deliverable — everything from Layer 2 onward consumes it.

### What each layer ships

| Layer | Artifact | What it enables | Effort |
|-------|----------|-----------------|--------|
| 0 | (current) `taxonomy.toml`, `users.toml` | Bootstrap tags so Archivist isn't picking from an empty list | shipped |
| 1 | `ontology.yaml` + loader | A canonical graph: aliases, relationships, custom fields, persons, orgs | **3-4h — this doc** |
| 2 | Updated classify prompt + resolver | Classifier picks canonical keys; new entities feed back into ontology | 4-6h |
| 3 | `fk knowledge ask` / `fk docs query` | Smart Q&A over Paperless docs with alias expansion + typed filters | 6-8h |
| 4 | Deriver integration | Ontology grows automatically; near-duplicates flagged | 4-6h |
| 5 | LanceDB + nomic-embed-text | Semantic recall for queries that miss the ontology | 8-10h (paid tier) |

## v1 ontology spec

### Where the file lives

**v1 location:** Forgejo repo `family/meta`, file `ontology.yaml` at the root. The Archivist treats Forgejo as the source of truth, the same way `git_mirror.py` already does for the document mirror.

**Beta gate.** Ontology v1 is beta. It requires the `code` stacklet (Forgejo) just like the existing `mirror_to_git` opt-in does. Default for the docs stacklet's `[settings]` is `mirror_ontology = false`; flipping it on assumes Forgejo is reachable. This keeps cold-install UX unchanged for users who haven't enabled the code stacklet yet.

**How it works at runtime** (mirrors `git_mirror.py`):
- Archivist startup: fetch `family/meta/ontology.yaml` via `ForgejoClient.get_file`, parse, cache in memory + on disk at `${DATA_DIR}/docs/bot/ontology-cache.yaml`
- Reads: always served from the in-memory cache (zero latency)
- Writes (auto-extend or correction): mutate in-memory, persist to local cache, async PUT back to Forgejo via the same client + bot user (`archivist-bot`) the document mirror already uses
- Forgejo unreachable: soft-fail like `git_mirror.py` — keep serving from cache, queue the change, retry on next opportunity. The bot never crashes because the wiki backend blinked.
- First-ever startup with no `family/meta` repo: bootstrap from the bundled seed pack (`ontology-seed-de.yaml` / `-en.yaml`), commit it as the initial state, continue.

**Why reuse `git_mirror.py`'s pattern instead of inventing local-only storage:**
- One I/O backend (`ForgejoClient`) for all bot-managed git data — documents *and* ontology
- One bot user (`archivist-bot`) with one credential set — already provisioned by the existing setup flow
- One audit surface (Forgejo's web UI, `git log`) — the family member who clicks "history" in the docs repo will find ontology history in the same UI
- One mental model — when we add the wiki repos later (`family/shared`, `family/homer`), they all use the same pattern

**Why YAML, not JSON / TOML / Markdown:**
- YAML is structured-but-readable; survives hand-editing as a fallback
- Comments survive (TOML and JSON have varying support)
- `yaml.safe_load` returns a Python dict — zero parsing
- Aligns with Paperless workflows, frontmatter conventions, and Obsidian

**Why one file, not many:** At household scale (~30 categories, ~10 doc types, ~5 persons, ~50 orgs, ~20 custom fields) the entire graph fits in ~5-10 KB. A single file is one atomic commit, one diff to review, one parse at startup. Splitting into `categories.yaml` / `persons.yaml` / `orgs.yaml` is busywork at this scale and creates cross-file consistency problems.

### Schema

```yaml
# family/meta/ontology.yaml  (on Forgejo, via the code stacklet)
# v1 — single source of truth for the household's structured vocabulary.
# Loaded into memory at Archivist startup from Forgejo, cached locally.
# Auto-maintained by the Archivist; corrected in-chat or via `fk ontology`.

version: 1
language: de   # primary household language; aliases mix freely

# ── Categories ─────────────────────────────────────────────────
# Canonical key (lowercase, snake_case) -> label + aliases + relations.
# Aliases include translations and colloquial forms — used for both
# classification (LLM picks the canonical) and retrieval (expand a
# query into all known surface forms).
# `related` is bidirectional in spirit; declare on either side.
# `color` syncs to the Paperless tag color on next seed run.

categories:
  insurance:
    label: Versicherung
    aliases: [Insurance, Versicherung, Police, Vollkasko, Haftpflicht, coverage]
    related: [finance, vehicle, medical, home]
    color: "#2196f3"

  finance:
    label: Finanzen
    aliases: [Finance, Finanzen, Bank, banking, Konto]
    related: [investments, taxes, income, insurance]
    color: "#4caf50"

  taxes:
    label: Steuern
    aliases: [Taxes, Steuern, tax, Steuererklärung]
    related: [finance, tax_office]
    color: "#ff5722"

  vehicle:
    label: Fahrzeug
    aliases: [Vehicle, Fahrzeug, Auto, KFZ, Werkstatt, car]
    related: [insurance, travel]
    color: "#607d8b"

  medical:
    label: Gesundheit
    aliases: [Medical, Gesundheit, health, Arzt, doctor, Krankenhaus]
    related: [insurance, vaccinations]
    color: "#f44336"

  school:
    label: Schule
    aliases: [School, Schule, Unterricht, education]
    related: [children]
    color: "#ff9800"

  # ... seed continues with all entries from current taxonomy.toml,
  # each promoted to the same shape (label + aliases + related + color)

# ── Document Types ─────────────────────────────────────────────
# `expected_fields` is the hint to the classifier: when the doc is
# of this type, *try* to populate these custom fields. Empty list
# = no structured extraction expected (e.g. "Letter").

document_types:
  invoice:
    label: Rechnung
    aliases: [Invoice, Rechnung, bill]
    expected_fields: [invoice_number, total_amount, due_date, issuer]

  receipt:
    label: Quittung
    aliases: [Receipt, Quittung, Beleg]
    expected_fields: [total_amount, date, merchant]

  contract:
    label: Vertrag
    aliases: [Contract, Vertrag, agreement]
    expected_fields: [start_date, end_date, parties, total_amount]

  certificate:
    label: Bescheinigung
    aliases: [Certificate, Bescheinigung, Urkunde]
    expected_fields: [issued_date, issuer]

  statement:
    label: Kontoauszug
    aliases: [Statement, Kontoauszug, account_statement]
    expected_fields: [period_start, period_end, balance]

  policy:
    label: Police
    aliases: [Policy, Police, insurance_policy]
    expected_fields: [policy_number, premium, expiry_date]

  notice:
    label: Bescheid
    aliases: [Notice, Bescheid, ruling]
    expected_fields: [reference_number, due_date, total_amount]

  letter:
    label: Brief
    aliases: [Letter, Brief, correspondence]
    expected_fields: []

# ── Persons ────────────────────────────────────────────────────
# Closed set, seeded from users.toml. The LLM picks from this list
# but cannot create new entries. Service mappings let other
# stacklets resolve "Homer" to a Matrix ID, Immich face, etc.

persons:
  homer:
    label: Homer
    aliases: [Papa]
    services:
      paperless_tag: "Person: Homer"
      matrix: "@homer:merles.eu"

  marge:
    label: Marge
    aliases: [Mama]
    services:
      paperless_tag: "Person: Marge"
      matrix: "@marge:merles.eu"

# ── Organizations ──────────────────────────────────────────────
# Open set. Bootstrap with a small list of universally-useful orgs
# (Amazon, PayPal, common utilities for the language pack); the
# Archivist auto-extends from day one. Every classification that
# encounters an unknown correspondent immediately writes a new
# entry here with inferred categories — no review queue.
# `categories` and `persons` are scope hints — used by retrieval
# to widen a query and by classification to narrow it.
#
# Provenance fields (`learned`, `docs_seen`, `confirmed`) are
# auto-maintained. Pre-seeded entries omit them. Bot-added entries
# carry the date and a usage counter; a human reaction in Matrix
# flips `confirmed: true`. A correction in Matrix mutates fields
# directly. See "How v1 grows" below.

organizations:
  # Pre-seeded entry — no provenance fields, highest trust.
  duff-insurance:
    label: Duff Insurance
    aliases: ["Duff Insurance e.V.", "Duff Insurance Autoversicherung", "Duff Insurance Versicherung"]
    categories: [insurance, vehicle]
    persons: [homer]
    paperless_correspondent: Duff Insurance

  # Auto-learned entry — Archivist wrote this after seeing the
  # correspondent on a doc. Categories inferred from that doc's
  # tags. `confirmed: false` means no human has acknowledged it
  # yet; the next correction in Matrix can cleanly overwrite.
  stadtwerke_munchen:
    label: Stadtwerke München
    aliases: [SWM]
    categories: [utilities]
    persons: [homer]
    paperless_correspondent: Stadtwerke München
    learned: 2026-04-19
    docs_seen: 3
    confirmed: false

# ── Custom Fields ──────────────────────────────────────────────
# Typed extraction. Maps 1:1 to Paperless custom fields.
# Types match Paperless's native types: string, date, monetary,
# integer, float, boolean, url, documentlink.
# Monetary values are formatted as ISO-currency-prefix + decimal,
# e.g. "EUR1664.58" — same convention as paperless-gpt.

custom_fields:
  invoice_number:
    type: string
    label: Invoice Number
    description: Vendor's invoice/order/reference number

  total_amount:
    type: monetary
    label: Total Amount
    description: Total due/paid (e.g. EUR1664.58, USD49.99)

  due_date:
    type: date
    label: Due Date
    description: Payment or action deadline (YYYY-MM-DD)

  policy_number:
    type: string
    label: Policy Number

  premium:
    type: monetary
    label: Premium

  expiry_date:
    type: date
    label: Expiry Date

  reference_number:
    type: string
    label: Reference Number

  issuer:
    type: string
    label: Issuer

  merchant:
    type: string
    label: Merchant

  start_date:
    type: date
    label: Start Date

  end_date:
    type: date
    label: End Date

  period_start:
    type: date
    label: Period Start

  period_end:
    type: date
    label: Period End

  balance:
    type: monetary
    label: Balance

  parties:
    type: string
    label: Parties

  issued_date:
    type: date
    label: Issued Date

  date:
    type: date
    label: Date

# ── Knowledge Types ────────────────────────────────────────────
# For the future Deriver / wiki layer. Each extracted fact is
# tagged with a knowledge_type that determines decay behavior.
# Not consumed by the Archivist directly — included here so the
# whole vocabulary is in one file when the Deriver lands.

knowledge_types:
  rule:        { label: Rule,        decay: null, description: Permanent, safety-critical }
  habit:       { label: Habit,       decay: 365,  description: Recurring pattern }
  fact:        { label: Fact,        decay: 90,   description: Verifiable info, limited shelf life }
  context:     { label: Context,     decay: 30,   description: Temporary situation awareness }
  event:       { label: Event,       decay: 14,   description: Time-bound occurrence }
  goal:        { label: Goal,        decay: 365,  description: Aspiration with horizon }
  preference:  { label: Preference,  decay: 180,  description: Personal choice or taste }
  reference:   { label: Reference,   decay: null, description: Pointer to external resource }
```

### Loader + Forgejo-backed store

Two modules:

**`stacklets/docs/bot/ontology.py`** — pure functions, no I/O, unit-testable like `matching.py`:

```python
def parse_ontology(yaml_text: str) -> Ontology:
    """Parse and validate raw YAML. Returns a frozen dataclass.
    Raises OntologyError on schema violations (unknown type, dangling
    relationship reference, duplicate canonical key, etc.)."""

def expand_query(ontology: Ontology, terms: list[str]) -> ExpandedQuery:
    """Take user query terms, return the alias-expanded set.
    'car insurance' -> {categories: [insurance, vehicle],
                        aliases: [Vollkasko, Haftpflicht, KFZ, ...],
                        organizations: [Duff Insurance, HUK, ...]}"""

def resolve_to_paperless(ontology: Ontology, key: str, kind: str) -> str | None:
    """Map canonical key -> Paperless tag/correspondent/type name.
    Used by Archivist when applying classification."""

def serialize_for_prompt(ontology: Ontology, *, language: str) -> str:
    """Render the ontology as a token-efficient prompt block.
    Strips internal-only fields (color, services). Compact form
    sized to fit in ~1-2K tokens."""

def record_learn(ontology: Ontology, kind: str, key: str, **fields) -> Ontology:
    """Add a new entity with learned/docs_seen/confirmed metadata.
    Returns a new Ontology value; caller persists via OntologyStore."""

def apply_correction(ontology: Ontology, correction: Correction) -> Ontology:
    """Mutate ontology per a parsed correction (merge, forget,
    set categories, add alias, confirm). Returns a new Ontology value."""
```

**`stacklets/docs/bot/ontology_store.py`** — Forgejo-backed persistence, modeled on `git_mirror.py`:

```python
class OntologyStore:
    """Loads from family/meta/ontology.yaml on startup, serves
    reads from an in-memory cache, flushes writes to Forgejo via
    ForgejoClient. Soft-fails exactly like GitMirror when Forgejo
    is unreachable — cache stays hot, writes queue for retry."""

    async def ensure_setup(self) -> bool: ...
    async def load(self) -> Ontology: ...          # fetch + parse + cache
    async def save(self, ontology: Ontology,
                   *, message: str) -> bool: ...    # PUT to Forgejo + update cache
    def snapshot(self) -> Ontology: ...            # in-memory read, no I/O
```

`OntologyStore` shares the same bot user, creds path, and Forgejo client as `GitMirror`. One connection, one auth story, one set of operational failure modes.

**Local cache:** `${DATA_DIR}/docs/bot/ontology-cache.yaml`. Always reflects the last known-good state from Forgejo. If Forgejo is unreachable at startup, the bot serves from cache; if the cache is missing too (first-ever run without Forgejo), the bot falls back to the bundled seed pack and logs a warning telling the user to enable the code stacklet.

**Refresh:** Reload on startup and on a Forgejo push webhook (future). Polling is unnecessary — the bot is the only writer.

## How v1 enables LLM-driven retrieval (the actual point)

The reason ontology v1 is worth the effort is **Layer 3** — the smart Q&A surface. Without ontology, retrieval is just full-text grep; with it, retrieval becomes intent-aware.

### Walkthrough: "What do we know about our car insurance?"

Without ontology (today):
```
1. grep "car insurance" across Paperless full-text
2. Misses every German document (Vollkasko, KFZ-Versicherung, Haftpflicht)
3. Returns 0-2 docs, mostly wrong ones
```

With v1 ontology:
```
1. LLM parses query intent → entities mentioned
   {categories: ["car", "insurance"], persons: [], orgs: []}

2. Resolve to canonical via ontology
   "car"       -> category vehicle
   "insurance" -> category insurance

3. Expand via aliases + related
   vehicle    -> [Vehicle, Fahrzeug, Auto, KFZ, Werkstatt, car]
   insurance  -> [Insurance, Versicherung, Police, Vollkasko, Haftpflicht]
   related    -> insurance ∩ vehicle organisations: Duff Insurance

4. Build the actual queries
   Paperless full-text:    OR(all 11 alias terms)
   Paperless tag filter:   tags ∈ {Versicherung, Fahrzeug}
   Paperless corresp.:     correspondent ∈ {Duff Insurance, ...}
   Custom-field filter:    pull premium, policy_number, expiry_date
                            from filtered docs

5. Multi-source fetch + dedup by paperless_id

6. Context assembly (top-K docs):
   For each doc: title, date, correspondent, custom_fields,
                 LLM summary (already in mirror frontmatter or
                 stored in Paperless's content)

7. LLM synthesis:
   "You have an Duff Insurance Vollkasko + Haftpflicht policy
    (#KFZ-2024-XXXXX), premium EUR 340/year, expires
    2026-06-30. Last renewal notice was filed 2026-03-15
    (paperless #247)."
```

The ontology turned a 0-result query into a structured answer with citations.

### Q&A flows v1 should support

| User question | What v1 enables |
|---------------|-----------------|
| "What do we know about car insurance?" | Category + alias expansion → multi-doc synthesis |
| "When does my Duff Insurance policy expire?" | Org → docs → custom_field `expiry_date` |
| "How much do we pay for insurance per year?" | Category insurance → all docs → sum custom_field `premium` |
| "Show me Marge's school documents" | Person filter + category school |
| "Anything due in the next 30 days?" | Filter all docs by custom_field `due_date` < today + 30d |
| "Who is the dentist again?" | Org search by category medical with role hint |
| "What's our marriage certificate number?" | Doc type certificate + persons [Homer, Marge] → custom_field `reference_number` |

The first three would be impossible without ontology v1. The rest become reliable rather than guesswork.

### CLI surface (v1)

```
fk docs ask "<question>"             Smart Q&A over Paperless via ontology
fk docs find <category|person|org>   List docs matching an ontology entity
fk docs facts <doc_id>               Show extracted custom fields for a doc
fk ontology show                     Print the ontology
fk ontology log [--since 7d]         Audit trail: what did the Archivist learn?
fk ontology aliases <key>            All aliases (seeded + learned) for a key
fk ontology check                    Validate ontology.yaml structure
```

`fk docs ask` is the marquee command — that's the user-facing payoff of v1. Corrections happen in chat (see "How v1 grows" below); the CLI is for inspection and the underlying library shared by the Archivist and the future deriver.

## How v1 grows over time

The default growth model is the same as today's Archivist behavior, lifted from "create a Paperless tag" to "extend the ontology." **No review queues, no pending sections, no manual merges.** The system writes what it learns, marks the provenance, and lets corrections come *after the fact* through the chat surface that's already there.

### Auto-extend on every classification (the default)

Every `_process_document` call in the Archivist already creates Paperless tags and correspondents on the fly when the LLM returns something unknown. Ontology v1 piggybacks on that path:

```
LLM returns correspondent="Stadtwerke München"
  ├─ existing path: create Paperless correspondent if missing
  └─ new in v1:    write organisations.stadtwerke_munchen to ontology.yaml
                    with categories inferred from the doc's tags,
                    persons from the doc's person tags,
                    learned: <today>, docs_seen: 1, confirmed: false

LLM returns topics=["Utilities", "Vermieter"] (Vermieter is new)
  ├─ existing path: create Paperless tag "Vermieter"
  └─ new in v1:    write categories.vermieter to ontology.yaml
                    with empty aliases, empty related,
                    learned: <today>, docs_seen: 1, confirmed: false

Subsequent docs touching the same entity:
  └─ docs_seen +=1, last_seen updated, no other change
```

ontology.yaml is git-tracked (lives with the docs stacklet — eventually in the `knowledge/meta` Forgejo repo). Every auto-extension is a commit with a structured message:

```
learn: organisation Stadtwerke München (utilities, homer)

  Paperless-Correspondent: Stadtwerke München
  First-Seen-In-Doc: 312
```

That commit log *is* the audit trail. `fk ontology log --since 7d` is `git log` filtered by `learn:` / `update:` / `merge:` prefixes. No separate state file.

### Corrections — by chat reply, on the doc that's wrong

The Archivist already posts a "Filed: ..." summary to #documents for every classified doc. That message is the natural correction surface — it's the moment a family member can see a misclassification ("no, that's not Shopping, that's Insurance") and reply.

v1 adds a small reply-handler in the Archivist's existing `_on_text` callback. When a message in #documents is a Matrix reply (`m.in_reply_to`) to a Filed message, the bot interprets the text as a correction targeting that specific document.

Supported correction grammar — kept tiny on purpose, the LLM resolves anything fuzzier:

```
Reply to a "Filed: ..." message:

  "from HUK not Duff Insurance"             → re-classify with hint: correspondent should be HUK
                                    (this doc gets fixed; ontology gets Duff Insurance's
                                    docs_seen decremented, HUK's incremented)

  "not insurance, vehicle"        → re-classify with hint: category vehicle
                                    (this doc gets re-tagged; downweights the
                                    insurance-on-this-correspondent association)

  "this is Marge not Homer"    → re-classify with person hint

  "wrong"                         → strip auto-applied tags, leave the doc for
                                    manual fixing in Paperless

  "delete"                        → delete the doc from Paperless
                                    (with a confirmation react)
```

The LLM is in the loop for parsing — not a regex. The Archivist sends "{correction text} → please reclassify document #{doc_id} with this hint" and applies the new classification. No new bot, no new room, no new commands to memorize.

### Corrections — ontology-wide

When something is wrong at the ontology level (an org has the wrong categories, two orgs are actually one), the family member doesn't reply to a single doc — they say it as a fresh message in #documents addressing the Archivist:

```
@archivist Stadtwerke München is utilities + finance
@archivist merge "Duff Insurance e.V." into Duff Insurance
@archivist forget the org "Spam Inc"
@archivist Duff Insurance is also called "Duff Insurance SE"
```

Same handler, parsed by the LLM, written directly to ontology.yaml as a commit:

```
update: organisation Duff Insurance categories +finance

  Confirmed-By: @homer:merles.eu
```

A correction always sets `confirmed: true` on the affected entries — that locks them against future auto-overrides from low-confidence learning.

For people who'd rather use the terminal, the same operations work via CLI (`fk ontology merge <a> <b>`, `fk ontology forget <key>`, `fk ontology set <key> categories=…`). It's the *same* underlying library, exposed in two places.

### Background cleanup (Layer 4, deferred)

The dream cycle eventually handles janitor work the Archivist shouldn't synchronously care about:

- **Near-duplicate detection** — "Springfield Tax Office" vs "Springfield Tax Office" with overlapping doc sets → auto-merge if confidence is high, post a one-line summary in #documents otherwise
- **Orphan archival** — entries with `docs_seen: 0` for 6+ months move to an `archive:` section (still queryable, no longer suggested in classification prompts)
- **Alias mining** — tokens that co-occur near a canonical key in 10+ doc summaries → auto-add as aliases
- **Confidence promotion** — entries with `docs_seen >= 5` and no corrections → auto-flip `confirmed: true`

All of this is *automatic* — the dream cycle posts a daily one-line digest ("Learned 4 new orgs, merged 1 duplicate, promoted 3 to confirmed") and that's the only thing a human sees unless something truly ambiguous happens.

This is Layer 4 territory; it doesn't gate v1.

### What about bad auto-learns?

The fear: the LLM hallucinates a correspondent ("DB" extracted from a fragment), it ends up in the ontology, and pollutes future classifications.

Three mitigations, in order of how aggressive they are:

1. **Provenance prevents propagation.** `learned: ...` + `confirmed: false` + `docs_seen: 1` entries are *included* in the classification prompt but *deprioritized* — the prompt says "prefer confirmed entries." A single bad guess won't snowball.
2. **Corrections cascade.** When a user corrects "from HUK not Duff Insurance" on a doc, the Archivist also recalculates Duff Insurance's `docs_seen` and, if the correction reveals the entry was a hallucination (e.g. the only doc citing "DB" gets fixed to "Deutsche Bank"), the entry auto-prunes itself.
3. **Dream cycle reaps junk.** Entries with `docs_seen: 1` and no corrections after 30 days are archived (not deleted — they go to `archive:` and stay grep-able).

Net: the system errs on the side of writing, but it doesn't err on the side of *trusting* what it wrote.

### Pre-seeded starter packs

`taxonomy.toml`'s current de/en split becomes `ontology-seed-de.yaml` / `ontology-seed-en.yaml` shipped with famstack. On first `stack up docs`, if no `ontology.yaml` exists, the appropriate seed is copied into place based on `[core].language`. Future starter packs:

- `ontology-seed-self-employed.yaml` — adds `Mandant`, `Rechnungsnummer`, `Honorar`, `USt-Voranmeldung`
- `ontology-seed-landlord.yaml` — adds `Mieter`, `Nebenkostenabrechnung`, `Mietvertrag`
- `ontology-seed-deskstack.yaml` — office-oriented vocabulary for the deskstack product (clients, matters, billable hours)

Starter packs are how we monetize tier specialization without bloating the default install. Each pack ships a curated baseline; auto-extension takes over from there.

## Implementation path

Sized like the layers in `knowledge-implementation.md`. Layer 1 is the v1 deliverable; Layers 2-4 are the immediate follow-ups that make v1 *useful and maintainable*.

### Phase A — Ship v1 ontology file + loader + Forgejo store (4-5h)
- Write `stacklets/docs/bot/ontology.py` with `parse_ontology`, `expand_query`, `resolve_to_paperless`, `serialize_for_prompt`, `record_learn`, `apply_correction` (pure functions only)
- Write `stacklets/docs/bot/ontology_store.py` modeled on `git_mirror.py` — same `ForgejoClient`, same `archivist-bot` user, new `family/meta` repo, same soft-fail pattern
- Add `mirror_ontology = false` to `bot.toml` `[settings]` (beta gate, mirrors `mirror_to_git`)
- Migrate `taxonomy.toml` content into bundled seed packs `ontology-seed-de.yaml` / `ontology-seed-en.yaml`
- On first startup with `mirror_ontology = true` and an empty `family/meta` repo: commit the appropriate seed pack as the initial ontology
- Update `seed.py` to read ontology from the store (categories → Paperless tags, document_types → Paperless types, custom_fields → Paperless custom fields)
- Update `on_start_ready.py` to bootstrap the store
- Unit tests for the pure functions (mirrors `test_archivist_matching.py`); integration test for the store against a throwaway Forgejo repo (mirrors how `git_mirror` is tested today)
- `fk ontology check` validates the in-memory snapshot

### Phase B — Auto-extend on classify + wire ontology into the prompt (4-6h)
- Replace the hand-rolled prompt strings in `archivist.py:_classify` with `ontology.serialize_for_prompt(...)`
- Ask the LLM to return canonical keys (`"category": "insurance"`) instead of free-text strings; the prompt notes "prefer confirmed entries"
- Resolve to Paperless tag names via `resolve_to_paperless(...)` before PATCHing
- Populate `expected_fields` based on returned `document_type` — still one classification call, the returned JSON includes the typed custom fields per doc type
- After classification: `record_learn(...)` writes any new orgs/categories to ontology.yaml with `learned: <today>, docs_seen: 1, confirmed: false` and commits with a structured message; existing entries get `docs_seen += 1`
- New tests against bilingual fixtures (German + English receipts, invoices, certificates)

### Phase C — Chat corrections + smart retrieval (6-8h)
- Extend `archivist.py:_on_text` to detect Matrix replies on Filed messages → route to a `_handle_correction(doc_id, text)` that re-classifies with the user's hint
- Add `@archivist <verb> <args>` parsing in `_on_text` for ontology-wide corrections (merge, forget, set categories, add alias). The LLM parses the verb + args from natural language; the bot calls into `apply_correction(...)` which mutates ontology.yaml + commits
- Implement `fk docs ask <question>` (the retrieval walkthrough) and `fk docs find` / `fk docs facts`
- Implement `fk ontology show` / `aliases` / `log` (the audit) / `check`
- Same `apply_correction` library backs both surfaces — chat and CLI

### Phase D — Background hygiene + Deriver hand-off (later)
- Nightly dream-cycle job: dedup near-duplicates, archive orphans (`docs_seen: 0` for 6+ months), promote confidence (`docs_seen >= 5` + zero corrections → `confirmed: true`), mine new aliases
- Daily one-line digest in #documents
- Layer 5 (vector overlay) only when retrieval starts missing in ways the ontology can't fix

## Decisions and open questions

**1. Single language vs per-language labels.**
v1 is single-language (`label:` field). Aliases mix freely. If we ever need true multilingual labels (`label.de:` / `label.en:`), the schema can extend without breaking. For now, KISS.

**2. Where do custom fields actually live — ontology.yaml or Paperless?**
Both. `ontology.yaml` is the schema (name, type, description, applies_to). Paperless stores the per-doc values. `seed.py` creates the Paperless custom fields from the schema; the Archivist populates them per doc.

**3. What about Immich faces, Matrix users, calendar attendees?**
Layer 1 only covers what the Archivist needs (categories, doc types, persons, orgs, custom fields, knowledge types). Cross-stacklet persons (Immich face IDs, calendar attendees) get added under `persons[].services[*]` when those stacklets land. The schema already has the slot; the values get filled later.

**4. Org category inheritance.**
If an org has `categories: [insurance, vehicle]`, should documents from that org auto-get those tags even if the LLM disagrees? **No** — the LLM has read the actual document text and may have good reason to deviate. The ontology hints; the LLM decides. The exception is when classification fails entirely — fallback to org-implied categories rather than untagged.

**4b. Conflicting corrections from different family members.**
Homer replies "from HUK not Duff Insurance" on the same doc Marge later replies "no, that was actually Duff Insurance after all." Last write wins on the doc; the ontology entries log both events (`docs_seen` jitters but converges). For ontology-wide corrections, `confirmed: true` is sticky — once set, only an explicit `@archivist unconfirm <key>` undoes it. This avoids ping-pong without a conflict-resolution UI.

**4c. Auto-extension of the persons list.**
**No.** Persons stay closed-set, seeded from `users.toml`. The LLM cannot create new ones. Adding a person is a deliberate household action (someone joined the family, a child gets their own account); this happens at the `users.toml` level and propagates on next `stack up docs`. Auto-creating persons would lead to "John Smith" tags from random documents the family received.

**9. Why require the code stacklet for v1 ontology?**
v1 is beta. The alternative — a parallel local-only storage path — means two persistence patterns to maintain (local-only vs Forgejo-mirrored) and two failure modes to test. The `git_mirror.py` pattern already handles "Forgejo unreachable" gracefully (cache + retry) and is the right reference. Reusing it means one I/O backend, one bot user, one credential set, one audit UI. When v1 leaves beta we can revisit whether a local-only mode is worth shipping; for now, "needs code stacklet" is an honest beta constraint, not technical debt.

**10. Will rebranding Forgejo (e.g. to "brain") affect the ontology?**
No. The ontology talks to Forgejo via `ForgejoClient`'s API, which doesn't care about UI branding. A future rebrand (theme + custom landing page, *not* a source fork) is purely cosmetic from the bot's side. The repo path `family/meta/ontology.yaml` would remain stable across the rebrand.

**5. How rich should `description:` fields get?**
Long enough that the LLM can disambiguate (`due_date: "Payment or action deadline (YYYY-MM-DD)"`); short enough that the prompt stays under 2K tokens. Aim for one sentence each.

**6. Versioning strategy.**
`version: 1` at the top of the file. Loader rejects unknown versions with a clear migration message. Schema changes that don't break older consumers can stay at v1; breaking changes bump to v2 and ship a `fk ontology migrate v1 v2` command.

**7. Alias collisions.**
Two categories listing the same alias is a validation error caught by `fk ontology check`. Aliases are a flat namespace.

**8. Should we contribute the ontology approach upstream to paperless-gpt?**
Yes — but as a config-file convention they could adopt, not as a PR. Their architecture treats Paperless as the source of truth; convincing them to introduce a parallel ontology layer is a hard sell. Better: a blog post on famstack.dev — *"Why your Paperless tags need an ontology"* — that demonstrates the retrieval payoff with concrete examples. Drives traffic, builds authority, and surfaces our approach to their userbase without trying to redirect their roadmap.

## Core insight

paperless-gpt assumes Paperless's flat data model is sufficient because their product ends at "doc is well-tagged." Our product ends at "ask the household anything, get a structured answer." That gap is filled exactly by a maintained ontology — one canonical vocabulary, with aliases and relationships, that turns "car insurance" into the right multi-pronged query across any language and any correspondent the household has ever interacted with.

Layer 1 is a single 200-line YAML file and a 150-line loader. It's the smallest possible deliverable that unlocks every layer above it. Ship it first; everything else builds on it.
