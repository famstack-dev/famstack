# Memory

The family's curated knowledge vault. One repository, several layers,
all plain text on disk.

This repo is meant to be cloned into [Obsidian](https://obsidian.md/)
or browsed through Forgejo's web UI. The git history *is* the
learning history — revert anything that got out of hand.

## Layout

```
ontology.toml          classifier seed (topics, types, synonyms)
facts.toml             household facts (people, services, dates)

documents/             documents-domain reference data
  correspondents/      hand-curated sender pages (banks, schools, ...)
    README.md          the shape and conventions
    adac.md            one file per organization
    aok.md
    ...

raw/                   the archivist writes here
  YYYY/MM/             classified documents from Paperless + captures
    2026-05-15-adac-rechnung-p247.md
    2026-05-17-reddit-llms-a7b3c2.md
  _unfiled/            entries with no usable date

wiki/                  the LLM wiki writes here (Phase 2: olw)
  ADAC.md              concept pages, regenerated from raw/
  LLMs.md
  ...

.olw/                  wiki engine state (sqlite, content hashes)
```

The `documents/` folder is reserved for documents-domain reference
data. Future domains (chat, calendar, contacts) get their own sibling
folders if and when they need hand-curated layers; nothing is forced
there preemptively.

## Who writes what

- **You.** Edit `ontology.toml`, `facts.toml`, and any
  `documents/correspondents/*.md` page directly. The commit log is
  your audit trail. Hand edits in `raw/` survive until a re-classify
  pass.
- **archivist-bot** writes `raw/`: one Markdown file per Paperless
  document (`*-p<id>.md`) or capture (`*-<hash>.md`). Frontmatter
  carries the classifier's structured take.
- **olw** (in the memory stacklet's container, Phase 2) reads `raw/`
  and writes `wiki/`. The wiki is regenerated; treat it as
  derived output. Hand edits there get overwritten on the next pass.

## Conventions

- One concept per file. Filename is the slug; canonical name lives
  in frontmatter.
- Frontmatter is the machine view (Dataview-compatible plain values).
- Wiki links (`[[ADAC]]`) belong in the body, not in frontmatter.
- Generated regions in correspondent pages are bracketed
  `<!-- begin: generated --> ... <!-- end: generated -->`; hand
  edits outside those brackets are preserved.

## Why correspondents live outside `raw/` and `wiki/`

The olw container only touches `raw/` (reads) and `wiki/` (writes).
Putting `documents/correspondents/` at the vault root keeps it out of
olw's reach — olw has no exclude config, so this layout is the seam.
Correspondents are also conceptually a documents-domain concern
(senders of mail), so the `documents/` namespace is where they
belong, not at the very top.
