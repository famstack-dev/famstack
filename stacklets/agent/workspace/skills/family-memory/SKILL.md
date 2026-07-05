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
- To find something across the family's notes, bookmarks and documents, run:
  `stack memory search "<keywords>"`
  It prints dated, attributed results with a snippet of the matching line. Add
  `--paths` for just the file paths, `--limit N` to cap results, or
  `--scope family/<topic>` to look **within one topic first**. When the question
  is about the topic I am in, I scope to it and widen only if that finds nothing.
- To read a full page, `read_file` on `vault/<path>`. Search prints paths
  relative to the vault, so `homer/about.md` is read as `vault/homer/about.md`.
- For the **full source document** behind a briefing (a scanned letter, a PDF),
  the vault page's frontmatter carries a `paperless_id`. I fetch the original
  body with `stack docs show <id> --content` — only when the briefing itself is
  not enough, since the source can be long.

## Where things live
- A person: `vault/<name>/about.md` (a full profile).
- A shared topic or plan: `vault/family/<topic>/about.md`, with its open items in
  `vault/family/<topic>/todos.md`.

## Changing todos (add, tick off, undo)
When someone asks to add something to a topic's list, or says they finished one
of its todos, I do it right away. I don't ask permission first, because it is
easy to undo.

  `stack memory topic <topic> todo add    "<item>"          --by <their handle>`
  `stack memory topic <topic> todo strike "<start of item>" --by <their handle>`

- **add** appends the item (and starts the list if the topic has none). I use it
  for "add X", "remind us to X", "put X on the list".
- **strike** ticks an item off. I name it by its **start**, so a few words are
  enough ("Sonnencreme", not the whole line). The open items come from
  `stack memory topic <topic> todo` (my briefing only says a list exists, not
  what is on it) so I run that when I need to know or list them.
- `--by` is the person I am replying to: the `@handle` from "You are speaking
  with ..." in my briefing.
- If strike answers "more than one match" and lists items, I give them the
  options and ask which one, then strike with a string that picks just one.
- `unstrike` (same item) undoes a strike. Each change commits to the family's
  store as that person, so it is theirs and shows up everywhere. I relay what I
  did in one short line ("Added: Zelt einpacken").

I answer only from what I actually read, and I keep it short.
