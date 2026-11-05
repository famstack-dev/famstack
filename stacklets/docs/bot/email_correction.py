"""email_correction: what a correction in an email's thread does to the email's card.

    fields = corrected_fields(card_frontmatter, model_persons=["bart"],
                              model_tags=["school", "gemuesekiste2"],
                              words="Add tag gemuesekiste2", people=people)
    content = correct_email_thread(card, fields=fields,
                                   from_path=path, shared_bucket="family", people=people)
    changes = changes_between(card_view(card), card_view(content))

An email is a thread of messages folded into one card
(`vault_entry.fold_email_message`). A family member corrects it by
writing in the email's thread ("Add tag gemuesekiste2"); the classifier
then answers with a whole new title, people and tags. This module turns
that answer into the change the person asked for, and writes it into
the card's head, without touching the thread.

Why the answer is not written as it is: the classifier reads the first
message's summary and the card's tags, never the card's people or the
later messages. Its answer drops a person a later message added, or a
tag an earlier correction added, though nobody asked for it. So a
correction starts from the card's own people and tags, adds what the
classifier added, and removes a tag or a person only when the
correction's words name it:

    card:   persons [bart, lisa]   tags [school, permission-slip]
    words:  "Add tag gemuesekiste2"
    model:  persons [bart]         tags [school, gemuesekiste2]
    result: persons [bart, lisa]   tags [school, permission-slip, gemuesekiste2]

    words:  "Remove tag school"
    model:  persons [bart]         tags [permission-slip]
    result: persons [bart, lisa]   tags [permission-slip]

The provenance tags (sender, mailbox, folder) are facts of the delivery
and always stay. The thread's messages stay byte for byte, from the
first `<!-- mid:` marker or `## ` heading on.

Pure: no I/O. `GitMirror.correct_email` reads the card, calls this
module and writes the result through the curator; the archivist's
answer shows the changes `card_view` reads off the card before and
after (`card_changes.changes_between`).
"""

from __future__ import annotations

import re
from typing import NamedTuple

from stack.cards.links import About
from stack.frontmatter import dump as frontmatter_dump
from stack.people import Person
from stack.vault import slug

from .vault_entry import split_frontmatter

__all__ = [
    "EMAIL_PROVENANCE_SLUGS",
    "CorrectedFields",
    "corrected_fields",
    "correct_email_thread",
    "card_view",
]

# The tags an email is filed with that say where it came from: its sender,
# its mailbox and its folder, as the card stores them (`Mailbox: family`
# becomes `mailbox-family`). A correction keeps them whatever the
# classifier answers, because they are facts of the delivery, not a
# reading of the text.
EMAIL_PROVENANCE_SLUGS = ("sender-", "mailbox-", "folder-")


class CorrectedFields(NamedTuple):
    """The title, people (ids) and tags an email card has after a correction.

        CorrectedFields("Field trip", ["bart", "lisa"], ["mailbox-family", "school"])
    """

    title: str
    persons: list[str]
    tags: list[str]


def corrected_fields(
    card: dict, *, model_title: str, model_persons: list[str], model_tags: list[str],
    words: str, people: list[Person] | None,
) -> CorrectedFields:
    """What a correction changes on an email card, from the card and the classifier's answer.

    ``card`` is the card's frontmatter. ``model_persons`` are the names
    (or ids) the classifier gave, ``model_tags`` its tags in the card's
    form, ``words`` what the person wrote. The card's people and tags
    stay in their order; the classifier's new ones follow; one of the
    card's goes only when the classifier left it out *and* ``words``
    name it. The title is the classifier's, on one line, else the card's.

        >>> card = {"title": "Field trip", "persons": ["bart", "lisa"],
        ...         "tags": ["mailbox-family", "school", "permission-slip"]}
        >>> corrected_fields(card, model_title="Field trip", model_persons=["Bart"],
        ...                  model_tags=["school", "gemuesekiste2"],
        ...                  words="Add tag gemuesekiste2", people=None)
        CorrectedFields(title='Field trip', persons=['bart', 'lisa'], \
tags=['mailbox-family', 'school', 'permission-slip', 'gemuesekiste2'])
        >>> corrected_fields(card, model_title="", model_persons=["bart", "lisa"],
        ...                  model_tags=["permission-slip"], words="Remove tag school",
        ...                  people=None).tags
        ['mailbox-family', 'permission-slip']
    """
    named = _words_of(words)
    known = people or []
    by_id = {p.id: p for p in known}
    # Without declared people (a test, an empty memory) a name is its own id.
    answered = About.of(model_persons, known).ids if known else \
        [str(n).strip().lower() for n in model_persons if str(n).strip()]
    persons = _merged(_strings(card.get("persons")), answered,
                      lambda pid: _names_any(named, _person_names(pid, by_id)))
    tags = _merged(_strings(card.get("tags")), _strings(model_tags),
                   lambda tag: _names_any(named, [tag]),
                   always_keep=lambda tag: tag.startswith(EMAIL_PROVENANCE_SLUGS))
    title = " ".join(str(model_title or "").split()) or str(card.get("title") or "Email thread")
    return CorrectedFields(title, persons, tags)


def correct_email_thread(
    existing_content: str, *, fields: CorrectedFields, from_path: str,
    shared_bucket: str, people: list[Person] | None = None,
) -> str:
    """The email card `existing_content` with the corrected title, people and tags.

    Only the card's head changes: the frontmatter's `title`, `persons`
    and `tags`, the `# title` line when the title changed, and the
    `**About**` line when the people changed. Every message of the
    thread stays byte for byte, as do every other field and line. A
    correction that changes nothing returns the card as it is.

        >>> card = ("---\\ntype: email\\ntitle: Field trip\\ntags:\\n  - mailbox-family\\n"
        ...         "  - school\\n---\\n\\n# Field trip\\n\\n> **Kind** email\\n\\n"
        ...         "<!-- mid:<1@school.test> -->\\n## 2026-11-02\\n")
        >>> print(correct_email_thread(card, fields=CorrectedFields(
        ...       "Power plant trip", [], ["mailbox-family", "school", "gemuesekiste2"]),
        ...       from_path="family/emails/x.md", shared_bucket="family"))
        ---
        type: email
        title: Power plant trip
        tags:
          - mailbox-family
          - school
          - gemuesekiste2
        ---
        <BLANKLINE>
        # Power plant trip
        <BLANKLINE>
        > **Kind** email
        <BLANKLINE>
        <!-- mid:<1@school.test> -->
        ## 2026-11-02
        <BLANKLINE>

    Raises ``FrontmatterError`` when the card's frontmatter does not
    parse: rewriting it would lose the card's type and id.
    """
    fm, body = split_frontmatter(existing_content)
    old = card_view(existing_content)
    if (fields.title, fields.persons, fields.tags) == \
            (old["title"], old["persons"], _strings(fm.get("tags"))):
        return existing_content
    fm["title"] = fields.title
    if fields.persons:
        fm["persons"] = fields.persons
    else:
        fm.pop("persons", None)
    if fields.tags:
        fm["tags"] = fields.tags
    else:
        fm.pop("tags", None)
    head, messages = _split_thread(body)
    if fields.title != old["title"]:
        head = _with_title(head, fields.title)
    if fields.persons != old["persons"]:
        head = _with_about(head, _about_line(fields.persons, people, from_path, shared_bucket))
    return f"---\n{frontmatter_dump(fm)}\n---\n" + head + messages


def card_view(content: str) -> dict:
    """An email card's title, people and tags, without the provenance tags.

    What a correction may change, in the shape `card_changes.changes_between`
    compares, so the answer names what the memory wrote.

        >>> card_view("---\\ntype: email\\ntitle: Trip\\ntags:\\n  - mailbox-family\\n"
        ...           "  - school\\n---\\n# Trip\\n")
        {'title': 'Trip', 'persons': [], 'tags': ['school']}
    """
    fm, _ = split_frontmatter(content)
    return {"title": str(fm.get("title") or ""),
            "persons": _strings(fm.get("persons")),
            "tags": [t for t in _strings(fm.get("tags"))
                     if not t.startswith(EMAIL_PROVENANCE_SLUGS)]}


# ── Merging the classifier's answer into the card ────────────────────────


def _merged(card: list[str], answered: list[str], named, *, always_keep=None) -> list[str]:
    """The card's items in order, less those the answer left out and the
    words name, then the answer's new items."""
    kept = [v for v in card
            if v in answered or (always_keep and always_keep(v)) or not named(v)]
    return kept + [v for v in answered if v not in kept]


def _words_of(words: str) -> list[str]:
    """The correction's words, lowercased, each also in its slugged form."""
    raw = re.findall(r"\w+", (words or "").lower())
    return [w for word in raw for w in dict.fromkeys((word, slug(word))) if w]


def _names_any(words: list[str], names: list[str]) -> bool:
    """Whether any of `names` (a tag, a person's names) appears in `words`.

    A name of several parts (`permission-slip`, `Bart Simpson`) must
    appear as those words in a row.
    """
    for name in names:
        parts = [p for p in re.split(r"[\W_]+", name.lower()) if p]
        for at in range(len(words) - len(parts) + 1):
            if parts and words[at:at + len(parts)] == parts:
                return True
    return False


def _person_names(pid: str, by_id: dict[str, Person]) -> list[str]:
    person = by_id.get(pid)
    return [pid, *person.all_names()] if person else [pid]


def _strings(value) -> list[str]:
    """A list field as its non-empty strings; a bare string is one item."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [str(s).strip() for s in items if str(s).strip()]


# ── Rewriting the card's head ────────────────────────────────────────────


def _split_thread(body: str) -> tuple[str, str]:
    """An email card's body as its head and its messages, at the first message."""
    found = re.search(r"^(<!-- mid:|## )", body, re.M)
    return (body, "") if found is None else (body[:found.start()], body[found.start():])


def _with_title(head: str, title: str) -> str:
    """The head with its `# title` line set to `title`."""
    return "\n".join(f"# {title}" if ln.startswith("# ") else ln for ln in head.split("\n"))


def _about_line(persons: list[str], people: list[Person] | None,
                from_path: str, shared_bucket: str) -> str | None:
    """The `**About**` line for the card's people (ids), None when it is about nobody."""
    by_id = {p.id: p for p in people or []}
    names = [by_id[pid].first_name if pid in by_id else pid for pid in persons]
    about = About.of(names, people)
    return ("> **About** " + about.links(from_path, bucket=shared_bucket)) if about else None


def _with_about(head: str, about_line: str | None) -> str:
    """The head with `about_line` as its `**About**` line, or none.

    The `**About**` line is the head's first `> ` line, as
    `vault_entry.render_email_thread` writes it; a card about nobody has none.
    """
    lines = [ln for ln in head.split("\n") if not ln.startswith("> **About** ")]
    if about_line:
        at = next((i for i, ln in enumerate(lines) if ln.startswith("> ")), None)
        if at is None:
            # No meta lines: the About line goes after the title and its blank line.
            at = next((i for i, ln in enumerate(lines) if ln.startswith("# ")), -2) + 2
            lines[at:at] = [about_line, ""]
        else:
            lines.insert(at, about_line)
    return "\n".join(lines)
