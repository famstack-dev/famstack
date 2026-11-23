"""A card's free-form tags, as the filing core reads them from the model's answer.

`stack.filing.card_tags.tag_list` is the classifier's `tags`, with types
and whitespace normalised. It names no person: the capture's `persons` does.
"""

from stack.filing.card_tags import tag_list


class TestTagList:
    """`tag_list` is the classifier's free-form tags, normalising types and
    whitespace. It names no person: the capture's `persons` does."""

    def test_tags_without_person_tags(self):
        c = {"tags": ["AI", "Productivity"], "persons": ["Homer"]}
        assert tag_list(c) == ["AI", "Productivity"]

    def test_single_string_tag_becomes_list(self):
        c = {"tags": "AI", "persons": ["Homer"]}
        assert tag_list(c) == ["AI"]

    def test_empty_classification_yields_empty(self):
        assert tag_list({}) == []

    def test_skips_non_string_values(self):
        c = {"tags": ["AI", None, 42, ""], "persons": ["Homer", ""]}
        assert tag_list(c) == ["AI"]

    def test_strips_whitespace(self):
        c = {"tags": ["  AI  ", "Productivity"], "persons": ["Homer"]}
        assert tag_list(c) == ["AI", "Productivity"]
