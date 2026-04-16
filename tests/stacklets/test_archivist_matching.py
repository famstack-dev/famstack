"""Entity matching specification for the Archivist.

All matching logic lives in matching.py as pure functions with zero
dependencies. Tests use Springfield-themed names throughout.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "stacklets" / "docs" / "bot"))

from matching import (
    fuzzy_match_entity,
    match_persons,
    build_person_lookup,
    deduplicate_hashtags,
    _is_word_boundary_match,
    _tokenize,
    MAX_TITLE_LENGTH,
)


# ── Tokenizer ───────────────────────────────────────────────────────────

class TestTokenize:

    def test_simple_words(self):
        assert _tokenize("Burns Industries") == ["burns", "industries"]

    def test_hyphenated(self):
        assert _tokenize("Kwik-E-Mart") == ["kwik", "e", "mart"]

    def test_possessive(self):
        assert _tokenize("Moe's Tavern") == ["moe", "s", "tavern"]

    def test_abbreviation_with_dots(self):
        assert _tokenize("ADAC e.V.") == ["adac", "e", "v"]

    def test_strips_whitespace(self):
        assert _tokenize("  Burns  Industries  ") == ["burns", "industries"]

    def test_empty_string(self):
        assert _tokenize("") == []


# ── Word boundary check ────────────────────────────────────────────────

class TestWordBoundaryMatch:

    def test_full_word_at_start(self):
        assert _is_word_boundary_match("Springfield", "Springfield Nuclear Power Plant") is True

    def test_mid_word_substring_blocked(self):
        assert _is_word_boundary_match("Spring", "Springfield") is False

    def test_suffix_substring_blocked(self):
        assert _is_word_boundary_match("Bank", "Bundesbank") is False

    def test_name_inside_longer_name_blocked(self):
        assert _is_word_boundary_match("Art", "Arthur") is False

    def test_multi_word_prefix(self):
        assert _is_word_boundary_match("Springfield Nuclear", "Springfield Nuclear Power Plant") is True

    def test_hyphenated_match(self):
        assert _is_word_boundary_match("Kwik-E-Mart", "Kwik-E-Mart Inc.") is True

    def test_abbreviation_match(self):
        assert _is_word_boundary_match("ADAC", "ADAC e.V.") is True

    def test_empty_shorter(self):
        assert _is_word_boundary_match("", "Springfield") is False

    def test_empty_longer(self):
        assert _is_word_boundary_match("Springfield", "") is False


# ── Fuzzy entity matching ───────────────────────────────────────────────

class TestExactCaseInsensitive:

    def test_lowercase_matches_titlecase(self):
        assert fuzzy_match_entity("kwik-e-mart", {"Kwik-E-Mart": 1}) == "Kwik-E-Mart"

    def test_uppercase_matches_titlecase(self):
        assert fuzzy_match_entity("KWIK-E-MART", {"Kwik-E-Mart": 1}) == "Kwik-E-Mart"

    def test_whitespace_stripped(self):
        assert fuzzy_match_entity("  Moe's Tavern  ", {"Moe's Tavern": 2}) == "Moe's Tavern"

    def test_exact_wins_over_containment(self):
        existing = {"Burns": 1, "Burns Industries": 2}
        assert fuzzy_match_entity("Burns", existing) == "Burns"


class TestWordBoundaryContainment:

    def test_llm_adds_suffix(self):
        # LLM: "Kwik-E-Mart Inc." -> Paperless: "Kwik-E-Mart"
        assert fuzzy_match_entity("Kwik-E-Mart Inc.", {"Kwik-E-Mart": 1}) == "Kwik-E-Mart"

    def test_llm_drops_suffix(self):
        # LLM: "Springfield Nuclear" -> Paperless: "Springfield Nuclear Power Plant"
        assert fuzzy_match_entity("Springfield Nuclear", {"Springfield Nuclear Power Plant": 1}) == "Springfield Nuclear Power Plant"

    def test_abbreviation_suffix(self):
        assert fuzzy_match_entity("ADAC e.V.", {"ADAC": 1}) == "ADAC"

    def test_single_word_full_token(self):
        assert fuzzy_match_entity("Springfield", {"Springfield Elementary": 1}) == "Springfield Elementary"

    def test_mid_word_substring_rejected(self):
        assert fuzzy_match_entity("Spring", {"Springfield Nuclear": 1}) is None

    def test_suffix_substring_rejected(self):
        assert fuzzy_match_entity("Bank", {"Bundesbank": 1}) is None

    def test_haus_not_matching_bauhaus(self):
        assert fuzzy_match_entity("Haus", {"Bauhaus": 1}) is None

    def test_multiple_matches_picks_shortest_by_default(self):
        # Default: shortest = most general. "Burns" over "Burns Industries Intl".
        existing = {"Burns Industries": 1, "Burns Industries International": 2}
        assert fuzzy_match_entity("Burns", existing) == "Burns Industries"

    def test_multiple_matches_picks_longest_when_requested(self):
        # prefer_longest=True: "Homer Jr" over "Homer" for person matching.
        existing = {"Homer": 1, "Homer Jr": 2}
        assert fuzzy_match_entity("Homer Jr. Simpson", existing, prefer_longest=True) == "Homer Jr"

    def test_two_chars_too_short(self):
        assert fuzzy_match_entity("AG", {"AG Versicherung": 1}) is None


class TestNoMatch:

    def test_completely_different(self):
        assert fuzzy_match_entity("Shelbyville Dam", {"Kwik-E-Mart": 1}) is None

    def test_empty_existing(self):
        assert fuzzy_match_entity("Springfield", {}) is None

    def test_empty_name(self):
        assert fuzzy_match_entity("", {"Kwik-E-Mart": 1}) is None

    def test_none_name(self):
        assert fuzzy_match_entity(None, {"Kwik-E-Mart": 1}) is None

    def test_whitespace_only(self):
        assert fuzzy_match_entity("   ", {"Kwik-E-Mart": 1}) is None


# ── Person lookup ───────────────────────────────────────────────────────

SIMPSON_TAGS = {
    "Person: Homer": 1,
    "Person: Marge": 2,
    "Person: Bart": 3,
    "Insurance": 10,
    "Shopping": 11,
}


class TestBuildPersonLookup:

    def test_extracts_person_tags(self):
        lookup = build_person_lookup(SIMPSON_TAGS)
        assert lookup == {"Homer": "Person: Homer", "Marge": "Person: Marge", "Bart": "Person: Bart"}

    def test_ignores_non_person_tags(self):
        assert build_person_lookup({"Insurance": 10}) == {}

    def test_empty_tags(self):
        assert build_person_lookup({}) == {}


# ── Person matching ─────────────────────────────────────────────────────

class TestMatchPersons:
    """match_persons resolves LLM output to Paperless "Person: X" tags.

    Handles single names, full names, lists, wrong case, prefixed forms.
    Returns a list of matched tags (can be empty, one, or multiple).
    """

    def test_single_first_name(self):
        # LLM: "Homer" -> ["Person: Homer"]
        assert match_persons("Homer", SIMPSON_TAGS) == ["Person: Homer"]

    def test_full_name_from_document(self):
        # LLM passes through "Homer J. Simpson" from the document text
        assert match_persons("Homer J. Simpson", SIMPSON_TAGS) == ["Person: Homer"]

    def test_case_insensitive(self):
        assert match_persons("homer", SIMPSON_TAGS) == ["Person: Homer"]

    def test_prefixed_form(self):
        # LLM includes the "Person: " prefix
        assert match_persons("Person: Homer", SIMPSON_TAGS) == ["Person: Homer"]

    def test_list_of_names(self):
        # Joint document (marriage certificate, family insurance)
        assert match_persons(["Homer", "Marge"], SIMPSON_TAGS) == ["Person: Homer", "Person: Marge"]

    def test_list_with_full_names(self):
        # LLM returns full names for both
        result = match_persons(["Homer J. Simpson", "Marge Simpson"], SIMPSON_TAGS)
        assert result == ["Person: Homer", "Person: Marge"]

    def test_list_deduplicates(self):
        # LLM returns same person twice
        assert match_persons(["Homer", "Homer"], SIMPSON_TAGS) == ["Person: Homer"]

    def test_list_with_unknown_filtered(self):
        # "Lisa" is not seeded -- silently dropped, Homer still matched
        result = match_persons(["Homer", "Lisa"], SIMPSON_TAGS)
        assert result == ["Person: Homer"]

    def test_ambiguous_picks_most_specific(self):
        # "Homer Jr. Simpson" should match "Homer Jr" not "Homer"
        tags_with_jr = {"Person: Homer": 1, "Person: Homer Jr": 2}
        assert match_persons("Homer Jr. Simpson", tags_with_jr) == ["Person: Homer Jr"]

    def test_ambiguous_exact_wins(self):
        # "Homer" exact-matches "Homer", not "Homer Jr"
        tags_with_jr = {"Person: Homer": 1, "Person: Homer Jr": 2}
        assert match_persons("Homer", tags_with_jr) == ["Person: Homer"]

    def test_unknown_returns_empty(self):
        assert match_persons("Lisa", SIMPSON_TAGS) == []

    def test_null_string_returns_empty(self):
        assert match_persons("null", SIMPSON_TAGS) == []

    def test_none_returns_empty(self):
        assert match_persons(None, SIMPSON_TAGS) == []

    def test_empty_string_returns_empty(self):
        assert match_persons("", SIMPSON_TAGS) == []

    def test_empty_list_returns_empty(self):
        assert match_persons([], SIMPSON_TAGS) == []

    def test_no_person_tags_returns_empty(self):
        assert match_persons("Homer", {"Insurance": 10}) == []

    def test_list_with_nulls_filtered(self):
        result = match_persons(["Homer", "null", None, ""], SIMPSON_TAGS)
        assert result == ["Person: Homer"]


# ── Hashtag deduplication ───────────────────────────────────────────────

class TestDeduplicateHashtags:

    def test_all_different(self):
        result = deduplicate_hashtags("Shopping", "Homer", "Invoice", "Kwik-E-Mart")
        assert result == ["#Shopping", "#Homer", "#Invoice", "#Kwik-E-Mart"]

    def test_duplicate_removed(self):
        result = deduplicate_hashtags("Invoice", "Homer", "Invoice", "Kwik-E-Mart")
        assert result == ["#Invoice", "#Homer", "#Kwik-E-Mart"]

    def test_case_insensitive_dedup(self):
        result = deduplicate_hashtags("invoice", "Homer", "Invoice", "Kwik-E-Mart")
        assert result == ["#invoice", "#Homer", "#Kwik-E-Mart"]

    def test_person_prefix_stripped(self):
        result = deduplicate_hashtags("Shopping", "Person: Homer", None, "Homer")
        assert result == ["#Shopping", "#Homer"]

    def test_none_filtered(self):
        result = deduplicate_hashtags("Shopping", None, "Invoice", None)
        assert result == ["#Shopping", "#Invoice"]

    def test_null_string_filtered(self):
        result = deduplicate_hashtags("Shopping", "null", "Invoice", "null")
        assert result == ["#Shopping", "#Invoice"]

    def test_all_none(self):
        assert deduplicate_hashtags(None, None, None, None) == []

    def test_no_args(self):
        assert deduplicate_hashtags() == []

    def test_multiple_persons(self):
        # Joint document: topic + two persons + correspondent
        result = deduplicate_hashtags("Insurance", "Homer", "Marge", "ADAC")
        assert result == ["#Insurance", "#Homer", "#Marge", "#ADAC"]


# ── Constants ───────────────────────────────────────────────────────────

class TestConstants:

    def test_paperless_title_limit(self):
        assert MAX_TITLE_LENGTH == 128
