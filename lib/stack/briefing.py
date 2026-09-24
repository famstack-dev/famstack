"""The briefing callout: summary, facts and action items on a vault page.

Every record in the vault that a model has read carries the same block
under its title, a `> [!summary]` callout holding the prose summary, the
facts and the action items. Documents, captures, emails and diary
records all render it here, so search, the agent and Obsidian read one
shape whoever wrote the page (docs/design/brain/content-pattern.md).

Stdlib only: the host CLI and every container import it.
"""

from __future__ import annotations


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
    """``{action, due}`` → ``- [ ] action — YYYY-MM-DD`` or ``- [ ] action``.

    Args:
        ai: Action item as a dict (with "action" and optional "due")
            or a plain string.

    Returns:
        Formatted checkbox line, or None when the item is empty/invalid.
    """
    if isinstance(ai, str):
        return f"- [ ] {ai.strip()}" if ai.strip() else None
    if not isinstance(ai, dict):
        return None
    action = (ai.get("action") or "").strip()
    if not action:
        return None
    due = ai.get("due")
    if isinstance(due, str):
        due_clean = due.strip()
        if due_clean and due_clean.lower() not in ("null", "none", "n/a"):
            return f"- [ ] {action} — {due_clean}"
    return f"- [ ] {action}"
