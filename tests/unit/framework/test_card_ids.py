"""Every card carries an id of its own, `card_id` (ADR-016 §2).

The id is made once, when a card is first written, and never derived
again: a record's from ten random hex digits, any other card's from its
slug. `with_card_id` gives one to a file that has none, and is what the
migration and the curator run over the memory repo, so these tests hold
it to what a person would check in the file afterwards: the id is there,
a file written by hand reads as a note, and nothing else in the file
moved by a byte.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack.cards import (  # noqa: E402
    CardIdError,
    card_id_from,
    card_identity,
    new_card_id,
    with_card_id,
)
from stack.frontmatter import parse  # noqa: E402

NOTE = (
    "---\n"
    "type: note\n"
    "title: Art supplies for next time\n"
    "persons:\n"
    "  - marge\n"
    "capture_id: $r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4\n"
    "---\n"
    "\n"
    "# Art supplies for next time\n"
    "\n"
    "Brushes and paper for the next trip.  \n"
)


class TestANewId:
    @pytest.mark.parametrize("card_type", ["document", "note", "bookmark", "email", "diary"])
    def test_a_record_gets_its_type_and_ten_random_hex_digits(self, card_type):
        card_id = new_card_id(card_type, "family/notes/whatever.md")
        assert re.fullmatch(rf"{card_type}-[0-9a-f]{{10}}", card_id)
        assert card_id != new_card_id(card_type, "family/notes/whatever.md")

    def test_a_person_and_a_list_get_their_slug(self):
        assert new_card_id("person", "family/people/homer.md") == "person-homer"
        assert new_card_id("list", "family/lists/markt.md") == "list-markt"

    def test_a_list_archive_gets_its_list_and_month(self):
        assert new_card_id("list", "family/lists/archive/markt/2026-10.md") == "list-markt-2026-10"

    def test_a_file_name_that_is_no_slug_is_slugged(self):
        assert new_card_id("person", "family/people/Grampa Simpson.md") == "person-grampa-simpson"

    def test_a_slug_another_card_carries_gets_a_number(self):
        """Two buckets may each keep a list called `markt`; their ids still differ."""
        taken = {"list-markt", "list-markt-2"}
        assert new_card_id("list", "homer/lists/markt.md", taken=taken) == "list-markt-3"


class TestACardWithoutAnId:
    def test_gets_one_after_its_type_and_nothing_else_changes(self):
        given = with_card_id(NOTE, "marge/notes/art-supplies.md")
        card_id = card_id_from(parse(given))
        assert re.fullmatch(r"note-[0-9a-f]{10}", card_id)
        assert given == NOTE.replace("type: note\n", f"type: note\ncard_id: {card_id}\n", 1)

    def test_a_list_gets_its_slug(self):
        text = "---\ntype: list\ntitle: Einkaufsliste\n---\n\n- [ ] Äpfel\n"
        assert with_card_id(text, "family/lists/einkaufsliste.md") == (
            "---\ntype: list\ncard_id: list-einkaufsliste\ntitle: Einkaufsliste\n---\n\n- [ ] Äpfel\n")

    def test_takes_an_id_no_other_card_carries(self):
        text = "---\ntype: list\ntitle: Markt\n---\n"
        assert card_id_from(parse(with_card_id(text, "homer/lists/markt.md",
                                               taken={"list-markt"}))) == "list-markt-2"


class TestACardWithAnId:
    def test_is_left_exactly_as_it_is(self):
        text = NOTE.replace("type: note\n", "type: note\ncard_id: note-7f3a9c21e4\n")
        assert with_card_id(text, "marge/notes/art-supplies.md") is text

    def test_given_another_id_carries_that_one_instead(self):
        """How the curator tells a copied note from the one it was copied from."""
        text = NOTE.replace("type: note\n", "type: note\ncard_id: note-7f3a9c21e4\n")
        assert with_card_id(text, "marge/notes/copy.md", "note-0b6e2d91aa") == \
            NOTE.replace("type: note\n", "type: note\ncard_id: note-0b6e2d91aa\n")


class TestAFileWrittenByHand:
    def test_without_frontmatter_becomes_a_note_titled_by_its_first_heading(self):
        body = "Some notes first.\n\n## Ideas for Lisa's birthday\n\n- a saxophone reed\n"
        given = with_card_id(body, "marge/ideas.md")
        fields = parse(given)
        assert fields["type"] == "note"
        assert fields["title"] == "Ideas for Lisa's birthday"
        assert re.fullmatch(r"note-[0-9a-f]{10}", fields["card_id"])
        assert given.endswith(f"---\n{body}"), "the body stays byte for byte"

    def test_without_a_heading_is_titled_by_its_file_name(self):
        fields = parse(with_card_id("just a line\n", "homer/donut-shops.md"))
        assert fields["title"] == "donut-shops"

    def test_with_frontmatter_but_no_type_keeps_every_field_it_had(self):
        text = "---\ntitle: Kwik-E-Mart hours\ntags:\n  - shopping\n---\n\nOpen all night.\n"
        given = with_card_id(text, "family/kwik-e-mart.md")
        fields = parse(given)
        assert (fields["type"], fields["title"], fields["tags"]) == (
            "note", "Kwik-E-Mart hours", ["shopping"])
        assert given.endswith("title: Kwik-E-Mart hours\ntags:\n  - shopping\n---\n\nOpen all night.\n")


class TestWhatIsNoCard:
    @pytest.mark.parametrize("path", ["README.md", "family/correspondents/README.md", "index.md"])
    def test_a_navigation_page_is_left_alone(self, path):
        assert with_card_id("# Memory\n", path) == "# Memory\n"

    def test_a_page_the_wiki_compiled_is_left_alone(self):
        text = "---\ntype: person\ngenerated: true\ntitle: Homer\n---\n"
        assert with_card_id(text, "homer/about.md") is text

    def test_a_file_in_a_hidden_folder_is_left_alone(self):
        assert with_card_id("# Workspace\n", ".obsidian/notes.md") == "# Workspace\n"


class TestWhatTheRuleWillNotGuess:
    @pytest.mark.parametrize("text, reason", [
        ("---\ntype: recipe\ntitle: Pretzels\n---\n", "no card type"),
        ("---\nkind: correspondent\ncanonical: Duff Brewery\n---\n", "no `type`"),
        ("---\ntitle: [unclosed\n---\n", "does not parse"),
        ("---\ntitle: Moe's\n\nno closing line\n", "no closing"),
    ])
    def test_says_why_and_changes_nothing(self, text, reason):
        with pytest.raises(CardIdError, match=re.escape(reason)):
            with_card_id(text, "family/odd.md")


class TestACardWrittenAgain:
    """`card_identity`: the id fields a writer puts on a card it writes."""

    def test_a_new_card_gets_a_new_id(self):
        assert re.fullmatch(r"note-[0-9a-f]{10}", card_identity({}, "note", "marge/n.md")["card_id"])

    def test_a_rebuild_keeps_the_id_and_the_ids_it_had_before(self):
        previous = {"type": "note", "card_id": "note-0b6e2d91aa",
                    "former_card_ids": ["bookmark-7f3a9c21e4"]}
        assert card_identity(previous, "note", "marge/n.md") == {
            "card_id": "note-0b6e2d91aa", "former_card_ids": ["bookmark-7f3a9c21e4"]}

    def test_a_card_that_changes_type_is_a_new_card_that_names_its_old_id(self):
        """ADR-016: the prefix is the type, so a bookmark refiled as a note gets a note's id."""
        identity = card_identity({"type": "bookmark", "card_id": "bookmark-7f3a9c21e4"},
                                 "note", "marge/n.md")
        assert re.fullmatch(r"note-[0-9a-f]{10}", identity["card_id"])
        assert identity["former_card_ids"] == ["bookmark-7f3a9c21e4"]
