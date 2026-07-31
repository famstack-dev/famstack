"""Correspondent loader + prompt-section renderer.

Tests construct a tmp vault with markdown files under the shared
bucket's `correspondents/` folder (default `family/correspondents/`,
slug configurable via stack.toml [core] shared_bucket), then load
them through the same path the archivist uses at startup. The
frontmatter shape is the contract: hand edits in Obsidian or the
Forgejo web UI must land here.
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
    """A vault layout with `family/correspondents/` ready to receive
    pages. The shared bucket holds institutional artifacts (banks,
    schools, insurers — senders of mail) and stays outside the wiki
    engine's regenerate scope."""
    (tmp_path / "family" / "correspondents").mkdir(parents=True)
    return tmp_path


def _write_page(vault, slug: str, body: str) -> Path:
    path = vault / "family" / "correspondents" / f"{slug}.md"
    path.write_text(body)
    return path


# ─── Loader ──────────────────────────────────────────────────────────────

class TestLoadCorrespondents:

    def test_returns_empty_when_directory_missing(self, tmp_path):
        # No family/correspondents/ at all — clean empty result.
        assert load_correspondents_from_vault(tmp_path) == []

    def test_loads_a_minimal_page(self, vault):
        _write_page(vault, "duff-insurance", """---
kind: correspondent
canonical: Duff Insurance
---

# Duff Insurance
""")
        cs = load_correspondents_from_vault(vault)
        assert len(cs) == 1
        c = cs[0]
        assert c.canonical == "Duff Insurance"
        assert c.aliases == []
        assert c.topics == []

    def test_loads_aliases_topics_and_contact_fields(self, vault):
        _write_page(vault, "duff-insurance", """---
kind: correspondent
canonical: Duff Insurance
aliases:
  - "Duff Insurance Ortsverband Springfield"
  - "Duff Insurance Versicherung AG"
topics:
  - insurance
  - vehicle
address: "Hansastraße 19, 80686 München"
phone: "089 7676 0"
website: "https://www.duff-insurance.de"
---

# Duff Insurance
""")
        c = load_correspondents_from_vault(vault)[0]
        assert c.aliases == ["Duff Insurance Ortsverband Springfield", "Duff Insurance Versicherung AG"]
        assert c.topics == ["insurance", "vehicle"]
        assert c.address == "Hansastraße 19, 80686 München"
        assert c.phone == "089 7676 0"
        assert c.website == "https://www.duff-insurance.de"

    def test_falls_back_to_filename_when_canonical_missing(self, vault):
        _write_page(vault, "springfield-mutual", """---
kind: correspondent
---

# Springfield Mutual
""")
        c = load_correspondents_from_vault(vault)[0]
        assert c.canonical == "springfield-mutual"

    def test_skips_pages_with_wrong_kind(self, vault):
        _write_page(vault, "topic-page", """---
kind: topic
canonical: insurance
---
""")
        assert load_correspondents_from_vault(vault) == []

    def test_orders_results_by_filename(self, vault):
        _write_page(vault, "zappos", "---\nkind: correspondent\ncanonical: Zappos\n---\n")
        _write_page(vault, "springfield-mutual",    "---\nkind: correspondent\ncanonical: Springfield Mutual\n---\n")
        _write_page(vault, "moe",    "---\nkind: correspondent\ncanonical: Moes\n---\n")

        names = [c.canonical for c in load_correspondents_from_vault(vault)]
        assert names == ["Moes", "Springfield Mutual", "Zappos"]

    def test_skips_pages_without_frontmatter(self, vault):
        # A README or stray markdown should not crash the loader.
        (vault / "family" / "correspondents" / "README.md").write_text(
            "# Just a note, no frontmatter\n"
        )
        assert load_correspondents_from_vault(vault) == []

    def test_known_names_includes_canonical_first(self):
        c = Correspondent(canonical="Duff Insurance", aliases=["Duff Insurance Ortsverband Springfield"])
        assert c.all_known_names() == ["Duff Insurance", "Duff Insurance Ortsverband Springfield"]

    def test_known_names_deduplicates(self):
        c = Correspondent(canonical="Duff Insurance", aliases=["Duff Insurance", "Duff Insurance Ortsverband Springfield"])
        assert c.all_known_names() == ["Duff Insurance", "Duff Insurance Ortsverband Springfield"]


# ─── Prompt renderer ─────────────────────────────────────────────────────

class TestCorrespondentsPromptSection:

    def test_empty_when_no_correspondents(self):
        assert correspondents_prompt_section([]) == ""

    def test_lists_canonical_without_aliases(self):
        section = correspondents_prompt_section([
            Correspondent(canonical="Springfield Mutual"),
        ])
        assert section == "Existing correspondents (canonical; aliases in parens):\n  - Springfield Mutual"

    def test_inlines_aliases_in_parens(self):
        section = correspondents_prompt_section([
            Correspondent(
                canonical="Duff Insurance",
                aliases=["Duff Insurance Ortsverband Springfield", "Duff Insurance Versicherung AG"],
            ),
        ])
        assert "Duff Insurance (Duff Insurance Ortsverband Springfield, Duff Insurance Versicherung AG)" in section

    def test_preserves_caller_order(self):
        section = correspondents_prompt_section([
            Correspondent(canonical="Duff Insurance"),
            Correspondent(canonical="Springfield Mutual"),
            Correspondent(canonical="Anthropic"),
        ])
        lines = section.splitlines()[1:]
        assert lines == ["  - Duff Insurance", "  - Springfield Mutual", "  - Anthropic"]
