# ADR-017: The Memory Format

## Status
Proposed. Replaces the file format in `docs/design/brain/vault-format.md`
(v1) once accepted; that note stays the record of v1.

## Context
The family memory is two git repos (ADR-011). The **memory repo**
(`family/memory`, cloned at `<data>/memory/vault`) is the source, written by
the bots and by people. The **brain** (`family/brain`) is the memory repo
plus every compiled page, rendered by the wiki, and can be rebuilt at any
time. A **card** is a file in the memory repo that stands for one thing; a
**page** is compiled from one or more cards, lives in the brain and is not a
card (ADR-016).

The memory repo is meant to be readable without famstack: in Obsidian, in
Forgejo, by any tool that reads markdown with frontmatter, and as an Open
Knowledge Format (OKF) bundle. That is claimed, not checked, and the files
written today break it in several places (all checked on a test instance):

- **People are linked two ways.** Records link a person to a compiled page
  in the brain (`homer/about.md`), slugged from the display name, which does
  not exist in the memory repo; lists link the person's file
  (`family/people/homer.md`), which the wiki answers with a 404. In Obsidian
  the first is a dead link, and `Grampa` links to a page nobody wrote.
- **Correspondents exist only in the brain.** Records link
  `correspondents/<slug>.md`, nothing writes that file in the memory repo, and
  the archivist reads it from there.
- **`persons` holds display names** (`Homer`), and tags repeat them as
  `Person: Homer`, which Obsidian rejects as a tag (no spaces, no colons).
  Topic tags are localized labels (`Gesundheit`), so two instances in two
  languages tag the same thing differently.
- **Navigation pages carry no `type`**, and list archives carry no `kind`, so
  a calendar's archive reads as a to-do list.

## Decision

### 1. The memory repo is the portable bundle
Obsidian and OKF v0.2 compatibility is claimed for the memory repo and
checked on it. The brain follows the same rules per file, with the
deviations the wiki needs (§9).

### 2. Ids
Every card carries `card_id` (ADR-016). The ids of a card's source stay
next to it, each meaning what it means today: `paperless_id` joins
Paperless, `capture_id` is the Matrix event a note was filed from,
`entry_id` names a diary entry.

### 3. Links
- **Relative markdown links with `.md`** in the memory repo:
  `[Homer](../../people/homer.md)`. It is the one form Obsidian (backlinks,
  graph, updated on rename), Forgejo, OKF and the wiki all follow.
- **A link points at the thing's own file in the memory repo**: a person at
  `<shared>/people/<id>.md`, a correspondent at
  `<shared>/correspondents/<slug>.md`, a record at its path. The wiki maps
  those files to their compiled pages in one place.
- **Wikilinks only for media embeds** (`![[/media/...]]`): markdown has no
  audio embed, and both Obsidian and the wiki play audio from it.
- **Frontmatter holds ids, the body holds links, and the writer derives the
  second from the first.** `persons: [homer]` and `**About:** [Homer](...)`.
  Machines read the frontmatter; graphs and people read the body.
- Brain pages may use root-absolute links (`/family/lists/dates.md`), since
  nothing edits them there.

### 4. People and tags
- `persons` holds person ids (`homer`), resolved from the classifier's names
  when the record is written.
- No person tags: `persons` already says who.
- `tags` holds topic slugs and the classifier's keywords, lowercase with
  hyphens. A topic tag is the ontology's id (`medical`); its label in the
  household's language comes from `ontology.toml`.

### 5. `type` and `kind`
`type` is a closed vocabulary, one for cards and one for pages.

Cards, in the memory repo:

| `type` | `kind` (absent means the first) | Class (§7) |
|---|---|---|
| `document` | | record |
| `note`, `bookmark`, `email` | | record |
| `diary` | `text`, `voice`, `image`, `video`, `file` | record |
| `list` | `todo`, `checklist`, `calendar`, `archive` | state |
| `person`, `correspondent`, `topic` | | state, declared once, never rewritten |
| `page` | | state: anything a person writes by hand |

Pages, in the brain, all with `generated: true`, `cites` (the card ids they
were compiled from) and, when about one thing, `about` (its card id):

| `type` | `kind` | About |
|---|---|---|
| `person-page`, `correspondent-page`, `topic-page` | | the person, correspondent or topic |
| `diary-page` | `front`, `year`, `month` | |
| `index` | `home`, `folder` | |

A page never carries a card type, so no reader takes it for a card. Reserved
for later: `pet`, `vehicle`, `place`.

`kind` is a fixed variant that changes behaviour or rendering, checked like
an enum. `document_type` stays: it is an open classification the household
extends in Paperless and the classifier fills, which a fixed set cannot
check.

### 6. One grammar, one parser, one type per key
- Frontmatter is top-level keys, scalars and one-deep string lists. Writers
  write lists in block style; the parser also reads a one-line flow list
  (`tags: [a, b]`, `tags: []`), because Obsidian and hand edits write it. A
  flow map, a block scalar or a nested map fails loudly.
- Every reader parses through `stack.frontmatter` (true today; it stays the
  rule).
- A key has one type in every file, because Obsidian types a property per
  key for the whole vault. The memory repo ships `.obsidian/types.json` with
  those types, so every laptop agrees (an `entry_id` of digits stays text).
- An optional field with no value is left out, never written empty.

### 7. Who changes what
| Class | Types | Rule |
|---|---|---|
| Record | `document`, `note`, `bookmark`, `email`, `diary` | Derived from a source and regenerated from it. A person's change is a commit and is applied again on every regenerate (ADR-015). |
| State | `list`, `person`, `correspondent`, `page` | The household's. Machines change only the lines they are about, or create a missing file and never rewrite it. |
| Page | everything with `generated: true` | Brain only, rebuilt at will, never a card. A page that shares its path with a card (a correspondent, the root `index.md`) keeps the card's text and fills only the region between `<!-- begin: generated -->` and `<!-- end: generated -->`. |

### 8. Correspondents and topics are declared like people
The archivist writes `<shared>/correspondents/<slug>.md` the first time a
document names a correspondent, with `type`, `title` and the aliases the
classifier returned, and never rewrites it. A topic gets
`<bucket>/<slug>/topic.md` the same way, the first time something is filed
into it.

### 9. Compatibility
**Obsidian.** Deliberate deviations: no nested properties (Obsidian's
Properties editor cannot edit them); `timestamp` is an ISO datetime with
`Z`, typed text so an edit cannot drop the offset; media embeds as above;
per-laptop state (`.obsidian/`) stays out of git except `types.json`.

**OKF v0.2.** Holds after the migration: `type` on every file, `title`,
`description`, `resource`, `tags` as a list, links as the graph, unknown
keys allowed. Deviations: the memory repo stays flat, without OKF's
`generated`, `verified` and `sources` maps, because people edit these files
and Obsidian cannot edit maps; an exporter can derive them. An OKF key is
never used with another shape (the planned `sources` on person pages is
named `cites`). The root `index.md` carries `okf_version: "0.2"`. The brain
is not an OKF bundle: the wiki needs its index pages.

### 10. Media stays out of git
Photos and recordings live in `<data>/memory/media`, outside both repos,
backed up with the data dir. On the server, a git-ignored link
`vault/media -> ../media` lets Obsidian there play them. A clone on a laptop
shows the links without the files. Media in git (LFS) would make every clone
complete and every clone large.

## Order
1. A format check (`stack memory check-format`): every file
   parses, has a `type` from §5, passes its fields and enums, every tag is
   valid, every relative link resolves, every `card_id` is unique.
2. One migration, together with ADR-016 step 1: `card_id` on every card,
   person links to the person's file, `persons` as ids, declared
   correspondents and topics, tags.
3. `kind` on archives and diary cards; the page types, `about` and `cites`
   on every compiled page; `types.json`; the root `index.md`.

## Verification
- The format check over the test instance's memory repo: no errors. Over
  its brain: no errors beyond §9.
- The migration run twice on a copy: one commit the first time, nothing the
  second.
- The memory repo opened in Obsidian: Homer's file lists every record about
  him as a backlink, `[[Grampa]]` opens Abraham's file, the tag pane holds no
  `Person:` tags, a renamed note keeps its links, and an edit in the
  Properties editor still parses.
- The card browser (ADR-016) shows one card per person and no links to
  nothing.

## Consequences
- The memory repo reads the same in Obsidian, Forgejo and the wiki, and
  outlives famstack as plain files.
- One migration touches every record. It runs once per instance (the
  migrations of `stack up`) and is one commit in the memory repo's history.
- A renamed person or correspondent file keeps its `card_id`, so chat links
  survive; links inside the memory repo follow the rename where Obsidian
  made it.

## Considered
- **No common id, each type's own field.** Rejected in ADR-016: hand-written
  notes and correspondents would have no id, and a rename would break chat
  links.
- **Wikilinks for people and topics.** Forgejo and OKF do not read them, and
  they resolve by name, so two files named alike collide.
- **Wikilinks inside properties** (`persons: ["[[homer]]"]`). Obsidian counts
  them; every other reader gets a bracketed string.
- **A fingerprint on every record**, so a writer rewrites only a file
  nobody edited. It freezes what it protects; ADR-015 keeps a person's
  change in git instead.
- **`kind` in place of `document_type`.** One key name saved, a migration and
  every wiki reader paid, and the enum check lost.
