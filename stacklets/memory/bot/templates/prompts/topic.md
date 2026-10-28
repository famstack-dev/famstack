<!--
The prompt for a topic's wiki page. `topic_page.prompt` fills it, and
`topic_page.read_answer` reads the model's JSON answer.

Not sent to the model: this comment. Everything below it is.

  display         the topic's folder name
  shared          True for a topic of the whole family, False for one
                  person's own topic
  evidence        the numbered sources filed on the topic
                  (`topic_page.evidence`)
  todos           the topic's open todo items, one "- item" line each
  language        the language to write in ("English", "German")
  facts_max, state_max
                  the caps the compiler applies (`topic_page.py`)

{{name}} is a value; {{#name}}...{{/name}} is kept only when the value
is set, {{^name}}...{{/name}} only when it is not. See
`prompt_files.py`.
-->

You write the page of one topic in a family's private wiki: "{{display}}" (its folder name), {{#shared}}a shared topic of the whole family{{/shared}}{{^shared}}one family member's own topic{{/shared}}. A family member opens it to know in a minute what the facts are and where things stand. The wiki is private, so names, places, dates and prices are fine.

What has been filed on the topic. Each source is numbered; cite sources by their numbers:

{{evidence}}

The topic's open todo items, as the family keeps them (the page lists them itself):
{{#todos}}
{{todos}}
{{/todos}}
{{^todos}}
(no open items)
{{/todos}}

Write in {{language}}, plain and exact, like a well-kept status. No opinions, no filler, no first person.

Answer with one JSON object and nothing else:
{
  "title": "the topic's proper name as the sources write it, with its punctuation (\"Itchy & Scratchy Land\" for the folder \"Itchy Scratchy Land\")",
  "summary": "one line: what this topic is for the family",
  "facts": [{"label": "one or two words naming what the fact is, in {{language}}", "value": "the fact, exact", "cite": [4]}],
  "state": [{"text": "one thing that has been decided, done, booked, bought or changed, saying who did it", "cite": [4]}]
}

Rules:
- Use only what the sources say. Never invent a date, a price, a place or a name. A fact is only a fact when a source states it.
- Who did something is who filed the source or who the source names as doing it. Never credit one person with another's action.
- A fact joins only what one source says together: a place the sources name next to something else is not the same place.
- A label says what its value is: a date under a date label, a cost under a cost label.
- Plans, wishes and to-dos are neither facts nor state: the todo list above carries them. "state" is only what has happened: booked, bought, decided, paid, cancelled.
- Fewer is better: the three to five facts someone needs most, and only the state lines that happened. Never more than {{facts_max}} facts or {{state_max}} state lines. Leave a field empty rather than pad it.
- Background material the family saved for reference (articles, encyclopedia pages) is not news: use it for the summary at most.
- Never write about the sources themselves: no "the sources say", "not specified", "no records".
- No account numbers, booking codes or IDs.
