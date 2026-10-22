# The memory format

The memory is a git repository of plain files: markdown cards, a JSON
sidecar beside some of them, and two TOML files. Nothing in it needs
famstack to be read. This document is for anyone who wants to read,
search, convert or back up their memory with their own tools: a text
editor, Obsidian, a script, another program.

It explains the format with examples. The precise definition is the JSON
Schema files in [`stacklets/memory/schemas/`](../stacklets/memory/schemas/):

| File | Defines |
|---|---|
| [`cards/base.json`](../stacklets/memory/schemas/cards/base.json) | the fields every card shares |
| `cards/<type>.json`, one per card type | the fields that type adds |
| [`sidecar.json`](../stacklets/memory/schemas/sidecar.json) | a card's sidecar |

Where this document and a schema disagree, the schema is right. The
reasons behind the format are in the decision records:
[ADR-015](adr/adr-015-a-change-to-a-record-is-a-persons-commit.md) (whose
change a commit is), [ADR-016](adr/adr-016-the-family-memory-is-read-as-cards.md)
(cards, ids and views), [ADR-017](adr/adr-017-the-memory-format.md) (the
format) and [ADR-018](adr/adr-018-a-cards-sources.md) (a card's sources).

All examples are made up, about the Simpsons.

## What is where

```
README.md                         a page about the repository, no card
index.md                          the home page, no card
ontology.toml                     the topics: their ids and labels per language
facts.toml                        facts written by hand, no card
family/                           the shared bucket: everyone may read it
  people/homer.md                 a person
  correspondents/duff-insurance.md
  documents/2026/10/2026-10-02-duff-insurance-policy-p72.md
  lists/shopping.md
  lists/archive/shopping/2026-09.md
  itchy-scratchy-land/topic.md    a topic, and what was filed into it
  itchy-scratchy-land/notes/2026/10/lisa-s-sax-lesson-7903f2.md
  itchy-scratchy-land/notes/2026/10/lisa-s-sax-lesson-7903f2.json
marge/                            Marge's bucket: only Marge may read it
  notes/2026/10/art-supplies-4c1d2e.md
  notes/2026/10/art-supplies-4c1d2e.json
bart/
  bookmarks/2026/10/krusty-burger-menu-5c4d3e.md
homer/
  diary/entries/2026/03/abe-s-birthday-lunch-3f9a0c.md
```

- **The first folder is the bucket, and it decides who may read a card.**
  The shared bucket (`family` unless the instance names another) is for
  everyone. A folder named after a person's id is that person's alone.
- **A folder inside a bucket that holds `notes`, `bookmarks`, `emails` or
  `documents` of its own is a topic.** Its card is `topic.md`, beside what
  was filed into the topic.
- **Records are filed by year and month** (`2026/10/`). A record without a
  date sits in `_unfiled/`.
- **A path is not a name.** A card moves when it is refiled into another
  topic or bucket. Refer to a card by its `card_id`, never by its path.
- `README.md` and `index.md`, wherever they are, are pages for browsing
  the repository, never cards.

Photos and recordings are not in the repository: see [Media](#media).

## A card

A card is one markdown file that stands for one thing: a letter, a note,
a person, a list. It starts with frontmatter, then the body:

```markdown
---
type: note
card_id: note-7f3a9c21e4
title: Art supplies for next time
description: Brushes and paper Marge wants for the next trip.
date: "2026-10-13"
persons:
  - marge
tags:
  - itchy-scratchy-land
  - watercolour
---

# Art supplies for next time

> **About** [Marge](../../../family/people/marge.md)

Brushes and paper for the next trip.

## Sources

> brushes + paper for next time
```

**The frontmatter** is flat: top-level keys, each holding a single value or
a list of strings. Writers write lists one item per line, as above; a
reader should also accept `tags: [a, b]` and `tags: []`, which Obsidian
and hand edits write. There are no nested maps. A field with no value is
left out, never written empty. A key has the same kind of value in every
file.

**The fields** are defined in `base.json` and the card type's schema. A
field neither names does not belong on the card.

**The body** has one shape for every type: the title as a heading, a meta
block of links derived from the fields (`> **About** ...`), the type's own
sections, and last the words the card was made from. A note's words are
under `## Sources` (see [A note's sources](#a-notes-sources)).

### The card id

Every card carries `card_id`: its type, a hyphen, and a value. It is made
once, when the card is first written, and never changes.

| Card | `card_id` |
|---|---|
| document, note, bookmark, email, diary | ten random hex digits: `note-7f3a9c21e4` |
| person, list | the file's name: `person-homer`, `list-shopping` |
| a list's month in its archive | the list's name and the month: `list-shopping-2026-09` |
| correspondent, topic | the file's or folder's name: `correspondent-duff-insurance`, `topic-itchy-scratchy-land` |

A card that became another type got a new id, and lists the ids it had
before in `former_card_ids`. A link to any of them still means this card.

A file with no frontmatter, or no `type`, was written by hand and is read
as a note.

## The card types

`type` is one of nine. `kind`, where a type has one, is one of a fixed set;
no `kind` means the first.

| `type` | `kind` | Lives in | Schema |
|---|---|---|---|
| `document` | | `<bucket>/[<topic>/]documents/` | `document.json` |
| `note` | | `<bucket>/[<topic>/]notes/` | `note.json` |
| `bookmark` | | `<bucket>/[<topic>/]bookmarks/` | `bookmark.json` |
| `email` | | `<bucket>/[<topic>/]emails/` | `email.json` |
| `diary` | `text`, `voice`, `image`, `video`, `file` | `<bucket>/diary/entries/` | `diary.json` |
| `list` | `todo`, `checklist`, `calendar`, `archive` | `<bucket>/lists/` | `list.json` |
| `person` | | `<shared>/people/<id>.md` | `person.json` |
| `correspondent` | | `<shared>/correspondents/<name>.md` | `correspondent.json` |
| `topic` | | `<bucket>/<topic>/topic.md` | `topic.json` |

Notes already keep where they came from in a sidecar. Documents,
bookmarks, emails and diary cards still keep it in their frontmatter
(`resource`, `capture_id`, `paperless_id`, `entry_id`, `timestamp`,
`model`, ...), until they move to the sidecar in their turn (ADR-018,
"Order"). Their schemas name those fields.

### document

A letter, a bill, a certificate: the text of a document in Paperless.

```markdown
---
type: document
card_id: document-3f9a0c1b2d
title: Duff Insurance policy
date: "2026-10-02"
correspondent: Duff Insurance
document_type: Policy
category: Insurance
persons:
  - homer
tags:
  - medical
  - car-insurance
paperless_id: 72
resource: "http://paperless.example/documents/72/details"
source: paperless
timestamp: "2026-10-02T09:14:00Z"
---

# Duff Insurance policy

> **From:** [Duff Insurance](../../../correspondents/duff-insurance.md) · **About:** [Homer](../../../people/homer.md)

The policy covers the car.
```

### note

What somebody sent or wrote down. The full example is under
[A note's sources](#a-notes-sources). A note written in the repository by
hand has no `## Sources` and no sidecar.

### bookmark

A web page somebody kept.

```markdown
---
type: bookmark
card_id: bookmark-5c4d3e2f1a
title: Krusty Burger menu
date: "2026-10-11"
persons:
  - bart
filed_by: bart
tags:
  - burgers
resource: "https://krusty.example/menu"
capture_id: $Bk2LmNoPqRsTuVwXyZ0123456789
timestamp: "2026-10-11T18:00:00Z"
---

# Krusty Burger menu

> **About** [Bart](../../../family/people/bart.md)

Why it was kept: the new shake.
```

### email

An email thread. `resource` is the `mid:` address of its first message.

```markdown
---
type: email
card_id: email-9e8d7c6b5a
title: School trip to the plant
date: "2026-10-09"
persons:
  - bart
resource: "mid:<trip-42@springfield-elementary.example>"
timestamp: "2026-10-09T07:30:00Z"
---

# School trip to the plant

> **About** [Bart](../../../people/bart.md)
> **Started** 2026-10-09 · **Kind** email
```

### diary

A diary entry, from a text, a voice message, a photo, a video or a file.
`kind` says which.

```markdown
---
type: diary
card_id: diary-5b0c1b2d3e
title: Abe's birthday lunch
date: "2026-03-15"
date_basis: spoken
persons:
  - homer
kind: voice
mode: monologue
duration_ms: 41000
entry_id: 3f9a0c1b2d
event_ids:
  - $Ev1AbCdEf
media:
  - /media/2026/03/Ev1AbCdEf.m4a
timestamp: "2026-03-15T12:00:00.123Z"
digest: 9f86d081884c7d65
---

# Abe's birthday lunch

We took Grampa to lunch.
```

`digest` is a hash of the card's text as it was compiled. A card whose
text no longer matches it was changed by a person, and the diary leaves it
as the person wrote it.

### list

A to-do list, a checklist, a calendar, or a month of a list's archive.

```markdown
---
type: list
card_id: list-packing
title: Packing
kind: checklist
topics:
  - camping
---

- [ ] Tent
- [x] Sleeping bags
```

Done items move each night into `lists/archive/<list>/<YYYY-MM>.md`, a
card with `kind: archive`. Everything in an archive is over, ticked or not.

### person

Somebody the memory knows. The file's name is the person's id, which every
other card uses in `persons`. `account` is their chat account, if they
have one.

```markdown
---
type: person
card_id: person-bart
title: Bart Simpson
aliases:
  - El Barto
account: bart
---

# Bart Simpson
```

### correspondent

A company, an office, anybody who sends documents. It is written the first
time a document names it.

```markdown
---
type: correspondent
card_id: correspondent-duff-insurance
title: Duff Insurance
aliases:
  - Duff Insurance Springfield
phone: "+1 555 0142"
website: https://duff-insurance.example
topics:
  - car-insurance
---

# Duff Insurance
```

### topic

A subject things are filed under. It is written the first time something is
filed into the topic.

```markdown
---
type: topic
card_id: topic-itchy-scratchy-land
title: Itchy Scratchy Land
---

# Itchy Scratchy Land
```

## Links, people and tags

**The frontmatter holds ids; the body holds links made from them.**
`persons: [marge]` on a card in `marge/notes/2026/10/` becomes
`> **About** [Marge](../../../family/people/marge.md)` under its title.
A program reads the frontmatter; people and link graphs read the body.

- **Links are relative markdown links ending in `.md`**, pointing at the
  file of the thing linked: a person at `<shared>/people/<id>.md`, a
  correspondent at `<shared>/correspondents/<name>.md`, any other card at
  its path. Obsidian, a git server's web view and the wiki all follow them.
- **The only other link form is a media embed**, `![[/media/...]]`,
  because markdown has no embed for audio.
- **`persons` holds person ids.** A name nobody in `people/` goes by stays
  a plain name in the text and is not in `persons`. There are no
  `Person: <name>` tags: `persons` says who.
- **`tags` holds topic ids and keywords.** A topic id comes from
  `ontology.toml` (`medical`), whose entries also give each topic's label
  per language (`Medical`, `Gesundheit`); a card stores the id, never the
  label. A topic card adds a topic of the household's own: its folder is
  the id (`itchy-scratchy-land`) and its title is the label, in every
  language. The ontology's ids come first, so a topic folder named like
  one of them (`medical`) adds no topic, and `stack memory check-format`
  lists it. A keyword is lowercase words joined by hyphens, with at least
  one letter (`car-insurance`, `watercolour`). Diary cards still carry
  topic labels as tags.

```toml
[topic.medical]
names = { de = "Gesundheit", en = "Medical" }
```

## A note's sources

A note keeps the words it was made from under `## Sources`, one blockquote
per thing it was made from, in order. Everything above them is the
interpretation: a clean version of the words, or a summary.

```markdown
---
type: note
card_id: note-967b020f7e
title: Lisa's saxophone lesson moved to Thursday
date: "2026-10-22"
persons:
  - lisa
tags:
  - music
---

# Lisa's saxophone lesson moved to Thursday

> **About** [Lisa](../../../../people/lisa.md)

Lisa's saxophone lesson moved to Thursday, 4 pm.
- Price: 30 dollars (raised from 25)
- Pay Mr. Largo in cash.

## Sources

> lisas sax lesson moved to thu 4pm, 25$ pay mr largo cash

> its 30 now, he raised it
```

A photo, a recording or a file is embedded or linked at the top of its
quote, followed by its caption or its transcript:

```markdown
## Sources

> ![[/media/2025/03/9ppnpSTkuiY9CsuUNyx73bGrhXcUmddM0KjFbpvpfqE.m4a]]
>
> Hey Bart, the cast is off. Your arm looks like a noodle.
```

## The sidecar

Beside a note with sources is its sidecar: the same name, ending in `.json`
instead of `.md`.

```
family/itchy-scratchy-land/notes/2026/10/lisa-s-sax-lesson-7903f2.md
family/itchy-scratchy-land/notes/2026/10/lisa-s-sax-lesson-7903f2.json
```

```json
{
  "card_id": "note-967b020f7e",
  "filed_by": "marge",
  "sources": [
    {
      "role": "original",
      "person": "marge",
      "time": "2026-10-21T08:55:00+02:00",
      "channel": "chat",
      "kind": "text",
      "id": "$Jn4qmFFnVqBMyoUJ3vcmPRc7SoqwUf_uMZfp7zq297M"
    },
    {
      "role": "reply",
      "person": "homer",
      "time": "2026-10-21T09:10:00+02:00",
      "channel": "chat",
      "kind": "text",
      "id": "$Hk2pQ9xLmW0aZr3T5yUvBn8cDeFgHiJkLmNoPqRsTu"
    }
  ],
  "made": {
    "at": "2026-10-21T07:12:03Z",
    "model": "qwen3",
    "code_commit": "50efe696",
    "prompt_template": "7c2e91a4",
    "requested_by": "marge"
  }
}
```

- **Quote n under `## Sources` is `sources[n]`.** The first quote above is
  Marge's message, the second Homer's reply.
- **`sources` says who sent each one, when, how, and what it was;** `made`
  says how the rest of the card was made from them. `sidecar.json` defines
  every field and the values each may take.
- **A source's `id` finds it again**: a chat message's event id, an email's
  message id, `paperless:<id>`; where the channel has no ids of its own
  (`cli`), one made up for it (`cli:9f2c41d07a6b3e58`).
- **A binary is named by its hash**: `"media": "sha256:4168fe99..."`, the
  hash of the file the quote embeds (see [Media](#media)). `text` says which
  text form of it the quote shows, such as `{"form": "transcript"}`.
- **`"reconstructed": true` marks a source rebuilt from a note filed before
  sidecars existed**, where the old card recorded something badly: a reply
  without its time or id (`"time": "unknown"`, `"id": "unknown"`), a photo
  the media store has no record of, words with prompt text in them. Only
  such a source may have the time `unknown`.
- **Sources are only appended.** A correction is a new source (`reply`, or
  `note` for one given to a refile), never a change to an old one.
- **Card and sidecar change together, in one commit.**

`made.correction_template` is present when a refile worked corrections in.
A rebuild with the card conversation (ADR-018 §7) may record
`conversation_template` instead.

## Media

Photos, recordings and files are kept outside the repository, in the
memory's media store (`memory/media/` in the instance's data folder), and
backed up with it. The file is named after the chat message it came in (its event id,
without the `$`), and a card embeds it by its path from the store's root,
`![[/media/2026/10/Jn4qmFFnVqBMyoUJ3vcmPRc7SoqwUf_uMZfp7zq297M.jpg]]`:

```
media/2026/10/Jn4qmFFnVqBMyoUJ3vcmPRc7SoqwUf_uMZfp7zq297M.jpg     the file as it was sent
media/2026/10/Jn4qmFFnVqBMyoUJ3vcmPRc7SoqwUf_uMZfp7zq297M.json    what the store knows of it
```

```json
{
  "event_id": "$Jn4qmFFnVqBMyoUJ3vcmPRc7SoqwUf_uMZfp7zq297M",
  "room_id": "!kitchen:springfield.example",
  "sender": "@marge:springfield.example",
  "filename": "IMG_0412.jpg",
  "mime": "image/jpeg",
  "size": 1843201,
  "sha256": "4168fe9973e0031ca1a1b8c445d653bc3f7c56a985eea2093c070ccc5cd8f008",
  "captured_at": "2026-10-21T06:55:00+00:00",
  "stored_at": "2026-10-21T06:55:04+00:00",
  "kind": "image",
  "source": "capture",
  "derived": []
}
```

The store's `sha256` is the hash a note's sidecar gives as `media`, so a
note's binary is found by its hash even if it moves. `derived` lists the
copies made for showing it (audio a browser plays, a smaller image). A
clone of the repository on another computer has the links without the
files.

## Views: pages that are not cards

The wiki shows pages compiled from the cards: a person's page, a
correspondent's, a topic's, the diary's front, year and month pages, the
home page and folder listings. They live in a second repository, the
brain, never in the memory, and are never cards. A view has `view` in its
frontmatter instead of `type`, and no `card_id`:

```markdown
---
view: person
title: Bart Simpson
about: person-bart
derived_from:
  - person-bart
  - note-967b020f7e
compiled_from:
  - person-bart@6d1f0e2a9c4b7d3e8f1a2b3c4d5e6f7a8b9c0d1e
  - note-967b020f7e@0a1b2c3d4e5f60718293a4b5c6d7e8f901234567
---
```

| Field | Meaning |
|---|---|
| `view` | `person`, `correspondent`, `topic`, `diary-front`, `diary-year`, `diary-month`, `home` or `folder` |
| `about` | the card it is about, when it is about one |
| `derived_from` | the cards it was compiled from |
| `compiled_from` | each of them with the version compiled, `<card id>@<git blob id>`; a card whose file has another blob id now is one the view is behind |

In the brain, a person's view is `<id>/about.md`, a topic's is `about.md`
in the topic's folder, a correspondent's is `about.md` in a folder named
after its card, beside it (`correspondents/duff-insurance/about.md`), the
diary's are `diary/about.md`, `diary/<year>/about.md` and
`diary/<year>/<month>.md` in the shared bucket, and a folder's listing is the
folder's `index.md` (`notes/index.md`); the home page is the root
`index.md`. A card keeps its own path in the brain, as the memory holds it.
A correspondent's view shows what its card says below the title, above
the documents from the correspondent.

A person's view also carries `canonical` and `synonyms`. A view a model
wrote keeps the model's last answer in a comment below the compiled part
(`<!-- answer: ... -->`). Older views say `generated: true` instead of
`view`. The home page shares its path with the welcome the memory is
seeded with, keeps that text and fills only what is between
`<!-- begin: generated -->` and `<!-- end: generated -->`.

## History

The repository's git log is the history of every card. The author of a
commit is the person the change is by or for; a bot that writes it for
them is the committer (ADR-015). The subject starts with what happened:

| Subject | What happened |
|---|---|
| `capture: <title>` | a new card from what somebody sent, or a new note written with `stack memory write` |
| `learn: <title>` | a document or a diary entry filed |
| `update: <title>` | a filing that grew: an email thread, a document, the diary written again |
| `reply: <title>` | a reply added to a note as a source |
| `reprocess: <title>` | a card made again from its sources; `Requested-By:` names who asked, `Replaces:` the commit it replaces. Authored by a person when they gave a correction with it |
| `correct: <title>` | a person's correction of a diary card, applied by a bot |
| `edit: <title>` | a person changed fields through the card editor |
| `chore(memory): <person> ...` | a person changed a file with `stack memory write` |
| `chore(lists): <person> ...` | a person changed a list with `stack memory list` |
| `people: ...` | the people's files changed |
| `forget: ...` | diary entries removed |
| `transcribe: <title>` | a better transcript of a recording |
| `declare: <path>` | a correspondent's or a topic's card written the first time |
| `rename: <from> → <to>` | a record moved |
| `lists: ...` | items put on a list from a record, or archived |
| `cards: ...`, `notes: ...`, `chore(ontology): ...` | a format migration; the `Migration:` trailer names it (`memory/0008`) |
| `seed: ...` | a new memory's first content |
| anything else, by a person | a change made by hand, in a git server's editor or a clone |

The list is closed: famstack writes no other subject. A commit is the
machine's own work when its author is a bot or the admin, or when it is
one of `transcribe:`, `declare:`, `rename:`, `lists:`, a migration or a
seed, whoever it names; so is the diary written again (`update:`,
committed by the memory bot).

You may change a card in any editor. The change is your commit. When
famstack makes a note again from its sources, it reads your commit and
keeps what you changed: the fields, and the words above `## Sources`. A
change to a quote under `## Sources` is read as your correction: the
quote goes back to the words as they were sent, and your words are added
below it as a source of yours. A diary card you changed is left as you
wrote it. Documents, bookmarks and emails do not keep a hand edit through
a reprocess yet. Leave the sidecar to famstack, and do not move or
rename a card: this version does not support it, and the card would
lose the sidecar beside it.

## Checking a memory

`stack memory check-format` reports every place where a memory does not
meet this format, including each sidecar value its schema does not allow.
An empty report means the memory meets it.

Without famstack, any JSON Schema validator (draft 2020-12) checks a
sidecar against `sidecar.json` as it is. The card schemas refer to one
resource no file holds, `urn:famstack:vault`, for what each memory decides
for itself: `person_id`, the file names under `<shared>/people/`;
`topic_id`, the topic ids in `ontology.toml` and the folders of the topic
cards; and `tag`, a topic id or a keyword. Give the validator that
resource and it checks a card's frontmatter as the memory stacklet does.

Other commands that read or change the memory: `stack memory find`,
`stack memory card`, `stack memory card edit`, `stack memory write`,
`stack memory reprocess` and `stack memory history`. Their `--help` says
what each does.
