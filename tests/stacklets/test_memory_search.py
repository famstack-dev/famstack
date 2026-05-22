"""CLI behaviour of `stack memory search` — the curated-layer query surface.

Search is rg under the hood: matches in the body of any markdown file
inside `<vault>`, post-filtered by YAML frontmatter (persons, ...).
Tests use a self-contained Simpsons vault under `tmp_path` and pass it
via `--vault`, so they exercise the real CLI subprocess without
depending on a configured `data_dir` or a live Forgejo.

Exit-code contract these tests pin:

    0  results found
    1  no results
    2  invalid arguments / usage error
    3  backend (ripgrep) failure  — not asserted here

Output shape is deliberately readable for both humans and agents; a
block per result is the format, `--paths` and `--count` are the
machine-friendly escapes.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


# ─── Fixture vault ───────────────────────────────────────────────────────

def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip("\n"))


@pytest.fixture
def vault(tmp_path):
    """A miniature Simpsons vault with frontmatter-rich markdown.

    Three documents covering the shapes the live archivist emits:
    a doc briefing (Bart), an entity note (Homer), and a shared note
    with two persons (Marge + Lisa). Enough surface to exercise body
    match, person filter, and result ranking by date.
    """
    v = tmp_path / "vault"

    _write(v / "family/documents/2026/03/2026-03-08-mmr-impfung.md", """
        ---
        title: Impfbescheinigung Bart MMR Auffrischung
        date: 2026-03-08
        persons:
          - Bart
        tags:
          - Topic:Health
        ---

        # Impfbescheinigung Bart MMR Auffrischung

        Tierarzt-style routine vaccination record entered today.
    """)

    _write(v / "homer/notes/2026/02/2026-02-10-radlager.md", """
        ---
        title: Radlager hinten links Mecha Mike
        date: 2026-02-10
        persons:
          - Homer
        tags:
          - Topic:Car
        ---

        # Radlager hinten links Mecha Mike

        Brummen aus dem linken Hinterrad ab 60 km/h.
    """)

    _write(v / "marge/notes/2026/02/2026-02-12-elternabend.md", """
        ---
        title: Elternabend Klasse 4b
        date: 2026-02-12
        persons:
          - Marge
          - Lisa
        tags:
          - 'Person: Marge'
          - 'Person: Lisa'
          - Topic:Education
        ---

        # Elternabend Klasse 4b

        Frau Hoover empfiehlt das Gymnasium fuer Lisa.
    """)

    # A fourth doc with a unique date — used by ordering tests so we
    # can assert newest-first across more than two date buckets.
    _write(v / "marge/bookmarks/2026/03/2026-03-05-quick-bread.md", """
        ---
        title: Saftiges Quick-Bread Rezept
        date: 2026-03-05
        persons:
          - Marge
        tags:
          - Topic:Cooking
        ---

        # Saftiges Quick-Bread Rezept

        Backzeit 55 Minuten bei 180 Grad. Mit Walnuessen.
    """)

    return v


# ─── Query ───────────────────────────────────────────────────────────────

class TestQuery:
    """Plain text matching against the markdown body."""

    def test_body_match_returns_zero_with_result(self, stack_cli, vault):
        code, out, _ = stack_cli(
            "memory", "search", "Brummen", "--vault", str(vault),
        )
        assert code == 0
        assert "radlager" in out.lower()

    def test_no_match_exits_one(self, stack_cli, vault):
        code, _, _ = stack_cli(
            "memory", "search", "spaceship", "--vault", str(vault),
        )
        assert code == 1

    def test_missing_query_exits_two(self, stack_cli, vault):
        code, _, _ = stack_cli("memory", "search", "--vault", str(vault))
        assert code == 2


# ─── Filters ─────────────────────────────────────────────────────────────

class TestFilters:
    """Frontmatter-aware narrowing on top of the body match."""

    def test_person_filter_excludes_non_matching(self, stack_cli, vault):
        # "Hoover" appears in the Elternabend note. Its persons are
        # Marge + Lisa — filtering by --person Bart must yield zero.
        code, _, _ = stack_cli(
            "memory", "search", "Hoover", "--person", "Bart",
            "--vault", str(vault),
        )
        assert code == 1

    def test_person_filter_keeps_matching(self, stack_cli, vault):
        code, out, _ = stack_cli(
            "memory", "search", "Hoover", "--person", "Lisa",
            "--vault", str(vault),
        )
        assert code == 0
        assert "elternabend" in out.lower()


# ─── Output modes ────────────────────────────────────────────────────────

class TestOutput:
    """The three output shapes: default (block), --paths, --count."""

    def test_paths_mode_prints_only_paths(self, stack_cli, vault):
        code, out, _ = stack_cli(
            "memory", "search", "Brummen", "--paths", "--vault", str(vault),
        )
        assert code == 0
        lines = [ln for ln in out.strip().splitlines() if ln]
        assert len(lines) == 1
        assert lines[0].endswith(".md")

    def test_count_mode_prints_integer(self, stack_cli, vault):
        code, out, _ = stack_cli(
            "memory", "search", "Hoover", "--count", "--vault", str(vault),
        )
        assert code == 0
        assert out.strip() == "1"

    def test_limit_truncates_results(self, stack_cli, vault):
        # All three docs contain a "title:" frontmatter key, so a body
        # query for "title" matches every file. --limit 2 must keep only
        # two of them.
        code, out, _ = stack_cli(
            "memory", "search", "title", "--limit", "2",
            "--paths", "--vault", str(vault),
        )
        assert code == 0
        lines = [ln for ln in out.strip().splitlines() if ln]
        assert len(lines) <= 2

    def test_excerpt_skips_frontmatter(self, stack_cli, vault):
        # "Bart" appears in the Bart doc's frontmatter (`persons: - Bart`,
        # `title: ... Bart ...`) AND in the body ("Tierarzt-style routine
        # vaccination..." — no Bart there actually). The excerpt must
        # surface a *body* line that mentions Bart, never a frontmatter
        # one, otherwise agents get noisy hits like "title: ...".
        code, out, _ = stack_cli(
            "memory", "search", "Bart", "--vault", str(vault),
        )
        assert code == 0
        # The excerpt line is the third line of the block, prefixed with
        # "  …". It must not contain the literal "title:" string from the
        # frontmatter.
        assert "title:" not in out
        assert "persons:" not in out


# ─── Tag filter ──────────────────────────────────────────────────────────

class TestTagFilter:
    """Same axis-shape as --person, but against the frontmatter `tags:` list.

    The list mixes Obsidian-style `Topic:Education` slugs and quoted
    `'Person: Marge'` (with a space) — the filter must normalize
    whitespace so users don't have to know which spelling the writer
    chose.
    """

    def test_tag_filter_keeps_matching(self, stack_cli, vault):
        code, out, _ = stack_cli(
            "memory", "search", "Hoover",
            "--tag", "Topic:Education",
            "--vault", str(vault),
        )
        assert code == 0
        assert "elternabend" in out.lower()

    def test_tag_filter_excludes_non_matching(self, stack_cli, vault):
        # Filtering Hoover-matching docs (Elternabend) by a tag that
        # only Bart's doc has (Topic:Health) must drop everything.
        code, _, _ = stack_cli(
            "memory", "search", "Hoover",
            "--tag", "Topic:Health",
            "--vault", str(vault),
        )
        assert code == 1

    def test_tag_filter_normalizes_whitespace(self, stack_cli, vault):
        # The Elternabend doc tags 'Person: Marge' (with space). The
        # filter accepts the same value without the space.
        code, out, _ = stack_cli(
            "memory", "search", "Hoover",
            "--tag", "Person:Marge",
            "--vault", str(vault),
        )
        assert code == 0
        assert "elternabend" in out.lower()


# ─── Ordering ────────────────────────────────────────────────────────────

class TestOrdering:
    """Newest first by frontmatter `date`; files without dates fall to the back."""

    def test_results_ordered_by_date_desc(self, stack_cli, vault):
        # All four fixture docs contain the literal "title:" (frontmatter
        # key). Newest is the MMR doc (2026-03-08). Listing paths gives
        # us a clean order to assert on.
        code, out, _ = stack_cli(
            "memory", "search", "title",
            "--paths", "--vault", str(vault),
        )
        assert code == 0
        lines = [ln for ln in out.strip().splitlines() if ln]
        assert lines[0].endswith("2026-03-08-mmr-impfung.md")
        # 2026-03-05 (quick-bread) > 2026-02-12 (elternabend) >
        # 2026-02-10 (radlager). Pull the YYYY-MM-DD prefix off each
        # filename to assert the order is monotonically descending.
        dates = [ln.split("/")[-1][:10] for ln in lines]
        assert dates == sorted(dates, reverse=True)
