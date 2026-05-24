"""Chat-reply rendering — pure layout, given a translator.

reply_presenter takes a `t(key, **kwargs)` callable and the
classification/enrichment data and returns the text the archivist
sends. Keeping it pure (no Matrix, no messages.yml) means we can pin
the layout AND prove translations flow through, in both languages,
without booting a bot. Supersedes the English-hardcoded classify_format.
"""

from __future__ import annotations

import sys
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from reply_presenter import render_filing_reply, render_capture_reply  # noqa: E402


def _en(key, **kw):
    """English-ish translator stub mirroring messages/archivist.yml shape."""
    templates = {
        "filed": "✅ Filed: {title} (#{doc_id})",
        "new_in_paperless": "🆕 New in Paperless: {items}",
        "reformat_failed": "Note: reformatting skipped — raw OCR kept.",
        "captured": "✅ Captured: {title}",
    }
    return templates.get(key, key).format(**kw)


def _de(key, **kw):
    templates = {
        "filed": "✅ Abgelegt: {title} (#{doc_id})",
        "captured": "✅ Gespeichert: {title}",
    }
    return templates.get(key, key).format(**kw)


FULL = {
    "summary": "Annual car insurance renewal at ADAC.",
    "facts": ["EUR 340.00/year", "Contract KFZ-2026"],
    "action_items": [
        {"action": "Pay invoice", "due": "2026-03-15"},
        {"action": "File copy", "due": ""},
    ],
}


class TestRenderFilingReply:

    def test_full_happy_path(self):
        out = render_filing_reply(
            _en,
            display_title="ADAC - Kfz-Versicherung",
            doc_id=10,
            resolved_topics=["Insurance"],
            resolved_persons=["Homer"],
            resolved_type="Invoice",
            resolved_correspondent="ADAC",
            date_applied="2026-03-15",
            classification=FULL,
            created_new=["Insurance"],
            reformat_failed=False,
            link="http://paperless/documents/10/details",
        )
        assert "✅ Filed: ADAC - Kfz-Versicherung (#10)" in out
        assert "Insurance | Homer | Invoice | ADAC | 2026-03-15" in out
        assert "Annual car insurance renewal at ADAC." in out
        assert "- EUR 340.00/year" in out
        assert "Pay invoice (due 2026-03-15)" in out
        assert "File copy" in out and "(due )" not in out
        assert "🆕 New in Paperless: Insurance" in out
        assert "http://paperless/documents/10/details" in out

    def test_translator_drives_language(self):
        out = render_filing_reply(
            _de, display_title="Rechnung", doc_id=7,
            resolved_topics=[], resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=False, link="",
        )
        assert "✅ Abgelegt: Rechnung (#7)" in out

    def test_no_meta_line_when_empty(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_topics=[], resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=False, link="",
        )
        assert " | " not in out

    def test_facts_capped_at_five(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_topics=[], resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={"facts": [f"f{i}" for i in range(10)]},
            created_new=[], reformat_failed=False, link="",
        )
        assert out.count("  - f") == 5

    def test_actions_capped_at_three(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_topics=[], resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={"action_items": [
                {"action": f"a{i}", "due": ""} for i in range(10)
            ]},
            created_new=[], reformat_failed=False, link="",
        )
        assert sum(1 for i in range(10) if f"a{i}" in out) == 3

    def test_reformat_failed_notice(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_topics=[], resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=True, link="",
        )
        assert "reformatting skipped" in out

    def test_link_omitted_when_empty(self):
        out = render_filing_reply(
            _en, display_title="x", doc_id=1,
            resolved_topics=[], resolved_persons=[], resolved_type=None,
            resolved_correspondent=None, date_applied=None,
            classification={}, created_new=[], reformat_failed=False, link="",
        )
        assert "http" not in out


class TestRenderCaptureReply:

    def test_full(self):
        out = render_capture_reply(
            _en,
            source_title_hint="Reddit thread",
            classification={
                "title": "Local LLM benchmarks",
                "topics": ["AI"], "persons": ["Arthur"],
                "summary": "M2 numbers.", "facts": ["60 tok/s"],
                "action_items": [{"action": "ignored for captures", "due": ""}],
            },
            link="https://reddit.com/r/LocalLLaMA/...",
        )
        assert "✅ Captured: Local LLM benchmarks" in out
        assert "AI | Arthur" in out
        assert "M2 numbers." in out
        assert "- 60 tok/s" in out
        assert "https://reddit.com/r/LocalLLaMA/..." in out

    def test_title_falls_back_to_hint_then_capture(self):
        out = render_capture_reply(
            _en, source_title_hint="Reddit thread",
            classification={}, link="(pasted text)",
        )
        assert "✅ Captured: Reddit thread" in out

        out2 = render_capture_reply(
            _en, source_title_hint=None, classification={}, link="(pasted text)",
        )
        assert "✅ Captured: Capture" in out2

    def test_link_footer_always_present(self):
        out = render_capture_reply(
            _en, source_title_hint="x", classification={}, link="(pasted text)",
        )
        assert out.rstrip().endswith("(pasted text)")
