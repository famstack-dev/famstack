"""Chat reply rendering from classification data.

Pure formatting: takes a classification dict and resolved metadata,
produces a list of text parts that the caller joins and optionally
translates.

The contract: one function, one shape, no env vars, no translations,
no Matrix knowledge. The archivist layer handles i18n and Matrix
sending around this.

Extracted from archivist._process_document's reply-rendering section
so the formatting logic can be unit-tested against canned classifications
and reused by the deriver (Phase 2) for its own notification surface."""
from __future__ import annotations


def format_reply_parts(
    *,
    title: str | None,
    display_name: str,
    doc_id: int,
    resolved_topics: list[str],
    resolved_persons: list[str],
    resolved_type: str | None,
    resolved_correspondent: str | None,
    date_applied: str | None,
    classification: dict,
    created_new: list[str] | None,
    reformat_failed: bool,
    link: str,
) -> list[str]:
    """Render the parts of a "document filed" chat reply.

    Takes the classification result and returns a list of text blocks
    that, when joined with newlines, form the full reply. The caller
    is responsible for translation and Matrix sending.

    Layout (for scanning in Element):

        Filed: Cursor - Pro Subscription USD 192.00 (#10)

        Subscription | Invoice | Cursor | 2025-03-27

        Summary text from LLM...

        - Invoice number: 4182A976 0001
        - Amount due: USD 192.00

        Payment of USD 192.00 due (due 2025-03-27)

        http://...

    The parts are returned as a flat list so the caller can:
    - Join them with "\\n" for the chat reply
    - Iterate over them for analytics or logging
    - Swap in translations by mapping each part to a key

    Args:
        title: LLM-provided title, or None.
        display_name: Original filename for display.
        doc_id: Paperless document ID.
        resolved_topics: Matched category tags.
        resolved_persons: Matched person tags.
        resolved_type: Resolved document type.
        resolved_correspondent: Resolved correspondent.
        date_applied: Date field applied by the classifier.
        classification: Full classification dict (summary, facts, action_items).
        created_new: Tags that were newly created in Paperless.
        reformat_failed: Whether the reformat pass failed.
        link: Paperless public URL for the document details page.

    Returns:
        List of text parts forming the reply. Empty parts serve as
        paragraph separators.
    """
    display_title = title or display_name
    parts: list[str] = [f"Filed: {display_title} (#{doc_id})"]

    # Compact metadata line: topic(s) | person(s) | type | from | date.
    meta_parts: list[str] = []
    meta_parts.extend(resolved_topics)
    meta_parts.extend(resolved_persons)
    if resolved_type:
        meta_parts.append(resolved_type)
    if resolved_correspondent:
        meta_parts.append(resolved_correspondent)
    if date_applied:
        meta_parts.append(date_applied)
    if meta_parts:
        parts.append("")
        parts.append("  " + " | ".join(meta_parts))

    # Prose summary
    doc_summary = classification.get("summary", "")
    if doc_summary and isinstance(doc_summary, str):
        parts.append("")
        parts.append(f"  {doc_summary}")

    # Facts
    facts = classification.get("facts", [])
    if facts and isinstance(facts, list):
        fact_lines = [f for f in facts if isinstance(f, str) and f.strip()]
        if fact_lines:
            parts.append("")
            for f in fact_lines[:5]:
                parts.append(f"  - {f}")

    # Action items
    action_items = classification.get("action_items", [])
    if action_items and isinstance(action_items, list):
        valid_actions = [a for a in action_items if isinstance(a, dict) and a.get("action")]
        if valid_actions:
            parts.append("")
            for a in valid_actions[:3]:
                due = a.get("due", "")
                due_str = f" (due {due})" if due else ""
                parts.append(f"  {a['action']}{due_str}")

    # Newly created tags notice
    if created_new:
        parts.append("")
        parts.append(f"  New in Paperless: {', '.join(created_new)}")

    # Reformat failure notice
    if reformat_failed:
        parts.append("")
        parts.append("  Reformat failed — showing raw OCR text.")

    # Link
    if link:
        parts.append("")
        parts.append(f"  {link}")

    return parts


def format_capture_parts(
    *,
    title: str | None,
    source_title_hint: str | None,
    resolved_topics: list[str],
    resolved_persons: list[str],
    classification: dict,
    display_link: str,
) -> list[str]:
    """Render the parts of a "capture saved" chat reply.

    Mirrors the document-filed reply layout: title line, optional
    meta row (topics | persons), summary paragraph, facts bullets,
    action items. No Paperless link — captures don't have one.

    Args:
        title: LLM-provided title, or None.
        source_title_hint: Extractor's title hint (fallback).
        resolved_topics: Matched category tags.
        resolved_persons: Matched person tags.
        classification: Full classification dict (summary, facts, action_items).
        display_link: Source URL or "(pasted text)" for the reply footer.

    Returns:
        List of text parts forming the reply.
    """
    display_title = title or source_title_hint or "Capture"
    parts: list[str] = [f"Captured: {display_title}"]

    # Meta line
    meta_parts: list[str] = []
    topics = classification.get("topics") or []
    if isinstance(topics, str):
        topics = [topics]
    meta_parts.extend(t for t in topics if isinstance(t, str))
    persons = classification.get("persons") or []
    if isinstance(persons, str):
        persons = [persons]
    meta_parts.extend(p for p in persons if isinstance(p, str))
    if meta_parts:
        parts.append("")
        parts.append("  " + " | ".join(meta_parts))

    # Summary
    summary = classification.get("summary")
    if summary and isinstance(summary, str):
        parts.append("")
        parts.append(f"  {summary}")

    # Facts
    facts = classification.get("facts") or []
    if isinstance(facts, list):
        fact_lines = [f for f in facts if isinstance(f, str) and f.strip()]
        if fact_lines:
            parts.append("")
            for f in fact_lines[:5]:
                parts.append(f"  - {f}")

    # Action items
    action_items = classification.get("action_items") or []
    if isinstance(action_items, list):
        valid = [a for a in action_items if isinstance(a, dict) and a.get("action")]
        if valid:
            parts.append("")
            for a in valid[:3]:
                due = a.get("due", "")
                due_str = f" (due {due})" if due else ""
                parts.append(f"  {a['action']}{due_str}")

    # Link
    parts.append("")
    parts.append(f"  {display_link}")

    return parts
