"""A person's wiki page: a Markdown template, the model writes the prose.

The page is a profile card a family member opens to see who someone is
right now. It has two kinds of content, and each comes from where it
is reliable:

- **Facts the compiler knows.** The name, the latest entries that name
  the person with their real dates, and the documents that name them.
  Rendered here from the index, so a date or a document is never made
  up and never credited to the wrong entry.
- **Prose only a model can write.** A one-line tagline, a short About,
  what the person loves, one warm line for each of the latest entries,
  and optionally one section of its own choosing. The model answers in
  JSON, one field each, so nothing here parses free text.

The layout is a Markdown template per language (`templates/person.<lang>.md`,
see `page_template`):
its headings are what the family reads. A paragraph whose slot is empty
is left out with its heading, so an empty section never reads "nothing
recorded".

Pure functions: no I/O beyond reading the template and the prompt
(`templates/prompts/`), no model calls.
The wiki compiler (`wiki._generate_member`) owns the evidence, the call
and the publish.

    >>> parts = read_answer('{"tagline": "The baby.", "about": "Maggie is one.", '
    ...                     '"loves": ["Her pacifier. [1]"]}', lately=[])
    >>> parts.loves
    ['Her pacifier. [1]']
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import prompt_files
from .page_template import (
    LANGUAGE_NAMES, cites, fact, fill, format_date, language, sentence, template, text,
)

# A card, not an archive: what fits on a phone screen above the fold
# plus a little more. The documents list is the way into the rest.
LATELY_MAX = 6
DOCUMENTS_MAX = 6
LOVES_MAX = 5
FACTS_MAX = 5


# ── What the compiler knows ──────────────────────────────────────────────

def latest(entries: list[dict]) -> list[int]:
    """Citation numbers of the newest dated entries, newest first.

    Numbers are positions in `entries` (1-based), the same numbering the
    prompt's evidence and the References block use. Documents are left
    out: they have their own list, and a policy renewal is not something
    that happened to a person.
    """
    dated = [(e.get("date") or "", n) for n, e in enumerate(entries, start=1)
             if e.get("date") and e.get("type") != "document"]
    dated.sort(key=lambda d: d[0], reverse=True)
    return [n for _, n in dated[:LATELY_MAX]]


def documents(entries: list[dict]) -> list[int]:
    """Citation numbers of the documents naming the person, newest first."""
    docs = [(e.get("date") or "", n) for n, e in enumerate(entries, start=1)
            if e.get("type") == "document"]
    docs.sort(key=lambda d: d[0], reverse=True)
    return [n for _, n in docs[:DOCUMENTS_MAX]]


# ── What the model writes ────────────────────────────────────────────────

@dataclass
class Parts:
    """The prose the model wrote for one page; every field may be empty."""

    tagline: str = ""
    about: str = ""
    facts: list[tuple[str, str, str]] = field(default_factory=list)
    loves: list[str] = field(default_factory=list)
    more_heading: str = ""
    more_text: str = ""
    lately: dict[int, str] = field(default_factory=dict)


class AnswerError(ValueError):
    """The model's answer is not the JSON object the prompt asked for."""


def read_answer(raw: str, *, lately: list[int]) -> Parts:
    """The model's JSON answer as `Parts`, or `AnswerError`.

    A field of the wrong shape is dropped rather than guessed at; the
    page then leaves that section out. A line for an entry the prompt
    did not offer is ignored.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise AnswerError(f"not JSON: {e}") from e
    if not isinstance(data, dict):
        raise AnswerError("not a JSON object")

    more = data.get("more")
    more = more if isinstance(more, dict) else {}
    lines = data.get("lately")
    lines = lines if isinstance(lines, dict) else {}
    loves = data.get("loves")
    loves = loves if isinstance(loves, list) else []
    facts = data.get("facts")
    facts = facts if isinstance(facts, list) else []
    more_text = more.get("text")
    return Parts(
        tagline=text(data.get("tagline")),
        about=text(data.get("about")),
        facts=[(text(f.get("label")), text(f.get("value")), cites(f.get("cite")))
               for f in facts
               if isinstance(f, dict) and text(f.get("label")) and text(f.get("value"))][:FACTS_MAX],
        loves=[t for t in map(text, loves) if t][:LOVES_MAX],
        more_heading=text(more.get("heading")),
        more_text=more_text.strip() if isinstance(more_text, str) else "",
        lately={n: text(lines.get(str(n))) for n in lately if text(lines.get(str(n)))},
    )


# ── The page ─────────────────────────────────────────────────────────────

def render(parts: Parts, *, name: str, first_name: str, entries: list[dict],
           lately: list[int], docs: list[int], links: dict[int, str], lang: str,
           open_items: str = "") -> str:
    """The page body, from the template for `lang`.

    `links` maps a citation number to the published path of its source,
    for the Documents list. Entries the model wrote no line for fall
    back to their title, so Lately never loses an entry. `open_items`
    are the list items linked to the person, already written out.
    """
    lang = language(lang)
    lately_lines = [
        f"- **{format_date(entries[n - 1]['date'], lang)}** · "
        f"{sentence(parts.lately.get(n) or entries[n - 1].get('title') or '')} [{n}]"
        for n in lately
    ]
    doc_lines = [
        f"- [{entries[n - 1].get('title') or ''}]({links[n]}) · "
        f"{format_date(entries[n - 1].get('date') or '', lang)}"
        for n in docs if links.get(n)
    ]
    more = f"## {parts.more_heading}\n{parts.more_text}" if parts.more_heading and parts.more_text else ""
    slots = {
        "name": name,
        "first_name": first_name,
        "tagline": parts.tagline,
        "about": parts.about,
        "facts": "\n".join(fact(label, value, c) for label, value, c in parts.facts),
        "loves": "\n".join(f"- {line}" for line in parts.loves),
        "more": more,
        "lately": "\n".join(lately_lines),
        "documents": "\n".join(doc_lines),
        "todos": open_items,
    }
    return fill(template("person", lang), slots)


# ── The prompt ───────────────────────────────────────────────────────────

def prompt(*, display: str, first_name: str, evidence: str, facts: list[tuple[str, str]],
           lately: list[int], lang: str, family: list[str] | None = None, previous: str = "",
           new_evidence: list[str] | None = None) -> str:
    """The instructions for one person's prose, asking for a JSON object (`templates/prompts/person.md`).

    `previous` is the page's current body: the model keeps its wording
    where the evidence still supports it, so a new filing changes a few
    lines rather than the whole page. Citation numbers in it already
    match `evidence` (the compiler renumbered them).
    """
    return prompt_files.render("person", {
        "display": display,
        "first_name": first_name,
        "family": "\n".join(f"- {line}" for line in family or []),
        "facts": "\n".join(f"- ({kind}) {text}" for kind, text in facts),
        "evidence": evidence,
        "previous": previous,
        "new_evidence": "\n".join(new_evidence or []),
        "language": LANGUAGE_NAMES[language(lang)],
        "lately": ", ".join(str(n) for n in lately),
        "loves_max": str(LOVES_MAX),
        "facts_max": str(FACTS_MAX),
    })
