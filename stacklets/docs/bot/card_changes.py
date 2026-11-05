"""What a correction changed on a card: the difference of two classifications.

    >>> changes_between(
    ...     {"title": "Elternabend", "persons": ["bart"], "tags": ["school"]},
    ...     {"title": "Elternabend", "persons": ["bart", "lisa"],
    ...      "tags": ["school", "gemuesekiste2"]},
    ... )
    [Change(field='person', kind='added', value='lisa', before=''), \
Change(field='tag', kind='added', value='gemuesekiste2', before='')]

After a family member corrects a filing in its thread, the archivist's
answer says what the correction did ("tag gemuesekiste2 added") before it
shows the card again, because "Reclassified with your note: <title>"
tells the reader nothing. This module only compares; the reply presenter
puts the changes into words through the archivist's translator.

`before` is the classification the person saw when they wrote the
correction: the `data` of the filing envelope the correction anchors to
(`ArchivistBot._collect_correction_chain`). `after` is what the
classifier made of it with the correction. Both are plain dicts with
`title`, `persons` (ids) and `tags`; anything else in them is ignored.
Pure: no I/O, no Matrix.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Change:
    """One change to a card's title, people or tags.

    `field` is `title`, `person` or `tag`; `kind` is `added`, `removed` or
    `changed` (only a title changes). `value` is the new title, or the
    person id or tag that came or went; `before` is the old title.

        Change(field="tag", kind="added", value="gemuesekiste2")
    """

    field: str
    kind: str
    value: str
    before: str = ""


def changes_between(before: dict, after: dict) -> list[Change]:
    """The changes from `before` to `after`, title first, then people, then tags.

    Lists keep the order `after` has them for what was added and the order
    `before` had them for what was removed. Returns [] when the
    correction changed none of the three.
    """
    changes: list[Change] = []
    old_title, new_title = _text(before.get("title")), _text(after.get("title"))
    if new_title and new_title != old_title:
        changes.append(Change("title", "changed", new_title, before=old_title))
    changes += _list_changes("person", _strings(before.get("persons")),
                             _strings(after.get("persons")))
    changes += _list_changes("tag", _strings(before.get("tags")),
                             _strings(after.get("tags")))
    return changes


# ── Helpers ──────────────────────────────────────────────────────────────


def _list_changes(field: str, old: list[str], new: list[str]) -> list[Change]:
    """What came into and what left a list of people or tags."""
    added = [Change(field, "added", v) for v in new if v not in old]
    removed = [Change(field, "removed", v) for v in old if v not in new]
    return added + removed


def _strings(value) -> list[str]:
    """A list field as its stripped, non-empty strings; a bare string is one item."""
    items = [value] if isinstance(value, str) else value if isinstance(value, list) else []
    return [s.strip() for s in items if isinstance(s, str) and s.strip()]


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""
