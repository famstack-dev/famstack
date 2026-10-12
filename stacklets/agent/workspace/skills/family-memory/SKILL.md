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
  topic  -> vault/family/<topic>/about.md
  list   -> vault/family/lists/<list>.md ; which lists exist -> `stack memory list show`
  search prints vault-relative paths -> read as vault/<path>

SOURCES (answer used a vault page, searched or read):
  end answer with: Sources: [<page title>](<link line from search output, verbatim>)
  only pages I read; never shorten|reuse|invent a link; no link line -> title only
  max 3 sources; nothing from vault -> no Sources line

LISTS:
  item op add|tick|untick|remove -> list_edit(list, op, items=[one or more])
  add, list not said             -> leave list out (topic room: topic=<topic>); known items find their list
  "we did that"                  -> tick, never remove; list not said -> leave list out
  "bought all|clear the list"    -> tick what they got; ticked items leave the list overnight
  "start the week|fresh list"    -> list_edit op=reset          # reopens all
  belongs on another list        -> op=move, to=<list>
  no list fits (a new store)     -> op=create list=<slug> title about topic sections, then add
  "belongs here|rename|new order|new sections" -> op=set list=<slug> with only what changes
  add to a list with sections    -> section = one of the list's own
  "for <name>" | "by <day>"      -> add with people=[name] | due=YYYY-MM-DD; never guess either
  answer "ambiguous|no item|no section|no list|nobody called"
                                 -> pick from the named choices, retry once
  restructure set cannot do (merge two lists, split a section)
                                 -> read_file then write_file(complete page)
     keep: frontmatter verbatim at column one, every [x], their order, their words
  write answer reports unintended unticked|REMOVED -> restore now, then say so
  edit answer = the truth -> relay it in one line
  edit answer prints a link line -> that page goes in Sources
  no tool call + answer read -> nothing happened, whatever I believe
  edits commit as the person I reply to
```

I answer only from what I read, and I keep it short.
