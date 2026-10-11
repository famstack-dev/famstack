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

Pure functions: no I/O beyond reading the template, no model calls.
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

from page_template import (
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
           lately: list[int], docs: list[int], links: dict[int, str], lang: str) -> str:
    """The page body, from the template for `lang`.

    `links` maps a citation number to the published path of its source,
    for the Documents list. Entries the model wrote no line for fall
    back to their title, so Lately never loses an entry.
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
    }
    return fill(template("person", lang), slots)


# ── The prompt ───────────────────────────────────────────────────────────

def prompt(*, display: str, first_name: str, evidence: str, facts: list[tuple[str, str]],
           lately: list[int], lang: str, family: list[str] | None = None, previous: str = "",
           new_evidence: list[str] | None = None) -> str:
    """The instructions for one person's prose, asking for a JSON object.

    `previous` is the page's current body: the model keeps its wording
    where the evidence still supports it, so a new filing changes a few
    lines rather than the whole page. Citation numbers in it already
    match `evidence` (the compiler renumbered them).
    """
    if facts:
        facts_block = (f"Facts the family typed in about {display} (always true):\n"
                       + "\n".join(f"- ({kind}) {text}" for kind, text in facts))
    else:
        facts_block = f"The family typed in no facts about {display}."

    anchor = ""
    if previous:
        fresh = "\n".join(new_evidence or []) or "Nothing is new since then."
        anchor = f"""
The page already exists. Keep the wording of its tagline, About, loves and own section wherever the sources still support it, and change only what new sources add to or contradict. Its [N] numbers already match the sources above.

New since the page was written:
{fresh}

THE PAGE AS IT IS NOW:
{previous}
"""

    family_block = ""
    if family:
        family_block = ("\nThe family, with every name each one goes by (a source may use any of them):\n"
                        + "\n".join(f"- {line}" for line in family) + "\n")

    lately_list = ", ".join(str(n) for n in lately) or "(none)"
    return f"""You write the page about one person in a family's private wiki. Family members open it to see who {display} is right now, and they should enjoy reading it.

This page is about: {display}.
{family_block}
{facts_block}

What the family has filed that involves {display}. Each source is numbered; cite it as [N] after the sentence it supports:

{evidence}
{anchor}
Write in {LANGUAGE_NAMES[language(lang)]}, the way a family member would describe {first_name} to a relative who is visiting: warm, concrete, specific, with the small details and the humour the sources have. Short sentences. Present tense for who they are, past tense for what happened.

Never sound like a report. Do not use words like "logistics", "dynamics", "engagement", "status", "is noted", "appears to", "records show", "sources", "documents indicate". Do not repeat {first_name}'s name at the start of every sentence.

Answer with one JSON object and nothing else:
{{
  "tagline": "one short line that says who {first_name} is in the family, the way a relative would put it",
  "about": "two to four sentences about {first_name} today: where they are in life, what fills their days, what makes them them. Cite [N].",
  "facts": [{{"label": "one or two words in {LANGUAGE_NAMES[language(lang)]}: Born, School, Job, Doctor, Car", "value": "short and exact", "cite": [2]}}],
  "loves": ["up to {LOVES_MAX} short lines, each a thing, person, food, place or pastime {first_name} loves, cited [N]. Not an event or an achievement."],
  "lately": {{"<N>": "for each of the sources {lately_list}: one line saying what {first_name} did or what happened to them, without the date, in the past tense, starting with the verb rather than the name or a pronoun. Cite nothing in it."}},
  "more": {{"heading": "a heading of your own", "text": "one theme about {first_name}, at most three sentences, cited [N]"}}
}}

"more" is optional: use it only when the sources hold something about {first_name} worth its own section that fits nowhere else (a running joke, a milestone, a story the family keeps telling). It is about {first_name}, not about someone else. Otherwise leave it out.

Rules:
- Use only what the sources and the facts say. Never invent. A field with nothing to say is left out or empty.
- The sources also involve other people (see "involves:"). A birth certificate is mostly about the parents; a school letter names a parent. Say something about {first_name} only when the source ties it to {first_name} by name. Never give {first_name} another person's role, job, costume, words or relationship.
- When a source says "the mother" or "her husband" without naming {first_name}, that is about someone else: leave it out.
- Who did something is who filed the source or who the source names as doing it. Never credit {first_name} with what another person did.
- A family member under another of their names is the same person, never a new child, sibling or twin.
- "facts" are what a relative would look up, at most {FACTS_MAX}: when {first_name} was born, their school and class or their job and employer, their doctor, the car they drive. Only from a source that names {first_name}, plain and exact, never a guess. No account, policy or ID numbers.
- No dates, numbers of accounts or IDs, and no source titles in the prose. The page lists the documents itself.
- Several sources for one sentence are cited together: [1, 3].
- Count people only from the sources: who the children, siblings and grandparents are comes from what the sources name.
"""
