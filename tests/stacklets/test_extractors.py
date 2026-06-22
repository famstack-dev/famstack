"""Tests for the source-content extractor layer.

The archivist used to assume one input shape — a file uploaded to
Matrix that Paperless OCRs into text. With pasted URLs treated as
first-class captures, we need a generic "produce SourceContent from a
source" step. This module pins the contract for the URL backend.

trafilatura runs against fixture HTML in-process — no network access.
HTTP transport is exercised against pytest-httpserver, so the fetch +
parse pipeline goes end-to-end without leaving the test runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

import aiohttp
import pytest

_BOT_DIR = Path(__file__).resolve().parent.parent.parent / "stacklets" / "docs" / "bot"
sys.path.insert(0, str(_BOT_DIR))

from extractors import (  # noqa: E402
    SourceContent,
    TextExtractor,
    UrlExtractor,
    email_to_source,
    parse_email,
)


# ── Fixture HTML ─────────────────────────────────────────────────────────

ARTICLE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
    <title>Why local LLMs matter</title>
    <meta name="description" content="A short note on local inference.">
</head>
<body>
    <header><nav>Home | About | Archive</nav></header>
    <main>
        <article>
            <h1>Why local LLMs matter</h1>
            <p>Running models on your own hardware changes the privacy
            calculus. Your prompts never leave the machine, the model
            never phones home, and you can iterate without quotas.</p>
            <p>For families, this means a server in the closet replaces
            three cloud subscriptions. The math works out at the four-
            month mark, give or take a power bill.</p>
            <h2>Apple Silicon as the default</h2>
            <p>A Mac Mini at idle draws under 10 watts. That's
            cheaper than a Raspberry Pi cluster and faster than every
            x86 mini-PC at the same price point.</p>
        </article>
    </main>
    <footer>© 2026 example.com — subscribe to the RSS feed</footer>
</body>
</html>"""


# ── Tests ────────────────────────────────────────────────────────────────

@pytest.fixture
async def http():
    """A real aiohttp ClientSession — the same shape the bot hands the extractor."""
    async with aiohttp.ClientSession() as session:
        yield session


class TestUrlExtractorHappyPath:
    """A 200 HTML response produces SourceContent with Markdown body + title."""

    async def test_returns_source_content_for_html_article(self, http, httpserver):
        httpserver.expect_request("/article").respond_with_data(
            ARTICLE_HTML, content_type="text/html; charset=utf-8",
        )
        url = httpserver.url_for("/article")

        extractor = UrlExtractor(http)
        content = await extractor.extract(url)

        assert isinstance(content, SourceContent)
        assert content.source_uri == url
        assert content.mime == "text/html"
        # Title is what `<title>` advertised — not the H1, not the body.
        assert content.title_hint == "Why local LLMs matter"
        # Body is Markdown — the article paragraph survived extraction.
        assert "local inference" not in content.text  # meta desc is stripped
        assert "Running models on your own hardware" in content.text
        # Boilerplate is gone — nav and footer don't bleed into the body.
        assert "Home | About | Archive" not in content.text
        assert "subscribe to the RSS feed" not in content.text

    async def test_markdown_preserves_headings(self, http, httpserver):
        httpserver.expect_request("/article").respond_with_data(
            ARTICLE_HTML, content_type="text/html",
        )

        content = await UrlExtractor(http).extract(httpserver.url_for("/article"))

        # An H2 inside the article becomes a Markdown heading. trafilatura
        # outputs `## ...` for sub-section headings in markdown mode.
        assert "Apple Silicon as the default" in content.text


class TestUrlExtractorFailureModes:
    """Failure paths return None, never raise — the chat reply handler
    distinguishes None from SourceContent and sends the right message."""

    async def test_http_404_returns_none(self, http, httpserver):
        httpserver.expect_request("/missing").respond_with_data(
            "not found", status=404,
        )
        result = await UrlExtractor(http).extract(httpserver.url_for("/missing"))
        assert result is None

    async def test_http_500_returns_none(self, http, httpserver):
        httpserver.expect_request("/broken").respond_with_data(
            "boom", status=500,
        )
        result = await UrlExtractor(http).extract(httpserver.url_for("/broken"))
        assert result is None

    async def test_non_html_content_type_returns_none(self, http, httpserver):
        # A pasted .zip / .png / arbitrary binary URL — trafilatura can't
        # do anything useful with it, so we reject it at the content-type
        # gate. The PDF path is a separate flow (existing `_handle_url`).
        httpserver.expect_request("/binary").respond_with_data(
            b"\x89PNG\r\n\x1a\n...", content_type="image/png",
        )
        result = await UrlExtractor(http).extract(httpserver.url_for("/binary"))
        assert result is None

    async def test_unreachable_host_returns_none(self, http):
        # Connection refused — port 1 is reserved as never-binds-here.
        result = await UrlExtractor(http).extract("http://127.0.0.1:1/article")
        assert result is None

    async def test_empty_html_returns_none(self, http, httpserver):
        # trafilatura returns nothing for a doc with no extractable body.
        httpserver.expect_request("/empty").respond_with_data(
            "<html><body></body></html>", content_type="text/html",
        )
        result = await UrlExtractor(http).extract(httpserver.url_for("/empty"))
        assert result is None


class TestUrlExtractorTitleHandling:
    """Title is best-effort — when the HTML has none, the SourceContent
    still carries the body and `title_hint=None` so the classifier can
    invent a title from the content."""

    async def test_no_title_tag_yields_none_title(self, http, httpserver):
        html = (
            "<html><body><article>"
            "<p>" + ("Standalone body paragraph with enough words to "
                    "exceed trafilatura's minimum length threshold and "
                    "produce a real extraction result. " * 4) + "</p>"
            "</article></body></html>"
        )
        httpserver.expect_request("/no-title").respond_with_data(
            html, content_type="text/html",
        )

        content = await UrlExtractor(http).extract(httpserver.url_for("/no-title"))

        assert content is not None
        assert content.title_hint is None
        assert "Standalone body paragraph" in content.text

    async def test_title_is_stripped(self, http, httpserver):
        html = (
            "<html><head><title>\n  Padded Title  \n</title></head>"
            "<body><article><p>"
            + ("Body text long enough for trafilatura to keep. " * 8)
            + "</p></article></body></html>"
        )
        httpserver.expect_request("/padded-title").respond_with_data(
            html, content_type="text/html",
        )

        content = await UrlExtractor(http).extract(
            httpserver.url_for("/padded-title"),
        )

        assert content is not None
        assert content.title_hint == "Padded Title"


# ── TextExtractor ────────────────────────────────────────────────────────
#
# Pass-through for pasted content. The body becomes SourceContent as-is.
# The Reddit-paste pattern is the load-bearing case: a wall of text with
# a source URL somewhere in the body. The extractor surfaces that URL as
# `source_uri` so the mirror can link back to the source, and pulls the
# first non-URL line as a title hint for the slug.

class TestTextExtractor:
    """Passes text through, surfaces the first URL it finds (if any),
    and picks a title hint from the first content-bearing line."""

    async def test_passes_text_through(self):
        body = "First line title\n\nSome body paragraph here.\n"
        content = await TextExtractor().extract(body)
        assert content is not None
        assert "Some body paragraph here." in content.text
        assert content.mime == "text/plain"
        assert content.title_hint == "First line title"
        assert content.source_uri is None

    async def test_extracts_url_as_source_uri(self):
        body = (
            "Interesting thread on local inference:\n\n"
            "https://reddit.com/r/LocalLLaMA/comments/abc123\n\n"
            "Top comment quotes 8B at 60 tok/s on M2 Pro."
        )
        content = await TextExtractor().extract(body)
        assert content is not None
        assert (
            content.source_uri
            == "https://reddit.com/r/LocalLLaMA/comments/abc123"
        )

    async def test_title_hint_skips_url_lines(self):
        # When the URL is on its own line at the top, the title hint
        # falls through to the next non-blank non-URL line.
        body = (
            "https://example.com/article\n"
            "\n"
            "The actual title here\n"
            "\n"
            "Body content..."
        )
        content = await TextExtractor().extract(body)
        assert content is not None
        assert content.title_hint == "The actual title here"

    async def test_title_hint_capped(self):
        # A very long first line gets truncated so it can become a
        # filesystem-friendly slug without ballooning the filename.
        long_first = "A " * 200  # 400 chars
        body = long_first + "\n\nbody"
        content = await TextExtractor().extract(body)
        assert content is not None
        assert len(content.title_hint) <= 120

    async def test_empty_returns_none(self):
        assert await TextExtractor().extract("") is None
        assert await TextExtractor().extract("   \n\n  ") is None

    async def test_strips_leading_trailing_whitespace(self):
        # Matrix clients sometimes prepend/append newlines on paste.
        # The body in SourceContent should be trimmed so the briefing
        # block doesn't render against a stack of blank lines.
        content = await TextExtractor().extract("\n\n\nReal content here.\n\n\n")
        assert content is not None
        assert content.text == "Real content here."

    async def test_finds_first_url_when_multiple(self):
        body = (
            "Discussion: https://example.com/article-1 vs "
            "https://example.com/article-2 — first one is better."
        )
        content = await TextExtractor().extract(body)
        # The first URL in the body wins. Heuristic, not authoritative:
        # the user's source URL is usually pasted first.
        assert content.source_uri == "https://example.com/article-1"

    async def test_url_alongside_text_stays_in_body(self):
        # The body is the user's paste verbatim — we don't strip the
        # URL out, it stays in the rendered capture's body. `source_uri`
        # is the metadata pointer; the URL in the body is the user's
        # text exactly as written.
        body = "Read this: https://example.com/x and tell me what you think."
        content = await TextExtractor().extract(body)
        assert "https://example.com/x" in content.text
        assert content.source_uri == "https://example.com/x"


# ── Email mapping ──────────────────────────────────────────────────────────

class TestEmailToSource:
    """`email_to_source` maps fetched email parts into SourceContent.

    Pure mapping, no I/O — the himalaya container already fetched the
    message. The thread root becomes an RFC 2392 `mid:` pointer that keys
    the thread file (every reply folds into the same entry)."""

    def test_maps_subject_body_and_thread_root(self):
        s = email_to_source(
            subject="Elternabend am Freitag",
            body="Bitte Formular zurücksenden.",
            thread_root="<abc123@school.example>",
        )
        assert isinstance(s, SourceContent)
        assert s.text == "Bitte Formular zurücksenden."
        assert s.title_hint == "Elternabend am Freitag"
        assert s.source_uri == "mid:abc123@school.example"

    def test_strips_angle_brackets_from_thread_root(self):
        s = email_to_source(subject="x", body="y", thread_root="  <id@h>  ")
        assert s.source_uri == "mid:id@h"

    def test_blank_subject_is_none(self):
        s = email_to_source(subject="   ", body="y", thread_root="<i@h>")
        assert s.title_hint is None

    def test_missing_thread_root_has_no_source_uri(self):
        s = email_to_source(subject="x", body="y", thread_root=None)
        assert s.source_uri is None
        s2 = email_to_source(subject="x", body="y", thread_root="")
        assert s2.source_uri is None


# ── RFC822 parsing ─────────────────────────────────────────────────────────

_EML = """From: Springfield School <office@springfield-school.example>
To: family@example.org
Subject: Elternabend am Freitag
Message-ID: <abc123@springfield-school.example>
Date: Fri, 20 Jun 2026 10:00:00 +0000
Content-Type: text/plain; charset=utf-8

Bitte das Formular bis Freitag zurücksenden.
"""


class TestParseEmail:
    """`parse_email` reads a Maildir RFC822 file with the stdlib email
    module — the robust ingestion contract, independent of himalaya's
    rendered output. Pinned against himalaya 1.2.0's Maildir files."""

    def test_extracts_all_fields(self):
        p = parse_email(_EML.encode("utf-8"))
        assert p.subject == "Elternabend am Freitag"
        assert p.from_name == "Springfield School"
        assert p.from_addr == "office@springfield-school.example"
        assert p.message_id == "abc123@springfield-school.example"
        assert p.date == "2026-06-20"
        assert p.body.strip() == "Bitte das Formular bis Freitag zurücksenden."

    def test_no_content_type_defaults_to_plain(self):
        eml = (
            "From: a@b.example\nSubject: Hi\n"
            "Message-ID: <x@y>\n\nplain body here\n"
        )
        p = parse_email(eml.encode("utf-8"))
        assert p.body.strip() == "plain body here"
        assert p.from_name is None  # no display name
        assert p.from_addr == "a@b.example"

    def test_missing_headers_collapse_to_none(self):
        p = parse_email(b"\njust a body, no headers\n")
        assert p.subject is None
        assert p.message_id is None
        assert p.date is None
        assert "just a body" in p.body

    def test_multipart_prefers_plain(self):
        eml = (
            "From: a@b.example\nSubject: multi\nMessage-ID: <m@id>\n"
            'Content-Type: multipart/alternative; boundary="B"\n\n'
            "--B\nContent-Type: text/plain\n\nthe plain part\n"
            "--B\nContent-Type: text/html\n\n<p>the html part</p>\n--B--\n"
        )
        p = parse_email(eml.encode("utf-8"))
        assert "the plain part" in p.body
        assert "<p>" not in p.body


class TestThreadRoot:
    """A thread folds into one vault entry keyed by its root Message-ID
    (ADR-010). References[0] is the root; else In-Reply-To; else the
    message starts its own thread."""

    def test_standalone_message_is_its_own_root(self):
        p = parse_email(b"Message-ID: <solo@h>\nSubject: x\n\nbody\n")
        assert p.references == []
        assert p.in_reply_to is None
        assert p.thread_root == "solo@h"

    def test_references_first_entry_is_root(self):
        eml = (
            b"Message-ID: <reply2@h>\n"
            b"In-Reply-To: <reply1@h>\n"
            b"References: <root@h> <reply1@h>\n"
            b"Subject: Re: x\n\nlater\n"
        )
        p = parse_email(eml)
        assert p.references == ["root@h", "reply1@h"]
        assert p.in_reply_to == "reply1@h"
        assert p.thread_root == "root@h"

    def test_in_reply_to_when_no_references(self):
        eml = (
            b"Message-ID: <reply1@h>\nIn-Reply-To: <root@h>\n"
            b"Subject: Re: x\n\nfirst reply\n"
        )
        p = parse_email(eml)
        assert p.thread_root == "root@h"
