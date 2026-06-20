"""Unit tests for vault_entry — pure path generation and markdown rendering.

No Forgejo I/O, no git operations. Tests use Springfield-themed names."""

from __future__ import annotations

import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "docs" / "bot"))

from vault_entry import (
    slug,
    document_filepath,
    capture_filepath,
    document_frontmatter,
    capture_frontmatter,
    render_document,
    render_capture,
)


# ── Slug generation ────────────────────────────────────────────────────

class TestSlug:

    def test_simple_lowercase(self):
        assert slug("Hello World") == "hello-world"

    def test_hyphenated(self):
        assert slug("Kwik-E-Mart") == "kwik-e-mart"

    def test_umlauts_stripped(self):
        assert slug("Rechnung Müller") == "rechnung-muller"

    def test_leading_trailing_spaces(self):
        assert slug("  Leading Spaces  ") == "leading-spaces"

    def test_empty_string(self):
        assert slug("") == "document"

    def test_only_special_chars(self):
        assert slug("!!!") == "document"

    def test_numbers_preserved(self):
        assert slug("Duff Insurance 2025") == "duff-insurance-2025"

    def test_ampersand(self):
        assert slug("Müller & Söhne") == "muller-sohne"

    def test_max_length_cap(self):
        long_title = "A" * 100
        result = slug(long_title)
        assert len(result) == 60
        assert result == "a" * 60

    def test_special_chars_become_hyphens(self):
        assert slug("Hello!!! World") == "hello-world"

    def test_unicode_emoji(self):
        assert slug("Invoice 📄") == "invoice"


# ── Document filepath generation ───────────────────────────────────────

class TestDocumentFilepath:

    def test_with_date_and_title(self):
        path = document_filepath(
            "family", "2025-03-27", 42, "Duff Insurance - Kfz", True,
        )
        assert path == "family/documents/2025/03/2025-03-27-duff-insurance-kfz-p42.md"

    def test_with_date_no_title(self):
        path = document_filepath(
            "family", "2025-03-27", 42, None, False,
        )
        assert path == "family/documents/2025/03/2025-03-27-p42.md"

    def test_no_date_with_title(self):
        path = document_filepath(
            "family", None, 42, "Duff Insurance - Kfz", True,
        )
        assert path == "family/documents/_unfiled/duff-insurance-kfz-p42.md"

    def test_no_date_no_title(self):
        path = document_filepath(
            "family", None, 42, None, False,
        )
        assert path == "family/documents/_unfiled/p42.md"

    def test_custom_bucket(self):
        path = document_filepath(
            "deskstack", "2025-03-27", 42, "Invoice", True,
        )
        assert path == "deskstack/documents/2025/03/2025-03-27-invoice-p42.md"

    def test_umlauts_in_slug(self):
        path = document_filepath(
            "family", "2025-03-27", 42, "Rechnung Müller", True,
        )
        assert path == "family/documents/2025/03/2025-03-27-rechnung-muller-p42.md"

    def test_title_with_special_chars(self):
        path = document_filepath(
            "family", "2025-03-27", 42, "Duff Insurance - Kfz-Versicherung 2025", True,
        )
        assert path == "family/documents/2025/03/2025-03-27-duff-insurance-kfz-versicherung-2025-p42.md"

    def test_has_title_false_no_slug(self):
        """has_title=False means don't use title in path, even if provided."""
        path = document_filepath(
            "family", "2025-03-27", 42, "Duff Insurance - Kfz", False,
        )
        assert path == "family/documents/2025/03/2025-03-27-p42.md"

    def test_has_title_true_empty_title(self):
        """has_title=True but empty title falls back to id-only."""
        path = document_filepath(
            "family", "2025-03-27", 42, "", True,
        )
        assert path == "family/documents/2025/03/2025-03-27-p42.md"


# ── Capture filepath generation ────────────────────────────────────────

class TestCaptureFilepath:

    def test_bookmark_with_date(self):
        path = capture_filepath(
            "homer", "bookmark", "2025-03-27",
            "Reddit Thread", "https://reddit.com/r/famstack/...",
        )
        assert path.startswith("homer/bookmarks/2025/03/")
        assert "reddit-thread-" in path
        assert path.endswith(".md")

    def test_note_with_date(self):
        path = capture_filepath(
            "marge", "note", "2025-03-27",
            "Meeting notes", "content hash here",
        )
        assert path.startswith("marge/notes/2025/03/")
        assert "meeting-notes-" in path
        assert path.endswith(".md")

    def test_no_date_falls_to_unfiled(self):
        path = capture_filepath(
            "homer", "note", None,
            "Random thought", "content hash",
        )
        assert "homer/notes/_unfiled/" in path
        assert "random-thought-" in path

    def test_no_title_uses_capture_slug(self):
        path = capture_filepath(
            "homer", "bookmark", "2025-03-27",
            None, "https://example.com",
        )
        assert "capture-" in path

    def test_same_hash_key_same_path(self):
        """Re-publishing the same source should yield the same path."""
        path1 = capture_filepath(
            "homer", "bookmark", "2025-03-27",
            "Reddit Thread", "https://reddit.com/r/famstack/...",
        )
        path2 = capture_filepath(
            "homer", "bookmark", "2025-03-27",
            "Reddit Thread", "https://reddit.com/r/famstack/...",
        )
        assert path1 == path2

    def test_different_hash_key_different_path(self):
        path1 = capture_filepath(
            "homer", "bookmark", "2025-03-27",
            "Thread A", "https://reddit.com/r/a",
        )
        path2 = capture_filepath(
            "homer", "bookmark", "2025-03-27",
            "Thread B", "https://reddit.com/r/b",
        )
        # Same title but different hash → different paths
        assert path1 != path2


# ── Document frontmatter ───────────────────────────────────────────────

class TestDocumentFrontmatter:

    def test_full_frontmatter(self):
        fm = document_frontmatter(
            title="Duff Insurance - Kfz-Versicherung",
            date="2025-03-27",
            correspondent="Duff Insurance",
            document_type="Invoice",
            category="Insurance",
            persons=["Homer"],
            tags=["Insurance", "Vehicle"],
            paperless_id=42,
            paperless_url="http://paperless:8000",
            processing="ai_formatted",
            model="mlx-model",
        )
        assert fm["type"] == "document"  # OKF concept kind
        assert fm["title"] == "Duff Insurance - Kfz-Versicherung"
        assert fm["date"] == "2025-03-27"
        assert fm["correspondent"] == "Duff Insurance"
        assert fm["document_type"] == "Invoice"  # Paperless subtype, separate axis
        assert fm["category"] == "Insurance"
        assert fm["persons"] == ["Homer"]
        assert fm["tags"] == ["Insurance", "Vehicle"]
        assert fm["paperless_id"] == 42
        assert fm["paperless_url"] == "http://paperless:8000"
        assert fm["resource"] == "http://paperless:8000/documents/42/details"
        assert fm["processing"] == "ai_formatted"
        assert fm["model"] == "mlx-model"
        assert fm["source"] == "paperless"
        assert "timestamp" in fm  # always present

    def test_minimal_frontmatter(self):
        fm = document_frontmatter(
            title="Untitled",
            date=None,
            correspondent=None,
            document_type=None,
            category=None,
            persons=[],
            tags=[],
            paperless_id=1,
            paperless_url="",
            processing="ocr",
            model=None,
        )
        assert fm["type"] == "document"
        assert fm["title"] == "Untitled"
        assert "date" not in fm
        assert "correspondent" not in fm
        assert "document_type" not in fm
        assert "category" not in fm
        assert "persons" not in fm
        assert "tags" not in fm
        assert fm["paperless_id"] == 1
        assert "paperless_url" not in fm
        assert "resource" not in fm  # no public URL -> no resource
        assert fm["processing"] == "ocr"
        assert "model" not in fm
        assert fm["source"] == "paperless"

    def test_paperless_version_included(self):
        fm = document_frontmatter(
            title="Test", date=None, correspondent=None,
            document_type=None, category=None, persons=[],
            tags=[], paperless_id=1, paperless_url="",
            processing="ocr", model=None, paperless_version="2.5.0",
        )
        assert fm["paperless_version"] == "2.5.0"


# ── Capture frontmatter ────────────────────────────────────────────────

class TestCaptureFrontmatter:

    def test_full_capture_frontmatter(self):
        fm = capture_frontmatter(
            title="Reddit Thread",
            captured_at="2025-03-27",
            kind="bookmark",
            source_uri="https://reddit.com/r/famstack/...",
            persons=["Homer"],
            tags=["Technology"],
            model="mlx-model",
        )
        assert fm["title"] == "Reddit Thread"
        assert fm["type"] == "bookmark"  # OKF concept kind, mirrors kind
        assert "kind" not in fm  # promoted to type
        assert fm["date"] == "2025-03-27"
        assert fm["resource"] == "https://reddit.com/r/famstack/..."
        assert fm["persons"] == ["Homer"]
        assert fm["tags"] == ["Technology"]
        assert fm["model"] == "mlx-model"
        assert "timestamp" in fm
        # Document fields should NOT be present
        assert "paperless_id" not in fm
        assert "paperless_url" not in fm
        assert "correspondent" not in fm
        assert "document_type" not in fm

    def test_note_without_source_uri(self):
        fm = capture_frontmatter(
            title="Meeting notes",
            captured_at="2025-03-27",
            kind="note",
            source_uri=None,
            persons=[],
            tags=[],
            model=None,
        )
        assert fm["type"] == "note"
        assert "kind" not in fm  # promoted to type
        assert "resource" not in fm
        assert "model" not in fm


# ── Document rendering ─────────────────────────────────────────────────

class TestRenderDocument:

    def test_full_document_render(self):
        fm = document_frontmatter(
            title="Duff Insurance - Kfz-Versicherung",
            date="2025-03-27",
            correspondent="Duff Insurance",
            document_type="Invoice",
            category="Insurance",
            persons=["Homer"],
            tags=["Insurance", "Vehicle"],
            paperless_id=42,
            paperless_url="http://paperless:8000",
            processing="ai_formatted",
            model="mlx-model",
        )
        content = render_document(
            from_path="family/documents/2026/03/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="Vehicle registration insurance renewal.\nAmount: EUR 340.00.",
            correspondent="Duff Insurance",
            persons=["Homer"],
            summary="Kfz-Versicherung renewal for Homer Simpson's 2025 vehicle.",
            facts=["Policy: 12345678", "Amount: EUR 340.00"],
            action_items=[{"action": "Pay invoice", "due": "2025-04-01"}],
            source_link=("Show Document", "http://paperless:8000/documents/42/details"),
        )
        assert "---" in content
        assert "# Duff Insurance - Kfz-Versicherung" in content
        assert "**From:** [Duff Insurance](" in content
        assert "correspondents/duff-insurance.md)" in content
        assert "**About:** [Homer](" in content
        assert "homer/about.md)" in content
        assert "> [!summary]" in content
        assert "Kfz-Versicherung renewal" in content
        assert "**Facts**" in content
        assert "**Action items**" in content
        assert "- [ ] Pay invoice — 2025-04-01" in content
        assert "[Show Document](http://paperless:8000/documents/42/details)" in content
        assert "Vehicle registration insurance renewal" in content

    def test_document_without_correspondent_or_persons(self):
        fm = document_frontmatter(
            title="Test", date=None, correspondent=None,
            document_type=None, category=None, persons=[],
            tags=[], paperless_id=1, paperless_url="",
            processing="ocr", model=None,
        )
        content = render_document(
            from_path="family/documents/2026/03/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="Test content.",
            correspondent=None,
            persons=[],
            summary=None,
            facts=None,
            action_items=None,
            source_link=None,
        )
        assert "---" in content
        assert "# Test" in content
        assert "**From:**" not in content
        assert "**About:**" not in content

    def test_document_without_body(self):
        fm = document_frontmatter(
            title="Test", date=None, correspondent=None,
            document_type=None, category=None, persons=[],
            tags=[], paperless_id=1, paperless_url="",
            processing="ocr", model=None,
        )
        content = render_document(
            from_path="family/documents/2026/03/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="",
            correspondent=None,
            persons=[],
            summary=None,
            facts=None,
            action_items=None,
            source_link=None,
        )
        assert "---" in content
        assert "# Test" in content
        # No body section when body is empty

    def test_briefing_suppressed_when_all_empty(self):
        fm = document_frontmatter(
            title="Test", date=None, correspondent=None,
            document_type=None, category=None, persons=[],
            tags=[], paperless_id=1, paperless_url="",
            processing="ocr", model=None,
        )
        content = render_document(
            from_path="family/documents/2026/03/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="Content.",
            correspondent=None,
            persons=[],
            summary=None,
            facts=None,
            action_items=None,
            source_link=None,
        )
        assert "> [!summary]" not in content

    def test_action_item_as_string(self):
        fm = document_frontmatter(
            title="Test", date=None, correspondent=None,
            document_type=None, category=None, persons=[],
            tags=[], paperless_id=1, paperless_url="",
            processing="ocr", model=None,
        )
        content = render_document(
            from_path="family/documents/2026/03/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="",
            correspondent=None,
            persons=[],
            summary=None,
            facts=None,
            action_items=["Pay the invoice"],
            source_link=None,
        )
        assert "- [ ] Pay the invoice" in content

    def test_action_item_with_null_due(self):
        fm = document_frontmatter(
            title="Test", date=None, correspondent=None,
            document_type=None, category=None, persons=[],
            tags=[], paperless_id=1, paperless_url="",
            processing="ocr", model=None,
        )
        content = render_document(
            from_path="family/documents/2026/03/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="",
            correspondent=None,
            persons=[],
            summary=None,
            facts=None,
            action_items=[{"action": "Pay", "due": "null"}],
            source_link=None,
        )
        assert "- [ ] Pay" in content
        assert "— null" not in content


# ── Capture rendering ──────────────────────────────────────────────────

class TestRenderCapture:

    def test_bookmark_render(self):
        fm = capture_frontmatter(
            title="Reddit Thread",
            captured_at="2025-03-27",
            kind="bookmark",
            source_uri="https://reddit.com/r/famstack/...",
            persons=["Marge"],
            tags=["Technology"],
            model="mlx-model",
        )
        content = render_capture(
            from_path="homer/bookmarks/2026/05/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="",  # bookmarks have no body
            kind="bookmark",
            captured_at="2025-03-27",
            source_uri="https://reddit.com/r/famstack/...",
            persons=["Marge"],
            summary="Discussion about document filing.",
            facts=["Subreddit: r/famstack"],
        )
        assert "---" in content
        assert "# Reddit Thread" in content
        assert "**About** [Marge](" in content
        assert "marge/about.md)" in content
        assert "**Captured** 2025-03-27" in content
        assert "**Kind** bookmark" in content
        assert "**Source** <https://reddit.com/r/famstack/...>" in content
        assert "> [!summary]" in content
        assert "Discussion about document filing" in content
        assert "> [!quote]" not in content  # bookmarks don't have quote block

    def test_note_render(self):
        fm = capture_frontmatter(
            title="Meeting notes",
            captured_at="2025-03-27",
            kind="note",
            source_uri=None,
            persons=["Marge"],
            tags=["Notes"],
            model=None,
        )
        content = render_capture(
            from_path="homer/bookmarks/2026/05/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="Discussed Q2 budget.\nAction: review expenses.",
            kind="note",
            captured_at="2025-03-27",
            source_uri=None,
            persons=["Marge"],
            summary="Q2 budget discussion notes.",
            facts=[],
        )
        assert "---" in content
        assert "# Meeting notes" in content
        assert "**About** [Marge](" in content
        assert "marge/about.md)" in content
        assert "**Kind** note" in content
        assert "**Captured** 2025-03-27" in content
        assert "Q2 budget discussion notes" in content
        # Notes get the collapsible quote block
        assert "> [!quote]- Original paste" in content
        assert "> Discussed Q2 budget." in content
        assert "> Action: review expenses." in content

    def test_note_without_body(self):
        fm = capture_frontmatter(
            title="Empty note",
            captured_at="2025-03-27",
            kind="note",
            source_uri=None,
            persons=[],
            tags=[],
            model=None,
        )
        content = render_capture(
            from_path="homer/bookmarks/2026/05/entry.md", shared_bucket="family",
            frontmatter=fm,
            body="",
            kind="note",
            captured_at="2025-03-27",
            source_uri=None,
            persons=[],
            summary="No content.",
            facts=[],
        )
        assert "> [!quote]" not in content  # no body → no quote block
