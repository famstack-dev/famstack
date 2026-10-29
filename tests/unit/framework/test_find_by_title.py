"""A named card is found by its title, whatever its file is called.

Correspondents, lists and topics are named cards: the archivist, the wiki and
the agent find the one a name means with `stack.cards.lookup.find_by_title`,
over a copy of the family memory, instead of spelling a path from the name.
A file name is spelled once, when the card is made; a card made before German
letters were spelled out (`muller.md` for Müller) is still found.
"""

from __future__ import annotations

import pytest

from stack.cards.lookup import AmbiguousTitle, find_by_title


def card(root, path: str, card_type: str, title: str, extra: str = "") -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f"---\ntype: {card_type}\ntitle: {title}\n{extra}---\n\n# {title}\n", encoding="utf-8")


def test_a_card_spelled_the_old_way_is_found_by_its_title(tmp_path):
    card(tmp_path, "family/correspondents/muller.md", "correspondent", "Müller")
    assert find_by_title("correspondent", "Müller", root=tmp_path, bucket="family") == \
        "family/correspondents/muller.md"


@pytest.mark.parametrize("name", ["müller", "MÜLLER", "Mueller", "  Müller "])
def test_case_spacing_and_spelled_out_letters_do_not_count(tmp_path, name):
    card(tmp_path, "family/correspondents/muller.md", "correspondent", "Müller")
    assert find_by_title("correspondent", name, root=tmp_path, bucket="family") == \
        "family/correspondents/muller.md"


def test_only_cards_of_the_type_asked_for_and_under_the_bucket(tmp_path):
    card(tmp_path, "family/lists/camping.md", "list", "Camping")
    card(tmp_path, "homer/camping/topic.md", "topic", "Camping")
    assert find_by_title("topic", "Camping", root=tmp_path, bucket="family") is None
    assert find_by_title("topic", "Camping", root=tmp_path, bucket="homer") == "homer/camping/topic.md"


def test_a_view_of_a_card_is_not_the_card(tmp_path):
    card(tmp_path, "family/correspondents/muller/about.md", "correspondent", "Müller",
         extra="view: correspondent\n")
    assert find_by_title("correspondent", "Müller", root=tmp_path, bucket="family") is None


def test_no_card_with_the_name_is_none(tmp_path):
    card(tmp_path, "family/correspondents/muller.md", "correspondent", "Müller")
    assert find_by_title("correspondent", "Moe's Tavern", root=tmp_path, bucket="family") is None
    assert find_by_title("correspondent", "!!!", root=tmp_path, bucket="family") is None


def test_two_cards_with_one_name_are_named_and_neither_is_chosen(tmp_path):
    card(tmp_path, "family/correspondents/muller.md", "correspondent", "Müller")
    card(tmp_path, "family/correspondents/mueller.md", "correspondent", "Mueller")
    with pytest.raises(AmbiguousTitle) as refused:
        find_by_title("correspondent", "Müller", root=tmp_path, bucket="family")
    assert refused.value.paths == ["family/correspondents/mueller.md", "family/correspondents/muller.md"]
    assert "family/correspondents/muller.md" in str(refused.value)
