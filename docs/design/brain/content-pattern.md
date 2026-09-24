# One content pattern for the brain

Status: built for the diary, 2026-09-24 (famstack #132). Decisions taken
in building it, where they differ from the proposal below:

- Entry unit: the message group (one recording, photo or note with its
  joined fragments and replies). Longer occasions ("Roadtrip 2026") are
  a later layer above the cards, not bigger cards.
- One compiler, changed in place, not two compared side by side: with
  the entry unit unchanged, a module test pins that the pages built from
  cards lose nothing of the entries.
- Cards live at `<bucket>/diary/entries/YYYY/MM/YYYY-MM-DD-<id>.md`, out
  of the wiki's way; `type: diary` (vault-format.md).
- Tags are the ontology's topic names and `Person: <name>`, as on
  documents, not `Topic:<name>`.
- The archivist, not a memory bot, posts the cards (once a day) and takes
  corrections in their threads, reusing its correction handling.
- A correction is applied once to the card in the vault and recorded in
  the vault commit under the corrector's name; the corrected card is the
  family's and is never recompiled. The thread is not replayed into it.
Related: [open-knowledge-format.md](open-knowledge-format.md),
[vault-format.md](vault-format.md), [diary-journal.md](diary-journal.md),
ADR-011 (vault as database, brain as projection).

## Goal

Every page type in the brain carries the same parts: the same
frontmatter fields, the same summary and facts block, the same tag
vocabulary. Search, the agent, `stack memory ask`, Obsidian and OKF
consumers then read all content the same way.

The pattern already exists for documents and captures. This note
applies it to the diary and to all content added later.

## Current state

| Content | Written by | Frontmatter | Summary and facts | Tags from the ontology |
|---|---|---|---|---|
| Document mirror | archivist, `vault_entry.render_document` | `type: document`, `title`, `persons`, `tags`, `document_type`, `paperless_id` | `> [!summary]` callout: summary, facts, action items | yes |
| Capture (note, bookmark) | archivist, `vault_entry.render_capture` | `type: <kind>`, `title`, `persons`, `tags`, `source_uri`, `added` | `> [!summary]` callout | yes |
| Email message | mail bot, `vault_entry.render_email_message_section` | per message section | per-message callout | yes |
| Diary month | diary compiler, `diary.render_month` | `title` only | none | no |

The diary reading step (`diary.Reading`) returns `mode`,
`spoken_date`, `addressee`, `gist` and `moments`. It returns no
persons, no tags and no facts.

### Effect on retrieval

Observed on 2026-09-19 with the agent on a diary question:

| Step | Model calls | Reason |
|---|---|---|
| search finds the month page | 1 | the page matches the keywords |
| grep the month page | 1-2 | the hit is one line without its day |
| read sections of the page | 1-2 | to find the day and the full entry |
| answer | 1 | |

Each extra call costs 5-25 s of prefill on the local model. A search
hit that already holds the entry, its day, its people and its facts
removes the grep and read calls.

## The pattern

### Frontmatter

| Field | OKF | Content | Source |
|---|---|---|---|
| `type` | required | `document`, `note`, `bookmark`, `email`, `diary-entry`, ... | writer |
| `title` | standard | short name of the item | classifier |
| `timestamp` | standard | when it happened (not when it was filed) | writer; for the diary, `date_for` |
| `description` | standard | one sentence: what this is | classifier |
| `resource` | standard | link to the original: Paperless document, audio, Matrix event | writer |
| `tags` | standard | ontology tags: `Topic:<name>`, `Person:<name>` | classifier |
| `persons` | custom | family members the item is about | classifier |
| type-specific | custom | `document_type`, `paperless_id`, `capture_id`, ... | writer |

The field renames in `open-knowledge-format.md` ("Changes", items 1-4:
`added` to `timestamp`, `source_uri` to `resource`, `type` everywhere)
are part of this pattern.

### Body

```markdown
> [!summary]
> One to three sentences, narrative, in the family's language.
>
> - Fact with a name, date, number or place
> - Fact ...

<content: the document text, the transcript, the note>
```

Rules, same as for documents today:

- Facts anchor on a name, date, number or place. A sentence without one
  is summary, not a fact.
- Summary and facts are generated. The content below them is the
  source. Quotes come only from the content (the diary rule in
  `diary.py`: "The diary quotes, it does not invent").
- One classifier vocabulary. The diary reading step uses the same
  ontology section (`ontology.classifier_prompt_section`) as the
  document classifier, so `Topic:Health` means the same on a letter
  from the doctor and on a diary entry.

## Diary: cards in the vault, pages as projections

The diary is personal and must read like one: day headings, the
family's own words, a short narrative, links to the audio. Tags, facts
and frontmatter do not belong on the page the family reads
(`diary-journal.md`: the Diary is personal and verbatim, the Journal is
operational and factual).

Search and the agent need the machine fields. Two stored artifacts,
one for people and one for machines, would be two sources of truth: a
correction would have to be made in both. So there is one source, the
**card**, and every view is generated from it.

### Flow

```
memo, photo or note in the memories room
  -> after the day's messages settle: extract one card per entry,
     write it to the vault
  -> the memory bot posts the card as a thread reply; the thread root
     is the entry's last message in the main timeline
  -> a family member replies in that thread to correct it
     -> the card is extracted again with the correction; the correction wins
  -> the diary compiler reads the cards and builds the warm pages (brain)
```

This is the flow the archivist already runs for documents: file,
reply with what was extracted, correct by a reply in the thread. The
correction pass starts from the state the person saw
(`_initial_classification_block` in `stacklets/docs/bot/pipeline.py`).

| Layer | Where | Role | Changed by |
|---|---|---|---|
| Card | vault (source) | one file per entry: machine frontmatter, summary and facts, the verbatim text or transcript, the audio link | the card extractor; corrections by thread reply |
| Card post | memories room, in the thread on the entry's last main-timeline message | shows the family what was extracted; the same thread is the place to correct it | the memory bot (edits its own post, `m.replace`) |
| Diary pages | brain (projection) | the warm month, year and index pages, composed from the cards | nobody; rebuilt from the cards |
| Search, agent, `stack memory ask`, OKF export | read the cards through the brain mirror | find and answer | nobody |

This is ADR-011's rule. A diary entry is a record, a thing that
happened, so its card lives in the vault. The month page can be
rebuilt losslessly, so it lives in the brain. Today the diary exists
only in the brain, which is also why an edit on a diary page would be
lost on the next compile.

### Rules

- **Thread on the entry's last message.** The card is a thread reply,
  and the thread root is the last message of the entry in the main
  timeline. That is the natural point: the entry is complete there,
  and the thread marker sits on it. The root must be a main-timeline
  message, because Matrix threads cannot be nested. The memories room
  keeps the family's own messages in its main timeline; cards and
  corrections stay in threads.
- **Corrections in the same thread.** A family member replies in the
  card's thread, as for documents.
- **Extract after the messages settle.** The compiler attaches late
  captions, replies and split uploads within time windows
  (`join_fragments`, `REMARK_WINDOW_S`, sync bursts). Cards are made in
  a nightly run or after a quiet period, not per message.
- **Late additions edit the card.** A message that joins an entry after
  its card was posted updates the card in the vault and edits the bot's
  post (`m.replace`). It does not post again.
- **One card per entry.** A card that covers several messages goes in
  the thread of the last of them in the main timeline at extraction
  time. A message that is itself a reply inside a thread is never the
  root.
- **The card holds the source text.** The transcript or text word for
  word, and the audio link. The diary compiler reads only the cards, so
  its quotes come from the same source as the facts.
- **Corrections are explicit.** A correction arrives as a thread reply,
  so the extractor knows it is human input and keeps it on every later
  pass. It never removes a heading or shortens the body
  (augmentation-strict, `open-knowledge-format.md`, Tier 1, item 3),
  and the write seam enforces this in code.
- **The memory stacklet owns it.** The memories room and the diary
  belong to memory, not to the archivist, which owns Paperless
  (AGENTS.md, principle 5). The thread-correction handling becomes a
  shared piece both bots use, not a copy.

### Card layout

```
family/diary/2026/06/2026-06-12-<id>.md     one card (vault)
```

Example card (demo family):

```markdown
---
type: diary-entry
title: Seepferdchen
timestamp: 2026-06-12
description: Bart passes his first swimming badge at the public pool.
persons:
  - Bart
  - Marge
tags:
  - Topic:Sport
  - Person:Bart
resource: https://matrix.to/#/!room/$event
---

> [!summary]
> Marge records Bart after his swimming test at the public pool.
>
> - Bart: Seepferdchen passed
> - Place: Springfield public pool

<the transcript or text, word for word; link to the audio>
```

The month page shows the transcript, the quotes and the audio link
from each card under its day heading. It does not show the
frontmatter, the summary callout or the facts. The diary compiler has
no model step of its own for entries; it formats cards. The month
digest paragraph stays generated, from the cards of that month.

### Path identity

The path must not change when a card is extracted again. The slug
comes from the entry's date and a short hash of its first event id. It
does not come from the generated title, because a correction can
change the title.

### Matrix load

At about 5 entries a day, the memories room gets about 1,800 threads
a year, one per card. Threads are ordinary events with an `m.thread`
relation, and Element loads the thread list lazily, so this is
expected to be fine.
Measure it before rollout: seed a test-rig room with a year of
synthetic entries and cards (`tools/family-memories`), then time room
open and thread-list scrolling in Element.

## What is a diary entry

The entry unit is an open decision, and the pattern depends on it.

Today the compiler's unit is a **message group** (`diary.Entry`,
`compile_entries`): one recording, photo or text, with its split
uploads joined (`join_fragments`) and its replies and captions attached
(`refers_to`). The **day** is only the page layout: `render_month` puts
the entries under day headings.

| Unit | For | Against |
|---|---|---|
| Message group (current `Entry`) | Exists and is tested. One source, so quotes stay safe. Stable identity from the first event id. | Many small files. A memo and a photo of the same occasion become two entries. |
| Day | Matches how the family reads the diary. One file per day. | A day mixes unrelated occasions (a zoo visit and a doctor's appointment); tags and facts become a mix, and a hit returns the whole day. |
| Occasion (model-grouped) | One file per thing that happened, across messages and across days. Best unit for tags, facts and retrieval. | Needs a grouping step with a model. New failure mode: wrong merges. Identity is harder when a later message joins an occasion. |

Criteria for the decision:

1. Retrieval: rank of the answering entry for the questions in the
   agent's search log (`[memory_search]` lines since 2026-09-19).
2. Purity: share of entries whose tags and facts describe one
   occasion only.
3. Correction: one correction touches one card.
4. Stability: two compiles of the same room give the same paths.

The month page groups by day whatever the record unit is, so the
reading experience does not depend on this choice.

## Build as a separate compiler, then compare

The current diary compiler stays as it is. A second compiler is built
next to it, with the same inputs, and the two outputs are compared
before one replaces the other.

| | Current compiler | New compiler |
|---|---|---|
| Input | room events, cached readings (`diary_store`) | the same |
| Reading step | `mode`, `spoken_date`, `addressee`, `gist`, `moments` | card extraction: the same, plus `title`, `description`, `persons`, `tags`, `summary`, `facts`; cached per entry |
| Entry unit | message group, rendered by day | the unit under test (see above); the compiler takes it as a parameter |
| Output | month pages (brain) | cards in a separate vault tree, card posts in a test room, month pages composed from the cards in a separate brain tree |
| Tests | `tests/unit/stacklets/test_memory_diary.py` | new module tests, written first |

### Comparison

| Measure | How |
|---|---|
| Retrieval | replay the questions from the agent's search log against both trees; rank of the answering page or entry |
| Agent cost | the same questions through the agent: model calls, wall time, prompt tokens |
| OKF conformance | the validator from `open-knowledge-format.md` ("Build", item 6) on both trees |
| Reading | the family reads both month pages for the same month |
| Cost | model calls and time for a full compile and for an incremental run |
| Stability | paths after two compiles of the same room |
| Corrections | a thread reply changes the card, and the next compile shows it on the diary page |
| Matrix load | room open and thread-list time in Element with a year of card threads |

The first runs use the synthetic corpus in `tools/family-memories`.
A run on the family's own room writes cards into the production
vault and posts into the memories room. It needs the owner's decision
first.

## Search changes that go with it

- Search matches `description` as it matches `title` and tag values
  today.
- Matches in the summary callout rank above matches in the body.
- Pages that are not diary entries (topic pages, emails, long
  documents) are split into heading sections for ranking, so a hit is
  the section that matched.

## Open questions

1. The entry unit (see above).
2. Cadence of card extraction: nightly, or after a quiet period per
   room, and how long a card stays open for late additions.
3. The memory bot becomes a vault writer. ADR-011 lists three writers
   (archivist, CLI, humans): the memory bot joins them, or it files
   through the CLI's write path.
4. OKF `index.md` against the diary's `about.md`. Quartz renders a
   folder's `index.md` without a body (`diary.pages_for`). The OKF
   exporter (`open-knowledge-format.md`, "Build", item 5) can write
   `index.md` for export and keep `about.md` for the wiki.
5. Replace the current diary tree, or keep both until the family
   agrees.
