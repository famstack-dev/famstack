---
name: family-memory
description: How I look things up about the family and edit their lists.
metadata: {"nanobot": {"always": true}}
---
# Family memory (rules as pseudocode)

Vault = all family knowledge. Look before answering.

```
LOOKUP:
  brief names the page        -> read_file(page)          # no search
  else                        -> memory_search(2-4 literal keywords)
     scope family/<topic> first; miss -> widen
     independent lookups      -> ONE call, queries=[..]   # max 3
     miss                     -> retry once, new keywords; then say tried + ask
  profile                     -> memory_person(name)
  "lately|since when|who did" -> memory_history           # search ranks NOW, not change
  full source document        -> paperless_id frontmatter + `stack docs show <id> --content`

PATHS:
  person -> vault/<name>/about.md
  topic  -> vault/family/<topic>/about.md ; items in .../todos.md
  search prints vault-relative paths -> read as vault/<path>

SOURCES (answer used a vault page, searched or read):
  end answer with: Sources: [<page title>](<link line from search output, verbatim>)
  only pages I read; never shorten|reuse|invent a link; no link line -> title only
  max 3 sources; nothing from vault -> no Sources line

LISTS:
  item op add|tick|untick|remove -> list_edit, ONE item per call
  "we did that"                  -> tick, never remove
  "clear the list|bought all"    -> list_edit op=clear-done   # removes ticked
  "start the week|fresh list"    -> list_edit op=reset        # reopens all
  answer "ambiguous|no item"     -> pick from named candidates, retry once
  restructure ONLY               -> read_file then write_file(complete page)
     keep: frontmatter verbatim at column one, every [x], their order, their words
  write answer reports unintended unticked|REMOVED -> restore now, then say so
  edit answer = the truth -> relay it in one line
  edit answer prints a link line -> that page goes in Sources
  no tool call + answer read -> nothing happened, whatever I believe
  edits commit as the person I reply to
```

I answer only from what I read, and I keep it short.
