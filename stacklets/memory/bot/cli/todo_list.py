"""Extracting `action_items` from list-notes, rendering them as todos.

One vocabulary across the stack: **action_items** are the extracted concept
(documents already produce them; a note announced as a list produces them too),
and **todos** are those action_items *transformed* into rendered, tickable
`- [ ]` lines (`_format_action_item` in vault_entry.py is that transform). This
module is the note half of the extraction plus the `todos.md` surface.

A list is something a family member *wrote on purpose* — a note whose first line
announces it ("Liste Bus Erweiterungen:", "Todo:", "Einkaufsliste:"). We
deliberately do NOT mine arbitrary notes: the capture classifier leaves
action_items out on purpose, because a pasted Reddit thread must not manufacture
a household todo (see tests/stacklets/test_capture_prompt.py). So detection here
is a narrow, deterministic signal — an explicit marker — not an LLM guess. High
precision, zero false positives, no model call.

The list lives as a source `todos.md` in the vault (Obsidian task lines), so the
wiki renders it and a family member ticks items off in Forgejo's editor.
"""

from __future__ import annotations

import re

# First-line markers that announce "this note is a list". German + English,
# the two household languages we ship. Matched case-insensitively, with or
# without a trailing colon, as the whole head or its leading word.
_LIST_MARKERS = (
    "liste", "todo", "to-do", "to do", "todos",
    "aufgaben", "einkaufsliste", "einkauf", "checkliste", "checklist", "list",
)

# An Obsidian task line, open or done: group 1 is the box char (" " open,
# "x"/"X" done), group 2 the task text.
_TASK_RE = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")


def detect_list(body: str) -> tuple[str, list[str]] | None:
    """Return ``(title, action_items)`` when `body` is an announced list.

    The signal is deliberate: the first non-empty line is a list marker
    (optionally `:`-terminated) and at least one item follows. The action_items
    are the remaining non-empty lines, verbatim. Anything without the marker
    returns None — we never infer a list from prose.
    """
    lines = [ln.strip() for ln in body.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    first = lines[0]
    head = first[:-1].strip() if first.endswith(":") else first
    if not _is_marker(head):
        return None
    return head, lines[1:]


def _is_marker(head: str) -> bool:
    h = head.lower()
    return any(h == m or h.startswith(m + " ") for m in _LIST_MARKERS)


def render_todo_doc(title: str, action_items: list[str]) -> str:
    """A fresh `todos.md`: a title heading plus one todo line per action item."""
    lines = [f"# {title}", ""]
    lines += [f"- [ ] {ai}" for ai in action_items]
    return "\n".join(lines) + "\n"


def add_items(existing: str, action_items: list[str]) -> str:
    """Append action items not already present to an existing `todos.md`.

    Matches on task text across both open and done lines, so a re-sent item
    neither doubles up nor resurrects something already ticked off. Existing
    lines (including `- [x]` done items) are left untouched.
    """
    seen = {m.group(2).strip()
            for ln in existing.splitlines()
            if (m := _TASK_RE.match(ln))}
    fresh = [ai for ai in action_items if ai.strip() and ai.strip() not in seen]
    if not fresh:
        return existing
    body = existing.rstrip("\n")
    body += "\n" + "\n".join(f"- [ ] {ai.strip()}" for ai in fresh)
    return body + "\n"


def update_todo_doc(existing: str | None, title: str,
                    action_items: list[str]) -> str:
    """Create the doc if missing, otherwise append the new action items."""
    if not existing:
        return render_todo_doc(title, action_items)
    return add_items(existing, action_items)


def read_todos(doc: str) -> tuple[list[str], list[str]]:
    """Split a rendered `todos.md` into ``(open, done)`` task texts, file order.

    The read side of the surface: `- [ ]` lines are open, `- [x]`/`- [X]` done;
    the title and blank lines are ignored. Kept beside the writers so one module
    owns what a todo line is -- the CLI list command reads through here.
    """
    open_items: list[str] = []
    done_items: list[str] = []
    for line in doc.splitlines():
        m = _TASK_RE.match(line)
        if not m:
            continue
        bucket = open_items if m.group(1) == " " else done_items
        bucket.append(m.group(2).strip())
    return open_items, done_items
