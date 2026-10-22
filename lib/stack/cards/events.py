"""The events a note's clean version is rebuilt from: corrections and a person's text edits.

ADR-018 §7. A card's history is its event log, and the card is its
projection. Two kinds of event change what a note's clean version says:

- a correction, a reply in the chat or a `--msg` note, kept as a source
  of the card (`stack.cards.sources`);
- a person's edit of the clean version, read from the card's history in
  git (`stack.cards.history`), never stored a second time.

A rebuild gives them to the model in time order, one message each, after
the clean version they apply to (`docs/bot/card_conversation.py`):

    Correction("homer", "2026-10-20T09:00:00+00:00", "we leave at 5, not 4").message()
    # 'Homer: we leave at 5, not 4'
    TextEdit("marge", "2026-10-20T10:00:00+02:00",
             (("Bring 3 sleeping bags.", "Bring 4 sleeping bags."),)).message()
    # "Marge edited the card: changed 'Bring 3 sleeping bags.' to 'Bring 4 sleeping bags.'"

The host reads the edits and hands them to the bot runner as JSON
(`to_json`, `edit_from_json`). Stdlib only: the host CLI and the bot
runner import it.
"""

from __future__ import annotations

import datetime as _dt
import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import ClassVar

# A list item's marker, which an edit's message leaves out: the measured
# messages name the item's words ("changed 'Budget max 200.' to ...").
_ITEM_MARKER = re.compile(r"^\s*[-*] ")
# Where an event with no recorded time sorts: before every timed one,
# in the order it was given.
_EARLIEST = _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)


def name_of(person: str) -> str:
    """A person's id as their name: `marge` is `Marge`."""
    return person[:1].upper() + person[1:] if person.islower() else person


@dataclass(frozen=True)
class Correction:
    """A reply or a note that corrects the note, by `person` at `at`."""

    person: str
    at: str
    words: str
    # Whether the event edits the clean version's text; `text_of` reads it.
    edits_the_text: ClassVar[bool] = False

    def message(self) -> str:
        return f"{name_of(self.person)}: {self.words}"


@dataclass(frozen=True)
class TextEdit:
    """A person's edit of the clean version: each change as (old, new)
    lines, list markers left out; "" for nothing on that side."""

    person: str
    at: str
    changes: tuple[tuple[str, str], ...]
    edits_the_text: ClassVar[bool] = True

    def message(self) -> str:
        parts = []
        for old, new in self.changes:
            if old and new:
                parts.append(f"changed '{old}' to '{new}'")
            elif new:
                parts.append(f"added '{new}'")
            else:
                parts.append(f"removed '{old}'")
        return f"{name_of(self.person)} edited the card: {'; '.join(parts)}"

    def to_json(self) -> dict:
        return {"person": self.person, "at": self.at, "changes": [list(c) for c in self.changes]}


Event = Correction | TextEdit


def edit_from_json(record: dict) -> TextEdit:
    return TextEdit(str(record.get("person") or ""), str(record.get("at") or ""),
                    tuple((str(old), str(new)) for old, new in record.get("changes") or []))


def changes_between(before: str, after: str) -> tuple[tuple[str, str], ...]:
    """What changed from one clean version to the next, line by line.

    Each run of changed lines is one change: its removed lines and its
    added lines, each joined with " / ", list markers and empty lines
    left out.

        >>> changes_between("- Budget max 200.\\n- Towels", "- Budget max 250.\\n- Towels")
        (('Budget max 200.', 'Budget max 250.'),)
    """
    old, new = before.splitlines(), after.splitlines()
    found = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        sides = [" / ".join(_ITEM_MARKER.sub("", ln).strip() for ln in lines if ln.strip())
                 for lines in (old[i1:i2], new[j1:j2])]
        if any(sides):
            found.append((sides[0], sides[1]))
    return tuple(found)


def in_order(events: Iterable[Event]) -> list[Event]:
    """`events` in the order they happened; one with no time first, as given."""
    return sorted(events, key=lambda e: _time(e.at))


def text_of(events: Sequence[Event]) -> str:
    """What the events say, without who said it: each correction's words,
    each edit's old and new lines, one per line. A check reads this
    (`clean_words.kept_rebuilt`); who wrote an event is not a value of it."""
    lines: list[str] = []
    for e in events:
        if e.edits_the_text:
            lines += [side for change in e.changes for side in change if side]  # pyright: ignore[reportAttributeAccessIssue]
        else:
            lines.append(e.words)  # pyright: ignore[reportAttributeAccessIssue]
    return "\n".join(lines)


def _time(at: str) -> _dt.datetime:
    try:
        moment = _dt.datetime.fromisoformat(at)
    except (TypeError, ValueError):
        return _EARLIEST
    return moment if moment.tzinfo else moment.replace(tzinfo=_dt.timezone.utc)
