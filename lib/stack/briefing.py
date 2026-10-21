"""The briefing callout: summary, facts and action items on a vault page.

Every record in the vault that a model has read carries the same block
under its title, a `> [!summary]` callout holding the prose summary, the
facts and the action items. Documents, captures, emails and diary
records all render it here, so search, the agent and Obsidian read one
shape whoever wrote the page (docs/design/brain/content-pattern.md).

Stdlib only: the host CLI and every container import it.
"""

from __future__ import annotations

from .lists import DUE, action_item

# A diary entry under this many characters gets no summary. Its card shows
# the words themselves, and a summary of a text this short is as long as
# the text: on the demo instance every diary entry (95 to 250 characters)
# and every typed note under 250 characters had a summary about as long
# as its words or longer (memory cards review, C1). 400 is a short
# paragraph, read in the ten seconds a summary is meant to save. A note
# gets no summary at any length it could have been typed at: it is filed
# as a clean version of its words (the archivist's `clean_words`).
SHORT_TEXT_MAX_CHARS = 400


def is_short_text(text: str) -> bool:
    """Whether `text` is too short to be worth a summary of its own.

    Whitespace is not counted, so a note's line breaks do not decide it.

        >>> is_short_text("Bart's dentist moved to Thursday.")
        True
        >>> is_short_text("word " * 100)
        False
    """
    return len(" ".join(text.split())) < SHORT_TEXT_MAX_CHARS


def render_briefing(
    *,
    summary: str | None,
    facts: list | None,
    action_items: list | None,
    source_link: tuple[str, str] | None = None,
) -> str:
    """Render the briefing as a ``> [!summary]`` callout.

    Sections are conditional: an empty prose summary, empty facts,
    or empty action items all drop out. When everything is empty the
    callout itself is suppressed — no stale ``> [!summary]`` shell.

    ``source_link`` is ``(label, url)``; when both are non-empty it
    renders as ``[label](url)`` directly under the prose.

    Args:
        summary: Prose summary text.
        facts: List of fact strings.
        action_items: List of action item dicts or strings.
        source_link: ``(label, url)`` tuple for a source link.

    Returns:
        Briefing callout string, or "" when all sections are empty.
    """
    sections: list[str] = []

    if summary and isinstance(summary, str) and summary.strip():
        sections.append(summary.strip())

    if source_link:
        label, url = source_link
        if label and url:
            sections.append(f"[{label}]({url})")

    fact_rows = fact_lines(facts or [])
    if fact_rows:
        sections.append("**Facts**\n" + "\n".join(fact_rows))

    task_lines = action_item_lines(action_items or [])
    if task_lines:
        sections.append("**Action items**\n" + "\n".join(task_lines))

    if not sections:
        return ""

    inner = "\n\n".join(sections)
    lines = ["> [!summary]"]
    for ln in inner.split("\n"):
        lines.append(f"> {ln}" if ln else ">")
    return "\n".join(lines)


def fact_lines(facts: list) -> list[str]:
    out = []
    for f in facts:
        if isinstance(f, str) and f.strip():
            out.append(f"- {f.strip()}")
    return out


def action_item_lines(items: list) -> list[str]:
    out: list[str] = []
    for ai in items:
        line = format_action_item(ai)
        if line:
            out.append(line)
    return out


def format_action_item(ai) -> str | None:
    """An action item as a line of the summary: `- action 📅 YYYY-MM-DD`.

    The same text and due date the item gets on the topic's list
    (`stack.lists.action_item`), without the checkbox: the list holds
    whether it is done, and a box here would never be ticked. Obsidian
    reads only checkbox lines as tasks, so this copy is not counted
    twice. None for an empty or invalid item.
    """
    item = action_item(ai)
    if item is None:
        return None
    return f"- {item.text} {DUE} {item.due}" if item.due else f"- {item.text}"


def read_briefing(text: str) -> tuple[str, list[str]]:
    """The summary and facts of the first briefing callout in `text`.

    The inverse of `render_briefing` for the two parts a reader needs
    back. A page a person edited is read the same way, so a fact they
    corrected in the callout is the fact that comes back. Action items
    are left out: they live on in the todo lists they were folded into.
    Returns ``("", [])`` when the page has no briefing.
    """
    lines = text.splitlines()
    try:
        start = next(i for i, ln in enumerate(lines)
                     if ln.strip() == "> [!summary]")
    except StopIteration:
        return "", []

    inner: list[str] = []
    for ln in lines[start + 1:]:
        if not ln.startswith(">"):
            break
        inner.append(ln[2:] if ln.startswith("> ") else ln[1:])

    summary_parts: list[str] = []
    facts: list[str] = []
    for section in "\n".join(inner).split("\n\n"):
        section = section.strip()
        if not section:
            continue
        head, _, rest = section.partition("\n")
        if head == "**Facts**":
            facts = [ln[2:].strip() for ln in rest.splitlines()
                     if ln.startswith("- ") and ln[2:].strip()]
        elif head.startswith("**"):
            continue
        elif not (section.startswith("[") and section.endswith(")")):
            summary_parts.append(section)
    return "\n\n".join(summary_parts), facts
