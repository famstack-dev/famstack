"""The capture's topics: a topic room's ids first, then the topics the model chose.

`stacklets.docs.bot.note_topics` is pure, so its rules are pinned here
directly; the capture's surface tests (`test_capture_names_people_and_tags.py`)
drive the same rules through the archivist.
"""

from stack.ontology import Ontology
from stack.filing.vocabulary import Vocabulary
from stacklets.docs.bot.note_topics import topics_hint, with_chosen_topics, with_room_topic


class TestTopicSeedMerge:
    """A topic room's ids come first: seed first, classifier additions
    second, duplicates collapsed. The room-based routing in the archivist
    leans on this contract."""

    def test_seed_survives_empty_classifier_tags(self):
        """The strongest property: a capture filed in a topic room
        retains the seed even when the classifier returns no tags
        at all. Room beats classifier in the empty case."""
        merged = with_room_topic(
            {"tags": []}, ["camping"],
        )
        assert merged["tags"] == ["camping"]

    def test_seed_first_then_classifier_additions(self):
        """Seed precedes classifier tags in the merged list. This
        is load-bearing for downstream consumers that read the first
        tag as the canonical topic (e.g. the dream cycle's pattern
        detection)."""
        merged = with_room_topic(
            {"tags": ["gear", "italy-2026"]},
            ["camping"],
        )
        assert merged["tags"] == ["camping", "gear", "italy-2026"]

    def test_seed_deduplicates_when_classifier_repeats_it(self):
        """The classifier may include the seed tag in its output
        despite the prompt's guidance. The merge collapses the
        duplicate; the seed's position wins."""
        merged = with_room_topic(
            {"tags": ["camping", "gear"]},
            ["camping"],
        )
        assert merged["tags"] == ["camping", "gear"]

    def test_no_seed_is_noop(self):
        """Non-topic-room captures pass `None` (or omit
        the kwarg). The classifier's tags are returned unchanged."""
        merged = with_room_topic(
            {"tags": ["gear"]}, None,
        )
        assert merged["tags"] == ["gear"]
        merged_empty = with_room_topic(
            {"tags": ["gear"]}, [],
        )
        assert merged_empty["tags"] == ["gear"]

    def test_classifier_missing_tags_key(self):
        """When the classifier degraded (no tags key in the payload),
        the seed still applies. The result has a fresh `tags` list."""
        merged = with_room_topic(
            {"persons": ["Homer"]}, ["camping"],
        )
        assert merged["tags"] == ["camping"]

    def test_classifier_returns_string_for_tags(self):
        """The classifier may return a bare string for `tags:` (some
        prompts produce that). The merge reads it as a list of one, so
        the seed prepends cleanly."""
        merged = with_room_topic(
            {"tags": "gear"}, ["camping"],
        )
        assert merged["tags"] == ["camping", "gear"]

    def test_seed_with_multiple_entries(self):
        """A topic room with multiple guaranteed tags (forward
        compatibility for `default_topics: [camping, outdoor]`)
        keeps all of them in order."""
        merged = with_room_topic(
            {"tags": ["italy-2026"]},
            ["camping", "outdoor"],
        )
        assert merged["tags"] == ["camping", "outdoor", "italy-2026"]

    def test_seed_skips_whitespace_and_non_string(self):
        """Defensive: malformed seed entries don't pollute the
        merged list. Empty strings and non-strings drop silently."""
        merged = with_room_topic(
            {"tags": ["gear"]},
            ["camping", "", None, "  ", 42],  # type: ignore[list-item]
        )
        assert merged["tags"] == ["camping", "gear"]


class TestChosenTopics:
    """The topics the model chose follow the keywords as ids of the card's
    vocabulary; a word that names no topic there is left out."""

    VOCABULARY = Vocabulary(Ontology.loads(
        '[topic.travel]\nnames = { en = "Travel", de = "Reise" }\nsynonyms = { en = ["holiday"] }\n'))

    def test_a_chosen_topic_follows_the_keywords_as_its_id(self):
        merged = with_chosen_topics({"tags": ["tent"], "topics": ["holiday", "Mars"]}, self.VOCABULARY)
        assert merged["tags"] == ["tent", "travel"]
        assert "topics" not in merged

    def test_an_answer_without_topics_keeps_its_keywords(self):
        assert with_chosen_topics({"tags": ["tent"]}, self.VOCABULARY)["tags"] == ["tent"]


def test_the_hint_is_one_json_line():
    """A title with a quote stays inside the list: the line parses as JSON after its label."""
    import json
    line = topics_hint(['Camper "Van"', "Health"])
    assert line.endswith("\n") and line.count("\n") == 1
    assert json.loads(line.split(": ", 1)[1]) == ['Camper "Van"', "Health"]
