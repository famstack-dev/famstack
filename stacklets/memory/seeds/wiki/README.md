# Memory

This is the family memory — the curated knowledge layer of the stack.

The repository is structured for [Obsidian](https://obsidian.md/)-style
navigation. Each entity (a person, a correspondent, a topic, a story)
gets its own markdown file under a domain bucket:

```
family/        # household-wide entities
<person>/      # one folder per household member
meta/          # synthesised indexes
```

Entity pages use YAML frontmatter for classification:

```markdown
---
kind: correspondent     # correspondent | person | topic | asset | story
aliases: ["AOK", "AOK Berlin"]
topics: ["insurance", "medical"]
---

# AOK
```

## How knowledge gets here

- The **memory stacklet** pushes initial seeds on first install.
- The **Stacker install interview** (Phase 3) seeds `facts.toml` from a
  short Q&A and creates stub entity pages for everyone in the household.
- The **Archivist bot** writes L1 document mirrors and (Phase 5)
  synthesises entity Timelines from them.
- **You** can edit any file directly — in the Forgejo web UI, by
  cloning this repository into Obsidian, or via `stack facts ...`.

Everything is a markdown or TOML file. The git history is the learning
history. Revert anything that got out of hand.
