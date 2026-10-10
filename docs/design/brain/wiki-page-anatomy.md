# Wiki Page Anatomy: Profile Cards and Spokes

> Status: Design draft
> Created: 2026-06-12
> Author: Homer + Claude
> Depends on: [wiki-engine.md](wiki-engine.md), [ontology-design.md](ontology-design.md)
> Evidence: generation experiments 2026-06-11/12 (white paper draft in
> familykit-workspace/content/drafts/family-knowledge-whitepaper.md)

## Goal

Generated wiki pages are **curated overviews, not ledgers**. The primary
reader is an assistant that loads a page before interacting with a person
or reasoning about an entity. The page answers: who/what is this, what is
current, what are the recent interests and topics, and where do I go
deeper. Humans browsing get the same overview for free.

Completeness is NOT the page's job. Detail lives one link away, behind
citations and spoke pages. Account numbers and SSNs stay off the card
for the same reason bar tabs do: they are not overview material, and
they cost tokens on every interaction. NOT because the assistant must
not see them. The AI is local; nothing in the vault is hidden from it,
and it can follow the links whenever a task needs the detail. No
redaction layer exists or is planned. That is the point of running
the model on our own hardware.

## What the experiments settled

Two arms (batch regeneration vs incremental update passes), two models
(Qwen3.5-9B local, Qwen3.6-35B remote), one member page, 79 ground-truth
facts:

| Coverage | Batch | Incremental |
|---|---|---|
| 9B | 44% | 46% |
| 35B | 33% | **100%** |

- A single LLM artifact gives you either curation or completeness,
  never both. The stronger model curates harder in batch mode (drops
  more facts, writes a nicer page) and transcribes perfectly in
  incremental mode (213-line ledger, one-sentence About).
- Style collapses through self-imitation in build-on-top updates on
  small models: the first lazy addition becomes the permanent template.
- Cross-entity fact transplantation ("Homer, date of birth: 01 April
  2016" copied from Bart's birth certificate) happens at every model
  size when the prompt lacks an anti-conflation rule.
- Supplying real reference paths eliminates link fabrication. A
  mechanical no-line-deleted guard reliably separates good update
  passes from bad ones.

Consequence: pages are regenerated (batch, strong model), not
incrementally patched. The update-pass harness is shelved as a
technique, not a shipping path. The "page must never lose content"
bar applied to a verified-artifact model we no longer pursue;
consistency of regen quality and a stable identity core replace it.

## Anatomy

Every entity the ontology knows gets a hub page and, as they earn
their keep, spokes.

```
<entity>/about.md          the profile card (hub, generated)
<entity>/timeline.md       append-only events, rendered from evidence
<entity>/todos.md          open action items, date-filtered
<member>/interests/<t>.md  periodic recaps per interest (later)
```

### The profile card (`about.md`)

1. **Identity header.** Name, type-specific key facts. Rendered from
   extracted facts where they exist, deterministic. Small.
2. **About prose.** Who/what this is right now. Recency-weighted:
   recent evidence outweighs the 1998 contract. LLM, strong model,
   token-capped (target a few hundred tokens; the card competes for
   context window on every assistant interaction).
3. **Trivia / preferences.** Only when extractable from evidence or
   primed by the user. Never invented to fill the section.
4. **Links.** Timeline, todos, interests, documents, related entities.
   The link graph is the API for agents that need to go deeper.

### The person page template (built)

A person's `about.md` is filled from a Markdown template per language,
`stacklets/memory/bot/templates/person.<lang>.md` (English and German).
The template's headings are what the family reads; each `{{slot}}` is
filled by one side:

| Slot | Filled by | What it holds |
|---|---|---|
| `name`, `first_name` | compiler | the person's file |
| `tagline` | model | one warm line: who they are in the family |
| `about` | model | two to four sentences about them today |
| `loves` | model | up to five things, people, places or pastimes they love |
| `more` | model, optional | one section of its own choosing, with its own heading |
| `lately` | compiler, one line each by the model | the six newest entries naming them (not documents), with their real dates |
| `documents` | compiler | the documents naming them, newest first, linked |

The model answers in JSON, one field per slot, and writes in the
household's voice: warm, concrete, the way a family member would
describe someone to a visiting relative. It never writes a date, never
lists a document, and cannot credit an entry to the wrong day, because
those slots are not its to fill. A paragraph whose slot is empty is
left out with its heading, so a page never says "nothing recorded". An
answer that is not the JSON object keeps the page as it was.

Each page carries a stamp with the layout it follows. A page with an
older stamp is regenerated from the evidence once instead of being
kept as the baseline for the next rebuild, so a layout change reaches
every page by the next nightly sweep.

### The home and topic page templates (built)

The home page and every topic page follow the same split, each from its
own template (`home.<lang>.md`, `topic.<lang>.md`, shared code in
`page_template.py`):

- **Home.** The compiler lists everyone the person files declare, linked
  and never dropped, in the order the model gives (oldest first). The
  model writes the welcome and one line per person, and reads relatives,
  friends and neighbours, the people who help the family, and the
  household facts (home, vehicles, insurance, subscriptions,
  memberships) out of the sources. The welcome is warm; the facts are
  plain.
- **Topic.** Facts and state, no prose: the model gives the key facts as
  label and value and what has happened (booked, bought, decided), with
  who did it. The compiler lists the open items of the topic's todo
  list as the family keeps it, and everything filed under the topic
  with who filed it and when.

Correspondent pages stay as they are: facts the compiler renders from
the documents, no model prose.

### Entity types and slot templates

The ontology defines which pages exist; each entity type brings a
small slot template for the identity header. Start minimal:

| Type | Slots (initial) |
|---|---|
| person | born, relationships, role/occupation, school |
| vehicle | model, plate, insurance policy + renewal, last service |
| pet | species, vet, next check-up |
| property | address, mortgage/rent key terms, utilities |
| topic | definition line, owner bucket |

Slots are filled from extracted facts, not generated. An empty slot
stays empty.

### Generation principles

- Batch regeneration per page, strong model, temperature 0.
- Recency weighting in evidence selection and in the prompt.
- Anti-conflation rule (facts must be tied to the page's subject by
  name) stays mandatory; it failed at both model sizes without it.
- Token cap per card.
- No ledger content: receipts and line items reach the timeline and
  document links only.

## Priming and corrections

A per-member primed note (written during onboarding or any time) is
high-trust evidence: "Homer: bowls Thursdays, hates Flanders." The
generator reads it like any other evidence, weighted highest. Because
it is input rather than output, it survives every regeneration.
Corrections follow the same path: fix the primed note, not the
generated page. This removes the need for preservation machinery on
generated pages.

## Todos (the voice-first capture)

Requirement from the household: drop "things that need doing" per
person as a voice message, and the system keeps bugging you until
they are done.

Flow sketch:

1. **Capture.** Voice message to the family bot (or a todos room),
   whisper transcribes, a small classification pass extracts person,
   task, due date if spoken. Filed as a todo note in the person's
   bucket with frontmatter (`person`, `due`, `status: open`).
2. **Render.** `<member>/todos.md` lists open items, date-filtered,
   newest first, each linking its source note. Done items drop off
   (history stays in git).
3. **Nag.** famstacker reminds via Matrix: on due date, then
   escalating. Done is declared conversationally (reply/reaction),
   which flips `status` in the note's frontmatter.

Open questions: where acknowledgment lives (reaction vs reply vs
command), whether todos are also assigned to non-capturing members
("Bart: clean room"), and nag cadence before it becomes noise.

## Deferred, deliberately

- **Household emergency page** (contracts, notice periods, where the
  proof lives, for the bus-factor-of-one scenario). The strongest
  emotional pitch of the product; parked, not deleted. The hub pages
  for vehicle/property recover much of it per entity.
- **Interest recaps and research agents** over topic pages.
- **Slot reconciliation** (new evidence updates a slot, conflicts
  surfaced, "as of" dates). The real architecture jump; design before
  building.
- famstacker `wiki` command and notify API (see cleanup backlog).

## Sequencing (each step shippable)

1. **Profile-card prompt** for person about pages: curated, recency-
   weighted, anti-conflation, token-capped, trivia-if-present. The
   35B batch run is the quality reference. Built as the person page
   template above (2026-10).
2. **One non-person entity page** (the car). Forces entity types and
   slot templates to become real instead of theoretical.
3. **Todos**: capture → render → nag, in that order; render alone is
   already useful.
4. Interest recaps, research entry points, household page: later.
