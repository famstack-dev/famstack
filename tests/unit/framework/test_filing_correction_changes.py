"""A card's fields after a correction: no tag or person is dropped that it does not name.

`stack.filing.correction.changes.named_changes` is pure, so these tests call it as the
note and document paths do, with the model's answer as the 16 GB model
wrote it on the rig. Each case states one rule of its table.
"""

from __future__ import annotations

from stack.people import Person
from stack.filing.correction.changes import named_changes

SIMPSONS = [Person("homer", "Homer Simpson"), Person("marge", "Marge Simpson"),
            Person("bart", "Bart Simpson"), Person("lisa", "Lisa Simpson")]
RECEIPT = {"title": "Texxon Tankquittung", "tags": ["Fahrzeug", "Einkauf"], "persons": ["Homer"]}
DRIFTED = {"title": "Texxon Fuel Camper van March",
           "tags": {"add": ["Reise"], "remove": ["Einkauf", "Fahrzeug"]},
           "persons": {"remove": ["Homer"]}}


def test_only_the_tag_the_correction_names_is_removed_and_homer_stays():
    """Given the model drops Homer and Fahrzeug with Einkauf, when the
    correction names only Einkauf and Reise, then Einkauf goes, Reise
    comes, Fahrzeug stays, and Homer stays: it names no person."""
    said = "Das Tag Einkauf ist falsch, das gehört zu Reise."
    corrected = named_changes(RECEIPT, DRIFTED, said=said, people=SIMPSONS)
    assert corrected.fields["tags"] == ["Fahrzeug", "Reise"]
    assert corrected.fields["persons"] == ["Homer"]


def test_the_title_follows_the_models_answer():
    corrected = named_changes(RECEIPT, {"title": "Camper fuel"}, said="this was for the camper")
    assert corrected.fields["title"] == "Camper fuel"
    assert corrected.changed == ("title",)


def test_no_title_answered_keeps_the_cards():
    assert named_changes(RECEIPT, {"title": " "}, said="x").fields["title"] == "Texxon Tankquittung"


def test_the_date_follows_the_models_answer_when_it_is_a_date():
    filed = {**RECEIPT, "date": "2026-11-12"}
    assert named_changes(filed, {"date": "2026-11-14"}, said="Friday").fields["date"] == "2026-11-14"
    assert named_changes(filed, {"date": "Friday"}, said="Friday").fields["date"] == "2026-11-12"


def test_only_me_removes_the_others_the_model_answers_removed():
    """Given a card with the whole family, when the writer says "only me"
    and the model removes the others, then the writer stays alone."""
    four = {**RECEIPT, "persons": ["Homer", "Marge", "Bart", "Lisa"]}
    corrected = named_changes(four, {"persons": {"remove": ["Marge", "Bart", "Lisa"]}},
                              said="only me", people=SIMPSONS)
    assert corrected.fields["persons"] == ["Homer"]


def test_a_whole_list_of_people_answered_only_adds():
    """Given a card with Homer, when the correction says Marge was there too
    and the model answers the list ["Marge"], then Homer stays: a person is
    lost only by an explicit remove."""
    corrected = named_changes(RECEIPT, {"persons": ["Marge"]}, said="Marge was there too",
                              people=SIMPSONS)
    assert corrected.fields["persons"] == ["Homer", "Marge"]


def test_a_correction_that_names_no_person_keeps_the_people():
    four = {**RECEIPT, "persons": ["Homer", "Marge", "Bart", "Lisa"]}
    corrected = named_changes(four, {"persons": ["Homer"]}, said="it was 13 euros", people=SIMPSONS)
    assert corrected.fields["persons"] == four["persons"]


def test_a_person_the_family_knows_is_added():
    corrected = named_changes(RECEIPT, {"persons": {"add": ["Marge Simpson"]}},
                              said="Marge was there too", people=SIMPSONS)
    assert corrected.fields["persons"] == ["Homer", "Marge"]


def test_a_person_named_in_the_correction_is_removed():
    corrected = named_changes(RECEIPT, {"persons": {"remove": ["Homer"]}},
                              said="Homer was not there", people=SIMPSONS)
    assert corrected.fields["persons"] == []


def test_a_person_the_family_does_not_know_is_not_added_and_named():
    corrected = named_changes(RECEIPT, {"persons": {"add": ["Lenny"]}},
                              said="Lenny was there too", people=SIMPSONS)
    assert corrected.fields["persons"] == ["Homer"]
    assert corrected.unknown == ("Lenny",)


def test_a_tag_is_named_by_its_name_in_another_language():
    card = {**RECEIPT, "tags": ["vehicle", "shopping"]}
    corrected = named_changes(card, {"tags": {"remove": ["Einkauf"], "add": ["Reise"]}},
                              said="Einkauf ist falsch", tag_names={"shopping": ["Einkauf"]})
    assert corrected.fields["tags"] == ["vehicle", "Reise"]


def test_a_whole_list_answered_is_read_as_its_difference():
    corrected = named_changes(RECEIPT, {"tags": ["Reise"]}, said="Einkauf is wrong")
    assert corrected.fields["tags"] == ["Fahrzeug", "Reise"]


def test_an_answer_that_is_no_object_changes_nothing():
    assert named_changes(RECEIPT, ["not", "an", "object"], said="Reise").fields == RECEIPT
