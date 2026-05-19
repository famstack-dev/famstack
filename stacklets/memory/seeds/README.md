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

family/                shared bucket (slug configurable via
                       stack.toml [core] shared_bucket; "family" is
                       the default)
  documents/           Paperless documents the archivist writes here
    YYYY/MM/
      2026-05-15-adac-rechnung-p247.md
    _unfiled/          entries with no usable date
  correspondents/      hand-curated sender pages (banks, schools, ...)
    README.md          the shape and conventions
    adac.md            one file per organization
    aok.md

homer/                 personal entity bucket — one per family member
  notes/               pasted-text captures (Matrix `note` flow)
    YYYY/MM/
      capybara-field-notes-d2b9cb.md
  bookmarks/           URL captures (Matrix `bookmark` flow)
    YYYY/MM/
      git-extracting-single-files-280cd1.md
marge/
bart/
lisa/

wiki/                  the wiki engine writes here (Phase 2)
  homer/               per-entity compiled view
  family/              shared compiled view
```

Each entity (a family member, or the shared `family` bucket) follows
the same shape — `<entity>/<kind>/...`. The deriver compiles a wiki
view per entity by reading its slice plus cross-references from the
other entities.

## Who writes what

- **You.** Edit `ontology.toml`, `facts.toml`, and any
  `<shared_bucket>/correspondents/*.md` page directly. The commit
  log is your audit trail. Hand edits in writer paths survive until
  a re-classify pass.
- **archivist-bot** writes documents under
  `<shared_bucket>/documents/` and captures under
  `<entity>/notes/` or `<entity>/bookmarks/`. Frontmatter carries
  the classifier's structured take.
- **wiki engine** (Phase 2) reads the entity buckets and writes
  `wiki/`. The wiki is regenerated; treat it as derived output.

## Conventions

- One concept per file. Filename is the slug; canonical name lives
  in frontmatter.
- Frontmatter is the machine view (Dataview-compatible plain values).
- Wiki links (`[[ADAC]]`) belong in the body, not in frontmatter.
- Generated regions in correspondent pages are bracketed
  `<!-- begin: generated --> ... <!-- end: generated -->`; hand
  edits outside those brackets are preserved.
