---
name: reminders
description: How I set, list and cancel reminders with the family's scheduler.
metadata: {"nanobot": {"always": true}}
---
# Reminders

A reminder is two scheduler entries at the same time: `remind-<x>` posts it in the chat, `remind-<x>-say` says it in the house.

```
"remind <me|someone> ... <time>" -> exec both, same <name> and <when>:
  `stack core schedule add <name> "<when>" --title "<text>" --by <sender> --for <person> -- messages send <Chat ID> "<text>" --as agent --mention <person>`
  `stack core schedule add <name>-say "<when>" --title "<spoken>" --by <sender> --for <person> -- ai say "<spoken>"`
  person -> "me" is the sender; else their name, lowercase
  name   -> remind-<person>-<one word of the text>: a-z, 0-9, dashes
  when   -> "in 10m" | "in 2h" | "at YYYY-MM-DD HH:MM" | "daily HH:MM" | "weekly mon..sun HH:MM"
            house time; "tomorrow" from Current Time
  text   -> their words, their language
  spoken -> "Reminder for Homer: <text>"; for the sender "Reminder: <text>"; their language, rules of speaking
  no time -> ask when; add nothing
  then   -> say in chat what I set and when
  error  -> say it was not set; never claim it was
"which reminders" -> exec `stack core schedule list`; each reminder once, skip names ending -say
"cancel <reminder>" -> exec `stack core schedule remove <name>` and `stack core schedule remove <name>-say`
  not named remind-... -> the admin's entry: say so, keep it
```

```
example: marge, Chat ID !kitchen:simpson, "remind me in 10 minutes to take the cake out"
stack core schedule add remind-marge-cake "in 10m" --title "Take the cake out" --by marge --for marge -- messages send !kitchen:simpson "Take the cake out" --as agent --mention marge
stack core schedule add remind-marge-cake-say "in 10m" --title "Reminder: Take the cake out" --by marge --for marge -- ai say "Reminder: Take the cake out"
example: marge, today 2026-11-26, "remind Homer tomorrow at 17:00 to take out the trash"
stack core schedule add remind-homer-trash "at 2026-11-27 17:00" --title "Take out the trash" --by marge --for homer -- messages send !kitchen:simpson "Take out the trash" --as agent --mention homer
stack core schedule add remind-homer-trash-say "at 2026-11-27 17:00" --title "Reminder for Homer: Take out the trash" --by marge --for homer -- ai say "Reminder for Homer: Take out the trash"
example: lisa, "every day at 08:00, my pills"
stack core schedule add remind-lisa-pills "daily 08:00" --title "Pills" --by lisa --for lisa -- messages send !kitchen:simpson "Pills" --as agent --mention lisa
stack core schedule add remind-lisa-pills-say "daily 08:00" --title "Reminder: Pills" --by lisa --for lisa -- ai say "Reminder: Pills"
example: bart, "every Tuesday at 17:00, football"
stack core schedule add remind-bart-football "weekly tue 17:00" --title "Football" --by bart --for bart -- messages send !kitchen:simpson "Football" --as agent --mention bart
stack core schedule add remind-bart-football-say "weekly tue 17:00" --title "Reminder: Football" --by bart --for bart -- ai say "Reminder: Football"
example: homer, "cancel the trash reminder"
stack core schedule remove remind-homer-trash
stack core schedule remove remind-homer-trash-say
```
