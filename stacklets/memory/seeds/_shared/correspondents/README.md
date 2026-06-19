# Correspondents

One markdown page per organization the family corresponds with — your
bank, the kids' school, the insurance company, an online service.

Pages live here, in the shared bucket's `correspondents/` folder
(typically `family/correspondents/` — the slug is configurable via
`stack.toml [core] shared_bucket`). The file stem is the
correspondent's identifier (`duff-insurance.md`, `springfield-mutual.md`). The frontmatter
is the machine view — the archivist reads it on startup to
canonicalize new correspondents before they hit Paperless.

Two things this layout buys us:

- **Bucket symmetry.** The shared bucket holds institutional artifacts
  (documents, correspondents). Personal entities (homer, marge, …)
  follow the same shape under their own folders.
- **Wiki immunity.** The wiki engine regenerates `wiki/*.md` from
  raw sources and only ever reads them. Living outside both keeps
  hand-curated correspondent pages sacrosanct.

## Shape

```markdown
---
kind: correspondent
canonical: Duff Insurance
aliases:
  - "Duff Insurance Ortsverband Springfield"
  - "Duff Insurance Versicherung AG"
topics: [insurance, vehicle]
address: "Hansastraße 19, 80686 München"
website: "https://www.duff-insurance.example"
---

# Duff Insurance

> Notes:
> Hand-write anything here — kept across rebuilds.

<!-- begin: generated -->
## Topics
[[insurance]], [[vehicle]]

## Documents
- 2024-03-15 [[Duff Insurance - Kfz-Versicherung 2024]]
- 2025-03-12 [[Duff Insurance - Kfz-Versicherung 2025]]
<!-- end: generated -->
```

Frontmatter holds plain values (Dataview-compatible). Use `[[wiki
links]]` in the body, not in frontmatter. Only the
`<!-- begin: generated --> ... <!-- end: generated -->` block is
intended for automated rewrites; everything outside it is yours to
edit.

## How pages get here

- New documents flow in; the classifier returns `correspondent_aliases`
  on its way to Paperless. Aliases discovered this way are folded into
  the correspondent's `aliases:` list on the next maintenance pass.
- Hand-edit any page in the Forgejo web UI or a local Obsidian clone
  — commit shows up in the memory repo's history.
