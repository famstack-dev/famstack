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

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(_REPO / "stacklets" / "memory"))
sys.path.insert(0, str(Path(__file__).parent))

from card_schema import KEYWORD, CardSchemas, VaultValues  # noqa: E402
from card_fields import SCHEMA_DIR  # noqa: E402
from simpsons_cards import CARDS, ONTOLOGY, PEOPLE, VAULT, diary  # noqa: E402

from stack.cards import CARD_KINDS, CARD_TYPES  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from stack.ontology import Ontology  # noqa: E402

SCHEMAS = CardSchemas.load()
VALUES = VaultValues(person_ids=[Path(p).stem for p in PEOPLE], ontology=Ontology.loads(ONTOLOGY))


def _schema(name: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{name}.json").read_text())


def _problems(fields: dict) -> list[dict]:
    return SCHEMAS.problems(fields, VALUES)


def _field(problems: list[dict], name: str) -> dict:
    [found] = [p for p in problems if p["field"] == name]
    return found


class TestOneDefinition:
    """The schema files define the card types and their kinds. The
    framework's own sets (`stack.cards`) and the base schema's list of
    types are held to them, so a type or a kind added in one place and
    not the others fails here, not in a household's memory."""

    def test_the_types_are_the_schema_files(self):
        assert set(SCHEMAS.types) == CARD_TYPES == set(_schema("base")["properties"]["type"]["enum"])

    def test_the_kinds_are_the_ones_the_schemas_allow(self):
        kinds = {t: tuple(kind["enum"]) for t in SCHEMAS.types
                 if (kind := _schema(t).get("properties", {}).get("kind"))}

        assert kinds == CARD_KINDS


class TestTheCardsTheStackWrites:

    @pytest.mark.parametrize("path", sorted(set(PEOPLE) | set(CARDS)))
    def test_each_card_meets_its_types_schema(self, path):
        """A diary card among them: the compile tags it with the topic's id,
        as every other writer does (ADR-017 §4)."""
        assert _problems(parse(VAULT[path])) == []

    def test_a_diary_card_tagged_by_a_topics_label_is_refused_for_its_tags(self):
        """As a release before migration memory/0010 wrote it."""
        problems = _problems(parse(diary("text", tags=("Gesundheit",))))

        assert [p["field"] for p in problems] == ["tags"]

    def test_a_note_written_in_the_memory_repo_meets_it_too(self):
        """A note filed from the chat and one written by hand have the same
        fields: where a filed one came from is in its sidecar (ADR-018)."""
        assert _problems(parse(CARDS["homer/notes/grill.md"])) == []


    def test_a_correspondent_keeps_what_the_household_wrote_about_it(self):
        """Where a correspondent is, how to reach it and the topics it writes
        about are the household's own words, kept as fields of its card."""
        fields = {**parse(CARDS["family/correspondents/duff-insurance.md"]),
                  "address": "Hansastraße 19, 80686 München", "phone": "+49 89 555 0199",
                  "email": "claims@duff-insurance.example",
                  "website": "https://www.duff-insurance.example", "topics": ["medical"]}

        assert _problems(fields) == []


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

    def test_a_correspondents_topic_is_one_of_the_ontologys(self):
        fields = {**parse(CARDS["family/correspondents/duff-insurance.md"]), "topics": ["beer"]}

        assert _field(_problems(fields), "topics")["allowed"] == ["itchy-scratchy-land", "medical"]

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


class TestATopicCardDeclaresATopic:
    """The memory's topic cards declare topics on top of the ontology: the
    family's `duff-gardens` folder is a topic id wherever the ontology's
    are, with the values built as the card write endpoint builds them, from
    the memory repo (`get_ontology`)."""

    @pytest.fixture
    def values(self, tmp_path) -> VaultValues:
        from lib import get_ontology

        vault = tmp_path / "vault"
        files = {**VAULT, "ontology.toml": ONTOLOGY, "family/duff-gardens/topic.md":
                 "---\ntype: topic\ncard_id: topic-duff-gardens\ntitle: Summer Trip to Duff Gardens\n---\n"}
        for path, text in files.items():
            (vault / path).parent.mkdir(parents=True, exist_ok=True)
            (vault / path).write_text(text, encoding="utf-8")
        return VaultValues(person_ids=[Path(p).stem for p in PEOPLE], ontology=get_ontology(vault))

    def test_its_id_is_a_tag(self, values):
        fields = {**parse(CARDS["marge/notes/2026/10/art-supplies.md"]), "tags": ["duff-gardens"]}

        assert SCHEMAS.problems(fields, values) == []

    def test_its_title_is_no_tag_and_its_id_is_listed_with_the_ontologys(self, values):
        fields = {**parse(CARDS["marge/notes/2026/10/art-supplies.md"]),
                  "tags": ["Summer Trip to Duff Gardens"]}

        problem = _field(SCHEMAS.problems(fields, values), "tags")

        assert problem["allowed"] == ["duff-gardens", "itchy-scratchy-land", "medical", KEYWORD]


def test_the_fields_no_change_may_touch_are_marked_read_only():
    """The card's id, its type and where it came from are the record's, never
    a person's change (ADR-016, ADR-017 §5)."""
    assert {"type", "card_id", "capture_id", "timestamp"} <= SCHEMAS.read_only("bookmark")
    assert {"paperless_id", "source"} <= SCHEMAS.read_only("document")
    assert {"entry_id", "event_ids", "digest"} <= SCHEMAS.read_only("diary")
    # A person with an account is a member of the household; a card edit does not make one.
    assert "account" in SCHEMAS.read_only("person")
