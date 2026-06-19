# Knowledge System: Layered Implementation Plan

> Status: Implementation plan
> Created: 2026-04-14
> Author: Homer + Claude
> Depends on: [knowledge-architecture.md](knowledge-architecture.md)

## Starting Point

The Archivist bot in #documents is the first knowledge pipeline. It already does: upload to Matrix, OCR via Paperless-ngx, LLM classification (title, category, person, document_type, correspondent, date, summary), auto-create missing tags/types, reformat OCR to clean Markdown, report back with link and summary.

What's weak: flat tags with no hierarchy or ontology, facts mentioned in summary but not structured, no cross-document awareness, no action item detection, tag proliferation over time, knowledge discarded after the chat message.

## The Layers

Each layer is independently shippable and adds value on its own.

### Layer 1: Smarter Classification

**Goal:** Better Paperless tags and richer extraction. Zero new infrastructure -- just a better prompt and tag discipline.

**1a. Tag taxonomy with conventions.**

Enforce naming conventions so tags are consistent and queryable:

```
Category:   Insurance, Finance, Medical, School, Home, Vehicle, Legal, Travel
Person:     Person: Homer, Person: Marge
Status:     Action: Review, Action: Expiring, Action: Paid, Action: Return
Period:     Year: 2026, Quarter: Q2
```

The Archivist's classify prompt already splits `person_tags` from `category_tags`. Extend this to recognize and enforce the full convention. New tags must follow the pattern -- the Archivist rejects or normalizes violations.

**1b. Fact extraction in the classify call.**

The LLM already reads the OCR text. Add to the classification response:

```json
{
  "title": "Duff Insurance Rechnung Marz 2026",
  "date": "2026-03-15",
  "category": "Insurance",
  "person": "Person: Homer",
  "document_type": "Invoice",
  "correspondent": "Duff Insurance",
  "summary": "Annual car insurance invoice, EUR 340, policy KFZ-2024-XXXXX",
  "facts": [
    "Car insurance premium: EUR 340/year",
    "Policy number: KFZ-2024-XXXXX",
    "Coverage: Vollkasko + Haftpflicht"
  ],
  "action_items": [
    {"action": "Insurance renewal due", "due": "2026-06-30"}
  ],
  "related_to": "Duff Insurance"
}
```

This costs zero extra LLM calls -- it's the same prompt, richer output schema. The facts and action_items ride along with the existing classification.

**1c. Tag deduplication in code.**

Before creating a new tag, fuzzy-match against existing:
- Lowercase + strip whitespace comparison
- Common prefix matching ("Springfield Tax Office" matches "Springfield Tax Office")
- The prompt already asks for this, but code enforces it as a safety net

**Time: 3-4 hours.** Changes to `archivist.py` only: updated prompt, tag validation, richer JSON parsing.

**Delivers:** Consistent tag taxonomy, structured facts in every classification, action items detected, less tag sprawl.

### Layer 2: Event Emission

**Goal:** Connect the Archivist to the wider knowledge system via the event factory pattern.

**2a. Implement the event system core.**

`lib/stack/events.py` with `FamstackEvent`, `EventSink` (abstract), `MatrixEventSink`, `StackletEventFactory`. Designed for famstack as a whole -- every stacklet can emit events, not just docs.

**2b. Wire DocsEventFactory into the Archivist.**

After successful classification, emit a `document.filed` event carrying the full classification including facts and action items. The event uses `dev.famstack.event` message type in #documents (invisible to Element, visible to bots).

```python
await self.events.emit(
    type="document.filed",
    summary=f"{title} filed and classified",
    data={
        "paperless_id": doc_id,
        "title": title,
        "correspondent": correspondent,
        "document_type": doc_type,
        "category": category,
        "person": person,
        "tags": applied_tags,
        "date": date,
        "facts": facts,
        "action_items": action_items,
        "summary": doc_summary,
    },
    actor=sender,
)
```

**Time: 2-3 hours.** New module + Archivist wiring.

**Delivers:** Every filed document produces a rich, structured event. Downstream consumers (Deriver, dashboard, notifications) get document knowledge for free. No second LLM call needed.

### Layer 3: Knowledge Wiki Bootstrap

**Goal:** Create the git knowledge repos on Forgejo, seed them, wire up the fk CLI.

**3a. Create repos on Forgejo.**

- `knowledge/meta` -- master index, ontology schema
- `knowledge/shared` -- household facts, insurance, contacts
- `knowledge/homer` -- personal
- `knowledge/marge` -- personal
- `knowledge/calendar` -- events, patterns

**3b. Seed with manual knowledge.**

Start with what Homer already knows: insurance details, emergency contacts, household facts. Use the Obsidian-compatible format:

```markdown
---
type: fact
domain: household
tags: [insurance, car, Duff Insurance]
created: 2026-04-14
source: manual
decay: 90d
---

# Car Insurance - Duff Insurance

Premium: EUR 340/year
Policy: KFZ-2024-XXXXX
Coverage: Vollkasko + Haftpflicht
Expires: 2026-06-30

See also: [[contacts#Duff Insurance]] | [[actions#insurance-renewal]]
```

**3c. Implement fk knowledge CLI.**

```
fk knowledge index [domain]
fk knowledge show <domain> <path> [-r <sha>]
fk knowledge search <query> [-d <domain>]
fk knowledge log <domain> [--since <period>]
```

**Time: 4-5 hours.**

**Delivers:** The knowledge wiki exists, is browsable on Forgejo and in Obsidian, and searchable via CLI. Even without automated extraction, this is a useful household knowledge base.

### Layer 4: Automated Knowledge Extraction (Deriver)

**Goal:** Document events automatically become wiki knowledge.

**4a. Build the Deriver bot.**

New MicroBot subclass. Joins all rooms, filters for `dev.famstack.event` messages. Buffers events, processes in batches.

For `document.filed` events, the pipeline:
1. Read the event data (facts, action items, correspondent, person, tags)
2. Load relevant domain index via `fk knowledge index shared`
3. Determine: update existing entry or create new one?
4. Write Markdown with frontmatter + wiki links
5. Git commit with structured message: `learn: Duff Insurance invoice, premium EUR 340/yr`
6. Update domain index

**4b. The extraction prompt.**

```
Given this document event and the current knowledge index:

Event: {event_data}
Current index: {domain_index}

Determine:
1. Which existing wiki file should be updated (path), or create new
2. The Markdown content to write (with YAML frontmatter, wiki links)
3. Updated index entry (one-line summary with ontology tag)
4. Any cross-references to add to other wiki files

Return JSON with: {file_updates: [{path, content, index_entry}], cross_refs: [{source, target, reason}]}
```

**4c. Action items to a tracked file.**

Facts with due dates go to `shared/household/actions.md`. Each entry has a status:

```markdown
- [ ] Compare insurance prices before renewal [due:2026-06-30] [src:document.filed:247]
- [x] Return school permission slip [due:2026-04-10] [completed:2026-04-09]
```

**Time: 6-8 hours.**

**Delivers:** Documents automatically populate the wiki. "When does our insurance expire?" is answerable. Action items are tracked. Knowledge compounds over time.

### Layer 5: Cross-Document Intelligence

**Goal:** The system connects documents to each other and to existing knowledge.

**5a. Context-aware extraction.**

The Deriver loads relevant wiki sections before processing a new event. When a new Duff Insurance invoice arrives, it sees the existing insurance.md and can:
- Update the premium if it changed
- Note the invoice as part of a series ("3rd Duff Insurance invoice this year")
- Flag contradictions ("last document said EUR 340, this one says EUR 380")

**5b. Relationship tracking via wiki links.**

The Deriver adds `[[wiki links]]` between related entries:
- `insurance.md` links to `contacts.md#Duff Insurance`
- `contacts.md#Dr-Weber` links to `medical.md`
- Invoice documents link to the correspondent's wiki entry

Obsidian's graph view renders these connections automatically.

**5c. Series detection.**

The Deriver notices patterns: monthly invoices from the same correspondent, recurring document types. These become `[habit]` entries in the wiki: "Duff Insurance sends annual invoice in March."

**Time: 4 hours.** Prompt enhancement + cross-reference logic.

**Delivers:** Knowledge graph emerges naturally. Kit can answer "what do we know about Duff Insurance?" with the full picture across all documents.

### Layer 6: Smart Tag Management

**Goal:** The ontology stays clean as document volume grows. Tags in Paperless align with knowledge types in the wiki.

See the dedicated ontology design section below -- this is a famstack-wide system, not just for documents.

**Time: 6-8 hours.**

### Layer 7: Proactive Document Intelligence (paid tier)

**Goal:** The system tells you things before you ask.

**7a. Morning briefing includes document insights.**

The briefing cron reads `actions.md` and surfaces:
- Expiring items within 30 days
- Overdue action items
- Recent document arrivals worth attention

**7b. Emergency surfacing.**

High-importance items push to Matrix immediately:
- "Your car insurance expires in 7 days. You wanted to compare prices."
- Documents tagged `Action: Review` that have been pending > 7 days

**7c. Document digest.**

Weekly summary posted to #documents: "This week: 3 invoices (EUR 1,240 total), 1 insurance letter, 2 school documents. New correspondent: Stadtwerke."

**Time: 4-6 hours.**

**Delivers:** The family server actively manages the household paperwork. This is the "it just tells you" experience that makes famstack worth paying for.

---

## Bot Architecture Decision

The Archivist's job is document filing -- the mechanical pipeline from Matrix upload to Paperless. It gets smarter (Layers 1-2) but stays focused on that pipeline.

Knowledge extraction (Layers 4+) is a different concern: reading events from any source, reasoning about them, and committing to the wiki. This is the **Deriver bot** -- a background worker that processes events from all stacklets, not just documents.

The Deriver doesn't need a chat personality. It's infrastructure. But it should have a Matrix user for transparency (activity visible in #bot-chat if anyone wants to watch).

**Naming options for the Deriver:**
- Keeper (knowledge keeper)
- Sage (the one who knows)
- Curator (manages the collection)
- Chronicler (records and connects)

Or keep the technical name "Deriver" -- it derives knowledge from events.

---

## Layer Timeline

| Layer | What | Hours | Dependencies |
|-------|------|-------|-------------|
| 1 | Smarter classification prompt + tag taxonomy | 3-4h | None (Archivist only) |
| 2 | Event emission via factory | 2-3h | None (new module + Archivist) |
| 3 | Knowledge wiki bootstrap | 4-5h | Forgejo repos + fk CLI |
| 4 | Automated extraction (Deriver) | 6-8h | Layers 2 + 3 |
| 5 | Cross-document intelligence | 4h | Layer 4 |
| 6 | Smart tag management (ontology) | 6-8h | Layer 3 (wiki for ontology storage) |
| 7 | Proactive intelligence | 4-6h | Layers 4 + dream cycle |

Layers 1-2 can ship independently. Layer 3 can ship independently. Layers 4+ build on each other.
