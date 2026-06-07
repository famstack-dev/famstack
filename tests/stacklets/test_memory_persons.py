"""Person loader + prompt-section renderer.

Tests construct a tmp vault with per-member `<slug>/about.md` pages,
then load them through the same path the archivist uses at startup.
The frontmatter shape is the contract: hand edits in Obsidian or the
Forgejo web UI must land here, and curated synonyms must ride into
the classifier prompt verbatim.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))

from lib import (  # noqa: E402
    Person,
    load_persons_from_vault,
    persons_prompt_section,
)


# ─── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture
def vault(tmp_path):
    """A bare vault — member buckets get created per test so each
    case can assert which directories the loader walks and which
    it skips."""
    return tmp_path


def _write_about(vault: Path, slug: str, body: str) -> Path:
    """Write a per-member about.md at `<vault>/<slug>/about.md`."""
    folder = vault / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "about.md"
    path.write_text(body)
    return path


# ─── Loader ──────────────────────────────────────────────────────────────

class TestLoadPersons:

    def test_returns_empty_when_vault_missing(self, tmp_path):
        # Nothing on disk -- no crash, just an empty list.
        assert load_persons_from_vault(tmp_path / "missing") == []

    def test_loads_a_minimal_page(self, vault):
        _write_about(vault, "homer", """---
title: Homer
slug: homer
canonical: Homer
---

# Homer
""")
        persons = load_persons_from_vault(vault)
        assert persons == [
            Person(
                canonical="Homer",
                slug="homer",
                synonyms=[],
                source_path=vault / "homer" / "about.md",
            ),
        ]

    def test_collects_synonyms(self, vault):
        _write_about(vault, "marge", """---
title: Marge
slug: marge
canonical: Marge
synonyms:
  - Marjorie
  - Marge Bouvier
  - Marge Simpson
---

# Marge
""")
        [p] = load_persons_from_vault(vault)
        assert p.canonical == "Marge"
        assert p.synonyms == ["Marjorie", "Marge Bouvier", "Marge Simpson"]
        assert p.all_known_names() == [
            "Marge", "Marjorie", "Marge Bouvier", "Marge Simpson",
        ]

    def test_skips_shared_bucket(self, vault):
        # The shared bucket carries household-wide content, not a
        # member; if it ever ends up with an about.md it must not
        # leak into the prompt.
        _write_about(vault, "family", """---
canonical: Family
---
""")
        assert load_persons_from_vault(vault, shared_bucket="family") == []

    def test_skips_non_member_directories(self, vault):
        # `wiki/`, `.git/`, `private/`, `templates/`, `_shared/`,
        # `.obsidian/` are reserved and never represent a household
        # member -- the loader keeps these out so accidental about.md
        # files there don't pollute the prompt.
        for reserved in ("wiki", ".git", ".obsidian", "private",
                         "templates", "_shared"):
            _write_about(vault, reserved, "---\ncanonical: bogus\n---\n")
        assert load_persons_from_vault(vault) == []

    def test_skips_pages_with_wrong_kind(self, vault):
        # `kind:` is optional, but when present must be "person".
        # A correspondent-shaped page sitting at a member slug path
        # is still not a person.
        _write_about(vault, "adac", """---
kind: correspondent
canonical: ADAC
---
""")
        assert load_persons_from_vault(vault) == []

    def test_falls_back_to_slug_when_canonical_absent(self, vault):
        # A freshly-created entity bucket without curated frontmatter
        # should still surface as a Person so the classifier sees
        # the name; the slug stands in for the canonical until the
        # household edits the page.
        _write_about(vault, "lisa", "---\ntitle: Lisa\n---\n")
        [p] = load_persons_from_vault(vault)
        assert p.canonical == "Lisa"
        assert p.slug == "lisa"

    def test_sorts_by_slug_for_deterministic_prompt(self, vault):
        # Prompt stability matters: the same vault must produce the
        # same prompt every call, so the renderer can't depend on
        # filesystem walk order.
        for slug in ("maggie", "bart", "homer", "lisa", "marge"):
            _write_about(vault, slug, f"---\ncanonical: {slug.title()}\n---\n")
        persons = load_persons_from_vault(vault)
        assert [p.slug for p in persons] == [
            "bart", "homer", "lisa", "maggie", "marge",
        ]


# ─── Prompt renderer ─────────────────────────────────────────────────────

class TestPersonsPromptSection:

    def test_empty_when_no_persons(self):
        # The bot falls back to the bare Paperless roster when the
        # vault hasn't been seeded yet; the renderer must signal that
        # cleanly via empty string.
        assert persons_prompt_section([]) == ""

    def test_renders_canonical_only(self):
        [section] = [persons_prompt_section([
            Person(canonical="Homer", slug="homer"),
        ])]
        assert section == (
            "Family members (canonical first name; synonyms in parens):\n"
            "  - Homer"
        )

    def test_renders_synonyms_inline(self):
        section = persons_prompt_section([
            Person(canonical="Marge", slug="marge",
                   synonyms=["Marjorie", "Marge Bouvier"]),
            Person(canonical="Homer", slug="homer"),
        ])
        assert section == (
            "Family members (canonical first name; synonyms in parens):\n"
            "  - Marge (Marjorie, Marge Bouvier)\n"
            "  - Homer"
        )
