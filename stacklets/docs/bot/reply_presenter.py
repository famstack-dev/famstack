"""Chat-reply rendering for the archivist: pure layout, given a translator.

Takes a `t(key, **kwargs)` callable (the archivist's bilingual lookup)
plus the classification / enrichment data, and returns the text the bot
sends. No Matrix, no env, no messages.yml: the archivist owns `t` and
the sending; this owns the layout.

What was filed is shown as a card, through the renderer every surface
uses (`stack.card_render`, ADR-016 §4): the card the archivist wrote,
from the same fields and the same briefing block, so the reply reads as
the card does in the card browser and to the agent. The reply's own
lines (what happened, what is new in Paperless, the link) go around it.
The text is markdown; the bot sends it as the plain `body` and as HTML.
"""

from __future__ import annotations

from typing import Callable

from stack.briefing import render_briefing
from stack.card_render import for_chat, render

# `t(key, **kwargs) -> str`: the archivist's translation lookup.
Translator = Callable[..., str]

# A reply is a glance at the card: the first few facts and to-dos.
_FACTS_SHOWN = 5
_TASKS_SHOWN = 3


def render_filing_reply(
    t: Translator,
    *,
    display_title: str,
    doc_id: int,
    resolved_persons: list[str],
    resolved_type: str | None,
    resolved_correspondent: str | None,
    date_applied: str | None,
    classification: dict,
    created_new: list[str] | None,
    reformat_failed: bool,
    link: str,
    language: str = "en",
) -> str:
    """Render the happy-path "document filed" reply.

        ✅ Filed: Duff Insurance - Kfz-Versicherung (#10)

        2026-03-15 · Homer · Duff Insurance · Invoice

        Annual car insurance renewal at Duff Insurance.

        - EUR 340.00/year

        **To do**

        - Pay invoice 📅 2026-03-15

        🆕 New in Paperless: Insurance

        http://paperless/...

    The first line, `new_in_paperless` and `reformat_failed` go through
    `t`; the card between them is the document card, its fields the
    resolved values written to Paperless and the vault.
    """
    card = card_in_reply(
        {"type": "document", "title": display_title, "date": date_applied,
         "persons": resolved_persons, "correspondent": resolved_correspondent,
         "document_type": resolved_type},
        classification, language=language,
    )
    blocks = [t("filed", title=display_title, doc_id=doc_id), card]
    if created_new:
        blocks.append(t("new_in_paperless", items=", ".join(created_new)))
    if reformat_failed:
        blocks.append(t("reformat_failed"))
    blocks.append(link)
    return _joined(blocks)


def render_reprocessed_reply(
    t: Translator,
    *,
    title: str,
    doc_id: int | None,
    resolved_persons: list[str],
    resolved_type: str | None,
    resolved_correspondent: str | None,
) -> str:
    """Render the "reclassified" confirmation after a reply-correction.

    Shared between document reprocess (where ``doc_id`` is the
    Paperless id) and capture reprocess (where ``doc_id`` is None:
    the capture's identity lives in the envelope's ``capture_id``
    and isn't shown to the human). A title line plus the card's line of
    facts: the reprocess pass corrects classification, the original
    filing already carried the rich detail.
    """
    if doc_id is None:
        head = t("reprocessed_capture", title=title)
        fields = {"type": "note", "persons": resolved_persons}
    else:
        head = t("reprocessed", title=title, doc_id=doc_id)
        fields = {"type": "document", "persons": resolved_persons,
                  "correspondent": resolved_correspondent, "document_type": resolved_type}
    return _joined([head, render(fields, "").facts])


def render_capture_reply(
    t: Translator,
    *,
    source_title_hint: str | None,
    classification: dict,
    link: str,
    transcript: str | None = None,
    todo_link: str = "",
    language: str = "en",
) -> str:
    """Render the "capture saved" reply.

    Mirrors the filing layout (title line, then the note card: people,
    summary, facts, to-dos) but the footer is the capture's link rather
    than a Paperless URL. Title falls back through the classifier title,
    the extractor's hint, then a generic "Capture".

    ``transcript`` is the verbatim text for voice captures; when set it
    renders as a block-quoted preamble above the title so the sender
    can verify whisper heard them correctly. None for every other
    capture shape.

    ``todo_link`` is a `/go/topic/<scope>/todo` link the caller builds
    when a topic capture produced action items; when set it renders as
    an "added to the todo list" line above the footer, so the family
    can jump to the checkable list. Empty for captures that made no
    todos or landed outside a topic scope.
    """
    blocks: list[str] = []
    if transcript:
        # Block-quote each line so multi-sentence memos still render as
        # one quote block in Element / SchildiChat / FluffyChat. The
        # mic emoji signals "this is the spoken content."
        quoted = "\n".join(f"> {line}" if line else ">"
                           for line in transcript.splitlines() or [transcript])
        blocks.append(f"\U0001F3A4 What I heard:\n\n{quoted}")

    title = classification.get("title") or source_title_hint or "Capture"
    persons = classification.get("persons") or []
    card = card_in_reply({"type": "note", "title": title,
                          "persons": [persons] if isinstance(persons, str) else persons},
                         classification, language=language)
    blocks += [t("captured", title=title), card]
    if todo_link:
        blocks.append(f"{t('added_to_todos')}\n{todo_link}")
    blocks.append(link)
    return _joined(blocks)


# ── The card in the reply ────────────────────────────────────────────────


def card_in_reply(fields: dict, classification: dict, *, language: str = "en") -> str:
    """The filed card below its title, as the chat shows it.

    Rendered by its type from `fields` and from the briefing block the
    vault file carries (`stack.briefing`), built from the classifier's
    `summary`, `facts` and `action_items`. A reply's own first line
    names the card, so the title is not repeated.
    """
    facts = [f for f in classification.get("facts") or [] if isinstance(f, str) and f.strip()]
    tasks = classification.get("action_items")
    briefing = render_briefing(
        summary=classification.get("summary"),
        facts=facts[:_FACTS_SHOWN],
        action_items=tasks[:_TASKS_SHOWN] if isinstance(tasks, list) else None,
    )
    persons = [p for p in fields.get("persons") or [] if isinstance(p, str) and p.strip()]
    present = {k: v for k, v in {**fields, "persons": persons}.items() if v}
    return for_chat(render(present, briefing, language=language).below_title)


def _joined(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b and b.strip())
