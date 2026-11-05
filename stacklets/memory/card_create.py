"""A new card and a source more: what the cards API writes for them (epic memory-sources, card 2).

A writer hands the memory a card as data, never as a file: its type, its
fields, its text, its first source and what made the text. The memory
names the file and writes the card and its sidecar in the notes' format
(`note_card.render_note`, `stack.cards.sources`), so every writer's note
reads alike. Later a source is only ever added (ADR-018 §4): its quote
goes last under `## Sources`, its record last in the sidecar.

    POST /cards                 {"type": "note", "in": "person-homer",
                                 "fields": {"title": ..., "date": ..., "persons": [...], "tags": [...]},
                                 "text": "...", "source": {"role": "original", ..., "quote": "..."},
                                 "made": {...}, "resource": "<link>", "words": "...",
                                 "via": "archivist-bot"}
    POST /cards/<id>/sources    {"source": {"role": "reply", ..., "quote": "..."},
                                 "text": "...", "fields": {...}, "made": {...},
                                 "resource": "<link>", "via": "archivist-bot"}

A source may come with what it changed: the text rewritten from it, the
fields (a merge patch, as `PATCH /cards/<id>` takes them) and what made
the text. Then the source, the text and the fields are one commit, as a
reply that refiles a note is one commit at the archivist today. Without
them, the text and the fields stay as they are.

`in` is the card id of the person or the topic the note belongs to. The
file is named as the archivist names it (`stack.vault.capture_filepath`),
after the note's title and its identity: its link (`resource`), or else
its words as they were sent (`words`), or else the words of its first
source. The words as sent are what the archivist names a note after; they
differ from the quote when the message had a caption or surrounding
whitespace, and a photo has none (""). A note with the same identity in the same
place is the same note (`stack.vault.same_capture`), and a second create
of it is refused with its id.

This module holds the pure part: reading a request and making the files.
`card_edit.CardEditor` answers the routes, checks the schema and commits.
Only `note` is created yet; the other types follow with their writers.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from stack.cards.commits import MEMORY_BOT
from stack.cards.sources import ORIGINAL, NOTE, REPLY, Source, render_sources, sidecar_text, source_from
from stack.people import Person
from stack.vault import capture_filepath

from .note_card import render_note

# What can be created through the API so far; the other types follow.
CREATABLE = ("note",)
# A create says what the card is; where it lies and how it is committed are the memory's.
_KEYS = frozenset({"type", "in", "fields", "text", "source", "made", "resource", "words", "via"})
_SOURCE_KEYS = frozenset({"source", "text", "fields", "made", "resource", "via"})
# The fields a note's card is written with first, in the archivist's order
# (`vault_entry.capture_frontmatter`); any other field follows as given.
_FIRST = ("title", "date", "persons", "tags")
_ROLES = (ORIGINAL, REPLY, NOTE)
_PERSON, _TOPIC = "person-", "topic-"


class Malformed(ValueError):
    """A request that is not a create or a source as the module docstring describes it."""


@dataclass(frozen=True)
class NewCard:
    """A create request, read: what the card is, without a path or a commit."""

    type: str
    belongs_to: str
    fields: dict
    text: str
    source: Source
    made: dict
    resource: str
    via: str
    words: str | None = None

    @property
    def identity(self) -> str:
        """What makes two notes the same note: the link, or else the words as
        sent, or else the first source's words.

        Words sent as "" (a photo) are kept: the archivist names such a note
        after them, so None, not "", says that none were given."""
        if self.resource:
            return self.resource
        return self.words if self.words is not None else self.source.words

    @property
    def title(self) -> str:
        return str(self.fields.get("title") or "")

    def trailers(self) -> dict[str, str]:
        """The commit's trailers, as the archivist's filing has them."""
        return _trailers(self.resource, self.made)


@dataclass(frozen=True)
class SourceAdded:
    """An add-source request, read: the source, and what it changed, if anything.

    `patch` is the change to the card it comes with (`fields`, `text`),
    {} for none; `made` what made the new text, None when nothing did.
    """

    source: Source
    patch: dict
    made: dict | None
    resource: str
    via: str

    def trailers(self) -> dict[str, str]:
        """The trailers of a refile, as the archivist's has them; none for a source alone."""
        return _trailers(self.resource, self.made) if self.made is not None else {}


def _trailers(resource: str, made: Mapping[str, object]) -> dict[str, str]:
    model = str(made.get("model") or "")
    return {"Source": resource or "(paste)", **({"Model": model} if model else {})}


# ── Reading a request ────────────────────────────────────────────────────

def new_card(body: bytes) -> NewCard:
    """The create request in `body`; raises `Malformed` for one that is not."""
    data = _object(body)
    if extra := sorted(set(data) - _KEYS):
        raise Malformed(f"a create names no {', '.join(extra)}: the memory names the file "
                        "and the commit")
    fields = data.get("fields")
    if not isinstance(fields, dict):
        raise Malformed("a create needs `fields`, an object")
    made = data.get("made") or {}
    if not isinstance(made, dict):
        raise Malformed("`made` is an object")
    words = data.get("words")
    if words is not None and not isinstance(words, str):
        raise Malformed("a create needs `words` as text")
    return NewCard(type=_text(data, "type"), belongs_to=_text(data, "in"), fields=fields,
                   text=_text(data, "text", default=""), source=_source(data.get("source")),
                   made=made, resource=_text(data, "resource", default=""),
                   via=_text(data, "via", default=MEMORY_BOT), words=words)


def new_source(body: bytes) -> SourceAdded:
    """An add-source request; raises `Malformed` for one that is not."""
    data = _object(body)
    if "source" not in data or set(data) - _SOURCE_KEYS:
        raise Malformed('a source is sent as {"source": {...}}, with `text`, `fields`, '
                        '`made`, `resource` and `via` at most')
    made = data.get("made")
    if made is not None and not isinstance(made, dict):
        raise Malformed("`made` is an object")
    return SourceAdded(source=_source(data["source"]),
                       patch={k: data[k] for k in ("fields", "text") if k in data}, made=made,
                       resource=_text(data, "resource", default=""),
                       via=_text(data, "via", default=MEMORY_BOT))


def _object(body: bytes) -> dict:
    try:
        data = json.loads(body or b"null")
    except ValueError as e:
        raise Malformed(f"the body is no JSON: {e}") from e
    if not isinstance(data, dict):
        raise Malformed("the body is a JSON object")
    return data


def _source(data: object) -> Source:
    if not isinstance(data, dict) or not isinstance(data.get("quote"), str) \
            or not data["quote"].strip():
        raise Malformed("a source is an object with its `quote`")
    if data.get("role") not in _ROLES:
        raise Malformed(f"a source's `role` is one of {', '.join(_ROLES)}")
    return source_from(data, data["quote"])


def _text(data: dict, key: str, *, default: str | None = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise Malformed(f"a create needs `{key}` as text")
    return value


# ── Where a new card lies ────────────────────────────────────────────────

def folder_of(belongs_to: str, *, people: Sequence[Person], topic_card: str | None,
              shared_bucket: str) -> str | None:
    """The folder a card that belongs to `belongs_to` is filed under, None for no such owner.

    A person's card files into their own bucket, a topic's into the
    topic's folder: where its card lies (`topic_card`, its path), or for a
    topic without a card yet, the shared bucket's folder of that name, as
    the archivist files into it.

    >>> folder_of("topic-camping", people=[], topic_card=None, shared_bucket="family")
    'family/camping'
    """
    if belongs_to.startswith(_PERSON):
        pid = belongs_to.removeprefix(_PERSON)
        return pid if any(p.id == pid for p in people) else None
    if belongs_to.startswith(_TOPIC) and (topic := belongs_to.removeprefix(_TOPIC)):
        return topic_card.rsplit("/", 1)[0] if topic_card else f"{shared_bucket}/{topic}"
    return None


def note_path(card: NewCard, folder: str) -> str:
    """Where the note lies, named as the archivist names a note's file."""
    return capture_filepath(folder, "note", str(card.fields.get("date") or ""),
                            card.title or None, card.identity)


# ── The files ────────────────────────────────────────────────────────────

def note_fields(card: NewCard, identity: Mapping[str, object]) -> dict:
    """The note's frontmatter: its type and id first, then its fields in the archivist's order."""
    given = dict(card.fields)
    given.pop("type", None)
    for key in identity:
        given.pop(key, None)
    first = {k: given.pop(k) for k in _FIRST if given.get(k) not in (None, "", [])}
    return {"type": card.type, **identity, **first, **given}


def note_files(card: NewCard, fields: dict, path: str, *, by: str,
               people: Sequence[Person], shared_bucket: str) -> tuple[str, str]:
    """The note's card and its sidecar, as `render_note` and `sidecar_text` write them.

    `by` files it: the sidecar's `filed_by`, as the archivist records its sender.
    """
    text = render_note(frontmatter=fields, sources=[card.source],
                       persons=list(fields.get("persons") or []), people=list(people),
                       from_path=path, shared_bucket=shared_bucket, clean=card.text)
    return text, sidecar_text(card_id=str(fields.get("card_id") or ""), filed_by=by,
                              sources=[card.source], made=card.made)


def with_source(card: str, sidecar: str, source: Source, *,
                made: Mapping[str, object] | None = None) -> tuple[str, str]:
    """`card` and `sidecar` with `source` appended: its quote last under `## Sources`
    (the card's last section), its record last in the sidecar. `made`, when
    given, replaces how the card was made, as `sidecar_text` writes it."""
    quoted = render_sources([source]).split("\n\n", 1)[1]
    side = json.loads(sidecar)
    side["sources"] = [*side.get("sources", []), source.record()]
    if made is not None:
        side["made"] = {k: v for k, v in made.items() if v}
    return (card.rstrip("\n") + "\n\n" + quoted + "\n",
            json.dumps(side, ensure_ascii=False, indent=2) + "\n")

