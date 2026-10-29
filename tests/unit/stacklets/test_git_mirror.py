"""Unit tests for the archivist's git mirror.

Covers the pure methods: filename generation, slug normalization,
frontmatter shape, markdown assembly. What the archivist files into the
memory, through its curator, and what its commits say, is in
`test_archivist_files_into_the_memory.py`.
"""

from __future__ import annotations

import sys
import yaml
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
# `memory.lib`, for the ontology a capture's tags are read through.
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))

from stack.people import Person  # noqa: E402
from stacklets.docs.bot.git_mirror import (  # noqa: E402
    GitMirror,
    _filer_localpart,
)


class TestFiledBy:
    """A capture names who filed it by their Matrix localpart. Who its
    commit is by is pinned across every path in
    `tests/memory/unit/test_memory_commit_author.py`."""

    def test_filer_localpart(self):
        assert _filer_localpart("@Marge:home.local") == "marge"
        assert _filer_localpart(None) is None


@pytest.fixture
def mirror(tmp_path):
    """GitMirror wired enough to exercise its pure methods."""
    return GitMirror(
        data_dir=tmp_path,
        paperless_version="2.14.5",
    )


# ── Filepath construction ──────────────────────────────────────────────────

class TestFilepath:
    """Documents land under `<shared_bucket>/documents/` — the shared
    institutional bucket inside the vault. Undated docs go to
    `<shared_bucket>/documents/_unfiled/`. The default mirror fixture
    uses `shared_bucket="family"`."""

    def test_with_title_and_date(self, mirror):
        path = mirror._filepath(
            date="2026-03-15", paperless_id=247,
            title="Duff Insurance Rechnung", has_title=True,
        )
        assert path == "family/documents/2026/03/2026-03-15-duff-insurance-rechnung-p247.md"

    def test_with_title_without_date_goes_to_unfiled(self, mirror):
        path = mirror._filepath(
            date=None, paperless_id=247,
            title="Duff Insurance Rechnung", has_title=True,
        )
        assert path == "family/documents/_unfiled/duff-insurance-rechnung-p247.md"

    def test_with_title_invalid_date_falls_through(self, mirror):
        path = mirror._filepath(
            date="not-a-date", paperless_id=42,
            title="A", has_title=True,
        )
        assert path == "family/documents/_unfiled/a-p42.md"

    def test_no_title_with_date(self, mirror):
        path = mirror._filepath(
            date="2026-03-15", paperless_id=42, title=None, has_title=False,
        )
        assert path == "family/documents/2026/03/2026-03-15-p42.md"

    def test_no_title_without_date(self, mirror):
        path = mirror._filepath(
            date=None, paperless_id=42, title=None, has_title=False,
        )
        assert path == "family/documents/_unfiled/p42.md"


# ── Frontmatter ────────────────────────────────────────────────────────────

class TestFrontmatter:
    def test_ai_full(self, mirror):
        fm = mirror._frontmatter(
            title="Duff Insurance Rechnung März 2026",
            date="2026-03-15",
            correspondent="Duff Insurance",
            document_type="Invoice",
            category="Insurance",
            persons=["Homer"],
            tags=["Insurance", "Person: Homer"],
            paperless_id=247,
            paperless_url="http://docs.home.local",
            processing="ai_formatted",
            model="qwen2.5:14b",
        )
        assert fm["type"] == "document"
        assert fm["resource"] == "http://docs.home.local/documents/247/details"
        assert fm["title"] == "Duff Insurance Rechnung März 2026"
        assert fm["paperless_id"] == 247
        assert fm["processing"] == "ai_formatted"
        assert fm["model"] == "qwen2.5:14b"
        assert fm["paperless_version"] == "2.14.5"
        assert fm["source"] == "paperless"
        assert fm["timestamp"].endswith("Z")
        # key order: type first (OKF convention), timestamp last; reflects insertion
        assert list(fm.keys())[0] == "type"
        assert list(fm.keys())[-1] == "timestamp"

    def test_ocr_omits_model(self, mirror):
        fm = mirror._frontmatter(
            title="Untitled", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=99,
            paperless_url="", processing="ocr", model=None,
        )
        assert fm["processing"] == "ocr"
        assert "model" not in fm
        assert "correspondent" not in fm
        assert "persons" not in fm

    def test_original_processing_is_text_file_provenance(self, mirror):
        """`original` is the provenance for text-like files (.md, .json…)
        whose body is the source bytes, not any LLM/OCR output."""
        fm = mirror._frontmatter(
            title="recipes", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=5,
            paperless_url="", processing="original", model=None,
        )
        assert fm["processing"] == "original"
        assert "model" not in fm

    def test_no_paperless_version_when_unset(self, tmp_path):
        m = GitMirror(
            data_dir=tmp_path,
        )
        fm = m._frontmatter(
            title="t", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=1,
            paperless_url="", processing="ai_formatted", model="x",
        )
        assert "paperless_version" not in fm


# ── Render (full markdown) ─────────────────────────────────────────────────

class TestRender:
    def test_full_document(self, mirror):
        fm = {
            "title": "Duff Insurance Rechnung", "paperless_id": 247,
            "processing": "ai_formatted", "source": "paperless",
        }
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter=fm,
            body="Policy number: KFZ-2024-XXX\n\nAmount: EUR 340.",
            correspondent="Duff Insurance",
            correspondent_card="family/correspondents/duff-insurance.md",
            persons=["Homer"], people=[Person("homer", "Homer Simpson")],
        )
        # Frontmatter fenced with ---
        assert out.startswith("---\n")
        # Parseable YAML block
        fm_block = out.split("---", 2)[1]
        parsed = yaml.safe_load(fm_block)
        assert parsed["paperless_id"] == 247

        assert "# Duff Insurance Rechnung" in out
        assert "**From:** [Duff Insurance](" in out
        assert "correspondents/duff-insurance.md)" in out
        assert "**About:** [Homer](../../../people/homer.md)" in out
        assert "Policy number: KFZ-2024-XXX" in out

    def test_no_wiki_header_when_no_entities(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
        )
        assert "[[" not in out
        assert "# t" in out
        assert "body" in out


class TestBriefingBlock:
    """The classifier's briefing — prose summary, optional source link,
    facts, and action items — is wrapped in a `> [!summary]` callout so
    it reads visually distinct from the OCR-cleaned body that follows."""

    def test_full_briefing_in_rendered_output(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "Duff Insurance Rechnung"},
            body="(OCR body)",
            correspondent="Duff Insurance", persons=["Homer"],
            summary="Annual renewal of comprehensive auto insurance.",
            facts=["Total: EUR 340.00", "Policy: KH-2026-987"],
            action_items=[
                {"action": "Confirm SEPA balance", "due": "2026-03-14"},
                {"action": "File for tax", "due": None},
            ],
        )

        assert "> [!summary]" in out
        assert "> Annual renewal of comprehensive auto insurance." in out
        assert "> **Facts**" in out
        assert "> - Total: EUR 340.00" in out
        assert "> - Policy: KH-2026-987" in out
        assert "> **Action items**" in out
        assert "> - Confirm SEPA balance 📅 2026-03-14" in out
        assert "> - File for tax" in out

    def test_briefing_appears_before_body(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="OCR PAYLOAD",
            correspondent=None, persons=[],
            summary="One liner.",
            facts=[], action_items=[],
        )
        assert out.index("> [!summary]") < out.index("OCR PAYLOAD")

    def test_omitted_entirely_when_classifier_returned_nothing(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None, facts=None, action_items=None,
        )
        assert "> [!summary]" not in out
        assert "**Facts**" not in out
        assert "**Action items**" not in out

    def test_individual_sections_drop_when_empty(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary="Just a summary.", facts=[], action_items=[],
        )
        assert "> [!summary]" in out
        assert "> Just a summary." in out
        assert "**Facts**" not in out
        assert "**Action items**" not in out

    def test_string_action_item_renders_as_task(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None, facts=None,
            action_items=["Just a string item"],
        )
        assert "> - Just a string item" in out

    def test_null_string_due_is_treated_as_no_due(self, mirror):
        # LLMs sometimes return the string "null" instead of true null.
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None, facts=None,
            action_items=[{"action": "Pay bill", "due": "null"}],
        )
        # No trailing "— null"
        assert "> - Pay bill" in out
        assert "null" not in out.split("**Action items**")[1]

    def test_skips_blank_facts_and_empty_actions(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
            summary=None,
            facts=["", "  ", "Real fact"],
            action_items=[{"action": "", "due": "2026-01-01"}, {"action": "Real action"}],
        )
        # Only the real fact + real action survive.
        assert "> - Real fact" in out
        assert "> - Real action" in out
        # No empty bullets / checkboxes.
        assert "- \n" not in out
        assert "- [ ] \n" not in out

    def test_source_link_appears_inside_callout(self, mirror):
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="(body)",
            correspondent=None, persons=[],
            summary="Prose.",
            facts=[], action_items=[],
            source_link=("Show Document", "https://paperless.local/documents/42/details"),
        )
        # Link sits inside the callout (prefixed with `> `) and points
        # at the Paperless doc details page.
        assert "> [Show Document](https://paperless.local/documents/42/details)" in out

    def test_source_link_dropped_when_url_missing(self, mirror):
        # Half-supplied source_link tuple (label without url, or vice versa)
        # must not produce a malformed `[label]()` link.
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter={"title": "t"}, body="(body)",
            correspondent=None, persons=[],
            summary="Prose.",
            facts=[], action_items=[],
            source_link=("Show Document", ""),
        )
        assert "Show Document" not in out


# ── Captures ─────────────────────────────────────────────────────────────
#
# A capture is a non-Paperless source (today: a pasted URL processed by
# trafilatura, or pasted text the user typed). It routes under the
# sender's own entity bucket — `<entity>/notes/...` for kind=note,
# `<entity>/bookmarks/...` for kind=bookmark — so per-entity wiki
# compilation is a single recursive glob. Cross-mentions stay with the
# author; the frontmatter `persons:` field indexes them for other
# entities' wiki compiles. The hash suffix disambiguates re-pastes of
# different sources resolving to the same slug, and makes a re-paste
# of the same source idempotent (same path → update, not duplicate).


class TestCapturePath:
    """`<entity>/<kind>s/YYYY/MM/<slug>-<hash>.md` shape — routed by
    the sender's slug and the capture kind.

    `hash_key` is the stable identity string the caller hashes into the
    filename suffix — typically the source URL for URL/paste captures,
    or a content hash when the paste has no source URL. The mirror
    doesn't care what's in the string, only that the same key yields
    the same path on re-publish."""

    def test_path_uses_captured_date_and_entity(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17",
            title="Why local LLMs matter",
            hash_key="https://example.com/llms",
        )
        assert path.startswith("homer/notes/2026/05/")
        assert path.endswith(".md")

    def test_bookmark_kind_uses_bookmarks_folder(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="bookmark",
            captured_at="2026-05-17",
            title="Why local LLMs matter",
            hash_key="https://example.com/llms",
        )
        assert path.startswith("homer/bookmarks/2026/05/")

    def test_slug_in_filename(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17",
            title="Why local LLMs matter",
            hash_key="https://example.com/llms",
        )
        # slug lives between "YYYY/MM/" and the "-<hash>.md" tail.
        assert "why-local-llms-matter" in path

    def test_same_key_yields_same_path(self, mirror):
        a = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/a",
        )
        b = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/a",
        )
        assert a == b

    def test_different_keys_yield_different_paths(self, mirror):
        a = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="Same Title",
            hash_key="https://example.com/article-1",
        )
        b = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="Same Title",
            hash_key="https://example.com/article-2",
        )
        # Same slug, same date — only the hash distinguishes.
        assert a != b

    def test_different_entities_yield_different_paths(self, mirror):
        a = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/x",
        )
        b = mirror._capture_filepath(
            entity="marge", kind="note",
            captured_at="2026-05-17", title="t",
            hash_key="https://example.com/x",
        )
        # Same content, different senders — routed to separate buckets.
        assert a.startswith("homer/notes/")
        assert b.startswith("marge/notes/")

    def test_no_title_falls_back_to_capture(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="2026-05-17", title=None,
            hash_key="https://example.com/x",
        )
        # No useful title → generic slug. Hash still disambiguates.
        assert "homer/notes/2026/05/" in path
        assert path.endswith(".md")

    def test_invalid_date_lands_in_unfiled(self, mirror):
        path = mirror._capture_filepath(
            entity="homer", kind="note",
            captured_at="not-a-date", title="hi",
            hash_key="https://example.com/x",
        )
        # Undated captures still live under the sender's bucket.
        assert path.startswith("homer/notes/_unfiled/")


class TestCaptureFrontmatter:
    """Captures carry `kind: bookmark|note` + optional `resource`.
    Bookmark = URL pointer; Note = pasted body the user typed.

    Document-shaped fields (correspondent, document_type, category) are
    intentionally absent — captures aren't part of the Paperless
    ontology. Persons + tags are present (load-bearing for interest
    derivation in the dream-cycle rebuild)."""

    def test_bookmark_minimum_shape(self, mirror):
        fm = mirror._capture_frontmatter(
            title="Why local LLMs matter",
            captured_at="2026-05-17",
            kind="bookmark",
            source_uri="https://example.com/llms",
            persons=[], tags=[], model=None,
        )
        assert fm["title"] == "Why local LLMs matter"
        assert fm["type"] == "bookmark"
        assert fm["resource"] == "https://example.com/llms"
        # Document-shaped fields are gone from captures.
        assert "correspondent" not in fm
        assert "document_type" not in fm
        assert "category" not in fm
        assert "paperless_id" not in fm
        assert "paperless_url" not in fm
        assert fm["timestamp"].endswith("Z")

    def test_a_notes_frontmatter_keeps_only_what_it_is_about(self, mirror):
        """Where a note came from and how it was made are in its sidecar
        (ADR-018 §3); the link in its words stays in its words."""
        fm = mirror._capture_frontmatter(
            title="Reddit thread title",
            captured_at="2026-05-17",
            kind="note",
            source_uri="https://reddit.com/r/x/comments/y",
            persons=["Homer"], tags=["LLMs"], model="m", capture_id="$x", filed_by="homer",
        )
        assert fm["type"] == "note"
        assert fm["tags"] == ["LLMs"]
        assert not {"resource", "capture_id", "model", "timestamp", "filed_by"} & set(fm)

    def test_note_without_source_uri(self, mirror):
        # Pure paste — no URL anywhere. Frontmatter omits source_uri so
        # Dataview queries can `where source_uri` filter to "captures
        # that link back to a source" cleanly.
        fm = mirror._capture_frontmatter(
            title="Some pasted thought",
            captured_at="2026-05-17",
            kind="note",
            source_uri=None,
            persons=["Homer"], tags=[], model=None,
        )
        assert fm["type"] == "note"
        assert "resource" not in fm

    def test_full_shape(self, mirror):
        fm = mirror._capture_frontmatter(
            title="Why local LLMs matter",
            captured_at="2026-05-17",
            kind="bookmark",
            source_uri="https://example.com/llms",
            persons=["Homer"],
            tags=["LLMs", "Local Inference", "Person: Homer"],
            model="qwen2.5:14b",
        )
        assert fm["persons"] == ["Homer"]
        assert fm["tags"] == ["LLMs", "Local Inference", "Person: Homer"]
        assert fm["model"] == "qwen2.5:14b"
        # date echoes the capture date, not the article's publish date.
        assert fm["date"] == "2026-05-17"

    def test_no_model_omits_field(self, mirror):
        fm = mirror._capture_frontmatter(
            title="t", captured_at="2026-05-17",
            kind="bookmark", source_uri="https://x/y",
            persons=[], tags=[], model=None,
        )
        assert "model" not in fm


class TestCaptureRender:
    """The captures use the same _render() as Paperless docs — same
    briefing block, same wiki-link header — but no document-shaped
    fields. Bookmarks drop the body section (the LLM summary IS the
    content); notes keep the body."""

    def test_bookmark_renders_without_body(self, mirror):
        # When the caller passes an empty body, the file ends at the
        # briefing block. That's the bookmark shape — the summary is
        # the content, the URL is the source.
        fm = mirror._capture_frontmatter(
            title="Why local LLMs matter",
            captured_at="2026-05-17",
            kind="bookmark",
            source_uri="https://example.com/llms",
            persons=["homer"], tags=["LLMs"], model="qwen2.5:14b",
        )
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter=fm, body="",
            correspondent=None, persons=["Homer"], people=[Person("homer", "Homer Simpson")],
            summary="A 200-word digest of the article's main points.",
            facts=["Mac Mini idles under 10W"],
            action_items=[],
        )
        assert "type: bookmark" in out
        # Resource URL is quoted because it contains ':'
        assert 'resource: "https://example.com/llms"' in out
        assert "# Why local LLMs matter" in out
        assert "**About:** [Homer](../../../people/homer.md)" in out
        assert "> [!summary]" in out
        assert "A 200-word digest" in out
        # No trailing empty body section — file ends after briefing.

    def test_note_renders_with_body(self, mirror):
        # Notes preserve the pasted body the user typed.
        fm = mirror._capture_frontmatter(
            title="Reddit thread on benchmarks",
            captured_at="2026-05-17",
            kind="note",
            source_uri="https://reddit.com/r/x/y",
            persons=["Homer"], tags=["Benchmarks"], model=None,
        )
        out = mirror._render(
            from_path="family/documents/2026/03/entry.md",
            frontmatter=fm,
            body="Top comment quotes 60 tok/s on M2 Pro.\n\nRest of thread...",
            correspondent=None, persons=["Homer"],
            summary="Comment thread comparing on-device inference speeds.",
            facts=[], action_items=[],
        )
        assert "type: note" in out
        assert "> [!summary]" in out
        assert "Comment thread comparing" in out
        assert "Top comment quotes 60 tok/s" in out


