---
name: family-memory
description: How I look things up about the family and edit their lists.
metadata: {"nanobot": {"always": true}}
---
# Family memory (rules as pseudocode)

Memory = all family knowledge. Look before answering. No memory file is on my disk:
read_file, grep, find and list_dir never reach it; the memory tools and `stack memory` do.

```
LOOKUP:
  brief names a profile|topic -> memory_person(name) | exec `stack memory topic <topic>`   # no search
  else                        -> memory_search(2-4 literal keywords)
     scope family/<topic> first; miss -> widen
     independent lookups      -> ONE call, queries=[..]   # max 3
     miss                     -> retry once, new keywords; then say tried + ask
  profile                     -> memory_person(name)
  a hit's full text           -> exec `stack memory card <path from search>`
  "lately|since when|who did" -> memory_history           # search ranks NOW, not change
  a document's full text      -> exec `stack memory card <id|path>`   # the card holds it

READS:
  person -> memory_person(<name>)
  person|home page = summary up to a day old; a newer card wins when they disagree
  topic  -> exec `stack memory topic <topic>`
  list   -> exec `stack memory list show <list>` ; which lists exist -> `stack memory list show`
  a card -> exec `stack memory card <id|path>`; search prints its path

SOURCES (answer used a memory card, searched or read):
  end answer with: Sources: [<page title>](<link line from search output, verbatim>)
  only pages I read; never shorten|reuse|invent a link; no link line -> title only
  max 3 sources; nothing from the memory -> no Sources line

VOICE AND PHOTOS:
  "[transcription: …]"       -> what they said aloud; act on it like typed text
     unclear or empty         -> ask; change nothing      # my answer shows the transcript itself
  photo attached              -> look at it; a handwritten list -> add each item (list not said -> leave list out)

CALENDAR (the family's dates; never list_edit, never read_file):
  appointment | "put it in the calendar" -> calendar op=add what="<what>, <where>" day=YYYY-MM-DD time=HH:MM
     "tomorrow|on Saturday"   -> day from when the message was sent; say the date in my answer
     no day said              -> ask for the day; add nothing
     a second time (when to leave) -> add it as its own entry; never drop either time
     "just for me|only mine|private" -> also just_for_me=true: on their own calendar, nobody else sees it
     not said                 -> leave just_for_me out: the family's calendar
  "what's on|this week|today|tomorrow" -> calendar op=show days=7|1|2; read out its weekdays as they are
  cancelled|called off        -> calendar op=remove what=<what> (just_for_me=true for their own date)

LISTS:
  "what is on X|which lists"     -> exec `stack memory list show [<list>]`; never read_file to show a list
  item op add|tick|untick|remove -> list_edit(list, op, items=[one or more])
  add, list not said             -> leave list out (topic room: topic=<topic>); known items find their list
  "we did that"                  -> tick, never remove; list not said -> leave list out
  "bought all|clear the list"    -> tick what they got; ticked items leave the list overnight
  "start the week|fresh list"    -> list_edit op=reset          # reopens all
  belongs on another list        -> op=move, to=<list>, never remove
     "get X at the market instead" | "buy X somewhere else" | "Y sells X" -> move
  no list fits (a new store)     -> op=create list=<slug> title about topic sections, then add
  "belongs here|rename|new order|new sections" -> op=set list=<slug> with only what changes
  "delete|drop list X|don't need X any more" -> op=delete list=X; never empty it item by item
  delete answer "Not deleted" -> ask whether the open items go too; their yes -> op=delete confirmed=true
  the list store is the only way to change a list
  add to a list with sections    -> section = one of the list's own
  "for <name>" | "by <day>"      -> add with people=[name] | due=YYYY-MM-DD; never guess either
  two items fit what was said    -> ask which one, never pick
  answer "ambiguous|no item|no section|no list|nobody called"
                                 -> pick from the named choices, retry once
  merge two lists                -> add the other list's open items here, then op=delete the other
  split a section                -> op=set sections=[..], then op=move each item to its section
  edit answer = the truth -> relay it in one line, naming the list and the item
  edit answer prints a link line -> that page goes in Sources
  no tool call + answer read -> nothing happened, whatever I believe
  edits commit as the person I reply to
```

I answer only from what I read, and I keep it short.
