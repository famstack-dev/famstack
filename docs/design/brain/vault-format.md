# Vault format specification (v1)

The on-disk contract for a famstack memory vault. This is the vault's
real API: every reader (host CLI, bots, agent, wiki, search, mem0
priming) and every writer (archivist, curator, CLI) must agree on it.
Grounded in the current builders (`stacklets/docs/bot/vault_entry.py`,
`stacklets/memory/bot/cli/wiki.py`) and aligned with the Open Knowledge
Format (see `open-knowledge-format.md`). Companion to ADR-011
(vault-as-database) and the domain model.

`format: 1`.

## 1. File shape

A vault entry is a UTF-8 markdown file:

```
---
<frontmatter>
---

<markdown body>
```

- Identity is the file path (OKF rule). No UUIDs in frontmatter.
- The body is CommonMark. Links between entries are **relative markdown
  links** (`[Homer](../../people/homer.md)`), never Obsidian `[[wikilinks]]`
  — relative links are the one syntax Obsidian, Forgejo, and OKF parsers
  all understand. A link points at the thing's own file in the vault: a
  person's declared file, a correspondent's card (ADR-017 §3). Records
  linked a person's generated page (`homer/about.md`) until migration
  `memory/0003`.

## 2. Frontmatter grammar (the strict subset — this IS the schema)

Frontmatter is NOT arbitrary YAML. It is a deliberately restricted
subset so a single stdlib-only parser can read it identically on the
host CLI and in containers. A writer MUST emit only this subset; a
reader MAY reject anything outside it.

Allowed:
- Delimited by a line `---` , content, a line `---`, then a blank line.
- **Top-level keys only:** `key: value`, one per line, no indentation on
  the key.
- **Scalar values:** string, integer, boolean (`true`/`false`), or a
  date-as-string (`2026-07-07`). Strings containing `:`, `#`, or leading
  special characters MUST be quoted.
- **One-deep string lists:**
  ```
  persons:
    - homer
    - marge
  ```
  A writer emits this block form. A reader also accepts a one-line flow
  list of scalars (`tags: [homer, "Person: Marge"]`, `tags: []`), the
  form people type by hand, and reads its items as strings.

Forbidden (a writer MUST NOT emit; a reader MUST fail loudly, not
silently drop):
- Nested maps, lists of maps, flow maps (`{}`), nested flow lists,
  anchors/aliases, multiline block scalars (`|`, `>`), multiple documents.
- Unquoted values YAML reads as something else: one that starts with a
  YAML indicator (`[ ] { } , & * ! | > # % @` and backtick, or `- `,
  `? `, `: `), or that contains `: ` or ` #`. Quote them. Quartz reads
  the wiki's pages with a full YAML parser, so a value both parsers
  accept means the same to both.

Rationale: full YAML's ambiguity buys nothing a family vault needs and
costs a C-extension dependency the host CLI cannot load. We own the
format; the subset is the spec.

## 3. Required fields (every entry)

| Field | Rule |
|---|---|
| `type` | REQUIRED. The OKF concept kind. Value MUST be from the closed vocabulary in §4. |

Everything else is per-type. OKF's standard optional fields (`title`,
`resource`, `tags`, `timestamp`) are used with their OKF meanings where
present.

## 4. The `type` vocabulary (closed)

Producers and the conformance validator MUST agree on this set. Adding a
type is a spec change (bump nothing below v2 until 1.0; pre-1.0 edits are
cheap).

**Records** (source; things that happened; never carry `view`):
- `document` — a Paperless-mirrored document.
- `note` — a captured note (verbatim paste).
- `bookmark` — a captured URL.
- `email` — a folded email thread.
- `diary` — one entry from the memories room (a recording, photo or note,
  with its joined fragments and replies).

**State documents** (the family's current truth; mutable through the
CLI or by hand; never carry `view`; see §7):
- `list`: a family list, such as groceries, a store or things to do (§5, §7).
- `person` (declared) — someone the family knows, one file each under
  `<shared_bucket>/people/`.

**Cards of entities** (declared; written once and never rewritten by a
machine; see §5):
- `correspondent` — an external party, `<shared_bucket>/correspondents/<slug>.md`.
- `topic` — a topic, `<bucket>/<slug>/topic.md`.

**Views** are not cards and carry no `type` (§6): what the curator
compiles into the brain from the cards, a person's, a correspondent's or
a topic's page, the diary's pages, the home page, a folder listing.
`type: index` is how the brain marked a navigation page before views.

Reserved for future (declare before use): `pet`, `vehicle`, `place`.

## 5. Per-type fields

Legend: **R** required, **O** optional (present-when-nonempty — absence
is a signal, so never emit an empty list/string). List fields are
one-deep string lists per §2.

Two person fields recur on records and mean different things:
`persons` lists the household members the record belongs to, by
person id (`homer`, the declared file's name; display names until
migration `memory/0003`, and still on diary cards); a name no declared
person goes by is left out and shown as a plain name in the body.
Records carry no `Person: <name>` tags. `mentions` lists the people outside the household it
names, each by the most specific name the record gives, or a
description through a member when it gives none ("Marge's mother").
Both come from the call that classifies the record. The person
registry is compiled from `mentions`; the record never links to it.

`tags` holds the ontology's topic ids (`medical`, `tax_office`) and
keywords, lowercase with hyphens between their words (`cropper-paper`),
each once (ADR-017 §4). A topic is never tagged by its label: the label
a reader sees comes from `ontology.toml` in the instance's language.
Writers map a topic's label in any language to its id and slug anything
else (`stack.tags`); migration `memory/0005` did the same for the files
written before, except that a tag it could not read stays and is
reported. Diary cards still carry labels and `Person:` tags.

### `document`
| Field | | Notes |
|---|---|---|
| `type` | R | `document` |
| `title` | R | |
| `timestamp` | R | ISO datetime the entry was written (OKF `timestamp`). |
| `source` | R | `paperless` |
| `paperless_id` | R | integer; the join key back to Paperless. |
| `resource` | O | per-document Paperless page URL (OKF `resource`). |
| `date` | O | document date (distinct from `timestamp`). |
| `correspondent` | O | external party name. |
| `document_type` | O | Paperless subtype (invoice, contract) — a different axis from `type`. |
| `category` | O | |
| `persons` | O | list. |
| `mentions` | O | list. |
| `tags` | O | list of topic ids and keywords (above). |
| `paperless_url` | O | base URL. |
| `processing` | O | `ai_formatted` \| `ocr` \| `original`. |
| `model` | O | model that classified it. |
| `paperless_version` | O | |

### `note` / `bookmark`
| Field | | Notes |
|---|---|---|
| `type` | R | `note` or `bookmark` |
| `title` | R | |
| `timestamp` | R | |
| `persons` | O | list. |
| `mentions` | O | list. |
| `filed_by` | O | Matrix localpart of the filer (mirrors the git author). |
| `tags` | O | list of topic ids and keywords (above). |
| `resource` | O | source URL (OKF `resource`; bookmarks always have one). |
| `capture_id` | O | the `dev.famstack.event` envelope id. |
| `date` | O | capture date. |
| `model` | O | |

### `email`
As `note`, plus the thread is a fold of per-message sections; each
message is preceded by an idempotency marker comment
`<!-- mid:<Message-ID> -->` in the body (not frontmatter). `resource`
may carry the mailbox/thread reference.

### `diary`
One file per entry at
`<bucket>/diary/entries/YYYY/MM/YYYY-MM-DD-<entry_id>.md`.
The diary pages in brain are compiled from these files and nothing
else, so a correction made here reaches every page.
The records are mirrored into brain like every source file, so search
and the agent read them, but the wiki does not publish `diary/entries/`.

| Field | | Notes |
|---|---|---|
| `type` | R | `diary` |
| `title` | R | |
| `timestamp` | R | when the entry's first message reached the room. |
| `date` | R | the day it happened (spoken date, else the day it was sent). |
| `entry_id` | R | short hash of the first message's event id; the path's identity. |
| `date_basis` | O | `spoken` \| `sent` \| `uncertain` \| `corrected` (a family member's correction set it). |

A correction is a reply in the card's thread in the room. It is read
again on every compile, applied to the card, and recorded in the vault
commit (`correct: <title>`, authored by whoever wrote it), not on the
card; its event id joins `event_ids`.
| `description` | O | one sentence. |
| `persons` | O | list. |
| `mentions` | O | list. |
| `tags` | O | list; still topic labels and `Person:` tags (not migrated, see above). |
| `filed_by` | O | Matrix localpart of the sender. |
| `addressee` | O | who the message is spoken to, as it names them; a correction can change it. |
| `medium` | O | `voice` \| `image` \| `video` \| `file` \| `text`. |
| `mode` | O | `monologue` \| `dialogue` \| `note`. |
| `duration_ms` | O | integer. |
| `resource` | O | Matrix permalink of the first message. |
| `event_ids` | O | list; every message folded into the entry. |
| `media` | O | list; site paths of the archived files. |
| `model` | O | |
| `digest` | O | hash of the file as the compiler wrote it. A file that no longer matches was edited by a person, and the compiler leaves it alone. |

The body holds the briefing callout, then `## Quotes`, `## Replies`,
and last the family's own words under `## Transcript`, `## Text` or
`## Caption`. The words come last so nothing in them can be mistaken
for a section.

### `person` (declared)
The family's file for one person, `<shared_bucket>/people/<id>.md`. The
file name is the id.

| Field | | Notes |
|---|---|---|
| `type` | R | `person` |
| `title` | R | the full name (OKF `title`). |
| `aliases` | O | list; every other name the family uses (Obsidian reads the same key). |
| `account` | O | the person's chat user name (Matrix localpart), when they have one. |

```
---
type: person
title: Maggie Simpson
aliases:
  - Margaret
---
```

A member of the household is a person with an `account` (first
iteration; `domain-model.md`, Member). Membership is derived, never a
field the family sets. A file that breaks the §2 subset or this table
is reported by path and left out, and the wiki retires no member page
while any file is reported.

The fields the person registry is to add (`relation_kind`, `relation`,
`first_seen`, `last_seen`, `sources`) were drafted for the person page the
wiki generated. That page is a view now (§6); where they go is decided
with the registry.

### `correspondent` (declared)
The card of one correspondent, `<shared_bucket>/correspondents/<slug>.md`,
written by the archivist the first time a document names it (or by
migration `memory/0004` for those named before) and never rewritten.

| Field | | Notes |
|---|---|---|
| `type` | R | `correspondent` |
| `card_id` | R | `correspondent-<slug>` |
| `title` | R | the name documents carry in `correspondent`. |
| `aliases` | O | list; the classifier's `correspondent_aliases` on the first document. |

A file of the older shape (`kind: correspondent`, `canonical`, no `type`)
got `type: correspondent` and its card id from migration `memory/0004`.
Its view in the brain shares the path: the card's text under the view's
frontmatter (§6), then the region the wiki fills.

### `topic` (declared)
The card of one topic, `<bucket>/<slug>/topic.md`, written the first time
a record is filed into the topic's folder (or by `memory/0004`) and never
rewritten: `type: topic`, `card_id: topic-<slug>`, `title`.

### `list`
The family's file for one list, `<bucket>/lists/<slug>.md`. The file
name is the slug. A state document (§7), changed by hand or with
`stack memory list`, never generated.

| Field | | Notes |
|---|---|---|
| `type` | R | `list` |
| `title` | R | what the family calls the list. |
| `description` | O | what goes on it ("Was wir im Supermarkt brauchen"); the agent picks a list by it. |
| `topics` | O | list; the topics the list belongs to, by slug. |
| `sections` | O | list; the sections in the order the family walks through the store. |
| `recurring` | O | `true` for a list used again and again (a packing list, the weekly chores): its ticked items stay until it is reset. |

```
---
type: list
title: Einkaufsliste
description: Was wir im Supermarkt brauchen
topics:
  - einkaeufe-besorgungen
sections:
  - Gemüse & Obst
  - Milchprodukte & Eier
---

- [ ] Batterien

## Gemüse & Obst
- [ ] Äpfel

## Milchprodukte & Eier
- [ ] Joghurt [Marge](../people/marge.md) 📅 2026-10-14
- [x] Milch ✅ 2026-10-10
```

The body holds one item per checkbox line. A section is a `##`
heading, kept in the order of `sections`; one the list does not name
comes after them, and an item without a section sits above the first
heading. Anything else in the body (a note under a heading, an indented
sub-item) belongs to the family and is left as it is.

An item is what the family wrote, then its fields, in this order, at
the end of the line:

- a relative link to a person in `<bucket>/people/` assigns the item to
  them; several links, several people, and one tick closes it for all;
- any other relative link into the vault names the record the item came
  from;
- `📅 YYYY-MM-DD` is the day it is due, `✅ YYYY-MM-DD` the day it was
  ticked: the Obsidian Tasks plugin's fields, so its queries read them.

Only the trailing run of these counts. A link inside the sentence, a
web link and any other field stay part of the text. The same line is
used wherever an item appears; a record's summary writes it without the
checkbox, because the list holds whether it is done.

A ticked item stays on its list for the rest of the day. The night after,
the curator moves it to the list's archive,
`<bucket>/lists/archive/<slug>/<YYYY-MM>.md` for the month it was ticked:
a list file of its own (`title: Einkaufsliste, 2026-10`, no topics), with
the line and its section as they were. A tick without a date gets the
day's date first. A `recurring` list is left alone. The archive is where
`stack memory list add` learns where an item was last bought, its
section and its spelling.

## 6. Views (ADR-016 §3, ADR-017 §7)

What the curator compiles into the brain is a view, never a card. Its
frontmatter:

| Field | | Notes |
|---|---|---|
| `view` | R | `person`, `correspondent`, `topic`, `diary-front`, `diary-year`, `diary-month`, `home`, `folder`. |
| `title` | R | its heading. |
| `about` | O | the card id of the one thing it is about: a person's, correspondent's or topic's card. |
| `derived_from` | R | list of the card ids it was compiled from. |
| `compiled_from` | R | list, each card of `derived_from` as `<card id>@<version>`; the version is the git blob id of the file, 10 hex digits (`stack.cards.card_version`). |
| `canonical`, `synonyms` | O | a person's view: the names the archivist and the diary recognise them by. |

- A view MUST NOT carry `type`, `card_id` or `generated`; a card MUST NOT
  carry `view`.
- `view` — not the filename — is the authoritative card-versus-view
  signal for the mirror, for the compiler's skip-over-hand-written-pages
  rule and for every reader (`stack.cards.is_view`, which still reads the
  `generated: true` older brains carry). Filename remains a backstop only
  for delete/rename diffs where content is unavailable.
- A view that shares its path with a card (a correspondent, the root
  `index.md`) keeps the card's text and fills only the region between
  `<!-- begin: generated -->` and `<!-- end: generated -->`.
- A view the model wrote keeps the model's last answer after that region,
  one line, `<!-- answer: <base64 JSON> -->`, so the curator can compile
  it again without the model, with the cards as they are now.
- When a card in `derived_from` is pushed, the curator compiles the view
  again: at once without the model, then with it once the pushes settle.
  When the vault holds another version of a card than `compiled_from`
  records, the view is behind that card, and the card's text counts.

## 7. State documents

`<bucket>/lists/<slug>.md` is a state document (§5, `list`): a tick is
information, so a list is never regenerated, and a command changes only
the lines it is about.

Before lists, each topic kept its todos in `<bucket>/<topic>/todos.md`.
`stack up memory` moves every one of them to `<bucket>/lists/<topic>.md`,
in one commit by the memory bot, keeping each line and tick and turning
the old due date into `📅`. Nothing writes `todos.md` any more.

`<shared_bucket>/people/<id>.md` is a declared person (§5): the
family's own facts about someone, starting with their names. On every
start `stack up memory` adds one for each account that has none, as
a commit by the memory bot, and never rewrites an existing file.
A family member adds a person without an account (a baby) or an alias
by writing the file in Obsidian, Forgejo or with `stack memory write`;
a hand edit is a commit like every other. The family's corrections to
what the registry compiles ("Ed is Ned", "Grampa is Homer's father")
go into the same file when the registry is built.

`ontology.toml` / `facts.toml` are TOML config at the vault root, outside
this markdown-frontmatter spec.

## 8. Parser / writer / validator contract

There MUST be exactly one implementation of each, shared by host and
containers (in `lib/stack/`, stdlib-only so the host CLI can import it):

- **Parser**: reads the §2 subset into a dict. On out-of-subset input it
  raises (or returns a typed error) — never silently drops fields.
- **Writer**: serializes a dict to the §2 subset, quoting strings that
  need it, omitting absent-optional fields (no empty lists/strings).
- **Validator**: given a parsed dict, checks §3–§5 — `type` present and
  in vocabulary, required fields per type present, list fields are lists,
  `generated` presence matches the record/projection class. Runs on
  WRITE (fail fast) and is available to a conformance test.

One malformed page currently takes down the whole Quartz build; validate
on write so a bad page never reaches disk, rather than catching it three
hops downstream at publish.

## 9. OKF conformance

This spec is OKF-conformant at rest: required `type`, standard
`title`/`resource`/`timestamp`, path-as-identity, relative-link graph,
`index.md` navigation. Custom fields (`persons`, `paperless_id`,
`aliases`, …) are OKF-legal — consumers use what they understand and
ignore the rest. See `open-knowledge-format.md` for the exporter and
conformance-test plan (Phase 2).
