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

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))

from wiki import (  # noqa: E402
    _correspondent_body,
    _correspondent_entries,
    _correspondent_preamble,
    _correspondent_roster,
    _member_preamble,
    _topic_cross_refs,
    _topic_entries,
    _topic_locations,
    _topic_preamble,
)


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
            tmp_path, shared_bucket="family", member_slugs=["arthur", "marge"],
        )
        assert ("family", "camping") in locs

    def test_personal_topic_discovered(self, tmp_path):
        _make_topic_folder(tmp_path, "arthur", "gravel")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["arthur", "marge"],
        )
        assert ("arthur", "gravel") in locs

    def test_personal_topic_skipped_for_unknown_member(self, tmp_path):
        """A folder at `bart/whatever/notes/...` doesn't get discovered
        as a topic if `bart` isn't in the member list. The wiki only
        generates pages for known members."""
        _make_topic_folder(tmp_path, "bart", "skateboarding")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["arthur"],
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
        (tmp_path / "family" / "correspondents" / "adac.md").write_text("...")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=[],
        )
        assert ("family", "correspondents") not in locs

    def test_unfiled_reserved(self, tmp_path):
        _make_bare_folder(tmp_path, "arthur", "_unfiled")
        (tmp_path / "arthur" / "_unfiled" / "x.md").write_text("...")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["arthur"],
        )
        assert ("arthur", "_unfiled") not in locs

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
        _make_topic_folder(tmp_path, "arthur", "gravel")
        locs = _topic_locations(
            tmp_path, shared_bucket="family", member_slugs=["arthur"],
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
        """A personal topic (`arthur/gravel/`) is isolated from arthur's
        plain personal notes (`arthur/notes/`)."""
        index = [
            {"rel": "arthur/gravel/notes/2026/06/a.md", "title": "Gravel A"},
            {"rel": "arthur/notes/2026/06/b.md", "title": "Personal B"},
            {"rel": "arthur/bookmarks/2026/06/c.md", "title": "Personal C"},
        ]
        entries = _topic_entries(index, "arthur", "gravel")
        rels = [e["rel"] for e in entries]
        assert rels == ["arthur/gravel/notes/2026/06/a.md"]

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
            {"rel": "family/documents/2026/06/adac-camping.md",
             "tags": ["camping", "Versicherung"], "topics": []},
            # Outside, no camping reference — excluded
            {"rel": "family/documents/2026/06/other.md",
             "tags": ["other"], "topics": []},
        ]
        refs = _topic_cross_refs(index, "family", "camping")
        rels = [e["rel"] for e in refs]
        assert "family/documents/2026/06/adac-camping.md" in rels
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
            {"rel": "arthur/notes/2026/05/idea.md",
             "tags": ["camping", "route"], "topics": []},
        ]
        refs = _topic_cross_refs(index, "family", "camping")
        rels = {e["rel"] for e in refs}
        assert rels == {
            "marge/notes/2026/05/gear.md",
            "arthur/notes/2026/05/idea.md",
        }

    def test_personal_topic_cross_refs(self):
        """A personal topic's cross-refs are everything OUTSIDE
        `arthur/gravel/`. The wider scope is intentional — the deriver's
        view of 'all gravel-relevant captures in the household' still
        includes shared captures."""
        index = [
            {"rel": "arthur/gravel/notes/a.md",
             "tags": ["gravel"], "topics": []},
            {"rel": "family/documents/bike-insurance.md",
             "tags": ["gravel"], "topics": []},
        ]
        refs = _topic_cross_refs(index, "arthur", "gravel")
        rels = [e["rel"] for e in refs]
        assert "family/documents/bike-insurance.md" in rels
        assert "arthur/gravel/notes/a.md" not in rels

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
        assert "type: person" in pre  # OKF concept kind
        assert "title: Margaret" in pre  # longest synonym is canonical
        assert "canonical: Margaret" in pre
        assert "slug: maggie" in pre
        assert pre.startswith("---")
        assert pre.rstrip().endswith("---")

    def test_no_synonyms_collapses_to_display(self):
        pre = _member_preamble("homer", "Homer", [])
        assert "type: person" in pre
        assert "title: Homer" in pre
        assert "synonyms:" not in pre


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
        assert "type: correspondent" in pre  # OKF concept kind
        assert "title: Duff Insurance" in pre
        assert "canonical: Duff Insurance" in pre
        assert "slug: duff-insurance" in pre
        assert pre.startswith("---")
        assert pre.rstrip().endswith("---")


class TestCorrespondentBody:

    def test_lists_documents_as_relative_links(self):
        entries = _correspondent_entries(
            [_doc("Duff Insurance", title="Auto Policy", date="2026-03-15",
                  rel="family/documents/2026/03/auto-policy-p247.md")],
            "Duff Insurance",
        )
        body = _correspondent_body(entries, page_dir="family/correspondents")
        assert body.startswith("## Documents")
        # leaf page lives in family/correspondents/ -> climb two to root
        assert "[Auto Policy](../../family/documents/2026/03/auto-policy-p247.md)" in body
        assert "2026-03-15" in body


# ── _topic_preamble ───────────────────────────────────────────────────


class TestTopicPreamble:
    """First-creation frontmatter for `about.md` when the page does not
    exist yet. The wiki command's splice writer lays this on top of
    the regenerate region before the LLM-generated body."""

    def test_shared_topic_carries_scope_and_slug(self):
        pre = _topic_preamble("camping", "Camping", "shared")
        assert "title: Camping" in pre
        assert "slug: camping" in pre
        assert "scope: shared" in pre
        assert "type: topic" in pre
        # Opens and closes with the YAML fence.
        assert pre.startswith("---")
        assert pre.rstrip().endswith("---")

    def test_personal_topic_scope_recorded(self):
        pre = _topic_preamble("gravel", "Gravel", "personal")
        assert "scope: personal" in pre

    def test_display_with_special_chars(self):
        """A topic named `Van Life` keeps the casing + spaces in the
        display title, even though the slug is hyphenated."""
        pre = _topic_preamble("van-life", "Van Life", "shared")
        assert "title: Van Life" in pre
        assert "slug: van-life" in pre
