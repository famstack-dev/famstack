"""Unit tests for the archivist's git mirror.

Covers the pure methods — filename generation, slug normalization,
frontmatter shape, commit trailer format, markdown assembly. Forgejo
HTTP interactions are exercised live in integration tests, not
stubbed here.
"""

from __future__ import annotations

import sys
import yaml
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from git_mirror import GitMirror  # noqa: E402


@pytest.fixture
def mirror(tmp_path):
    """GitMirror wired enough to exercise its pure methods."""
    return GitMirror(
        code_url="http://stack-code:3000",
        admin_user="stackadmin",
        admin_password="secret",
        admin_usernames=["homer"],
        data_dir=tmp_path,
        paperless_version="2.14.5",
    )


# ── Slug normalization ─────────────────────────────────────────────────────

class TestSlug:
    def test_ascii(self, mirror):
        assert mirror._slug("ADAC Rechnung Marz 2026") == "adac-rechnung-marz-2026"

    def test_umlauts_normalize(self, mirror):
        # Non-ASCII (ü, ä, ö) becomes their base letters after NFKD decompose
        assert mirror._slug("Müller Straße") == "muller-strasse" or mirror._slug("Müller Straße") == "muller-strae"

    def test_punctuation_collapses(self, mirror):
        assert mirror._slug("Kwik-E-Mart, Inc.") == "kwik-e-mart-inc"

    def test_empty_fallback(self, mirror):
        assert mirror._slug("") == "document"

    def test_length_cap(self, mirror):
        long_title = "a" * 200
        assert len(mirror._slug(long_title)) == 60


# ── Filepath construction ──────────────────────────────────────────────────

class TestFilepath:
    def test_ai_with_date(self, mirror):
        path = mirror._filepath(
            date="2026-03-15", paperless_id=247,
            title="ADAC Rechnung", has_ai=True,
        )
        assert path == "2026/03/2026-03-15-adac-rechnung-p247.md"

    def test_ai_without_date_goes_to_unfiled(self, mirror):
        path = mirror._filepath(
            date=None, paperless_id=247,
            title="ADAC Rechnung", has_ai=True,
        )
        assert path == "_unfiled/adac-rechnung-p247.md"

    def test_ai_with_invalid_date_falls_through(self, mirror):
        path = mirror._filepath(
            date="not-a-date", paperless_id=42,
            title="A", has_ai=True,
        )
        assert path == "_unfiled/a-p42.md"

    def test_no_ai_with_date(self, mirror):
        path = mirror._filepath(
            date="2026-03-15", paperless_id=42, title=None, has_ai=False,
        )
        assert path == "2026/03/2026-03-15-p42.md"

    def test_no_ai_without_date(self, mirror):
        path = mirror._filepath(
            date=None, paperless_id=42, title=None, has_ai=False,
        )
        assert path == "_unfiled/p42.md"


# ── Frontmatter ────────────────────────────────────────────────────────────

class TestFrontmatter:
    def test_ai_full(self, mirror):
        fm = mirror._frontmatter(
            title="ADAC Rechnung März 2026",
            date="2026-03-15",
            correspondent="ADAC",
            document_type="Invoice",
            category="Insurance",
            persons=["Homer"],
            tags=["Insurance", "Person: Homer"],
            paperless_id=247,
            paperless_url="http://docs.home.local/documents/247",
            processing="ai",
            model="qwen2.5:14b",
        )
        assert fm["title"] == "ADAC Rechnung März 2026"
        assert fm["paperless_id"] == 247
        assert fm["processing"] == "ai"
        assert fm["model"] == "qwen2.5:14b"
        assert fm["paperless_version"] == "2.14.5"
        assert fm["source"] == "paperless"
        assert fm["added"].endswith("Z")
        # key order: title first, added last, key ordering reflects insertion
        assert list(fm.keys())[0] == "title"
        assert list(fm.keys())[-1] == "added"

    def test_ocr_only_omits_model(self, mirror):
        fm = mirror._frontmatter(
            title="Untitled", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=99,
            paperless_url="", processing="ocr_only", model=None,
        )
        assert fm["processing"] == "ocr_only"
        assert "model" not in fm
        assert "correspondent" not in fm
        assert "persons" not in fm

    def test_no_paperless_version_when_unset(self, tmp_path):
        m = GitMirror(
            code_url="", admin_user="", admin_password="",
            admin_usernames=[], data_dir=tmp_path,
        )
        fm = m._frontmatter(
            title="t", date=None,
            correspondent=None, document_type=None, category=None,
            persons=[], tags=[], paperless_id=1,
            paperless_url="", processing="ai", model="x",
        )
        assert "paperless_version" not in fm


# ── Commit message ─────────────────────────────────────────────────────────

class TestCommitMessage:
    def test_learn_with_model(self, mirror):
        msg = mirror._commit_message(
            verb="learn", title="ADAC Rechnung",
            paperless_id=247, processing="ai", model="qwen2.5:14b",
        )
        lines = msg.split("\n")
        assert lines[0] == "learn: ADAC Rechnung"
        assert lines[1] == ""
        assert "Paperless-Id: 247" in lines
        assert "Processing: ai" in lines
        assert "Model: qwen2.5:14b" in lines

    def test_update_without_model(self, mirror):
        msg = mirror._commit_message(
            verb="update", title="x", paperless_id=1,
            processing="ocr_only", model=None,
        )
        assert msg.startswith("update: x\n\n")
        assert "Paperless-Id: 1" in msg
        assert "Processing: ocr_only" in msg
        assert "Model:" not in msg


# ── Render (full markdown) ─────────────────────────────────────────────────

class TestRender:
    def test_full_document(self, mirror):
        fm = {
            "title": "ADAC Rechnung", "paperless_id": 247,
            "processing": "ai", "source": "paperless",
        }
        out = mirror._render(
            frontmatter=fm,
            body="Policy number: KFZ-2024-XXX\n\nAmount: EUR 340.",
            correspondent="ADAC",
            persons=["Homer"],
        )
        # Frontmatter fenced with ---
        assert out.startswith("---\n")
        # Parseable YAML block
        fm_block = out.split("---", 2)[1]
        parsed = yaml.safe_load(fm_block)
        assert parsed["paperless_id"] == 247

        assert "# ADAC Rechnung" in out
        assert "**From:** [[ADAC]]" in out
        assert "**About:** [[Homer]]" in out
        assert "Policy number: KFZ-2024-XXX" in out

    def test_no_wiki_header_when_no_entities(self, mirror):
        out = mirror._render(
            frontmatter={"title": "t"}, body="body",
            correspondent=None, persons=[],
        )
        assert "[[" not in out
        assert "# t" in out
        assert "body" in out
