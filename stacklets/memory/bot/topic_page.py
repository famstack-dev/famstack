"""A topic's wiki page: the facts of the topic and where it stands.

A topic is something the family works on or keeps track of: a trip, the
car, the kitchen renovation. Its page answers, in a minute: what are the
facts, what has been decided or done and by whom, what is still open.
It is filled from `templates/topic.<lang>.md` (see `page_template`).

- **The model** answers in JSON: the topic's name, one line on what it
  is, the key facts as label and value, and what has been decided, done
  or changed, each with the sources it cites. Plain and exact, the way a
  well-kept status reads.
- **The compiler** lists the rest: the open items of the topic's todo
  list as the family keeps it, and everything filed under the topic,
  with who filed it and when, linked.

Pure functions: no I/O beyond reading the template, no model calls.
The wiki compiler (`wiki._generate_topic`) owns the evidence, the call
and the publish.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from page_template import (
    LANGUAGE_NAMES, cites, fact, fill, format_date, language, sentence, template, text,
)

FACTS_MAX = 8
STATE_MAX = 6


@dataclass
class Answer:
    """What the model read from the topic's sources; every field may be empty."""

    title: str = ""
    summary: str = ""
    facts: list[tuple[str, str, str]] = field(default_factory=list)
    state: list[tuple[str, str]] = field(default_factory=list)


class AnswerError(ValueError):
    """The model's answer is not the JSON object the prompt asked for."""


def read_answer(raw: str) -> Answer:
    """The model's JSON answer, or `AnswerError`. Ill-shaped fields are dropped."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AnswerError(f"not JSON: {e}") from e
    if not isinstance(data, dict):
        raise AnswerError("not a JSON object")
    facts = data.get("facts")
    state = data.get("state")
    return Answer(
        title=text(data.get("title")),
        summary=text(data.get("summary")),
        facts=[(text(f.get("label")), text(f.get("value")), cites(f.get("cite")))
               for f in (facts if isinstance(facts, list) else [])
               if isinstance(f, dict) and text(f.get("label")) and text(f.get("value"))][:FACTS_MAX],
        state=[(text(s.get("text")), cites(s.get("cite")))
               for s in (state if isinstance(state, list) else [])
               if isinstance(s, dict) and text(s.get("text"))][:STATE_MAX],
    )


# ── The page ─────────────────────────────────────────────────────────────

@dataclass
class Source:
    """One capture as the page lists it."""

    n: int
    kind: str          # "note", "bookmark", "document", or "elsewhere"
    title: str
    link: str
    date: str = ""
    filed_by: str = ""


def render(answer: Answer, *, display: str, sources: list[Source],
           open_items: str, lang: str) -> str:
    """The topic page body. `open_items` are the topic's lists, already written out."""
    def listed(kind: str) -> str:
        rows = sorted((s for s in sources if s.kind == kind),
                      key=lambda s: s.date, reverse=True)
        return "\n".join(_source_line(s, lang) for s in rows)

    slots = {
        "title": answer.title or display,
        "summary": answer.summary,
        "facts": "\n".join(fact(label, value, c) for label, value, c in answer.facts),
        "state": "\n".join(f"- {sentence(t)}{c}" for t, c in answer.state),
        "todos": open_items,
        "notes": listed("note"),
        "bookmarks": listed("bookmark"),
        "documents": listed("document"),
        "elsewhere": listed("elsewhere"),
    }
    return fill(template("topic", lang), slots)


def _source_line(s: Source, lang: str) -> str:
    bits = [b for b in (s.filed_by, format_date(s.date, lang)) if b]
    tail = f" · {' · '.join(bits)}" if bits else ""
    return f"- [{s.title}]({s.link}){tail}" if s.link else f"- {s.title}{tail}"


# ── The prompt ───────────────────────────────────────────────────────────

def evidence(sources: list[Source], summaries: dict[int, str]) -> str:
    """The numbered sources for the prompt, each with who filed it and when."""
    lines: list[str] = []
    for s in sources:
        who = f", filed by {s.filed_by}" if s.filed_by else ""
        where = " (filed elsewhere, mentions this topic)" if s.kind == "elsewhere" else ""
        lines.append(f"[{s.n}] {s.date or 'undated'} · {s.kind} · {s.title}{who}{where}")
        lines.append("    " + (summaries.get(s.n) or "").replace("\n", "\n    "))
        lines.append("")
    return "\n".join(lines).rstrip()


def prompt(*, display: str, scope: str, evidence_text: str, todos: list[str],
           lang: str) -> str:
    """The instructions for one topic's facts and state, asking for JSON."""
    whose = ("a shared topic of the whole family" if scope == "shared"
             else "one family member's own topic")
    todo_block = ("\n".join(f"- {t}" for t in todos) if todos
                  else "(no open items)")
    return f"""You write the page of one topic in a family's private wiki: "{display}" (its folder name), {whose}. A family member opens it to know in a minute what the facts are and where things stand. The wiki is private, so names, places, dates and prices are fine.

What has been filed on the topic. Each source is numbered; cite sources by their numbers:

{evidence_text}

The topic's open todo items, as the family keeps them (the page lists them itself):
{todo_block}

Write in {LANGUAGE_NAMES[language(lang)]}, plain and exact, like a well-kept status. No opinions, no filler, no first person.

Answer with one JSON object and nothing else:
{{
  "title": "the topic's proper name as the sources write it, with its punctuation (\"Itchy & Scratchy Land\" for the folder \"Itchy Scratchy Land\")",
  "summary": "one line: what this topic is for the family",
  "facts": [{{"label": "one or two words naming what the fact is, in {LANGUAGE_NAMES[language(lang)]}", "value": "the fact, exact", "cite": [4]}}],
  "state": [{{"text": "one thing that has been decided, done, booked, bought or changed, saying who did it", "cite": [4]}}]
}}

Rules:
- Use only what the sources say. Never invent a date, a price, a place or a name. A fact is only a fact when a source states it.
- Who did something is who filed the source or who the source names as doing it. Never credit one person with another's action.
- A fact joins only what one source says together: a place the sources name next to something else is not the same place.
- A label says what its value is: a date under a date label, a cost under a cost label.
- Plans, wishes and to-dos are neither facts nor state: the todo list above carries them. "state" is only what has happened: booked, bought, decided, paid, cancelled.
- Fewer is better: the three to five facts someone needs most, and only the state lines that happened. Never more than {FACTS_MAX} facts or {STATE_MAX} state lines. Leave a field empty rather than pad it.
- Background material the family saved for reference (articles, encyclopedia pages) is not news: use it for the summary at most.
- Never write about the sources themselves: no "the sources say", "not specified", "no records".
- No account numbers, booking codes or IDs.
"""
