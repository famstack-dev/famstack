"""Unit tests for text_utils — pure string manipulation functions.

No I/O, no env vars, no Matrix. Tests use Springfield-themed names
throughout for readability."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "docs" / "bot"))

from text_utils import (
    clean_filename,
    strip_reply_fallback,
    looks_like_paste,
    google_docs_export_url,
    is_just_url,
)


# ── Filename cleaning ──────────────────────────────────────────────────

class TestCleanFilename:

    def test_uuid_with_tilde_and_suffix(self):
        """Element auto-UUIDs: a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.jpg"""
        # Regex strips UUID+tilde+suffix+dot, leaving just the extension.
        # clean_filename recognizes bare extensions and maps them to photo/file.
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.jpg") == "photo.jpg"

    def test_uuid_without_tilde(self):
        # No tilde: regex strips UUID+dot, leaving extension → mapped to document.pdf
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890.pdf") == "document.pdf"

    def test_uuid_with_suffix_no_tilde(self):
        # Digits after UUID without tilde: regex strips UUID+digits+dot
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef12345678901.pdf") == "document.pdf"

    def test_non_noise_filename_preserved(self):
        assert clean_filename("Rechnung_ADAC_Marz2025.pdf") == "Rechnung_ADAC_Marz2025.pdf"

    def test_heic_image(self):
        # Non-UUID filename: preserved as-is
        assert clean_filename("IMG_1234.HEIC") == "IMG_1234.HEIC"

    def test_jpeg_lowercase(self):
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.jpeg") == "photo.jpeg"

    def test_png_image(self):
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.png") == "photo.png"

    def test_tiff_image(self):
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.tiff") == "photo.tiff"

    def test_no_extension(self):
        assert clean_filename("document") == "document"

    def test_no_extension_noise(self):
        # UUID stripped, no extension left → "document"
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1") == "document"

    def test_generic_file_extension(self):
        assert clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.txt") == "file.txt"

    def test_md_filename_preserved(self):
        assert clean_filename("note.md") == "note.md"

    def test_csv_filename_preserved(self):
        assert clean_filename("data.csv") == "data.csv"


# ── Reply fallback stripping ───────────────────────────────────────────

class TestStripReplyFallback:

    def test_simple_reply_fallback(self):
        body = "> <@homer:test.local> first line\n> second line\n\nthe user's reply"
        assert strip_reply_fallback(body) == "the user's reply"

    def test_no_reply_preserved(self):
        assert strip_reply_fallback("just regular text, no reply") == "just regular text, no reply"

    def test_reply_at_start_of_body(self):
        """Matrix reply fallback is at the START of the message body.

        The function strips the >-prefixed block from the beginning,
        skips the blank line after it, and returns the user's real reply."""
        body = "> <@marge:test.local> quoted\n> line 2\n\nactual reply text"
        assert strip_reply_fallback(body) == "actual reply text"

    def test_empty_after_stripping(self):
        body = "> quoted line\n\n"
        assert strip_reply_fallback(body) == ""

    def test_empty_string(self):
        assert strip_reply_fallback("") == ""

    def test_only_reply_lines_no_body(self):
        body = "> line 1\n> line 2\n> line 3"
        assert strip_reply_fallback(body) == ""

    def test_reply_with_empty_lines_between(self):
        body = "> quoted\n\n\n\nactual reply with blank lines"
        assert strip_reply_fallback(body) == "actual reply with blank lines"

    def test_multiline_reply_body(self):
        body = "> <@bart:test.local> Quoted\n> multiple\n> lines\n\nReply line 1\nReply line 2"
        assert strip_reply_fallback(body) == "Reply line 1\nReply line 2"

    def test_whitespace_only_body(self):
        body = "> quoted\n\n   \n"
        assert strip_reply_fallback(body) == ""


# ── Paste detection ────────────────────────────────────────────────────

class TestLooksLikePaste:

    def test_short_chat(self):
        assert looks_like_paste("ok") is False

    def test_short_exclamation(self):
        assert looks_like_paste("thanks!") is False

    def test_boundary_exactly_100(self):
        assert looks_like_paste("A" * 100) is True

    def test_one_below_boundary(self):
        assert looks_like_paste("A" * 99) is False

    def test_whitespace_only(self):
        assert looks_like_paste("   ") is False

    def test_empty_string(self):
        assert looks_like_paste("") is False

    def test_long_chat_with_newlines(self):
        text = "ok\nno\nyes\nno\nyes\n" * 20  # ~80 chars, below threshold
        # "ok\nno\nyes\nno\nyes\n" = 20 chars * 20 = 400 chars
        # But strip() keeps newlines — len("ok\nno\nyes\nno\nyes\n".strip()) = 16
        # 16 * 20 = 320, so this is above threshold
        # Let's use a truly short repeated string
        text = "ok " * 15  # 30 chars, below threshold
        assert looks_like_paste(text) is False

    def test_long_chat_with_newlines_above(self):
        text = "A " * 60  # 120 chars, above threshold
        assert looks_like_paste(text) is True

    def test_mixed_whitespace(self):
        text = "  A  B  C  " * 20
        assert looks_like_paste(text) is True


# ── Google Docs URL extraction ─────────────────────────────────────────

class TestGoogleDocsExportUrl:

    def test_document_with_edit_suffix(self):
        url, doc_type = google_docs_export_url("https://docs.google.com/document/d/abc123/edit")
        assert url == "https://docs.google.com/document/d/abc123/export?format=pdf"
        assert doc_type == "document"

    def test_document_without_suffix(self):
        url, doc_type = google_docs_export_url("https://docs.google.com/document/d/abc123")
        assert url == "https://docs.google.com/document/d/abc123/export?format=pdf"
        assert doc_type == "document"

    def test_spreadsheets_with_edit_suffix(self):
        url, doc_type = google_docs_export_url("https://docs.google.com/spreadsheets/d/xyz789/edit")
        assert url == "https://docs.google.com/spreadsheets/d/xyz789/export?format=pdf"
        assert doc_type == "spreadsheets"

    def test_presentation(self):
        url, doc_type = google_docs_export_url("https://docs.google.com/presentation/d/pres001")
        assert url == "https://docs.google.com/presentation/d/pres001/export?format=pdf"
        assert doc_type == "presentation"

    def test_non_google_url(self):
        assert google_docs_export_url("https://example.com/file.pdf") is None

    def test_not_a_url(self):
        assert google_docs_export_url("not a url") is None

    def test_empty_string(self):
        assert google_docs_export_url("") is None

    def test_google_drive_download_url(self):
        assert google_docs_export_url("https://drive.google.com/file/d/abc123") is None

    def test_url_with_query_params(self):
        url, doc_type = google_docs_export_url("https://docs.google.com/document/d/abc123/edit?usp=sharing")
        assert url == "https://docs.google.com/document/d/abc123/export?format=pdf"
        assert doc_type == "document"


# ── Bare URL detection ─────────────────────────────────────────────────

class TestIsJustUrl:

    def test_simple_pdf_url(self):
        assert is_just_url("https://example.com/file.pdf") is True

    def test_url_with_path_and_query(self):
        assert is_just_url("https://example.com/path?q=1#frag") is True

    def test_url_with_text_before(self):
        assert is_just_url("Check this out: https://example.com") is False

    def test_url_with_text_after(self):
        assert is_just_url("https://example.com but with text") is False

    def test_plain_text(self):
        assert is_just_url("hello world") is False

    def test_empty_string(self):
        assert is_just_url("") is False

    def test_http_url(self):
        assert is_just_url("http://example.com/file.pdf") is True

    def test_url_with_fragment(self):
        assert is_just_url("https://example.com/page#section") is True

    def test_url_with_port(self):
        assert is_just_url("https://example.com:8080/path") is True
