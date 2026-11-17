"""Chat-reply rendering for the archivist: pure layout, given a translator.

Takes a `t(key, **kwargs)` callable (the archivist's bilingual lookup)
plus the classification / enrichment data, and returns the text the bot
sends. No Matrix, no env, no messages.yml: the archivist owns `t` and
the sending; this owns the layout.

A capture (a note, a bookmark, an email, a voice memo) and a document
filed in Paperless are shown as the card's glance (`card_glance`): the
type's icon and the title as the link to the card, one line of people and
day (a document's also names its correspondent), the summary cut short,
the first to-dos, the tags. A document's reply adds what is new in
Paperless; a document filed without an AI says so in the same short form.
The answer to a correction names what changed in one line, then the card
as it is now, folded away in the HTML.
`card_glance` says why the card looks like this. The text is markdown;
the bot sends it as the plain `body` and as HTML, or with the HTML a
`Reply` carries.

    reply = render_capture_reply(t, source_title_hint=None, link=link,
                                 classification=classification, card_type="email")
    answer = render_capture_correction_reply(t, before=before, after=after,
                                             link=link, card_type="note")
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from stack.briefing import render_briefing
from stack.card_render import for_chat, render
from stack.matrix.html import matrix_html

from .card_changes import Change, changes_between
from .card_glance import card_glance

__all__ = [
    "Reply",
    "Translator",
    "card_in_reply",
    "render_capture_correction_reply",
    "render_capture_reply",
    "render_filing_reply",
    "render_unclassified_filing_reply",
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
    resolved_persons: list[str],
    resolved_correspondent: str | None,
    resolved_topics: list[str],
    date_applied: str | None,
    classification: dict,
    created_new: list[str] | None,
    reformat_failed: bool,
    link: str,
    language: str = "en",
) -> str:
    """Render the happy-path "document filed" reply: the document's card at a glance.

        📄 [Duff Insurance - Kfz-Versicherung](https://home.example/go/card/document-1a2b3c4d5e)

        🏢 Duff Insurance · 👤 Homer · 📅 Sun 15 Mar

        > Annual car insurance renewal at Duff Insurance.

        - EUR 340.00/year

        - [ ] Pay invoice 📅 Sun 15 Mar

        🏷️ insurance · car

        🆕 New in Paperless: Insurance

    The card is `card_glance`, as for a note or an email: the title is the
    link to the document's card, so no bare link follows. `resolved_topics`
    are the tags the document got. Of the classifier's facts only the
    amounts are shown, because the other facts repeat the summary. The
    `new_in_paperless` and `reformat_failed` lines go through `t`.
    """
    blocks = [
        card_glance(
            t, "document", {**classification, "persons": resolved_persons,
                            "tags": resolved_topics},
            title=display_title, link=link, captured_at=date_applied,
            language=language, correspondent=resolved_correspondent,
            facts=_amount_facts(classification),
        ),
    ]
    if created_new:
        blocks.append(t("new_in_paperless", items=", ".join(created_new)))
    if reformat_failed:
        blocks.append(t("reformat_failed"))
    return _joined(blocks)


def render_unclassified_filing_reply(
    t: Translator, *, title: str, link: str, note: str | None = None,
    language: str = "en",
) -> str:
    """Render the reply to a document filed without a classification, in the same short form.

        📄 [Scan 2026-03-15](https://home.example/go/card/document-1a2b3c4d5e)

        It is searchable. It has no tags, because no AI is set up yet.

    Only the title and the link are known, so the card is its heading;
    `note` says why there is nothing more (no AI set up).
    """
    return _joined([card_glance(t, "document", {}, title=title, link=link,
                                captured_at=None, language=language), note or ""])


def render_reprocessed_reply(
    t: Translator,
    *,
    before: dict,
    after: dict,
    link: str,
    correspondent: str | None = None,
    date: str | None = None,
    language: str = "en",
) -> "Reply":
    """Render the answer to a correction of a document: what changed, then the card as it is now.

        🔁 Updated [Kfz-Versicherung](https://home.example/go/card/document-1a2b3c4d5e): Bart added

        ▶ The card now          (HTML: collapsed until clicked)

    The same answer as a capture's correction (`render_capture_correction_reply`),
    with the document's sender and day on the card's line. `before` is
    the classification the person corrected and `after` the new one,
    each with `title`, `persons` (names) and `tags` (the document's topics).
    """
    return render_capture_correction_reply(
        t, before=before, after=after, link=link, card_type="document",
        captured_at=date, language=language, correspondent=correspondent,
        facts=_amount_facts(after),
    )


def render_capture_reply(
    t: Translator,
    *,
    source_title_hint: str | None,
    classification: dict,
    link: str,
    transcript: str | None = None,
    card_type: str = "note",
    captured_at: str | None = None,
    language: str = "en",
    source: str | None = None,
) -> str:
    """Render the reply to a filed note, bookmark, email or voice memo: the card's glance.

        📧 [Elternabend](https://home.example/go/card/email-1a2b3c4d5e)

        👤 Bart, Marge · 📅 Thu 5 Nov

        > Parents' evening on Friday at 19:00 in room 12.

        - [ ] Return the trip form 📅 Fri 26 Jun

        🏷️ school · gemuesekiste

    The card as `card_glance` shows it: the title is the link to the card,
    so the reply carries no bare URL for a client to preview. `card_type`
    is the type the capture was filed as (`note`, `bookmark`, `email`) and
    picks the icon; `captured_at` is the card's day; `source` is the page a
    bookmark points to, named by its site as a link. The caller passes only
    the tags to show. The title falls back through the classifier's title,
    the extractor's hint, then a generic "Capture".

    ``transcript`` is the verbatim text of a voice memo; it renders as a
    quote above the card, so the sender can check that it was heard right.
    """
    blocks: list[str] = []
    if transcript:
        # Block-quote each line so multi-sentence memos still render as
        # one quote block in Element / SchildiChat / FluffyChat. The
        # mic emoji signals "this is the spoken content."
        quoted = "\n".join(f"> {line}" if line else ">"
                           for line in transcript.splitlines() or [transcript])
        blocks.append(f"{t('voice_heard')}\n\n{quoted}")

    title = classification.get("title") or source_title_hint or t("capture_title")
    blocks.append(card_glance(t, card_type, classification, title=title, link=link,
                              captured_at=captured_at, language=language, source=source))
    return _joined(blocks)


@dataclass(frozen=True)
class Reply:
    """A reply whose HTML is not simply its markdown converted.

    `body` is the markdown every client reads (and a notification shows);
    `html` is the `formatted_body` for clients that render HTML.

        answer = render_capture_correction_reply(t, before=before, after=after, link=link)
        answer.body   # "🔁 Updated [Tent](...): Bart added", then the card, open
        answer.html   # the same line, then the card inside <details>
    """

    body: str
    html: str


def render_capture_correction_reply(
    t: Translator,
    *,
    before: dict,
    after: dict,
    link: str,
    card_type: str = "note",
    captured_at: str | None = None,
    language: str = "en",
    changes: "list[Change] | None" = None,
    source: str | None = None,
    correspondent: str | None = None,
    facts: list[str] | None = None,
) -> Reply:
    """Render the answer to a correction of a capture: what changed, then the card as it is now.

        🔁 Updated [Field trip](https://home.example/go/card/email-1a2b3c4d5e): renamed, Lisa added, tag school removed

        ▶ The card now          (HTML: collapsed until clicked)

    The first line names every change in one line. The card after the
    correction follows, with every tag, so the person can check that the
    correction did what they asked: in the HTML it is folded into
    `<details>`, in the plain body it is shown open, because a
    notification or a client without HTML shows tags as text.

    `before` is the classification the person corrected, `after` the one
    the correction made, each with only the tags to show. `changes` are
    what the memory wrote, when the writer says (an email's correction,
    `Filed.changes`); they are named in place of the difference of the two
    classifications, which can name a change that was not written.
    "Nothing changed on the card" stands in for them when there is none.
    `source` is the page a bookmark points to, as in `render_capture_reply`;
    `correspondent` and `facts` are a document's, as in `card_glance`.
    """
    title = after.get("title") or before.get("title") or "Capture"
    if changes is None:
        changes = changes_between(before, after)
    linked = f"[{title}]({link})" if link else title
    said = ", ".join(_change_in_words(t, c, language) for c in changes)
    line = (t("updated_capture", title=linked, changes=said) if said
            else t("unchanged_capture", title=linked))
    card = card_glance(t, card_type, after, title=title, link=link,
                       captured_at=captured_at, language=language,
                       source=source, correspondent=correspondent, facts=facts)
    html = (f"{matrix_html(line)}\n<details><summary>{t('card_now')}</summary>\n"
            f"{matrix_html(card)}\n</details>")
    return Reply(body=_joined([line, card]), html=html)


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


# The facts a document's glance keeps: those that carry an amount of money.
_AMOUNT = re.compile(r"[€$£]|\b(?:EUR|USD|GBP|CHF)\b|\bEuro\b", re.IGNORECASE)
_AMOUNTS_SHOWN = 2


def _amount_facts(classification: dict) -> list[str]:
    """The first facts of a classification that name an amount, as text.

        >>> _amount_facts({"facts": ["Contract KFZ-2026", "EUR 340.00/year"]})
        ['EUR 340.00/year']

    Other facts are left out: they repeat the summary.
    """
    facts = [f.strip() for f in classification.get("facts") or []
             if isinstance(f, str) and _AMOUNT.search(f)]
    return facts[:_AMOUNTS_SHOWN]


def _change_in_words(t: Translator, change: Change, language: str) -> str:
    """One change as the reply says it: "tag gemuesekiste2 added"."""
    if change.field == "title":
        return t("change_renamed")
    if change.field == "person":
        name = render({"type": "note", "persons": [change.value]}, "", language=language).facts
        return t(f"change_person_{change.kind}", name=name)
    return t(f"change_tag_{change.kind}", tag=change.value)


def _joined(blocks: list[str]) -> str:
    return "\n\n".join(b for b in blocks if b and b.strip())
