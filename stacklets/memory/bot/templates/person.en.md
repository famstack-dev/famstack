<!--
The page the wiki writes for each person, in English.

Slots in double braces are filled by the compiler. A paragraph whose
slot is empty is left out whole, heading included, so a person with no
documents has no Documents heading.

  name, first_name   from the person's file
  tagline            one warm line, written by the model
  about              a short paragraph, written by the model
  loves              bullets, written by the model
  more               a section the model may add, with its own heading
  lately             the latest entries naming them, dated by the compiler
  documents          the documents naming them, listed by the compiler

This comment is not published.
-->
# {{name}}
> {{tagline}}

{{about}}

## What {{first_name}} loves
{{loves}}

{{more}}

## Lately
{{lately}}

## Documents
{{documents}}
