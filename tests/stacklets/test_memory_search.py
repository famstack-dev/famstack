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

import sys
import textwrap
from pathlib import Path

import pytest

# In-process import for the lib-level test class. The CLI tests below
# exercise the same engine via subprocess; these pin the API shape the
# archivist bot consumes directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent
                       / "stacklets" / "memory"))
from lib import search_memory  # noqa: E402


# Probe that matches every fixture doc via body content (one keyword
# per doc, OR'd together). Body-only search means we can no longer rely
# on a frontmatter key like "title" as a universal hit, so the limit /
# ordering tests use this instead.
_BODY_PROBE_ALL = "Tierarzt|Brummen|Hoover|Backzeit"


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


class TestBodyOnly:
    """The regex matches only the body of each file, not its YAML
    frontmatter.

    Why this matters: a question-mode rewrite that extracts the
    keyword `date` from "when was the last invoice?" would otherwise
    match every memory file via its `date:` frontmatter line. Same
    failure mode for `tags`, `persons`, `correspondent`, `title`,
    etc. Body-only matching means those structural terms only hit
    files that *also* mention the term in prose.
    """

    def test_frontmatter_field_name_does_not_match(self, vault):
        # No fixture body contains the literal word "date". With
        # frontmatter pollution this query used to return every doc
        # (each has `date: YYYY-MM-DD`). After the fix: empty.
        assert search_memory("date", vault) == []

    def test_tags_field_name_does_not_match(self, vault):
        # Same shape for the `tags:` key + bullet list under it.
        assert search_memory("tags", vault) == []

    def test_title_field_name_does_not_match(self, vault):
        # `title:` is in every fixture's frontmatter but in no body.
        # Old behaviour: 4 hits. New behaviour: 0.
        assert search_memory("title", vault) == []

    def test_body_word_still_matches(self, vault):
        # The radlager doc has "Brummen" in the body; the match is
        # unaffected by the frontmatter strip.
        results = search_memory("Brummen", vault)
        assert len(results) == 1
        assert results[0]["rel"].endswith("radlager.md")

    def test_title_word_in_body_still_matches(self, vault):
        # "Radlager" appears in the *body* `# Title` line *and* in
        # the frontmatter `title:`. Even after the strip, the body
        # heading carries it -- the match still lands.
        results = search_memory("Radlager", vault)
        assert len(results) == 1


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
        # Probe matches every fixture doc via body keywords. --limit 2
        # must keep only two of them.
        code, out, _ = stack_cli(
            "memory", "search", _BODY_PROBE_ALL, "--limit", "2",
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
        # The probe matches every doc via body keywords. Newest is the
        # MMR doc (2026-03-08). Listing paths gives us a clean order
        # to assert on.
        code, out, _ = stack_cli(
            "memory", "search", _BODY_PROBE_ALL,
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


# ─── In-process engine (archivist call path) ─────────────────────────────

class TestSearchMemoryLib:
    """The `search_memory` lib function — what the archivist bot calls.

    The CLI tests above exercise the same engine via subprocess. This
    class pins the in-process API shape directly: argument names, the
    fields each result dict carries, and the empty-list return on
    failure modes (vs the CLI's exit-3 + stderr message).
    """

    def test_returns_result_dicts_with_expected_keys(self, vault):
        results = search_memory("Brummen", vault)
        assert len(results) == 1
        r = results[0]
        assert set(r.keys()) == {
            "path", "rel", "title", "date",
            "persons", "tags", "excerpt", "summary",
            "paperless_id", "capture_id",
        }
        assert r["rel"].endswith("radlager.md")
        assert r["persons"] == ["Homer"]
        assert "Brummen" in r["excerpt"]
        # Fixture docs predate the classifier callout, so no summary
        # is present. The key exists (downstream code can rely on it)
        # but it's empty.
        assert r["summary"] == ""

    def test_returns_empty_list_when_no_match(self, vault):
        assert search_memory("spaceship", vault) == []

    def test_summary_extracted_from_callout(self, tmp_path):
        # A more representative doc that includes the archivist's
        # `> [!summary]` callout block, matching what classify
        # actually writes. The synthesis step depends on this
        # extraction returning the prose + facts, stripped of the
        # blockquote prefix.
        v = tmp_path / "vault"
        _write(v / "family/documents/2026/02/invoice.md", """
            ---
            title: Anthropic Invoice
            date: 2026-02-22
            persons:
              - Homer
            tags:
              - Topic:Subscription
            ---

            # Anthropic Invoice

            > [!summary]
            > Invoice from Anthropic for the Max plan subscription.
            > Total €90.00 due 2026-02-22.
            >
            > **Facts**
            > - Invoice number: MDIIDNBM-0006
            > - Total: €90.00

            Body paragraph that must not leak into the summary.
        """)
        results = search_memory("Anthropic", v)
        assert len(results) == 1
        s = results[0]["summary"]
        assert "Max plan subscription" in s
        assert "Total €90.00 due 2026-02-22" in s
        assert "MDIIDNBM-0006" in s
        # The blockquote prefix is stripped.
        assert "> " not in s
        # Body content outside the callout is not part of the summary.
        assert "Body paragraph" not in s

    def test_returns_empty_list_when_vault_missing(self, tmp_path):
        assert search_memory("anything", tmp_path / "nope") == []

    def test_returns_empty_list_on_invalid_regex(self, vault):
        # Unclosed character class — re.compile raises, search swallows.
        assert search_memory("[unclosed", vault) == []

    def test_person_filter_is_kwarg_named_persons(self, vault):
        # The archivist passes positional args today; this test guards
        # the keyword shape so future kwargs callers don't drift.
        results = search_memory("Hoover", vault, persons=["Lisa"])
        assert len(results) == 1
        assert results[0]["rel"].endswith("elternabend.md")

    def test_tag_filter_normalizes_whitespace(self, vault):
        # 'Person: Marge' in the file, 'Person:Marge' in the filter.
        results = search_memory("Hoover", vault, tags=["Person:Marge"])
        assert len(results) == 1
        assert results[0]["rel"].endswith("elternabend.md")

    def test_results_sorted_by_date_desc(self, vault):
        # The probe matches every fixture doc via body keywords --
        # same trick the CLI ordering test uses, applied to the
        # in-process surface.
        results = search_memory(_BODY_PROBE_ALL, vault)
        dates = [r["date"] for r in results]
        assert dates == sorted(dates, reverse=True)

    def test_limit_truncates(self, vault):
        results = search_memory(_BODY_PROBE_ALL, vault, limit=2)
        assert len(results) == 2


class TestSearchMemoryScopes:
    """Scoping by allowed path prefix.

    The vault is entity-rooted: shared docs under `family/`, each
    person's private notes under their own slug. Scopes are how the
    archivist enforces who-sees-what. The fixture vault has docs in
    `family/`, `homer/`, and `marge/` -- enough to assert that the
    filter keeps the right files and drops the rest.
    """

    def test_none_scope_keeps_historic_open_behavior(self, vault):
        # No scopes argument == "search every entity tree", matching
        # behaviour before the parameter existed.
        results = search_memory(_BODY_PROBE_ALL, vault)
        rels = [r["rel"] for r in results]
        assert any(r.startswith("family/") for r in rels)
        assert any(r.startswith("homer/") for r in rels)
        assert any(r.startswith("marge/") for r in rels)

    def test_empty_scope_denies_everything(self, vault):
        # Empty list != None: the caller explicitly said "no prefixes
        # allowed". This is the safety-net branch for an unmapped
        # sender on a stack with no shared bucket configured.
        assert search_memory(_BODY_PROBE_ALL, vault, scopes=[]) == []

    def test_family_only_scope(self, vault):
        # The MMR doc (family/...) is the only shared file in the
        # fixture; Homer/Marge notes must drop out.
        results = search_memory(_BODY_PROBE_ALL, vault, scopes=["family/"])
        rels = [r["rel"] for r in results]
        assert all(r.startswith("family/") for r in rels)
        assert len(rels) == 1

    def test_marge_asking_sees_family_plus_marge(self, vault):
        # Marge's scope is the archivist's default for a sender
        # whose Matrix localpart is "marge". Homer's notes must
        # not leak; family/ and marge/ both visible.
        results = search_memory(
            _BODY_PROBE_ALL, vault, scopes=["family/", "marge/"],
        )
        rels = [r["rel"] for r in results]
        assert all(
            r.startswith("family/") or r.startswith("marge/")
            for r in rels
        )
        assert not any(r.startswith("homer/") for r in rels)

    def test_trailing_slash_optional(self, vault):
        # The contract: callers don't need to remember the trailing
        # slash. Equally important: a prefix without a slash must
        # not match "margery/..." if such a sibling slug existed.
        with_slash = search_memory(_BODY_PROBE_ALL, vault, scopes=["marge/"])
        without = search_memory(_BODY_PROBE_ALL, vault, scopes=["marge"])
        assert [r["rel"] for r in with_slash] == [r["rel"] for r in without]

    def test_prefix_does_not_match_longer_sibling(self, tmp_path):
        # The bug we're guarding against: "marge" must not match
        # "margery/...". Build a tiny vault with a deliberate
        # margery-like sibling to pin the boundary.
        v = tmp_path / "vault"
        _write(v / "marge/notes/a.md", """
            ---
            title: real-marge
            ---
            shared body keyword zzz
        """)
        _write(v / "margery/notes/b.md", """
            ---
            title: not-marge
            ---
            shared body keyword zzz
        """)
        results = search_memory("zzz", v, scopes=["marge"])
        rels = [r["rel"] for r in results]
        assert any("marge/notes/a.md" in r for r in rels)
        assert not any("margery" in r for r in rels)


class TestScopeCli:
    """The `--scope` flag mirrors the lib's `scopes` argument."""

    def test_scope_flag_narrows_results(self, stack_cli, vault):
        # The probe matches every doc via body keywords. Scoping to
        # `family/` keeps only the MMR doc.
        code, out, _ = stack_cli(
            "memory", "search", _BODY_PROBE_ALL,
            "--scope", "family/",
            "--paths", "--vault", str(vault),
        )
        assert code == 0
        lines = [ln for ln in out.strip().splitlines() if ln]
        assert all(ln.startswith("family/") for ln in lines)
        assert len(lines) == 1

    def test_scope_flag_repeats_or_within_axis(self, stack_cli, vault):
        # Marge's two docs (notes + bookmarks) + the family MMR doc
        # = three hits; Homer's radlager must not appear.
        code, out, _ = stack_cli(
            "memory", "search", _BODY_PROBE_ALL,
            "--scope", "family/", "--scope", "marge/",
            "--paths", "--vault", str(vault),
        )
        assert code == 0
        lines = [ln for ln in out.strip().splitlines() if ln]
        assert not any(ln.startswith("homer/") for ln in lines)
        assert len(lines) == 3
