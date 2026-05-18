# Correspondents

One markdown page per organization the family corresponds with — your
bank, the kids' school, the insurance company, an online service.

Pages live here, in `documents/correspondents/` (the documents-domain
folder at the vault root, sibling to `raw/` and `wiki/`). The file
stem is the correspondent's identifier (`adac.md`, `aok.md`). The
frontmatter is the machine view — the archivist reads it on startup
to canonicalize new correspondents before they hit Paperless.

Two things this layout buys us:

- **Domain scope.** Correspondents belong to the documents pipeline.
  Putting them under `documents/` leaves room for future domain
  peers (`chat/`, `calendar/`, ...) without conceptual collisions.
- **Wiki immunity.** The olw container regenerates `wiki/*.md` from
  `raw/` and only ever reads `raw/`. Living outside both keeps
  hand-curated correspondent pages sacrosanct.

## Shape

```markdown
---
kind: correspondent
canonical: ADAC
aliases:
  - "ADAC Ortsverband Manzell"
  - "ADAC Versicherung AG"
topics: [insurance, vehicle]
address: "Hansastraße 19, 80686 München"
website: "https://www.adac.de"
---

# ADAC

> Notes:
> Hand-write anything here — kept across rebuilds.

<!-- begin: generated -->
## Topics
[[insurance]], [[vehicle]]

## Documents
- 2024-03-15 [[ADAC - Kfz-Versicherung 2024]]
- 2025-03-12 [[ADAC - Kfz-Versicherung 2025]]
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
