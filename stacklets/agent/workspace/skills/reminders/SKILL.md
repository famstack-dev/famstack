---
name: reminders
description: How I set, list and cancel reminders with the family's scheduler.
metadata: {"nanobot": {"always": true}}
---
# Reminders

A reminder is a scheduler entry: `remind-<x>` posts it in the chat at the time.
At the same time its twin `remind-<x>-say` says it in the house.

```
asks to be reminded, or to remind someone, later, in any language -> a reminder; never the calendar
  exec each line, same name and <when>:
  `stack core schedule add remind-<person>-<word> "<when>" --title "<text>" --by <sender> --for <person> -- messages send <room> "<text>" --as agent --mention <person>`
  `stack core schedule add remind-<person>-<word>-say "<when>" --title "<spoken>" --by <sender> --for <person> -- ai say "<spoken>"`
  person -> "me" is the sender; else their name, lowercase
  word   -> one word of the text: a-z, 0-9, dashes
  when   -> "in 10m" | "in 2h" | "at YYYY-MM-DD HH:MM" | "daily HH:MM" | "weekly mon..sun HH:MM"
            house time; "tomorrow" from the "Sent ..." line
  room   -> the id on the "This room: ..." line; never one from an example
  text   -> their words, their language; in "double quotes",
            with $, a backquote or a backslash in 'single quotes'
  spoken -> the -say text: "Reminder for Homer: <text>"; for the sender "Reminder: <text>"; their language, rules of speaking
  no time -> ask when; add nothing
  then   -> say in chat what I set and when
  error  -> say it was not set; never claim it was
"which reminders" -> exec `stack core schedule list`; each reminder once
  skip names ending -say
"cancel <reminder>" -> exec, its name from the list, `stack core schedule remove remind-<person>-<word>`
  and `stack core schedule remove remind-<person>-<word>-say`
  not named remind-... -> the admin's entry: say so, keep it
```

```
example: marge, "remind me in 10 minutes to take the cake out"
stack core schedule add remind-marge-cake "in 10m" --title "Take the cake out" --by marge --for marge -- messages send <room> "Take the cake out" --as agent --mention marge
stack core schedule add remind-marge-cake-say "in 10m" --title "Reminder: Take the cake out" --by marge --for marge -- ai say "Reminder: Take the cake out"
example: marge, Sent Thursday, 2026-11-26, "remind Homer tomorrow at 17:00 to take out the trash"
stack core schedule add remind-homer-trash "at 2026-11-27 17:00" --title "Take out the trash" --by marge --for homer -- messages send <room> "Take out the trash" --as agent --mention homer
stack core schedule add remind-homer-trash-say "at 2026-11-27 17:00" --title "Reminder for Homer: Take out the trash" --by marge --for homer -- ai say "Reminder for Homer: Take out the trash"
example: lisa, "every day at 08:00, my pills"
stack core schedule add remind-lisa-pills "daily 08:00" --title "Pills" --by lisa --for lisa -- messages send <room> "Pills" --as agent --mention lisa
stack core schedule add remind-lisa-pills-say "daily 08:00" --title "Reminder: Pills" --by lisa --for lisa -- ai say "Reminder: Pills"
example: bart, "every Tuesday at 17:00, football"
stack core schedule add remind-bart-football "weekly tue 17:00" --title "Football" --by bart --for bart -- messages send <room> "Football" --as agent --mention bart
stack core schedule add remind-bart-football-say "weekly tue 17:00" --title "Reminder: Football" --by bart --for bart -- ai say "Reminder: Football"
example: homer, "cancel the trash reminder"
stack core schedule remove remind-homer-trash
stack core schedule remove remind-homer-trash-say
```
