"""A list page, and what changed between two versions of one.

A family's list lives in `todos.md`, and more than one thing writes it: the
curator merging extracted action items, a person editing it in Forgejo's
editor, and an agent asked to tidy it up. The interesting failure is not a
malformed document. It is a quiet one: six of twenty-five items gone and a
cheerful confirmation that everything went fine.

So this module answers two questions and no others:

    parse(doc)          what items does this page hold, and where
    diff(before, after) what did this edit actually do

Both are pure. No I/O, no git, no Matrix. The write path calls `diff` to turn
an opaque rewrite into a reviewable one, and hands the result back to whoever
made the edit; the same report is specific enough to serve as the commit line,
so intent comes out of what changed rather than a sentence the caller invents.

WHY REWORDING IS ITS OWN CATEGORY
    A real family list went from thirteen items to twenty-seven because each
    pass through the classifier renamed things: "Alternative Dachbox" came back
    as "suchen", then "recherchieren", then "prüfen", then "besorgen". Nothing
    was lost and nothing was really added, but a report that called that four
    deletions and four additions would bury the one signal that matters.
    Pairing stays deliberately conservative -- an unrelated new item is never
    guessed to "replace" a deleted one, because hiding a deletion is the single
    thing this module exists to prevent.

SEE ALSO
    docs/design/brain/write-layer.md   why this is the first piece
    stacklets/memory/bot/cli/todo_list.py   the task-line grammar it shares
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# An Obsidian task line, open or done: group 1 is the box char, group 2 the
# task text. Same grammar `todo_list.py` reads and writes, restated here
# because this module is the one both sides will eventually share.
_TASK = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
_SECTION = re.compile(r"^\s*##\s+(.+?)\s*$")

# How close two texts must be before one is called a rewording of the other,
# rather than a deletion and an unrelated addition. Tuned to catch a suffix
# being appended ("Kühlbox" -> "Kühlbox mitbringen", which scores only 0.56 on
# raw similarity) while leaving genuinely different items unpaired.
_SIMILAR_ENOUGH = 0.8


@dataclass(frozen=True)
class Item:
    """One task line: its words, whether it is ticked, and which list it is in.

    `section` is the `##` heading above it, or `""` for a page that has no
    headings at all -- which is every list that exists today.
    """

    text: str
    done: bool
    section: str = ""


@dataclass(frozen=True)
class Change:
    """What one edit did, in the terms a person would use to check it.

    `removed` is the only field that means something was destroyed. Ticking an
    item off, renaming it, or moving it under a heading all leave it in the
    list, so they are reported separately and never inflate the alarm.
    """

    struck: list[str] = field(default_factory=list)
    reopened: list[str] = field(default_factory=list)
    added: list[Item] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    reworded: list[tuple[str, str]] = field(default_factory=list)
    moved: list[tuple[str, str, str]] = field(default_factory=list)

    def any(self) -> bool:
        """True when the edit did anything at all."""
        return bool(self.struck or self.reopened or self.added
                    or self.removed or self.reworded or self.moved)

    def destructive(self) -> bool:
        """True when items left the list without being ticked off.

        The question a caller actually has to gate on. Everything else is
        informational; this is the one that should stop an unattended write.
        """
        return bool(self.removed)

    def summary(self) -> str:
        """One line naming what happened, losses first and named in full.

        Counts are not checkable by a family member -- "8 items became 7" tells
        nobody which one went. So a removal always names every item, while the
        ordinary categories stay short.
        """
        if not self.any():
            return "no change"

        parts: list[str] = []
        if self.removed:
            parts.append(f"REMOVED {len(self.removed)}: " + ", ".join(self.removed))
        if self.struck:
            parts.append(f"ticked off {len(self.struck)}: {_few(self.struck)}")
        if self.reopened:
            parts.append(f"reopened {len(self.reopened)}: {_few(self.reopened)}")
        if self.added:
            parts.append(f"added {len(self.added)}: "
                         f"{_few([i.text for i in self.added])}")
        if self.reworded:
            parts.append(f"reworded {len(self.reworded)}: " + ", ".join(
                f"{old} -> {new}" for old, new in self.reworded[:3]))
        if self.moved:
            parts.append(f"moved {len(self.moved)}")
        return "; ".join(parts)


def parse(doc: str) -> list[Item]:
    """Read a list page into its items, in document order.

    Everything that is not a task line is context: the title, prose, blank
    lines. A `##` heading opens a named list and applies to the items under it.
    """
    items: list[Item] = []
    section = ""
    for line in (doc or "").splitlines():
        if heading := _SECTION.match(line):
            section = heading.group(1).strip()
            continue
        if task := _TASK.match(line):
            items.append(Item(
                text=task.group(2).strip(),
                done=task.group(1) != " ",
                section=section,
            ))
    return items


def diff(before: str, after: str) -> Change:
    """Say what turning `before` into `after` did to the list."""
    by_before = _group(parse(before))
    by_after = _group(parse(after))

    struck: list[str] = []
    reopened: list[str] = []
    moved: list[tuple[str, str, str]] = []
    gone: list[Item] = []
    fresh: list[Item] = []

    # Items whose text survived: same item, so any difference is a state or a
    # location change, never a loss. Counting occurrences rather than assuming
    # uniqueness keeps a list that already holds duplicates readable.
    for key, olds in by_before.items():
        news = by_after.get(key, [])
        for old, new in zip(olds, news):
            if old.done != new.done:
                (struck if new.done else reopened).append(new.text)
            if old.section != new.section:
                moved.append((new.text, old.section, new.section))
        gone.extend(olds[len(news):])

    for key, news in by_after.items():
        fresh.extend(news[len(by_before.get(key, [])):])

    reworded, removed, added = _pair_rewordings(gone, fresh)
    return Change(
        struck=struck, reopened=reopened, added=added,
        removed=removed, reworded=reworded, moved=moved,
    )


# ── internals ───────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """Whitespace- and case-insensitive identity of a task."""
    return " ".join(text.split()).lower()


def _group(items: list[Item]) -> dict[str, list[Item]]:
    """Items keyed by identity, preserving document order within a key."""
    out: dict[str, list[Item]] = {}
    for item in items:
        out.setdefault(_norm(item.text), []).append(item)
    return out


def _is_rewording(old: str, new: str) -> bool:
    """Whether `new` is plausibly `old` restated rather than a different item.

    Two shapes count. One text extending the other at a word boundary covers
    the classifier's habit of appending a verb, which raw similarity scores too
    low to catch on short items. Otherwise a high similarity ratio covers a
    swapped word. Anything else stays unpaired, so a deletion is never
    explained away by an unrelated arrival.
    """
    a, b = _norm(old), _norm(new)
    longer, shorter = (a, b) if len(a) >= len(b) else (b, a)
    if longer.startswith(shorter + " "):
        return True
    return SequenceMatcher(None, a, b).ratio() >= _SIMILAR_ENOUGH


def _pair_rewordings(gone: list[Item], fresh: list[Item]):
    """Match departures to arrivals that are the same item under new words."""
    reworded: list[tuple[str, str]] = []
    removed: list[str] = []
    claimed: set[int] = set()

    for old in gone:
        match = next(
            (i for i, new in enumerate(fresh)
             if i not in claimed and _is_rewording(old.text, new.text)),
            None,
        )
        if match is None:
            removed.append(old.text)
            continue
        claimed.add(match)
        reworded.append((old.text, fresh[match].text))

    added = [new for i, new in enumerate(fresh) if i not in claimed]
    return reworded, removed, added


def _few(names: list[str], cap: int = 3) -> str:
    """The first few names, with a count for the rest."""
    if len(names) <= cap:
        return ", ".join(names)
    return ", ".join(names[:cap]) + f", and {len(names) - cap} more"
