<!--
The page the wiki writes for each topic, in English: the facts of the
topic and where it stands, for someone who wants to know in a minute.

Slots in double braces are filled by the compiler. A paragraph whose
slot is empty is left out whole, heading included.

  title       the topic's name, from the model, else from its folder
  summary     one line: what the topic is for the family, from the model
  facts       the key facts as label and value, from the model
  state       what has been decided, done or changed, and by whom, from the model
  todos       the open items of the lists naming the topic, under each list's name,
              copied by the compiler
  notes, bookmarks, documents
              everything filed under the topic, newest first, by the compiler
  elsewhere   pages filed elsewhere that mention the topic, by the compiler

This comment is not published.
-->
# {{title}}
> {{summary}}

## At a glance
{{facts}}

## Where things stand
{{state}}

## Still to do
{{todos}}

## Notes
{{notes}}

## Links
{{bookmarks}}

## Documents
{{documents}}

## Elsewhere in the wiki
{{elsewhere}}
