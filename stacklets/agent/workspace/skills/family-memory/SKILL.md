---
name: family-memory
description: How I look things up about the family. I use this before answering anything about a person, topic, plan, or the family.
metadata: {"nanobot": {"always": true}}
---
# Looking things up

Everything I know about the family is in the vault. I look it up before I answer
anything about a person, a topic, a plan, or the family. I never say "I don't
know" or "your profile is blank" without searching first.

## Find, then read
- To find something across all the family's notes, bookmarks and documents, run:
  `stack memory search "<keywords>"`
  It prints dated, attributed results with a snippet of the matching line. Add
  `--paths` for just the file paths, or `--limit N` to cap the results.
- To read a full page, `read_file` on `vault/<path>`. Search prints paths
  relative to the vault, so `homer/about.md` is read as `vault/homer/about.md`.

## Where things live
- A person: `vault/<name>/about.md` (a full profile).
- A shared topic or plan: `vault/family/<topic>/about.md`, with its open items in
  `vault/family/<topic>/todos.md`.

I answer only from what I actually read, and I keep it short.
