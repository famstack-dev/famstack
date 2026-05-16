# Correspondents

One markdown page per organization the family corresponds with — your
bank, the kids' school, the insurance company, an online service.

Pages live here, in `wiki/correspondents/`. The file stem is the
correspondent's identifier (`adac.md`, `aok.md`). The frontmatter is
the machine view — the archivist reads it on startup to canonicalize
new correspondents before they hit Paperless.

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
overwritten by `stack memory wiki-rebuild` (coming in 0.4.0). Everything
else is yours to edit.

## How pages get here

- New documents flow in; the classifier returns `correspondent_aliases`
  on its way to Paperless. The wiki-rebuild aggregates these into
  alias lists on the correspondent's page.
- Hand-edit any page in the Forgejo web UI or a local Obsidian clone
  — commit shows up in the memory repo's history.
