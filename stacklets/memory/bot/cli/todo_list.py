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


def _norm(text: str) -> str:
    """Whitespace- and case-normalised task text for matching."""
    return " ".join(text.split()).lower()


def set_todo_done(doc: str, item: str, *, done: bool) -> tuple[str, str]:
    """Flip the task matching `item` to done (`[x]`) or open (`[ ]`).

    The write counterpart to `read_todos`, kept in the module that owns the
    task-line grammar. You identify the task by the **start** of its text, not
    the whole line -- "buy sunscreen" finds "buy sunscreen for Bart" -- because
    the striker (a family member in chat, or the agent on their behalf) rarely
    quotes it exactly. An exact text wins over a longer sibling ("milk" over
    "milk and eggs"). Returns `(new_doc, matched_text)` with the exact task
    struck, so the caller can echo precisely what happened.

    Raises ValueError when nothing starts with the string, or when it starts
    more than one task -- the message then lists the matches so the caller can
    ask for a string that identifies just one. The single "same text open and
    already done" case is not ambiguous: it resolves to the copy whose state
    would actually change.
    """
    needle = _norm(item)
    lines = doc.splitlines(keepends=True)
    tasks = [(i, m.group(2).strip(), m.group(1) != " ")
             for i, line in enumerate(lines)
             if (m := _TASK_RE.match(line))]

    hits = [t for t in tasks if _norm(t[1]).startswith(needle)]
    if not hits:
        raise ValueError(f"no todo matching {item!r}")
    if exact := [t for t in hits if _norm(t[1]) == needle]:
        hits = exact

    if len(hits) > 1:
        # A string that also lands on an already-done copy resolves to the one
        # whose state would flip; otherwise it is genuinely ambiguous.
        changeable = [t for t in hits if t[2] != done]
        if len(changeable) == 1:
            hits = changeable
        else:
            opts = "\n  ".join(dict.fromkeys(t[1] for t in hits))
            raise ValueError(
                "more than one match; the string must match only one item:\n  " + opts)

    idx, matched, _ = hits[0]
    box = "x" if done else " "
    lines[idx] = re.sub(r"\[[ xX]\]", f"[{box}]", lines[idx], count=1)
    return "".join(lines), matched
