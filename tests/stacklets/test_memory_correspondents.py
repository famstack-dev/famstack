"""Correspondent wiki loader + prompt-section renderer.

Tests construct a tmp vault with markdown files under
`wiki/correspondents/`, then load them through the same path the
archivist uses at startup. The frontmatter shape is the contract:
hand edits in Obsidian or the Forgejo web UI must land here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))

from lib import (  # noqa: E402
    Correspondent,
    correspondents_prompt_section,
    load_correspondents_from_vault,
)


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path):
    """A vault layout with `wiki/correspondents/` ready to receive pages."""
    (tmp_path / "wiki" / "correspondents").mkdir(parents=True)
    return tmp_path


def _write_page(vault, slug: str, body: str) -> Path:
    path = vault / "wiki" / "correspondents" / f"{slug}.md"
    path.write_text(body)
    return path


# ─── Loader ──────────────────────────────────────────────────────────────

class TestLoadCorrespondents:

    def test_returns_empty_when_directory_missing(self, tmp_path):
        # No wiki/correspondents/ at all — clean empty result.
        assert load_correspondents_from_vault(tmp_path) == []

    def test_loads_a_minimal_page(self, vault):
        _write_page(vault, "adac", """---
kind: correspondent
canonical: ADAC
---

# ADAC
""")
        cs = load_correspondents_from_vault(vault)
        assert len(cs) == 1
        c = cs[0]
        assert c.canonical == "ADAC"
        assert c.aliases == []
        assert c.topics == []

    def test_loads_aliases_topics_and_contact_fields(self, vault):
        _write_page(vault, "adac", """---
kind: correspondent
canonical: ADAC
aliases:
  - "ADAC Ortsverband Manzell"
  - "ADAC Versicherung AG"
topics: [insurance, vehicle]
address: "Hansastraße 19, 80686 München"
phone: "089 7676 0"
website: "https://www.adac.de"
---

# ADAC
""")
        c = load_correspondents_from_vault(vault)[0]
        assert c.aliases == ["ADAC Ortsverband Manzell", "ADAC Versicherung AG"]
        assert c.topics == ["insurance", "vehicle"]
        assert c.address == "Hansastraße 19, 80686 München"
        assert c.phone == "089 7676 0"
        assert c.website == "https://www.adac.de"

    def test_falls_back_to_filename_when_canonical_missing(self, vault):
        _write_page(vault, "aok", """---
kind: correspondent
---

# AOK
""")
        c = load_correspondents_from_vault(vault)[0]
        assert c.canonical == "aok"

    def test_skips_pages_with_wrong_kind(self, vault):
        _write_page(vault, "topic-page", """---
kind: topic
canonical: insurance
---
""")
        assert load_correspondents_from_vault(vault) == []

    def test_orders_results_by_filename(self, vault):
        _write_page(vault, "zappos", "---\nkind: correspondent\ncanonical: Zappos\n---\n")
        _write_page(vault, "aok",    "---\nkind: correspondent\ncanonical: AOK\n---\n")
        _write_page(vault, "moe",    "---\nkind: correspondent\ncanonical: Moes\n---\n")

        names = [c.canonical for c in load_correspondents_from_vault(vault)]
        assert names == ["AOK", "Moes", "Zappos"]

    def test_skips_pages_without_frontmatter(self, vault):
        # A README or stray markdown should not crash the loader.
        (vault / "wiki" / "correspondents" / "README.md").write_text(
            "# Just a note, no frontmatter\n"
        )
        assert load_correspondents_from_vault(vault) == []

    def test_known_names_includes_canonical_first(self):
        c = Correspondent(canonical="ADAC", aliases=["ADAC Ortsverband Manzell"])
        assert c.all_known_names() == ["ADAC", "ADAC Ortsverband Manzell"]

    def test_known_names_deduplicates(self):
        c = Correspondent(canonical="ADAC", aliases=["ADAC", "ADAC Ortsverband Manzell"])
        assert c.all_known_names() == ["ADAC", "ADAC Ortsverband Manzell"]


# ─── Prompt renderer ─────────────────────────────────────────────────────

class TestCorrespondentsPromptSection:

    def test_empty_when_no_correspondents(self):
        assert correspondents_prompt_section([]) == ""

    def test_lists_canonical_without_aliases(self):
        section = correspondents_prompt_section([
            Correspondent(canonical="AOK"),
        ])
        assert section == "Existing correspondents (canonical; aliases in parens):\n  - AOK"

    def test_inlines_aliases_in_parens(self):
        section = correspondents_prompt_section([
            Correspondent(
                canonical="ADAC",
                aliases=["ADAC Ortsverband Manzell", "ADAC Versicherung AG"],
            ),
        ])
        assert "ADAC (ADAC Ortsverband Manzell, ADAC Versicherung AG)" in section

    def test_preserves_caller_order(self):
        section = correspondents_prompt_section([
            Correspondent(canonical="ADAC"),
            Correspondent(canonical="AOK"),
            Correspondent(canonical="Anthropic"),
        ])
        lines = section.splitlines()[1:]
        assert lines == ["  - ADAC", "  - AOK", "  - Anthropic"]
