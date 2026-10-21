"""The card types as JSON Schema: every card the writers produce, and what a fault says.

ADR-017 says what each card type holds (§5, §6). The schemas under
`stacklets/memory/schemas/cards/` say it as JSON Schema, which the card
write endpoint checks every change against. Two things are pinned here:
that the cards the stack writes today meet them, so a check does not
refuse the memory a household already has; and that a card which does
not names the field and what it may hold, which is what a person or an
agent fixing it needs to read.

The values that depend on the instance (who the people are, which topics
there are) come from the memory repo, as the endpoint builds them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(_REPO / "stacklets" / "memory"))
sys.path.insert(0, str(Path(__file__).parent))

from card_schema import KEYWORD, CardSchemas, VaultValues  # noqa: E402
from simpsons_cards import CARDS, ONTOLOGY, PEOPLE, VAULT, diary  # noqa: E402

from stack.frontmatter import parse  # noqa: E402
from stack.ontology import Ontology  # noqa: E402

SCHEMAS = CardSchemas.load()
VALUES = VaultValues(person_ids=[Path(p).stem for p in PEOPLE], ontology=Ontology.loads(ONTOLOGY))
DIARY = {path for path in CARDS if "/diary/" in path}


def _problems(fields: dict) -> list[dict]:
    return SCHEMAS.problems(fields, VALUES)


def _field(problems: list[dict], name: str) -> dict:
    [found] = [p for p in problems if p["field"] == name]
    return found


class TestTheCardsTheStackWrites:

    def test_there_is_a_schema_for_every_card_type(self):
        assert set(SCHEMAS.types) == {"document", "note", "bookmark", "email", "diary", "list",
                                      "person", "correspondent", "topic"}

    @pytest.mark.parametrize("path", sorted(set(PEOPLE) | set(CARDS) - DIARY))
    def test_each_card_meets_its_types_schema(self, path):
        assert _problems(parse(VAULT[path])) == []

    @pytest.mark.parametrize("path", sorted(DIARY))
    def test_a_diary_card_meets_it_but_for_its_tags(self, path):
        """The diary compile tags a card with the topics' labels in the
        instance's language, where every other card holds the topic's id
        (ADR-017 §4). It is the one field written today that no schema
        here describes: the base's tag rule refuses the label."""
        problems = _problems(parse(VAULT[path]))

        assert [p["field"] for p in problems] == ["tags"]
        assert _problems(parse(diary("text", tags=("medical",)))) == []

    def test_a_note_written_in_the_memory_repo_meets_it_too(self):
        """A note filed from the chat and one written by hand have the same
        fields: where a filed one came from is in its sidecar (ADR-018)."""
        assert _problems(parse(CARDS["homer/notes/grill.md"])) == []


class TestWhatAFaultSays:

    def test_a_kind_outside_its_types_set_names_the_kinds(self):
        fields = {**parse(CARDS["family/lists/termine.md"]), "kind": "agenda"}

        problem = _field(_problems(fields), "kind")

        assert problem["allowed"] == ["todo", "checklist", "calendar", "archive"]

    def test_a_kind_on_a_type_without_variants_is_refused(self):
        fields = {**parse(CARDS["family/itchy-scratchy-land/topic.md"]), "kind": "trip"}

        assert _field(_problems(fields), "kind")["message"] == "a topic card has no field `kind`"

    def test_an_unknown_person_names_the_people_there_are(self):
        fields = {**parse(CARDS["marge/notes/2026/10/art-supplies.md"]), "persons": ["marge", "grampa"]}

        problem = _field(_problems(fields), "persons")

        assert "'grampa'" in problem["message"]
        assert problem["allowed"] == ["bart", "homer", "maggie", "marge"]

    def test_a_tag_that_is_no_topic_and_no_keyword_names_both(self):
        """A topic's label is no tag (`Gesundheit`): the id is (ADR-017 §4)."""
        fields = {**parse(CARDS["marge/notes/2026/10/art-supplies.md"]), "tags": ["Gesundheit"]}

        problem = _field(_problems(fields), "tags")

        assert problem["allowed"] == ["itchy-scratchy-land", "medical", KEYWORD]

    def test_a_missing_required_field_is_named(self):
        fields = parse(CARDS["family/documents/2026/10/duff-insurance-policy-p72.md"])
        del fields["paperless_id"]

        assert _field(_problems(fields), "paperless_id")["message"] == "a document card needs `paperless_id`"

    def test_a_field_no_schema_names_is_refused_with_the_fields_there_are(self):
        """A card holds the fields its type's schema and the base name, and no
        other: a misspelt field is refused, not kept unchecked."""
        fields = {**parse(PEOPLE["family/people/bart.md"]), "alias": "Bartman"}

        problem = _field(_problems(fields), "alias")

        assert problem["message"] == "a person card has no field `alias`"
        assert "aliases" in problem["allowed"]

    def test_a_named_field_of_another_shape_is_refused(self):
        """Frontmatter is scalars and one-deep lists of strings (ADR-017 §8)."""
        fields = {**parse(CARDS["homer/notes/grill.md"]), "persons": [["homer"]]}

        assert _field(_problems(fields), "persons")

    def test_a_card_of_no_type_names_the_types(self):
        problem = _field(_problems({"type": "recipe", "title": "Duff stew"}), "type")

        assert "note" in problem["allowed"]


def test_the_fields_no_change_may_touch_are_marked_read_only():
    """The card's id, its type and where it came from are the record's, never
    a person's change (ADR-016, ADR-017 §5)."""
    assert {"type", "card_id", "capture_id", "timestamp"} <= SCHEMAS.read_only("bookmark")
    assert {"paperless_id", "source"} <= SCHEMAS.read_only("document")
    assert {"entry_id", "event_ids", "digest"} <= SCHEMAS.read_only("diary")
