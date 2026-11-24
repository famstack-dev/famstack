"""note: a corrected note's reading, from the model's answer to its card conversation.

    card = card_of(rebuild, prefer_tags=["camping"])
    answer = await classifier.correct_card(build_conversation(..., filed=card, ...), text=True)
    reading = reading_after(answer, rebuild, card, words=words, prior=prior,
                            people=people, tag_names=names, todos=True, model="qwen")

The model reads a corrected or edited note as the card conversation
(ADR-018 §7, `conversation`). The archivist makes that call. This module
holds the steps before and after the call, without I/O. `card_of` gives
the card that the conversation starts from. `reading_after` gives the
reading that the filing core writes from the answer.

The fields get one guard for tags and people (`changes`). The text is
checked against the note's words. A text that the check refuses keeps
the text that the card shows, with the edits of people. A to-do is kept
only when a correction names one of its words. A wrong item on a list is
worse than a missing item.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from stack.cards.events import text_of
from stack.filing.file import Reading

from ..clean_words import kept_rebuilt
from .changes import named_changes

if TYPE_CHECKING:
    from stack.filing.refile import Rebuild

__all__ = ["card_of", "reading_after"]


def card_of(rebuild: "Rebuild", prefer_tags: list[str] | None) -> dict:
    """The card's fields that the conversation starts from.

    The tags are only those that the room of the correction can see
    (``prefer_tags``), because the model must not see a private topic's
    name. The filing core puts the other tags back (`Refile.own_tags`).

        card_of(rebuild, ["camping"])
        # {'title': 'Campsite', 'tags': ['camping'], 'persons': ['homer'], 'date': '2026-11-20'}
    """
    card = {key: rebuild.fields.get(key) for key in ("title", "tags", "persons", "date")}
    tags = [str(t) for t in card.get("tags") or []]
    card["tags"] = [t for t in tags if t in prefer_tags] if prefer_tags is not None else tags
    card["persons"] = [str(p) for p in card.get("persons") or []]
    card["date"] = str(card["date"]) if card.get("date") else None
    return card


def reading_after(answer: dict, rebuild: "Rebuild", card: dict, *, words: str, prior: str,
                  people: list, tag_names: dict[str, list[str]], todos: bool,
                  model: str | None) -> Reading:
    """The reading of a corrected note from the model's `answer` to its
    card conversation.

    ``card`` is the card that the conversation showed (`card_of`).
    ``words`` are the note's words. ``prior`` is the clean version that
    the conversation showed. ``todos`` is true when the request asked for
    to-dos, which happens in a topic with a list.

    The fields get one guard for tags and people (`changes.named_changes`).
    The text is checked (`_text_after`). A to-do is kept only when a
    correction names it (`_todos`). The reading records the template of
    the conversation.

        reading_after({"clean": "Lunch on Friday at 1."}, rebuild, card, words="lunch fri at 12",
                      prior="Lunch on Friday at 12.", people=[], tag_names={},
                      todos=False, model="qwen").clean
        # 'Lunch on Friday at 1.' (an event of `rebuild` says "it's at 1")
    """
    said = text_of(rebuild.events)
    corrected = named_changes(card, answer, said=said, people=people, tag_names=tag_names)
    clean, why = _text_after(answer, rebuild, words=words, prior=prior, said=said)
    template = str(answer.get("prompt_template") or "")
    classification = {**corrected.fields, "summary": "", "facts": [],
                      "action_items": _todos(answer, said) if todos else []}
    return Reading(classification, clean or None, why, {"conversation_template": template},
                   model, template, unknown=corrected.unknown)


# ── The text and the to-dos ───────────────────────────────────────────


def _todos(answer: dict, said: str) -> list:
    """The to-dos of the answer that the corrections name.

    An item is plain words or an object `{"action", "due"}`, as the
    capture prompt answers it (`stack.lists.action_item`). Any other
    value is not an item.

    An item is kept only when ``said`` holds one of its words with at
    least `_TODO_WORD` letters. ``said`` is the text of the corrections.
    The filter exists because the model also lists the note's own points.
    A wrong item on a list is worse than a missing item. The list's merge
    decides which kept items go on the list.

        _todos({"action_items": ["Call the campsite", "Bart helps", 3]}, said="call them")
        # ['Call the campsite']
    """
    items = answer.get("action_items")
    if not isinstance(items, list):
        return []
    spoken = {w.casefold() for w in re.findall(r"\w+", said)}
    return [i for i in items if isinstance(i, (str, dict)) and i
            and any(w.casefold() in spoken for w in re.findall(r"\w+", _todo_text(i))
                    if len(w) >= _TODO_WORD)]


# The shortest word that ties a to-do to a correction, because short words
# such as "the", "at" and "to" are in nearly every sentence.
_TODO_WORD = 4


def _todo_text(item: str | dict) -> str:
    """A to-do's words, from plain words or the object the model answers."""
    if isinstance(item, dict):
        return " ".join(str(v) for k, v in item.items() if k in ("action", "task", "text") and v)
    return item


def _text_after(answer: dict, rebuild: "Rebuild", *, words: str, prior: str, said: str) -> tuple[str, str]:
    """A corrected note's clean version, and the reason for a refusal.

    The reason is "" when the text is kept. An answer with a text is the
    model's rewrite. The rewrite is checked against the note's words
    (`clean_words.kept_rebuilt`).

    An answer without a text changes no text. The card then keeps the
    text that it shows now, with the edits of people. That text is not
    checked, because people wrote or accepted it. A card without a text
    gets the clean version that the conversation showed (``prior``).

    A rewrite that the check refuses also keeps the shown text. The
    reason says so. The note's words alone would lose the edits and every
    earlier correction.

        _text_after({"title": "BBQ"}, rebuild, ...)   # (rebuild.current, "")
    """
    if answer.get("clean"):
        clean, why = kept_rebuilt(words, str(answer["clean"]), events=said)
        if why and rebuild.current:
            return rebuild.current, f"{why}; the card keeps the text it shows"
        return clean, why
    if rebuild.current:
        return rebuild.current, ""
    return kept_rebuilt(words, prior, events=said)
