"""What a correction changed on a card, as the archivist's answer names it.

Drives `card_changes.changes_between` with the classification a person
corrected and the one the correction made, as an email's thread has them.
"""

from __future__ import annotations

from stacklets.docs.bot.card_changes import Change, changes_between

BEFORE = {"title": "Elternabend", "persons": ["bart"], "tags": ["school"]}


def test_an_added_tag_is_named():
    after = {**BEFORE, "tags": ["school", "gemuesekiste2"]}
    assert changes_between(BEFORE, after) == [Change("tag", "added", "gemuesekiste2")]


def test_a_new_title_people_and_tags_are_named_in_that_order():
    after = {"title": "School trip", "persons": ["lisa"], "tags": []}
    assert changes_between(BEFORE, after) == [
        Change("title", "changed", "School trip", before="Elternabend"),
        Change("person", "added", "lisa"),
        Change("person", "removed", "bart"),
        Change("tag", "removed", "school"),
    ]


def test_a_correction_that_changed_nothing_names_nothing():
    # The summary changed, which the card shows anyway.
    assert changes_between(BEFORE, {**BEFORE, "summary": "Now on Monday."}) == []


def test_a_missing_title_is_not_a_new_title():
    assert changes_between(BEFORE, {"persons": ["bart"], "tags": ["school"]}) == []
