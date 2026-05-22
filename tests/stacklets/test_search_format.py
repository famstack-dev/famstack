"""Search result formatters — memory + Paperless hits as Matrix blocks.

The formatting is what the family sees in chat, so it needs to be
predictable across the cases we care about: with and without a public
URL, with and without an excerpt, with and without persons. Pure
functions, no Matrix needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "docs" / "bot"))

from search_format import (  # noqa: E402
    format_paperless_hit,
    format_memory_hit,
    paperless_doc_url,
    memory_doc_url,
)


# ── URL builders ────────────────────────────────────────────────────────

class TestMemoryDocUrl:

    def test_returns_empty_without_public_url(self):
        # Internal container hostnames are useless from a phone; we'd
        # rather render no link than a broken one.
        assert memory_doc_url("family/documents/foo.md") == ""

    def test_builds_forgejo_blob_url(self):
        url = memory_doc_url(
            "marge/notes/2026/02/elternabend.md",
            code_public_url="https://code.example",
        )
        assert url == "https://code.example/family/memory/src/branch/main/marge/notes/2026/02/elternabend.md"

    def test_strips_trailing_slash_on_base(self):
        url = memory_doc_url(
            "a.md", code_public_url="https://code.example/",
        )
        assert "https://code.example/family/" in url
        assert "//family/" not in url

    def test_respects_custom_org(self):
        # Households on a custom mirror_org (e.g. "smith") get URLs
        # under that org, not the default "family".
        url = memory_doc_url(
            "a.md", code_public_url="https://c", mirror_org="smith",
        )
        assert "/smith/memory/" in url


class TestPaperlessDocUrl:

    def test_returns_empty_without_public_url(self):
        assert paperless_doc_url(42) == ""

    def test_returns_empty_without_doc_id(self):
        # A doc without an id is malformed; surface that as "no link"
        # so the formatter falls back to a bold title.
        assert paperless_doc_url(None, public_url="https://p") == ""

    def test_builds_detail_page_url(self):
        url = paperless_doc_url(42, public_url="https://paperless.example")
        assert url == "https://paperless.example/documents/42/details"


# ── Memory hit formatting ────────────────────────────────────────────────

class TestFormatMemoryHit:

    @staticmethod
    def _hit(**overrides) -> dict:
        # A representative memory hit shape -- one of each metadata
        # field, plus an excerpt. Tests tweak individual fields.
        return {
            "rel": "homer/notes/2026/02/radlager.md",
            "title": "Radlager Mecha Mike",
            "date": "2026-02-10",
            "persons": ["Homer"],
            "tags": ["Topic:Car"],
            "excerpt": "Brummen aus dem linken Hinterrad",
        } | overrides

    def test_includes_linked_title_when_public_url_set(self):
        out = format_memory_hit(
            self._hit(), 1,
            code_public_url="https://code.example",
        )
        # The title becomes the link text, the URL points to the blob.
        assert "[Radlager Mecha Mike](https://code.example/family/memory/src/branch/main/homer/notes/2026/02/radlager.md)" in out
        # The path doesn't appear separately when the link carries it.
        assert "`homer/notes/" not in out

    def test_falls_back_to_path_when_no_public_url(self):
        out = format_memory_hit(self._hit(), 1)
        # Bold title, then path in backticks on the next line as the
        # locator the human can copy-paste into `stack memory show`.
        assert "**Radlager Mecha Mike**" in out
        assert "`homer/notes/2026/02/radlager.md`" in out

    def test_meta_line_includes_date_and_persons(self):
        out = format_memory_hit(
            self._hit(), 1, code_public_url="https://c",
        )
        # Date and persons join with the middle-dot, separated from
        # the title by an em-dash.
        assert "— 2026-02-10 · Homer" in out

    def test_meta_omits_empty_segments(self):
        # A doc without persons skips the · separator -- avoids
        # awkward "2026-02-10 · " trailing nothing.
        out = format_memory_hit(
            self._hit(persons=[]), 1, code_public_url="https://c",
        )
        assert "— 2026-02-10" in out
        # No bullet hanging off the end.
        assert "·" not in out.split("\n", 1)[0]

    def test_excerpt_rendered_as_italic_indented_line(self):
        # The excerpt is visual quiet text: italic + two-space indent
        # so it groups under the title block.
        out = format_memory_hit(self._hit(), 1)
        assert "   _Brummen aus dem linken Hinterrad_" in out

    def test_no_excerpt_no_excerpt_line(self):
        # A hit whose excerpt extraction came back empty (e.g.
        # query only hit the frontmatter) renders title + meta only.
        out = format_memory_hit(
            self._hit(excerpt=""), 1, code_public_url="https://c",
        )
        # Two-space-italic shouldn't appear at all.
        assert "   _" not in out

    def test_title_falls_back_to_path_when_missing(self):
        # A memory file without a `title:` frontmatter key still
        # surfaces -- the relative path is the readable identity.
        out = format_memory_hit(
            self._hit(title=""), 1, code_public_url="https://c",
        )
        assert "homer/notes/2026/02/radlager.md" in out

    def test_numbering_is_one_indexed(self):
        # The synthesis step will eventually cite `[1]`, `[2]`,
        # matching the numbering the human reads in chat.
        out = format_memory_hit(self._hit(), 7)
        assert out.startswith("7. ")


# ── Paperless hit formatting ────────────────────────────────────────────

class TestFormatPaperlessHit:

    @staticmethod
    def _doc(**overrides) -> dict:
        return {
            "id": 42,
            "title": "Allianz KFZ Versicherung 2026",
            "created": "2026-01-15T08:00:00Z",
        } | overrides

    def test_includes_linked_title_with_public_url(self):
        out = format_paperless_hit(
            self._doc(), 1, public_url="https://paperless.example",
        )
        assert "[Allianz KFZ Versicherung 2026](https://paperless.example/documents/42/details)" in out

    def test_meta_carries_date_and_doc_id(self):
        out = format_paperless_hit(
            self._doc(), 1, public_url="https://p",
        )
        # The created stamp is truncated to YYYY-MM-DD; #id is the
        # second metadata segment.
        assert "— 2026-01-15 · #42" in out

    def test_falls_back_to_bold_title_without_url(self):
        out = format_paperless_hit(self._doc(), 1)
        assert "**Allianz KFZ Versicherung 2026**" in out
        assert "https://" not in out

    def test_untitled_doc_renders_placeholder(self):
        # Paperless allows untitled uploads -- the formatter must
        # surface them rather than erroring out, because the user
        # may be looking specifically for "the one I forgot to name".
        out = format_paperless_hit(
            {"id": 9, "title": "", "created": "2026-01-01T00:00:00Z"}, 1,
        )
        assert "**Untitled**" in out

    def test_numbering_is_one_indexed(self):
        out = format_paperless_hit(self._doc(), 5)
        assert out.startswith("5. ")
