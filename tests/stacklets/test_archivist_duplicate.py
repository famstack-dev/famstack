"""Duplicate-detection regex for Paperless task FAILURE results.

The archivist parses Paperless task failure messages so it can tell a
content-hash duplicate rejection apart from a real OCR/OCR-config
failure. These tests lock in the exact patterns Paperless emits so a
future Paperless upgrade doesn't silently regress us back to the
misleading 'OCR failed' message.
"""

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "core" / "bot-runner"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from archivist import _DUPLICATE_RE, PaperlessDuplicateError  # noqa: E402


class TestDuplicateRegex:
    """Pin the shape Paperless actually produces in `task.result`."""

    def test_standard_message(self):
        result = ("DB_Ticket_829506319010.pdf: Not consuming DB_Ticket_829506319010.pdf: "
                  "It is a duplicate of EVG - Online-Ticket 25,70 EUR 08.03.2026 (#3).")
        m = _DUPLICATE_RE.search(result)
        assert m is not None
        assert m.group(1) == "EVG - Online-Ticket 25,70 EUR 08.03.2026"
        assert int(m.group(2)) == 3

    def test_trash_suffix_does_not_break_match(self):
        """Paperless adds 'Note: existing document is in the trash.' sometimes."""
        result = ("foo.pdf: Not consuming foo.pdf: It is a duplicate of "
                  "Insurance 2026 (#12). Note: existing document is in the trash.")
        m = _DUPLICATE_RE.search(result)
        assert m is not None
        assert m.group(1) == "Insurance 2026"
        assert int(m.group(2)) == 12

    def test_title_with_special_chars(self):
        result = "foo.pdf: It is a duplicate of Müller & Co. — Rechnung 2024/Q1 (#42)."
        m = _DUPLICATE_RE.search(result)
        assert m is not None
        assert m.group(1) == "Müller & Co. — Rechnung 2024/Q1"
        assert int(m.group(2)) == 42

    def test_unrelated_failure_does_not_match(self):
        """Generic OCR / tesseract errors must not look like duplicates."""
        result = "OCR failed: tesseract returned non-zero exit code"
        assert _DUPLICATE_RE.search(result) is None

    def test_empty_string(self):
        assert _DUPLICATE_RE.search("") is None


class TestDuplicateError:
    def test_carries_doc_id_and_title(self):
        e = PaperlessDuplicateError(3, "EVG - Online-Ticket")
        assert e.doc_id == 3
        assert e.title == "EVG - Online-Ticket"
        assert "duplicate of #3" in str(e)
