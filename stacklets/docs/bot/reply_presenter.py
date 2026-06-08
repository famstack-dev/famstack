"""Chat-reply rendering for the archivist — pure layout, given a translator.

Takes a `t(key, **kwargs)` callable (the archivist's bilingual lookup)
plus the classification / enrichment data, and returns the text the bot
sends. No Matrix, no env, no messages.yml — the archivist owns `t` and
the sending; this owns the layout.

Extracted from `_process_document` / `_render_capture_reply` so the
rendering is unit-testable in both languages and a document pipeline
can stay Matrix-free, handing back structured data the orchestrator
renders here. Supersedes classify_format.py, which hard-coded English
and so could never sit on the bilingual reply path.
"""

from __future__ import annotations

from typing import Callable

# `t(key, **kwargs) -> str`: the archivist's translation lookup.
Translator = Callable[..., str]


def render_filing_reply(
    t: Translator,
    *,
    display_title: str,
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
) -> str:
    """Render the happy-path "document filed" reply.

    Layout, optimised for scanning in Element:

        ✅ Filed: ADAC - Kfz-Versicherung (#10)

        Insurance | Homer | Invoice | ADAC | 2026-03-15

        Annual car insurance renewal at ADAC.

        - EUR 340.00/year

        Pay invoice (due 2026-03-15)

        🆕 New in Paperless: Insurance

        http://paperless/...

    Translatable lines (`filed`, `new_in_paperless`, `reformat_failed`)
    go through `t`; the metadata row, summary, facts and action items
    are the LLM's own content and render verbatim.
    """
    lines = [t("filed", title=display_title, doc_id=doc_id)]

    # Compact metadata row: topic(s) | person(s) | type | from | date.
    # Built from the resolved_* values so what's on screen matches what
    # was written to Paperless — no translation-key round-trip.
    meta_parts: list[str] = [*resolved_topics, *resolved_persons]
    if resolved_type:
        meta_parts.append(resolved_type)
    if resolved_correspondent:
        meta_parts.append(resolved_correspondent)
    if date_applied:
        meta_parts.append(date_applied)
    if meta_parts:
        lines.extend(["", "  " + " | ".join(meta_parts)])

    summary = classification.get("summary", "")
    if summary and isinstance(summary, str):
        lines.extend(["", f"  {summary}"])

    lines.extend(_fact_lines(classification.get("facts", [])))
    lines.extend(_action_item_lines(classification.get("action_items", [])))

    if created_new:
        lines.extend(["", f"  {t('new_in_paperless', items=', '.join(created_new))}"])
    if reformat_failed:
        lines.extend(["", f"  {t('reformat_failed')}"])
    if link:
        lines.extend(["", f"  {link}"])

    return "\n".join(lines)


def render_reprocessed_reply(
    t: Translator,
    *,
    title: str,
    doc_id: int | None,
    resolved_topics: list[str],
    resolved_persons: list[str],
    resolved_type: str | None,
    resolved_correspondent: str | None,
) -> str:
    """Render the "reclassified" confirmation after a reply-correction.

    Shared between document reprocess (where ``doc_id`` is the
    Paperless id) and capture reprocess (where ``doc_id`` is None
    -- the capture's identity lives in the envelope's ``capture_id``
    and isn't shown to the human). A title line plus the compact
    metadata row -- the reprocess pass corrects classification, the
    original filing already carried the rich detail.
    """
    if doc_id is None:
        lines = [t("reprocessed_capture", title=title)]
    else:
        lines = [t("reprocessed", title=title, doc_id=doc_id)]
    meta_parts: list[str] = [*resolved_topics, *resolved_persons]
    if resolved_type:
        meta_parts.append(resolved_type)
    if resolved_correspondent:
        meta_parts.append(resolved_correspondent)
    if meta_parts:
        lines.extend(["", "  " + " | ".join(meta_parts)])
    return "\n".join(lines)


def render_capture_reply(
    t: Translator,
    *,
    source_title_hint: str | None,
    classification: dict,
    link: str,
) -> str:
    """Render the "capture saved" reply.

    Mirrors the filing layout — title, optional meta row (topics |
    persons), summary, facts, action items — but the footer is the
    source link / "(pasted text)" placeholder rather than a Paperless
    URL. Title falls back through the classifier title, the extractor's
    hint, then a generic "Capture".
    """
    title = classification.get("title") or source_title_hint or "Capture"
    lines = [t("captured", title=title)]

    # Captures classify under `tags`; the documents pipeline uses
    # `topics`. Accept either so the same presenter works for both
    # and we don't end up with a silent empty meta row on captures.
    topics = classification.get("topics") or classification.get("tags") or []
    if isinstance(topics, str):
        topics = [topics]
    persons = classification.get("persons") or []
    if isinstance(persons, str):
        persons = [persons]
    meta_parts = [x for x in (*topics, *persons) if isinstance(x, str) and x.strip()]
    if meta_parts:
        lines.extend(["", "  " + " | ".join(meta_parts)])

    summary = classification.get("summary")
    if summary and isinstance(summary, str):
        lines.extend(["", f"  {summary}"])

    lines.extend(_fact_lines(classification.get("facts", [])))
    lines.extend(_action_item_lines(classification.get("action_items", [])))

    lines.extend(["", f"  {link}"])
    return "\n".join(lines)


# ── Shared section builders ──────────────────────────────────────────────


def _fact_lines(facts) -> list[str]:
    """Up to five `  - fact` bullets, preceded by a blank separator."""
    if not isinstance(facts, list):
        return []
    fact_lines = [f for f in facts if isinstance(f, str) and f.strip()]
    if not fact_lines:
        return []
    out = [""]
    out.extend(f"  - {f}" for f in fact_lines[:5])
    return out


def _action_item_lines(action_items) -> list[str]:
    """Up to three `  action (due DATE)` lines for valid dict items."""
    if not isinstance(action_items, list):
        return []
    valid = [a for a in action_items if isinstance(a, dict) and a.get("action")]
    if not valid:
        return []
    out = [""]
    for a in valid[:3]:
        due = a.get("due", "")
        due_str = f" (due {due})" if due else ""
        out.append(f"  {a['action']}{due_str}")
    return out
