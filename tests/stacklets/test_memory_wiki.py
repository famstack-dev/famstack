"""`stack memory wiki` — topic discovery, slicing, cross-references.

The wiki command rebuilds derived pages from the vault. This file
pins the topic-folder extension: discovering topic folders by the
bucket-shape signal, slicing the index, finding cross-references in
other buckets via `topics:` or `tags:` frontmatter, and building the
first-creation frontmatter for each topic's `about.md`.

The LLM-call paths (`_generate_topic`, `_build_topic_prompt`) stay
covered by the e2e rig — these tests pin the pure data shaping that
the deriver and future wiki rebuilds will keep working over.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

from wiki import (  # noqa: E402
    _build_topic_prompt,
    _collect_todo_items,
    _capture_index_pages,
    _clean_generated,
    _correspondent_body,
    _correspondent_entries,
    _correspondent_preamble,
    _correspondent_roster,
    _entry_kind,
    _format_topic_evidence,
    _frontmatter_error,
    _generated_pages_on_disk,
    _index_vault,
    _is_affirmative,
    _is_generated_page,
    _member_preamble,
    _member_slugs,
    _month_label,
    _open_todos,
    _publish,
    _render_capture_index,
    _topic_cross_refs,
    _topic_entries,
    _topic_locations,
    _topic_preamble,
    _yaml_str,
)


def _frontmatter(preamble: str) -> dict:
    """Parse a `---`-fenced preamble the way Quartz's YAML parser does.

    The wiki publishes these blocks verbatim; a value YAML mis-reads
    (a bare colon, a leading `&`) hard-fails the whole site build. These
    tests assert the block round-trips through a real YAML parser.
    """
    body = preamble.strip()
    assert body.startswith("---") and body.endswith("---")
    return yaml.safe_load(body.strip("-\n")) or {}


# ── Fixture helpers ─────────────────────────────────────────────────────


def _make_topic_folder(vault: Path, bucket: str, slug: str,
                      *, kind: str = "notes",
                      file_name: str = "a.md",
                      body: str = "# Capture\n\n> [!summary]\n> A capture") -> None:
    """Create a topic-shaped folder: `<bucket>/<slug>/<kind>/...md`.

    The discovery heuristic keys on the presence of one of the
    capture-type subdirectories (`notes`, `bookmarks`, `documents`).
    A folder with none of those is not treated as a topic.
    """
    target = vault / bucket / slug / kind
    target.mkdir(parents=True, exist_ok=True)
    (target / file_name).write_text(body, encoding="utf-8")


def _make_bare_folder(vault: Path, *parts: str) -> None:
    (vault.joinpath(*parts)).mkdir(parents=True, exist_ok=True)


# ── _topic_locations ───────────────────────────────────────────────────


class TestTopicLocations:
    """`_topic_locations` walks the vault and returns
    (bucket_prefix, topic_slug) tuples for every directory that follows
    the topic shape: nested inside a known bucket, containing at least
    one of `notes/` / `bookmarks/` / `documents/`. Reserved subdir
    names are skipped (they ARE the capture-type folders themselves)."""

    def test_shared_topic_discovered(self, tmp_path):
        _make_topic_folder(tmp_path, "family", "camping")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["homer", "marge"],
        )
        assert ("family", "camping") in locs

    def test_personal_topic_discovered(self, tmp_path):
        _make_topic_folder(tmp_path, "homer", "gravel")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["homer", "marge"],
        )
        assert ("homer", "gravel") in locs

    def test_personal_topic_skipped_for_unknown_member(self, tmp_path):
        """A folder at `bart/whatever/notes/...` doesn't get discovered
        as a topic if `bart` isn't in the member list. The wiki only
        generates pages for known members."""
        _make_topic_folder(tmp_path, "bart", "skateboarding")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["homer"],
        )
        assert ("bart", "skateboarding") not in locs

    def test_reserved_subdir_is_not_a_topic(self, tmp_path):
        """`family/documents/` is the institutional bucket for filed
        Paperless mirrors, not a topic. The discovery must skip it
        even though it contains markdown files."""
        _make_bare_folder(tmp_path, "family", "documents", "2026", "06")
        (tmp_path / "family" / "documents" / "2026" / "06" / "doc.md").write_text("...")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=[],
        )
        assert ("family", "documents") not in locs

    def test_correspondents_reserved(self, tmp_path):
        _make_bare_folder(tmp_path, "family", "correspondents")
        (tmp_path / "family" / "correspondents" / "duff-insurance.md").write_text("...")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=[],
        )
        assert ("family", "correspondents") not in locs

    def test_unfiled_reserved(self, tmp_path):
        _make_bare_folder(tmp_path, "homer", "_unfiled")
        (tmp_path / "homer" / "_unfiled" / "x.md").write_text("...")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["homer"],
        )
        assert ("homer", "_unfiled") not in locs

    def test_empty_folder_is_not_a_topic(self, tmp_path):
        """A folder under a bucket with no `notes/`, `bookmarks/`, or
        `documents/` subdir is not a topic. The bucket-shape signal
        is what tells us this folder is meant to hold captures."""
        _make_bare_folder(tmp_path, "family", "camping")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=[],
        )
        assert ("family", "camping") not in locs

    def test_topic_with_only_bookmarks_discovered(self, tmp_path):
        """Any one of the capture-type subdirs is enough — a bookmark-
        only topic (URL captures, no notes) still counts."""
        _make_topic_folder(tmp_path, "family", "research", kind="bookmarks")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=[],
        )
        assert ("family", "research") in locs

    def test_results_are_sorted(self, tmp_path):
        """Sorted output keeps the discovery + render order stable,
        which makes test assertions and human review easier."""
        _make_topic_folder(tmp_path, "family", "photography")
        _make_topic_folder(tmp_path, "family", "camping")
        _make_topic_folder(tmp_path, "homer", "gravel")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["homer"],
        )
        assert locs == sorted(locs)

    def test_multiple_topics_per_bucket(self, tmp_path):
        _make_topic_folder(tmp_path, "family", "camping")
        _make_topic_folder(tmp_path, "family", "photography")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=[],
        )
        assert ("family", "camping") in locs
        assert ("family", "photography") in locs

    def test_deskstack_shared_bucket(self, tmp_path):
        """A deskstack household's shared bucket is `office`. The
        discovery reads the configured slug, not a hard-coded `family`."""
        _make_topic_folder(tmp_path, "office", "client-x")
        locs = _topic_locations(
            tmp_path, shared_bucket="office", member_slugs=["paul"],
        )
        assert ("office", "client-x") in locs


# ── _topic_entries ─────────────────────────────────────────────────────


class TestTopicEntries:
    """Slice the existing vault index to files whose path lives under
    the topic's folder. Reuses the index `_index_vault` already builds
    for the home + member loops; no separate walk."""

    def test_slice_by_bucket_and_slug(self):
        index = [
            {"rel": "family/camping/notes/2026/06/a.md", "title": "A"},
            {"rel": "family/camping/bookmarks/2026/06/b.md", "title": "B"},
            {"rel": "family/documents/2026/06/c.md", "title": "C"},
            {"rel": "family/photography/notes/2026/06/d.md", "title": "D"},
        ]
        entries = _topic_entries(index, "family", "camping")
        rels = [e["rel"] for e in entries]
        assert "family/camping/notes/2026/06/a.md" in rels
        assert "family/camping/bookmarks/2026/06/b.md" in rels
        assert "family/documents/2026/06/c.md" not in rels
        assert "family/photography/notes/2026/06/d.md" not in rels

    def test_personal_topic_slice(self):
        """A personal topic (`homer/gravel/`) is isolated from homer's
        plain personal notes (`homer/notes/`)."""
        index = [
            {"rel": "homer/gravel/notes/2026/06/a.md", "title": "Gravel A"},
            {"rel": "homer/notes/2026/06/b.md", "title": "Personal B"},
            {"rel": "homer/bookmarks/2026/06/c.md", "title": "Personal C"},
        ]
        entries = _topic_entries(index, "homer", "gravel")
        rels = [e["rel"] for e in entries]
        assert rels == ["homer/gravel/notes/2026/06/a.md"]

    def test_slug_prefix_collision(self):
        """A topic `camp` must not pull in `camping` content (the slash
        boundary on the prefix prevents this)."""
        index = [
            {"rel": "family/camp/notes/a.md", "title": "Camp"},
            {"rel": "family/camping/notes/b.md", "title": "Camping"},
        ]
        entries = _topic_entries(index, "family", "camp")
        rels = [e["rel"] for e in entries]
        assert "family/camp/notes/a.md" in rels
        assert "family/camping/notes/b.md" not in rels

    def test_empty_index(self):
        assert _topic_entries([], "family", "camping") == []


# ── _topic_cross_refs ──────────────────────────────────────────────────


class TestTopicCrossRefs:
    """The deriver-style grep over the index: files OUTSIDE the topic
    bucket whose frontmatter `topics:` or `tags:` mentions the topic
    slug. Captures use `tags:` (the seed merges into the classifier's
    tags); document mirrors use `topics:` (the ontology field). The
    cross-ref check unions both so it works for either shape."""

    def test_cross_ref_via_tags(self):
        index = [
            # In-bucket — skipped, even though it carries the tag
            {"rel": "family/camping/notes/a.md",
             "tags": ["camping", "gear"], "topics": []},
            # Outside, has camping in tags — pulled in
            {"rel": "family/documents/2026/06/duff-insurance-camping.md",
             "tags": ["camping", "Versicherung"], "topics": []},
            # Outside, no camping reference — excluded
            {"rel": "family/documents/2026/06/other.md",
             "tags": ["other"], "topics": []},
        ]
        refs = _topic_cross_refs(index, "family", "camping")
        rels = [e["rel"] for e in refs]
        assert "family/documents/2026/06/duff-insurance-camping.md" in rels
        assert "family/documents/2026/06/other.md" not in rels
        assert "family/camping/notes/a.md" not in rels

    def test_cross_ref_via_topics(self):
        """Document mirrors carry `topics:` (the ontology classification);
        captures don't. Both surfaces should be discoverable."""
        index = [
            {"rel": "family/documents/2026/04/policy.md",
             "topics": ["camping", "insurance"], "tags": ["Versicherung"]},
        ]
        refs = _topic_cross_refs(index, "family", "camping")
        assert len(refs) == 1
        assert refs[0]["rel"] == "family/documents/2026/04/policy.md"

    def test_cross_ref_across_buckets(self):
        """Marge's personal note tagged with `camping` shows up under
        the family/camping/ topic's cross-references. The grep
        respects the entire vault except the topic's own folder."""
        index = [
            {"rel": "marge/notes/2026/05/gear.md",
             "tags": ["camping", "warmth"], "topics": []},
            {"rel": "homer/notes/2026/05/idea.md",
             "tags": ["camping", "route"], "topics": []},
        ]
        refs = _topic_cross_refs(index, "family", "camping")
        rels = {e["rel"] for e in refs}
        assert rels == {
            "marge/notes/2026/05/gear.md",
            "homer/notes/2026/05/idea.md",
        }

    def test_personal_topic_cross_refs(self):
        """A personal topic's cross-refs are everything OUTSIDE
        `homer/gravel/`. The wider scope is intentional — the deriver's
        view of 'all gravel-relevant captures in the household' still
        includes shared captures."""
        index = [
            {"rel": "homer/gravel/notes/a.md",
             "tags": ["gravel"], "topics": []},
            {"rel": "family/documents/bike-insurance.md",
             "tags": ["gravel"], "topics": []},
        ]
        refs = _topic_cross_refs(index, "homer", "gravel")
        rels = [e["rel"] for e in refs]
        assert "family/documents/bike-insurance.md" in rels
        assert "homer/gravel/notes/a.md" not in rels

    def test_missing_topics_and_tags_fields(self):
        """Index entries with neither `topics` nor `tags` (a hand-edited
        file, or an old file pre-dating the seed) don't cause a crash
        and don't match."""
        index = [{"rel": "family/documents/2025/01/old.md"}]
        refs = _topic_cross_refs(index, "family", "camping")
        assert refs == []


# ── _member_preamble ──────────────────────────────────────────────────


class TestMemberPreamble:
    """First-creation frontmatter for a member `about.md`."""

    def test_carries_okf_type_and_canonical(self):
        pre = _member_preamble("maggie", "Maggie", ["Maggie", "Margaret"])
        fm = _frontmatter(pre)
        assert fm["type"] == "person"  # OKF concept kind
        assert fm["title"] == "Margaret"  # longest synonym is canonical
        assert fm["canonical"] == "Margaret"
        assert fm["slug"] == "maggie"

    def test_no_synonyms_collapses_to_display(self):
        pre = _member_preamble("homer", "Homer", [])
        fm = _frontmatter(pre)
        assert fm["type"] == "person"
        assert fm["title"] == "Homer"
        assert "synonyms" not in fm


# ── Correspondents ────────────────────────────────────────────────────


def _doc(correspondent, *, title="A Doc", date="2026-03-15", rel=None):
    """A minimal index entry for a document referencing a correspondent."""
    return {
        "title": title,
        "date": date,
        "rel": rel or f"family/documents/2026/03/{title.lower().replace(' ', '-')}.md",
        "correspondent": correspondent,
        "persons": [],
    }


class TestCorrespondentRoster:

    def test_dedups_and_sorts_by_canonical(self):
        index = [
            _doc("Duff Insurance"), _doc("Springfield Mutual"),
            _doc("Duff Insurance"), _doc(""),
        ]
        assert _correspondent_roster(index) == [
            ("duff-insurance", "Duff Insurance"),
            ("springfield-mutual", "Springfield Mutual"),
        ]

    def test_blank_correspondents_excluded(self):
        index = [_doc(""), _doc("   "), _doc(None)]
        assert _correspondent_roster(index) == []

    def test_slug_normalises_name(self):
        roster = _correspondent_roster([_doc("Springfield Tax Office")])
        assert roster == [("springfield-tax-office", "Springfield Tax Office")]


class TestCorrespondentEntries:

    def test_filters_by_canonical_newest_first(self):
        index = [
            _doc("Duff Insurance", title="Old", date="2024-01-01"),
            _doc("Springfield Mutual", title="Other", date="2026-01-01"),
            _doc("Duff Insurance", title="New", date="2026-05-01"),
        ]
        entries = _correspondent_entries(index, "Duff Insurance")
        assert [e["title"] for e in entries] == ["New", "Old"]


class TestCorrespondentPreamble:

    def test_carries_okf_type_and_canonical(self):
        pre = _correspondent_preamble("duff-insurance", "Duff Insurance")
        fm = _frontmatter(pre)
        assert fm["type"] == "correspondent"  # OKF concept kind
        assert fm["title"] == "Duff Insurance"
        assert fm["canonical"] == "Duff Insurance"
        assert fm["slug"] == "duff-insurance"

    def test_canonical_with_colon_stays_valid_yaml(self):
        # A correspondent like "Müller: Steuerberatung" carries a colon;
        # unquoted it would break the whole Quartz build (the prod bug).
        pre = _correspondent_preamble("mueller", "Müller: Steuerberatung")
        fm = _frontmatter(pre)
        assert fm["title"] == "Müller: Steuerberatung"
        assert fm["canonical"] == "Müller: Steuerberatung"


class TestCorrespondentBody:

    def test_lists_documents_as_relative_links(self):
        entries = _correspondent_entries(
            [_doc("Duff Insurance", title="Auto Policy", date="2026-03-15",
                  rel="family/documents/2026/03/auto-policy-p247.md")],
            "Duff Insurance",
        )
        body = _correspondent_body(entries, page_dir="family/correspondents")
        assert body.startswith("## Documents")
        # Links are full paths from the vault root (Quartz "absolute"), so the
        # same form resolves no matter how deep the page lives.
        assert "[Auto Policy](/family/documents/2026/03/auto-policy-p247.md)" in body
        assert "2026-03-15" in body


# ── _topic_preamble ───────────────────────────────────────────────────


class TestTopicPreamble:
    """First-creation frontmatter for `about.md` when the page does not
    exist yet. The wiki command's splice writer lays this on top of
    the regenerate region before the LLM-generated body."""

    def test_shared_topic_carries_scope_and_slug(self):
        pre = _topic_preamble("camping", "Camping", "shared")
        fm = _frontmatter(pre)
        assert fm["title"] == "Camping"
        assert fm["slug"] == "camping"
        assert fm["scope"] == "shared"
        assert fm["type"] == "topic"

    def test_personal_topic_scope_recorded(self):
        pre = _topic_preamble("gravel", "Gravel", "personal")
        assert _frontmatter(pre)["scope"] == "personal"

    def test_display_with_special_chars(self):
        """A topic named `Van Life` keeps the casing + spaces in the
        display title, even though the slug is hyphenated."""
        pre = _topic_preamble("van-life", "Van Life", "shared")
        fm = _frontmatter(pre)
        assert fm["title"] == "Van Life"
        assert fm["slug"] == "van-life"

    def test_display_with_colon_stays_valid_yaml(self):
        # A topic display carrying a colon ("Itchy: The Park") must quote,
        # or the YAML parser reads a nested mapping and fails the build.
        pre = _topic_preamble("itchy", "Itchy: The Park", "shared")
        assert _frontmatter(pre)["title"] == "Itchy: The Park"


# ── _yaml_str ──────────────────────────────────────────────────────────


class TestYamlStr:
    """Every human string in frontmatter routes through `_yaml_str`. A
    bare colon here is what took the live wiki build down."""

    def test_index_title_with_colon_round_trips(self):
        # The exact prod failure: a folder-index title `Notes: Admin`
        # emitted unquoted as `title: Notes: Admin` is invalid YAML.
        title = _yaml_str("Notes: Admin")
        assert yaml.safe_load(f"title: {title}") == {"title": "Notes: Admin"}

    def test_quotes_and_backslashes_escaped(self):
        value = 'He said "hi" \\ bye'
        assert yaml.safe_load(f"x: {_yaml_str(value)}") == {"x": value}

    def test_yaml_special_leads_round_trip(self):
        # Leading `&`, `*`, `#`, `-` all change meaning in a bare scalar.
        for value in ("&anchor", "*alias", "# not a comment", "- dash"):
            assert yaml.safe_load(f"x: {_yaml_str(value)}") == {"x": value}


# ── _frontmatter_error (write-boundary gate) ───────────────────────────


class TestFrontmatterError:
    """The publish gate parses a page's frontmatter the strict way Quartz
    does, so a page that would crash the build never reaches Forgejo."""

    def test_valid_page_passes(self):
        page = '---\ntitle: "Notes: Admin"\ntype: note\n---\n\n# body\n'
        assert _frontmatter_error(page) is None

    def test_unquoted_colon_title_is_rejected(self):
        # The exact prod page that took the wiki down.
        page = "---\ntitle: Notes: Admin\n---\n\n# body\n"
        assert _frontmatter_error(page) is not None

    def test_no_frontmatter_is_not_an_error(self):
        # A bodyless page or one without a block is valid, not malformed.
        assert _frontmatter_error("# just a heading\n") is None

    def test_unterminated_block_is_rejected(self):
        assert _frontmatter_error("---\ntitle: x\nno closing fence\n") is not None


# ── _is_generated_page (clean's delete filter) ─────────────────────────


class TestIsGeneratedPage:
    """`clean` deletes a page only if this returns True. It must never
    match a source capture, or clean would eat real content."""

    def test_generated_page_matches(self):
        page = '---\ntitle: "Camping"\n---\n<!-- begin: generated -->\nbody\n<!-- end: generated -->\n'
        assert _is_generated_page(page) is True

    def test_note_capture_does_not_match(self):
        # A note capture carries frontmatter + body, no splice marker.
        note = "---\ntype: note\ntopics: [camping]\n---\n\n# Tent idea\n\nbuy a bigger tent\n"
        assert _is_generated_page(note) is False

    def test_email_capture_with_mid_marker_does_not_match(self):
        # Email threads carry `mid:` markers, which must not be confused
        # with the generated-region marker.
        email = "---\ntype: email\n---\n<!-- mid:<abc@host> -->\n## 2026-06-01 - Bart\n\nhi\n"
        assert _is_generated_page(email) is False


class TestIsAffirmative:
    """The clean confirmation defaults to no -- only an explicit yes deletes."""

    def test_yes_variants(self):
        for r in ("y", "Y", "yes", "YES", " yes "):
            assert _is_affirmative(r) is True

    def test_everything_else_is_no(self):
        for r in ("", "n", "no", "\n", "yeah", "sure"):
            assert _is_affirmative(r) is False


# ── Anchored regen helpers ──────────────────────────────────────────────

from wiki import _previous_generated, _renumber_citations  # noqa: E402


class TestPreviousGenerated:
    def _page(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "about.md"
        p.write_text(
            "---\ntitle: Homer\n---\n\n"
            "<!-- begin: generated -->\n\n"
            f"{body}\n\n"
            "<!-- end: generated -->\n",
            encoding="utf-8",
        )
        return p

    def test_missing_page_is_empty(self, tmp_path: Path) -> None:
        assert _previous_generated(tmp_path / "nope.md") == ("", {})

    def test_page_without_markers_is_empty(self, tmp_path: Path) -> None:
        p = tmp_path / "about.md"
        p.write_text("# Hand-written page\n", encoding="utf-8")
        assert _previous_generated(p) == ("", {})

    def test_strips_references_and_returns_map(self, tmp_path: Path) -> None:
        p = self._page(
            tmp_path,
            "# Homer\n\nFact [1]. Other [2].\n\n"
            "## References\n\n"
            "- [1] [Birth Cert](../family/documents/bc.md) - 1956-05-15\n"
            "- [2] [Note](notes/n.md) - 2026-06-12",
        )
        body, refs = _previous_generated(p)
        assert "## References" not in body
        assert body.startswith("# Homer")
        assert refs == {"../family/documents/bc.md": 1, "notes/n.md": 2}


class TestRenumberCitations:
    def test_single_and_grouped(self) -> None:
        out = _renumber_citations("A [1]. B [1, 3]. C [2].", {1: 2, 2: 3, 3: 1})
        assert out == "A [2]. B [2, 1]. C [3]."

    def test_chained_remap_applies_once(self) -> None:
        # 1→2 while 2→3: [1] must become [2], not slide on to [3].
        assert _renumber_citations("[1] [2]", {1: 2, 2: 3}) == "[2] [3]"

    def test_unmapped_numbers_pass_through(self) -> None:
        assert _renumber_citations("Kept [7].", {1: 2}) == "Kept [7]."


# ── Topic page: typed sections (bookmarks vs notes) + living About ──────────

def _topic_entry(rel: str, title: str, *, date: str = "2026-06-10",
                 summary: str = "a summary", filed_by: str = "") -> dict:
    return {"rel": rel, "title": title, "date": date,
            "summary": summary, "filed_by": filed_by}


class TestEntryKind:
    """A capture's kind is read from the folder after the topic slug."""

    def test_bookmark_note_document(self):
        assert _entry_kind("family/camping/bookmarks/2026/06/x.md", "camping") == "bookmark"
        assert _entry_kind("family/camping/notes/2026/06/x.md", "camping") == "note"
        assert _entry_kind("family/camping/documents/2026/06/x.md", "camping") == "document"

    def test_unknown_folder_defaults_to_note(self):
        assert _entry_kind("family/camping/misc/x.md", "camping") == "note"

    def test_slug_absent_defaults_to_note(self):
        assert _entry_kind("family/other/notes/x.md", "camping") == "note"


class TestFormatTopicEvidence:
    """Evidence is grouped by kind, but [N] tracks list order so the
    deterministic References mapping ([N] -> entries[N-1]) stays aligned."""

    def test_grouped_by_kind_with_list_order_citations(self):
        # note first in the list, bookmark second -> bookmark keeps [2]
        entries = [
            _topic_entry("family/camping/notes/2026/06/n.md", "Checkliste"),
            _topic_entry("family/camping/bookmarks/2026/06/b.md", "Fenstertasche"),
        ]
        ev = _format_topic_evidence(entries, "camping")
        assert "Bookmarks:" in ev and "Notes:" in ev
        assert ev.index("Bookmarks:") < ev.index("Notes:")  # section order by kind
        assert "[2] 2026-06-10 · Fenstertasche" in ev        # bookmark kept its index
        assert "[1] 2026-06-10 · Checkliste" in ev

    def test_filer_surfaced_in_evidence(self):
        entries = [_topic_entry("family/camping/bookmarks/2026/06/b.md",
                                "Fenstertasche", filed_by="marge")]
        ev = _format_topic_evidence(entries, "camping")
        assert "filed by marge" in ev


class TestBuildTopicPrompt:
    """The page separates bookmarks from notes and frames About as a
    living, recency-weighted overview rather than a flat activity feed."""

    BOOKMARK = _topic_entry("family/camping/bookmarks/2026/06/b.md", "Fenstertasche")
    NOTE = _topic_entry("family/camping/notes/2026/06/n.md", "Checkliste")

    def test_typed_sections_for_present_kinds_only(self):
        prompt = _build_topic_prompt(
            "Camping", "camping", "shared", [self.BOOKMARK, self.NOTE], [], lang="de",
        )
        assert "## Bookmarks" in prompt
        assert "## Notes" in prompt
        assert "## Documents" not in prompt   # no document capture present

    def test_no_flat_recent_activity_section(self):
        prompt = _build_topic_prompt(
            "Camping", "camping", "shared", [self.BOOKMARK], [], lang="de",
        )
        assert "Recent Activity" not in prompt

    def test_about_is_recency_weighted_overview(self):
        prompt = _build_topic_prompt(
            "Camping", "camping", "shared", [self.NOTE], [], lang="de",
        )
        lower = prompt.lower()
        assert "weighting recent captures" in lower
        assert "current overview" in lower

    def test_cross_references_section_when_present(self):
        cross = [_topic_entry("family/insurance/documents/2026/06/d.md", "Police")]
        prompt = _build_topic_prompt(
            "Camping", "camping", "shared", [self.NOTE], cross, lang="de",
        )
        assert "## Cross-references" in prompt

    def test_sections_request_attribution(self):
        prompt = _build_topic_prompt(
            "Camping", "camping", "shared", [self.BOOKMARK], [], lang="de",
        )
        assert "who filed it" in prompt.lower()


class TestIndexFiledBy:
    """The vault index carries filed_by so attribution reaches the page."""

    def test_filed_by_indexed_from_frontmatter(self, tmp_path):
        d = tmp_path / "family" / "camping" / "bookmarks" / "2026" / "06"
        d.mkdir(parents=True)
        (d / "b.md").write_text(
            "---\ntype: bookmark\ntitle: Fenstertasche\nfiled_by: marge\n---\n"
            "# Fenstertasche\n\n> [!summary]\n> Eine Fenstertasche.\n",
            encoding="utf-8",
        )
        index = _index_vault(tmp_path)
        assert len(index) == 1
        assert index[0]["filed_by"] == "marge"


# ── Folder index pages (notes/ and bookmarks/ landing pages) ────────────────

class TestMonthLabel:
    def test_parses_month_year(self):
        assert _month_label("2026-06-25") == "June 2026"

    def test_undated_fallback(self):
        assert _month_label("") == "Undated"
        assert _month_label("not-a-date") == "Undated"


def _idx_entry(rel, title, date, *, filed_by="", tags=None):
    return {"rel": rel, "title": title, "date": date,
            "filed_by": filed_by, "tags": tags or []}


class TestRenderCaptureIndex:
    """The folder landing page: newest-first, grouped by month, with who
    filed each item, its tags, and absolute links."""

    def test_newest_first_month_grouped_attributed(self):
        entries = [
            _idx_entry("family/camping/notes/2026/06/a-1.md", "Older note",
                       "2026-06-10", filed_by="homer", tags=["camping"]),
            _idx_entry("family/camping/notes/2026/06/b-2.md", "Newer note",
                       "2026-06-22", filed_by="marge", tags=["packliste"]),
        ]
        out = _render_capture_index(entries, "note", "Camping")
        assert out.startswith("# Notes: Camping")
        assert out.index("Newer note") < out.index("Older note")   # newest first
        assert "## June 2026" in out
        assert "_marge_" in out
        assert "[Newer note](/family/camping/notes/2026/06/b-2.md)" in out  # absolute link

    def test_count_is_singular_for_one(self):
        out = _render_capture_index(
            [_idx_entry("x/notes/n.md", "T", "2026-06-01")], "note", "X")
        assert "1 note, newest first" in out

    def test_tags_omitted_to_stay_scannable(self):
        out = _render_capture_index(
            [_idx_entry("x/notes/n.md", "T", "2026-06-01",
                        tags=["camping", "Person: Bart"])], "note", "X")
        assert "/tags/" not in out and "camping" not in out


class TestCaptureIndexPages:
    def test_only_folders_with_entries_under_page_dir(self):
        index = [
            _idx_entry("family/camping/notes/2026/06/a-1.md", "N", "2026-06-01"),
            _idx_entry("family/camping/bookmarks/2026/06/b-2.md", "B", "2026-06-01"),
            _idx_entry("family/other/notes/x.md", "Other", "2026-06-01"),  # ignored
        ]
        pages = _capture_index_pages(index, "family/camping", "Camping")
        assert {kind for kind, _t, _c in pages} == {"bookmark", "note"}
        targets = {t for _k, t, _c in pages}
        assert "family/camping/notes/index.md" in targets
        assert "family/camping/bookmarks/index.md" in targets

    def test_absent_kind_gets_no_page(self):
        index = [_idx_entry("family/camping/notes/2026/06/a-1.md", "N", "2026-06-01")]
        pages = _capture_index_pages(index, "family/camping", "Camping")
        assert {kind for kind, _t, _c in pages} == {"note"}


class TestMemberSlugs:
    """The wiki roster is family members, not bots."""

    def test_excludes_bot_buckets(self, tmp_path):
        for name in ("homer", "marge", "mail-bot", "family"):
            (tmp_path / name).mkdir()
        slugs = _member_slugs(tmp_path, [], shared_bucket="family")
        assert "homer" in slugs and "marge" in slugs
        assert "mail-bot" not in slugs   # bot, not a member
        assert "family" not in slugs     # shared bucket, not a member

    def test_excludes_bot_named_in_persons(self, tmp_path):
        index = [{"persons": ["homer", "scribe-bot"]}]
        slugs = _member_slugs(tmp_path, index, shared_bucket="family")
        assert "homer" in slugs and "scribe-bot" not in slugs


# ── _open_todos / _collect_todo_items ────────────────────────────────────


@pytest.mark.skip(reason="moved in B2")
class TestOpenTodos:
    """Collect open `- [ ]` task lines out of the captures' summary
    callouts. The archivist already writes the classifier's action
    items as Obsidian task lines inside `> [!summary]`; `_index_vault`
    carries that callout text on each entry as `summary`. We read the
    unchecked boxes from it — no second walk, no new capture type."""

    def test_extracts_open_checkboxes(self):
        entries = [
            {"rel": "family/trip/notes/a.md", "title": "Trip plan",
             "summary": "Planning notes\n\n**Action items**\n"
                        "- [ ] book tickets — 2026-04-01\n- [ ] confirm hotel"},
        ]
        texts = [t["text"] for t in _open_todos(entries)]
        assert "book tickets — 2026-04-01" in texts
        assert "confirm hotel" in texts

    def test_excludes_done(self):
        entries = [{"rel": "x.md", "title": "X",
                    "summary": "- [x] already done\n- [X] also done\n"
                               "- [ ] still open"}]
        assert [t["text"] for t in _open_todos(entries)] == ["still open"]

    def test_preserves_source(self):
        entries = [{"rel": "family/trip/notes/a.md", "title": "Trip",
                    "summary": "- [ ] pack bags"}]
        todo = _open_todos(entries)[0]
        assert todo["rel"] == "family/trip/notes/a.md"
        assert todo["title"] == "Trip"

    def test_ignores_plain_bullets(self):
        # Facts in the same callout are plain bullets, not task lines.
        entries = [{"rel": "a.md", "title": "A",
                    "summary": "- a fact\n- another fact\n- [ ] real todo"}]
        assert [t["text"] for t in _open_todos(entries)] == ["real todo"]

    def test_entries_without_summary(self):
        assert _open_todos([{"rel": "a.md", "title": "A"}]) == []

    def test_empty_index(self):
        assert _open_todos([]) == []


@pytest.mark.skip(reason="moved in B2")
class TestCollectTodoItems:
    """Flatten a scope's open todos into the plain, deduplicated text
    list `_generate_todos` hands to `update_todo_doc`. Done boxes are
    already dropped by `_open_todos`; here we only pin the flatten and
    the dedup (an action item re-sent across two captures must not
    double up in the merged `todos.md`), with first-seen order kept."""

    def test_flattens_texts_across_entries(self):
        entries = [
            {"rel": "family/trip/notes/a.md", "title": "Plan",
             "summary": "- [ ] book tickets\n- [ ] confirm hotel"},
            {"rel": "family/trip/notes/b.md", "title": "Idea",
             "summary": "- [ ] pack bags"},
        ]
        assert _collect_todo_items(entries) == [
            "book tickets", "confirm hotel", "pack bags",
        ]

    def test_dedups_repeated_item_keeping_first_seen_order(self):
        entries = [
            {"rel": "a.md", "title": "A", "summary": "- [ ] volltanken"},
            {"rel": "b.md", "title": "B",
             "summary": "- [ ] parkkarten\n- [ ] volltanken"},
        ]
        assert _collect_todo_items(entries) == ["volltanken", "parkkarten"]

    def test_empty_when_no_open_todos(self):
        entries = [{"rel": "a.md", "title": "A", "summary": "- [x] done"}]
        assert _collect_todo_items(entries) == []

    def test_empty_index(self):
        assert _collect_todo_items([]) == []


# ── On-disk projection write (slice 4: materialize into the brain copy) ─────


class TestPublishOnDisk:
    """`_publish` writes generated pages into the brain working copy on
    disk (the curator commits + pushes). It splices against the file
    already there and refuses a page whose frontmatter won't parse. Brain
    is resolved from BRAIN_REPO_DIR."""

    def test_creates_new_page_with_preamble(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
        rc = _publish(
            "# Homer\n\nA profile.",
            target_path="homer/about.md",
            default_preamble="---\ntitle: Homer\ntype: person\n---",
        )
        assert rc == 0
        written = (tmp_path / "homer" / "about.md").read_text()
        assert written.startswith("---\ntitle: Homer")
        assert "<!-- begin: generated -->" in written
        assert "A profile." in written

    def test_missing_brain_dir_refuses_memory_fallback(self, tmp_path, monkeypatch):
        memory = tmp_path / "memory"
        memory.mkdir()
        monkeypatch.delenv("BRAIN_REPO_DIR", raising=False)
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory))

        rc = _publish(
            "body",
            target_path="homer/about.md",
            default_preamble="---\ntitle: Homer\n---",
        )

        assert rc == 1
        assert list(memory.rglob("*.md")) == []

    def test_splice_preserves_content_outside_markers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
        page = tmp_path / "homer" / "about.md"
        page.parent.mkdir(parents=True)
        page.write_text(
            "---\ntitle: Homer\n---\n\nHand-written welcome.\n\n"
            "<!-- begin: generated -->\n\nold body\n\n<!-- end: generated -->\n",
            encoding="utf-8",
        )
        _publish("new body", target_path="homer/about.md")
        written = page.read_text()
        assert "Hand-written welcome." in written   # outside-markers survives
        assert "new body" in written
        assert "old body" not in written

    def test_invalid_frontmatter_refused_and_not_written(self, tmp_path, monkeypatch):
        monkeypatch.setenv("BRAIN_REPO_DIR", str(tmp_path))
        rc = _publish(
            "body",
            target_path="x/about.md",
            # Unquoted colon — the exact Quartz-build-killer the gate blocks.
            default_preamble="---\ntitle: Notes: Admin\n---",
        )
        assert rc == 1
        assert not (tmp_path / "x" / "about.md").exists()

    def test_memory_is_never_written(self, tmp_path, monkeypatch):
        # Two trees: a brain (write target) and a memory (must stay clean).
        brain = tmp_path / "brain"
        memory = tmp_path / "memory"
        brain.mkdir()
        memory.mkdir()
        monkeypatch.setenv("BRAIN_REPO_DIR", str(brain))
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory))
        _publish("body", target_path="homer/about.md",
                 default_preamble="---\ntitle: Homer\n---")
        assert (brain / "homer" / "about.md").exists()
        assert list(memory.rglob("*.md")) == []   # memory untouched


class TestGeneratedPagesOnDisk:
    """The clean filter: a page counts as generated only if it carries
    the splice marker. Source captures never do; README is exempt."""

    def _write(self, root, rel, content):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")

    def test_finds_only_marker_bearing_pages(self, tmp_path):
        self._write(tmp_path, "homer/about.md",
                    "---\ntitle: Homer\n---\n<!-- begin: generated -->\nx\n<!-- end: generated -->\n")
        self._write(tmp_path, "homer/notes/2026/06/a-1.md",
                    "---\ntype: note\n---\n\n# Note\n\nbody\n")   # source, no marker
        self._write(tmp_path, "README.md",
                    "<!-- begin: generated -->\nguide\n<!-- end: generated -->\n")  # exempt
        found = {str(p.relative_to(tmp_path)) for p in _generated_pages_on_disk(tmp_path)}
        assert found == {"homer/about.md"}

    def test_email_capture_is_not_generated(self, tmp_path):
        self._write(tmp_path, "bart/emails/2026/06/t-abc.md",
                    "---\ntype: email\n---\n<!-- mid:<x@h> -->\n## 2026 - Bart\n\nhi\n")
        assert _generated_pages_on_disk(tmp_path) == []


class TestCleanGeneratedOnDisk:
    """`clean` removes generated pages from the brain working copy on disk
    (curator's next commit records it). --dry-run touches nothing."""

    def _gen(self, root, rel):
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(
            "---\ntitle: X\n---\n<!-- begin: generated -->\nbody\n<!-- end: generated -->\n",
            encoding="utf-8",
        )
        return p

    def test_deletes_generated_pages(self, tmp_path):
        about = self._gen(tmp_path, "homer/about.md")
        index = self._gen(tmp_path, "family/camping/notes/index.md")
        note = tmp_path / "homer" / "notes" / "a.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text("---\ntype: note\n---\n\nbody\n", encoding="utf-8")

        rc = _clean_generated(brain=tmp_path, dry_run=False, assume_yes=True)
        assert rc == 0
        assert not about.exists()
        assert not index.exists()
        assert note.exists()   # source capture survives

    def test_dry_run_deletes_nothing(self, tmp_path):
        about = self._gen(tmp_path, "homer/about.md")
        rc = _clean_generated(brain=tmp_path, dry_run=True, assume_yes=True)
        assert rc == 0
        assert about.exists()

    def test_nothing_to_clean_is_ok(self, tmp_path):
        (tmp_path / "n.md").write_text("---\ntype: note\n---\n\nx\n", encoding="utf-8")
        rc = _clean_generated(brain=tmp_path, dry_run=False, assume_yes=True)
        assert rc == 0
