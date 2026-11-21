# Correspondents

One markdown page per organization the family corresponds with: the
bank, the kids' school, the insurance company, an online service. Each
page is a card of type `correspondent`.

Pages live here, in the shared bucket's `correspondents/` folder
(typically `family/correspondents/`). The slug is configurable with
`stack.toml [core] shared_bucket`. The file stem is the correspondent's
identifier (`duff-insurance.md`). The frontmatter is the machine view.
The archivist reads it on startup to name new correspondents before they
reach Paperless.

## Shape

```markdown
---
type: correspondent
card_id: correspondent-duff-insurance
title: Duff Insurance
aliases:
  - "Duff Insurance Ortsverband Springfield"
  - "Duff Insurance Versicherung AG"
topics: [insurance, vehicle]
address: "Hansastraße 19, 80686 München"
website: "https://www.duff-insurance.example"
---

# Duff Insurance

Notes written by hand stay as they are.
```

- `type`, `card_id` and `title` are required. `card_id` is made once and
  never changes.
- `aliases` lists the other names for this correspondent: the ones the
  classifier gave when the page was made, and the ones you add.
- `address`, `website`, `phone`, `email` and `topics` are optional.
  `topics` holds topic ids.
- Frontmatter holds plain values. Write `[[wiki links]]` in the body, not
  in the frontmatter.
- The schema is `stacklets/memory/schemas/cards/correspondent.json`. A
  field it does not name is refused.

## How pages get here

- A document names a new correspondent. The classifier returns
  `correspondent_aliases`. The archivist writes a page for a new name,
  with those aliases. It never changes a page that exists. Add aliases
  by hand.
- Edit any page in the Forgejo web UI or in a local clone. The commit
  appears in the memory repo's history.
