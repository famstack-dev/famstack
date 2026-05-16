"""Behavioural tests for the framework ontology module.

The ontology defines a stack's shared vocabulary — topics ("Insurance",
"Tax"), document types ("Invoice", "Contract"), with localized names
and synonyms. It is loaded once from TOML and consumed by classifiers
(via `classifier_prompt_section`) and retrievers (via `resolve_topic`).

These tests pin the contract: a small TOML fixture goes in, an
Ontology with predictable lookups comes out. The module itself stays
product-agnostic — no famstack vocabulary inside `lib/stack/`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from stack.ontology import Ontology, Topic, DocType


# ─── Fixture ─────────────────────────────────────────────────────────────

ONTOLOGY_TOML = """
# Two topics + two doctypes with localized names, synonyms, and keywords.

[topic.insurance]
names    = { de = "Versicherung", en = "Insurance" }
synonyms = { de = ["Police"], en = ["policy", "coverage"] }
keywords = { de = ["versichert"], en = ["insured", "claim"] }
types    = ["policy", "invoice"]

[topic.tax]
names    = { de = "Steuer", en = "Tax" }
synonyms = { en = ["taxation"] }

[doctype.invoice]
names    = { de = "Rechnung", en = "Invoice" }
synonyms = { en = ["bill"] }

[doctype.policy]
names    = { de = "Police", en = "Policy" }
"""


@pytest.fixture
def ontology(tmp_path: Path) -> Ontology:
    path = tmp_path / "ontology.toml"
    path.write_text(ONTOLOGY_TOML)
    return Ontology.load(path)


# ─── Load ────────────────────────────────────────────────────────────────

class TestLoad:

    def test_loads_topics_keyed_by_id(self, ontology):
        assert set(ontology.topics) == {"insurance", "tax"}

    def test_loads_doctypes_keyed_by_id(self, ontology):
        assert set(ontology.doctypes) == {"invoice", "policy"}

    def test_topic_has_localized_names(self, ontology):
        t = ontology.topics["insurance"]
        assert t.name("en") == "Insurance"
        assert t.name("de") == "Versicherung"

    def test_unknown_language_falls_back_to_english(self, ontology):
        t = ontology.topics["insurance"]
        assert t.name("fr") == "Insurance"

    def test_language_prefix_matching(self, ontology):
        # "de-DE" should match "de"
        t = ontology.topics["insurance"]
        assert t.name("de-DE") == "Versicherung"

    def test_topic_carries_synonyms_per_language(self, ontology):
        t = ontology.topics["insurance"]
        assert "policy" in t.synonyms["en"]
        assert "Police" in t.synonyms["de"]

    def test_topic_can_cross_reference_doctypes(self, ontology):
        t = ontology.topics["insurance"]
        assert t.types == ["policy", "invoice"]

    def test_topic_keywords_optional(self, ontology):
        # "tax" defines no keywords — empty dict, not a load error.
        t = ontology.topics["tax"]
        assert t.keywords == {}


# ─── Resolve ─────────────────────────────────────────────────────────────

class TestResolveTopic:

    def test_resolves_by_canonical_name(self, ontology):
        t = ontology.resolve_topic("Insurance", lang="en")
        assert t is not None and t.id == "insurance"

    def test_resolution_is_case_insensitive(self, ontology):
        t = ontology.resolve_topic("INSURANCE", lang="en")
        assert t is not None and t.id == "insurance"

    def test_resolves_by_synonym(self, ontology):
        t = ontology.resolve_topic("coverage", lang="en")
        assert t is not None and t.id == "insurance"

    def test_resolves_in_per_language_vocabulary(self, ontology):
        t = ontology.resolve_topic("Versicherung", lang="de")
        assert t is not None and t.id == "insurance"

    def test_returns_none_when_unknown(self, ontology):
        assert ontology.resolve_topic("badminton", lang="en") is None


# ─── Classifier prompt ───────────────────────────────────────────────────

class TestClassifierPromptSection:

    def test_lists_topics_for_requested_language(self, ontology):
        section = ontology.classifier_prompt_section("en")
        assert "Insurance" in section
        assert "Tax" in section
        # German names must not leak into the English prompt.
        assert "Versicherung" not in section

    def test_lists_doctypes_for_requested_language(self, ontology):
        section = ontology.classifier_prompt_section("en")
        assert "Invoice" in section
        assert "Policy" in section

    def test_includes_synonyms_inline_for_disambiguation(self, ontology):
        section = ontology.classifier_prompt_section("en")
        # "coverage" is a synonym of Insurance; the LLM needs to see it
        # so it knows the two map to the same topic.
        assert "coverage" in section

    def test_swaps_vocabulary_per_language(self, ontology):
        section_de = ontology.classifier_prompt_section("de")
        assert "Versicherung" in section_de
        assert "Steuer" in section_de
        # English names must not leak into the German prompt.
        assert "Insurance" not in section_de


# ─── Empty ontology ──────────────────────────────────────────────────────

class TestEmpty:

    def test_load_empty_file_yields_empty_ontology(self, tmp_path):
        (tmp_path / "empty.toml").write_text("")
        ont = Ontology.load(tmp_path / "empty.toml")
        assert ont.topics == {}
        assert ont.doctypes == {}

    def test_classifier_section_on_empty_ontology_is_valid_string(self, tmp_path):
        (tmp_path / "empty.toml").write_text("")
        ont = Ontology.load(tmp_path / "empty.toml")
        # Should not crash and should still produce a usable prompt fragment.
        section = ont.classifier_prompt_section("en")
        assert isinstance(section, str)
