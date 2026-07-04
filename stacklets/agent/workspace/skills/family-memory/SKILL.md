---
name: family-memory
description: How I look things up about the family. I use this before answering anything about a person, topic, plan, or the family.
metadata: {"nanobot": {"always": true}}
---
# Looking things up

Everything I know about the family is markdown in my workspace under `vault/`
(read-only). I do not know it from memory. Before I answer anything about a
person, a topic, a plan, or the family, I look it up there first. I never say "I
don't know" or "your profile is blank" without searching `vault/` first.

## Where things live
- A person: `vault/<name>/about.md` — a full profile (who they are, interests,
  health, routines). The names are the folders directly under `vault/`.
- A shared topic or plan: `vault/family/<topic>/about.md`; its open items:
  `vault/family/<topic>/todos.md`.
- Someone's own notes and bookmarks: under `vault/<name>/notes/` and
  `vault/<name>/bookmarks/`.

## How
- To find something, `grep` for the keywords inside `vault/`.
- To read a page, `read_file` on its path.

I answer only from what I actually read, and I keep it short.
