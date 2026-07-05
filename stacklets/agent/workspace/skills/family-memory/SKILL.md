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

## Ticking todos off (and undoing)
When someone says they finished or handled one of a topic's todos, I tick it off
for them, right away. I don't ask permission first, because it is easy to undo.

  `stack memory topic <topic> todo strike "<start of the item>" --by <their handle>`

- I identify the item by its **start**, so a few words are enough
  ("Sonnencreme", not the whole line). The open items are in my briefing, or
  `stack memory topic <topic> todo` lists them.
- `--by` is the person I am replying to: the `@handle` from "You are speaking
  with ..." in my briefing.
- If it answers "more than one match" and lists items, I tell them the options
  and ask which one, then strike with a string that picks just one.
- I use `unstrike` (same item) to undo. Each change commits to the family's store
  as that person, so it is theirs and shows up everywhere. I relay what I did in
  one short line ("Ticked off: Sonnencreme aufladen").

I answer only from what I actually read, and I keep it short.
