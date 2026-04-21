"""Taxonomy seeding specification for the Docs stacklet.

Tests the taxonomy loading and seed logic. Uses the actual
taxonomy.toml file to verify it parses correctly.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "stacklets" / "docs"))

from seed import _load_taxonomy, TAXONOMY_PATH


class TestLoadTaxonomy:

    def test_taxonomy_file_exists(self):
        assert TAXONOMY_PATH.exists(), "taxonomy.toml must exist"

    def test_german_tags_loaded(self):
        t = _load_taxonomy("de")
        assert "Versicherung" in t["tags"]
        assert "Steuer" in t["tags"]
        assert "Wohnen" in t["tags"]
        assert "Nebenkosten" in t["tags"]

    def test_german_types_loaded(self):
        t = _load_taxonomy("de")
        assert "Rechnung" in t["types"]
        assert "Vertrag" in t["types"]
        assert "Bescheinigung" in t["types"]

    def test_english_tags_loaded(self):
        t = _load_taxonomy("en")
        assert "Insurance" in t["tags"]
        assert "Tax" in t["tags"]
        assert "Housing" in t["tags"]
        assert "Utility" in t["tags"]

    def test_english_types_loaded(self):
        t = _load_taxonomy("en")
        assert "Invoice" in t["types"]
        assert "Contract" in t["types"]
        assert "Certificate" in t["types"]

    def test_unknown_language_falls_back_to_english(self):
        t = _load_taxonomy("fr")
        assert "Insurance" in t["tags"]

    def test_language_prefix_matching(self):
        # "de-DE" should match "de"
        t = _load_taxonomy("de-DE")
        assert "Versicherung" in t["tags"]

    def test_german_tag_count(self):
        t = _load_taxonomy("de")
        assert len(t["tags"]) >= 15, f"Expected 15+ tags, got {len(t['tags'])}"

    def test_english_tag_count(self):
        t = _load_taxonomy("en")
        assert len(t["tags"]) >= 15, f"Expected 15+ tags, got {len(t['tags'])}"

    def test_no_inline_comments_in_values(self):
        """Inline YAML comments must be stripped during parsing."""
        for lang in ("de", "en"):
            t = _load_taxonomy(lang)
            for tag in t["tags"]:
                assert "#" not in tag, f"Comment leaked into tag: {tag}"
            for typ in t["types"]:
                assert "#" not in typ, f"Comment leaked into type: {typ}"

    def test_no_empty_values(self):
        for lang in ("de", "en"):
            t = _load_taxonomy(lang)
            for tag in t["tags"]:
                assert tag.strip(), "Empty tag found"
            for typ in t["types"]:
                assert typ.strip(), "Empty type found"
