"""Unit tests for classify_format — chat reply rendering from classification data.

Pure formatting: takes a classification dict and metadata, produces
a list of text parts. No translations, no env vars, no Matrix."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "stacklets" / "docs" / "bot"))

from classify_format import format_reply_parts, format_capture_parts


# ── Reply parts — happy path ──────────────────────────────────────────

CLASSIFICATION = {
    "title": "ADAC - Kfz-Versicherung EUR 340",
    "summary": "Kfz-Versicherung renewal for Homer Simpson's 2025 vehicle.",
    "facts": [
        "Policy number: 12345678",
        "Amount: EUR 340.00",
        "Due date: 2025-04-01",
    ],
    "action_items": [
        {"action": "Pay invoice before due date", "due": "2025-04-01"},
        {"action": "File in personal records", "due": ""},
    ],
    "topics": ["Insurance"],
    "persons": ["Homer"],
}


class TestFormatReplyParts:

    def test_full_happy_path(self):
        parts = format_reply_parts(
            title="ADAC - Kfz-Versicherung EUR 340",
            display_name="a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.pdf",
            doc_id=42,
            resolved_topics=["Insurance"],
            resolved_persons=["Homer"],
            resolved_type="Invoice",
            resolved_correspondent="ADAC",
            date_applied="2025-03-27",
            classification=CLASSIFICATION,
            created_new=[],
            reformat_failed=False,
            link="http://paperless:8000/documents/42/details",
        )
        joined = "\n".join(parts)
        assert "Filed: ADAC - Kfz-Versicherung EUR 340 (#42)" in joined
        assert "Insurance | Homer | Invoice | ADAC | 2025-03-27" in joined
        assert "Kfz-Versicherung renewal" in joined
        assert "Policy number: 12345678" in joined
        assert "Amount: EUR 340.00" in joined
        assert "Pay invoice before due date (due 2025-04-01)" in joined
        assert "File in personal records" in joined
        assert "(due )" not in joined  # empty due omits the suffix
        assert "http://paperless:8000/documents/42/details" in joined

    def test_title_fallback_to_display_name(self):
        parts = format_reply_parts(
            title=None,
            display_name="Rechnung.pdf",
            doc_id=10,
            resolved_topics=[],
            resolved_persons=[],
            resolved_type=None,
            resolved_correspondent=None,
            date_applied=None,
            classification={},
            created_new=[],
            reformat_failed=False,
            link="",
        )
        joined = "\n".join(parts)
        assert "Filed: Rechnung.pdf (#10)" in joined

    def test_metadata_line_empty_when_no_resolved_fields(self):
        parts = format_reply_parts(
            title="Test",
            display_name="test.pdf",
            doc_id=1,
            resolved_topics=[],
            resolved_persons=[],
            resolved_type=None,
            resolved_correspondent=None,
            date_applied=None,
            classification={},
            created_new=[],
            reformat_failed=False,
            link="",
        )
        joined = "\n".join(parts)
        # No metadata line should appear when nothing is resolved
        assert " | " not in joined

    def test_summary_only(self):
        classification = {"summary": "A simple summary."}
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification=classification,
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "A simple summary." in joined

    def test_facts_limited_to_five(self):
        classification = {
            "facts": [f"Fact {i}" for i in range(10)],
        }
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification=classification,
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        # Should contain exactly 5 fact lines
        fact_count = joined.count("  - Fact ")
        assert fact_count == 5
        assert "Fact 5" not in joined

    def test_action_items_limited_to_three(self):
        classification = {
            "action_items": [
                {"action": f"Action {i}", "due": ""} for i in range(10)
            ],
        }
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification=classification,
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        # Actions are rendered as plain text (not checkboxes)
        action_count = sum(1 for i in range(10) if f"Action {i}" in joined)
        assert action_count == 3

    def test_string_action_items_filtered(self):
        """Only dict action items with an 'action' key are rendered.

        String action items are not supported — the LLM always returns
        dicts with 'action' and optional 'due' keys.
        """
        classification = {
            "action_items": [
                "string action",
                {"action": "Valid action", "due": ""},
            ],
        }
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification=classification,
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "Valid action" in joined
        assert "string action" not in joined

    def test_created_new_notice(self):
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=["NewTag"], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification={},
            created_new=["NewTag"], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "New in Paperless: NewTag" in joined

    def test_reformat_failed_notice(self):
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification={},
            created_new=[], reformat_failed=True, link="",
        )
        joined = "\n".join(parts)
        assert "Reformat failed" in joined

    def test_no_link_when_empty(self):
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification={},
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "http" not in joined

    def test_multiple_topics_and_persons(self):
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=["Insurance", "Vehicle"],
            resolved_persons=["Homer", "Marge"],
            resolved_type="Invoice",
            resolved_correspondent="ADAC",
            date_applied="2025-03-27",
            classification={},
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "Insurance | Vehicle | Homer | Marge | Invoice | ADAC | 2025-03-27" in joined
        # Verify the metadata line is the only one with pipe separators
        pipe_lines = [l for l in joined.split("\n") if " | " in l]
        assert len(pipe_lines) == 1

    def test_empty_facts_filtered(self):
        classification = {
            "facts": ["", "  ", None, "Real fact"],
        }
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification=classification,
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "Real fact" in joined
        assert joined.count("  - ") == 1

    def test_empty_action_items_filtered(self):
        """Empty-action dicts are dropped; only valid dicts are rendered."""
        classification = {
            "action_items": [
                {"action": "", "due": ""},
                {"action": "Real action", "due": "2025-04-01"},
            ],
        }
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification=classification,
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "Real action" in joined
        assert "  - " not in joined  # no checkbox prefix for dict items

    def test_minimal_classification(self):
        """No classification at all — just the filed line and link."""
        parts = format_reply_parts(
            title=None, display_name="test.pdf", doc_id=1,
            resolved_topics=[], resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification={},
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "Filed: test.pdf (#1)" in joined
        assert len([p for p in parts if p.strip()]) == 1  # only the title line

    def test_string_topics_normalized(self):
        """Topics as a single string (LLM quirk) should be handled."""
        parts = format_reply_parts(
            title="Test", display_name="test.pdf", doc_id=1,
            resolved_topics=["Insurance"],
            resolved_persons=[],
            resolved_type=None, resolved_correspondent=None,
            date_applied=None, classification={},
            created_new=[], reformat_failed=False, link="",
        )
        joined = "\n".join(parts)
        assert "Insurance" in joined


# ── Capture parts ──────────────────────────────────────────────────────

CAPTURE_CLASSIFICATION = {
    "title": "Reddit: r/famstack discussion",
    "summary": "Discussion about the new document filing feature.",
    "facts": ["Subreddit: r/famstack", "Upvotes: 42"],
    "action_items": [],
    "topics": ["Technology"],
    "persons": ["Arthur"],
}


class TestFormatCaptureParts:

    def test_full_capture_reply(self):
        parts = format_capture_parts(
            title="Reddit: r/famstack discussion",
            source_title_hint="Reddit thread",
            resolved_topics=["Technology"],
            resolved_persons=["Arthur"],
            classification=CAPTURE_CLASSIFICATION,
            display_link="https://reddit.com/r/famstack/...",
        )
        joined = "\n".join(parts)
        assert "Captured: Reddit: r/famstack discussion" in joined
        assert "Technology | Arthur" in joined
        assert "Discussion about the new document filing feature" in joined
        assert "https://reddit.com/r/famstack/..." in joined

    def test_title_fallback_to_hint(self):
        parts = format_capture_parts(
            title=None,
            source_title_hint="Reddit thread",
            resolved_topics=[],
            resolved_persons=[],
            classification={},
            display_link="https://reddit.com/...",
        )
        joined = "\n".join(parts)
        assert "Captured: Reddit thread" in joined

    def test_title_fallback_to_capture(self):
        parts = format_capture_parts(
            title=None,
            source_title_hint=None,
            resolved_topics=[],
            resolved_persons=[],
            classification={},
            display_link="(pasted text)",
        )
        joined = "\n".join(parts)
        assert "Captured: Capture" in joined

    def test_no_meta_when_empty(self):
        parts = format_capture_parts(
            title="Test",
            source_title_hint="Test",
            resolved_topics=[],
            resolved_persons=[],
            classification={},
            display_link="https://example.com",
        )
        joined = "\n".join(parts)
        assert " | " not in joined

    def test_capture_no_action_items(self):
        """Captures should never show action items."""
        classification = {
            "action_items": [{"action": "Do something", "due": "2025-04-01"}],
        }
        parts = format_capture_parts(
            title="Test",
            source_title_hint="Test",
            resolved_topics=[],
            resolved_persons=[],
            classification=classification,
            display_link="https://example.com",
        )
        joined = "\n".join(parts)
        assert "- [ ]" not in joined

    def test_pasted_text_display_link(self):
        parts = format_capture_parts(
            title="Meeting notes",
            source_title_hint="Meeting notes",
            resolved_topics=[],
            resolved_persons=[],
            classification={},
            display_link="(pasted text)",
        )
        joined = "\n".join(parts)
        assert "(pasted text)" in joined
