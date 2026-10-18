---
name: speaking
description: How I say something aloud in the house.
metadata: {"nanobot": {"always": true}}
---
# Speaking aloud

```
"say|tell <someone> aloud|announce|out loud" -> exec `stack ai say "<text>"`
  text   -> plain spoken sentences in the language they asked for
            no markdown, lists, links, emoji; numbers as people say them
  voice  -> leave out; `--voice <name>` only when they name one
  language -> `--language <code>` (en, de) when the text is not in the house's language
  loud   -> `--volume 30` for "leise"/"quietly"; else leave it out
  private (mail, documents, health) -> aloud only when its owner asked for it
  then   -> say in chat, in their language, what I said aloud
  error  -> tell them it could not be spoken; never claim it was
```
