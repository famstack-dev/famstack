"""document: a filed document's correction, asked as the card conversation.

    correction = DocumentCorrection.of(filed, title="scan-47")
    answer = await classifier.correct_card(
        correction.messages(receipt_text, corrections), text=False)
    classification, corrected = correction.after(answer, corrections, people=people)
    # classification: the filing with the corrected title, topics, persons
    # and date, which the archivist writes to Paperless

A document is corrected from its first filing and every correction in
time order. The first filing is the data of the `document.filed`
envelope. The model reads the card conversation of ADR-018 §7: the
document's text, the first filing as its own answer, then each
correction. It answers what the corrections change.

`changes.named_changes` keeps the tags and people that the corrections do
not name. The conversation does not ask for the type, the correspondent
and the summary. They stay as filed. The module is pure. The
caller makes the model call.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from stack.cards.events import Correction, text_of
from stack.people import Person

from .conversation import build_conversation
from .changes import Corrected, named_changes

__all__ = ["DocumentCorrection"]


@dataclass(frozen=True)
class DocumentCorrection:
    """A document's filing as the conversation shows it.

    ``first`` is the filing as first made: title, date, topics, persons,
    correspondent and document type. ``card`` holds the fields that a
    correction can change: title, tags, persons and date.

        DocumentCorrection.of({"title": "Fuel receipt", "topics": ["vehicle"]}, title="scan-47").card
        # {'title': 'Fuel receipt', 'tags': ['vehicle'], 'persons': [], 'date': None}
    """

    first: dict
    card: dict

    @classmethod
    def of(cls, filed: dict | None, *, title: str | None) -> DocumentCorrection:
        """The correction of a document filed as `filed`.

        A thread that does not reach the filing gives no `filed`. The
        correction then starts from the document's title alone."""
        first = dict(filed or {"title": title})
        return cls(first, {"title": first.get("title"), "tags": list(first.get("topics") or []),
                           "persons": list(first.get("persons") or []), "date": first.get("date")})

    def messages(self, text: str, corrections: Sequence[Correction]) -> list[dict]:
        """The card conversation for `corrections` of the document `text`."""
        return build_conversation(writer="Someone", kind="document", original=text,
                                  filed=self.card, events=[c.message() for c in corrections])

    def after(self, answer: object, corrections: Sequence[Correction], *,
              people: Sequence[Person]) -> tuple[dict, Corrected]:
        """The filing after the model's `answer`, and the change of the fields.

        The filing is a classification that `apply_classification` writes
        to Paperless."""
        corrected = named_changes(self.card, answer, said=text_of(corrections), people=people)
        fields = corrected.fields
        return ({**self.first, "title": fields["title"], "topics": fields["tags"],
                 "persons": fields["persons"], "date": fields.get("date")}, corrected)
