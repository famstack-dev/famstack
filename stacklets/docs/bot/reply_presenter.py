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

A capture (a note, a bookmark, an email) has one filing reply and one
correction reply, whatever its type; the type only picks the renderer.
The answer to a correction says what the correction changed
(`card_changes`), then shows the card again, so the family sees whether
the correction did what they asked. The card's tags are part of the chat
surface (`card_in_reply`), because the renderer leaves tags to the surface.

    reply = render_capture_reply(t, source_title_hint=None, link=link,
                                 classification=classification, card_type="email")
    answer = render_capture_correction_reply(t, before=before, after=after,
                                             link=link, card_type="note")
"""

from __future__ import annotations

from typing import Callable

from stack.briefing import render_briefing
from stack.card_render import for_chat, render

from .card_changes import Change, changes_between

__all__ = [
    "Translator",
    "card_in_reply",
    "render_capture_correction_reply",
    "render_capture_reply",
    "render_filing_reply",
    "render_reprocessed_reply",
]

# `t(key, **kwargs) -> str`: the archivist's translation lookup.
Translator = Callable[..., str]

# A reply is a glance at the card: the first few facts and to-dos.
_FACTS_SHOWN = 5
_TASKS_SHOWN = 3

# Leads the tags line of a reply; a symbol, so no language needs a word.
_TAGS = "\U0001F3F7\uFE0F"


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
    doc_id: int,
    resolved_persons: list[str],
    resolved_type: str | None,
    resolved_correspondent: str | None,
) -> str:
    """Render the "reclassified" confirmation after a document's correction.

        🔁 Reclassified with your note: Kfz-Versicherung (#10)

        Homer · Duff Insurance · Invoice

    A title line plus the document card's line of facts: the reprocess
    pass corrects the classification, the original filing already
    carried the rich detail. A capture's correction answers with
    `render_capture_correction_reply` instead.
    """
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
    card_type: str = "note",
    language: str = "en",
) -> str:
    """Render the "capture saved" reply, for a note, a bookmark or an email.

        ✅ Captured: Elternabend

        Bart, Marge

        Parents' evening on Friday at 19:00 in room 12.

        - Bring the signed trip form

        🏷️ school · gemuesekiste

        https://home.example/go/email-1a2b3c4d5e

    Mirrors the filing layout (title line, then the card: people,
    summary, facts, to-dos, tags) but the footer is the capture's link
    rather than a Paperless URL. `card_type` is the type the capture was
    filed as (`note`, `bookmark`, `email`) and picks the renderer. The
    caller passes only the tags to show. Title falls back through the
    classifier title, the extractor's hint, then a generic "Capture".

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
    blocks += [t("captured", title=title), _capture_card(card_type, classification, language)]
    if todo_link:
        blocks.append(f"{t('added_to_todos')}\n{todo_link}")
    blocks.append(link)
    return _joined(blocks)


def render_capture_correction_reply(
    t: Translator,
    *,
    before: dict,
    after: dict,
    link: str,
    card_type: str = "note",
    language: str = "en",
    changes: "list[Change] | None" = None,
) -> str:
    """Render the answer to a correction of a capture: what changed, then the card.

        🔁 Reclassified with your note: Elternabend

        - tag gemuesekiste2 added

        ...the card, as `render_capture_reply` shows it...

        https://home.example/go/email-1a2b3c4d5e

    `before` is the classification the person corrected, `after` the one
    the correction made, each with only the tags to show. `changes` are
    what the memory wrote, when the writer says (an email's correction,
    `Filed.changes`); they are named in place of the difference of the two
    classifications, which can name a change that was not written. "Nothing
    changed on the card" stands in for the list when there is none.
    """
    title = after.get("title") or before.get("title") or "Capture"
    if changes is None:
        changes = changes_between(before, after)
    said = "\n".join(f"- {_change_in_words(t, c, language)}" for c in changes)
    return _joined([t("reprocessed_capture", title=title), said or t("card_unchanged"),
                    _capture_card(card_type, after, language), link])


# ── The card in the reply ────────────────────────────────────────────────


def card_in_reply(fields: dict, classification: dict, *, language: str = "en") -> str:
    """The filed card below its title, as the chat shows it, then its tags.

    Rendered by its type from `fields` and from the briefing block the
    vault file carries (`stack.briefing`), built from the classifier's
    `summary`, `facts` and `action_items`. A reply's own first line
    names the card, so the title is not repeated. The card renderer
    leaves tags to the surface (ADR-016 §4); the chat shows `fields`'
    `tags` as one line under the card, for every card type:

        🏷️ school · gemuesekiste
    """
    facts = [f for f in classification.get("facts") or [] if isinstance(f, str) and f.strip()]
    tasks = classification.get("action_items")
    briefing = render_briefing(
        summary=classification.get("summary"),
        facts=facts[:_FACTS_SHOWN],
        action_items=tasks[:_TASKS_SHOWN] if isinstance(tasks, list) else None,
    )
    persons = [p for p in fields.get("persons") or [] if isinstance(p, str) and p.strip()]
    present = {k: v for k, v in {**fields, "persons": persons}.items()
               if v and k != "tags"}
    card = for_chat(render(present, briefing, language=language).below_title)
    listed = fields.get("tags")
    tags = [x.strip() for x in listed if isinstance(x, str) and x.strip()] \
        if isinstance(listed, list) else []
    return _joined([card, f"{_TAGS} " + " · ".join(tags) if tags else ""])


def _capture_card(card_type: str, classification: dict, language: str) -> str:
    """A capture's card below its title, from its classification."""
    persons = classification.get("persons") or []
    return card_in_reply(
        {"type": card_type, "persons": [persons] if isinstance(persons, str) else persons,
         "tags": classification.get("tags")},
        classification, language=language,
    )


def _change_in_words(t: Translator, change: Change, language: str) -> str:
    """One change as the reply says it: "tag gemuesekiste2 added"."""
    if change.field == "title":
        return t("change_title", title=change.value)
    if change.field == "person":
        name = render({"type": "note", "persons": [change.value]}, "", language=language).facts
        return t(f"change_person_{change.kind}", name=name)
    return t(f"change_tag_{change.kind}", tag=change.value)


def _joined(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b and b.strip())
