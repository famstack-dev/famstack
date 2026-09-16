"""Source-content extractors — the OCR step, generalized.

The archivist's classifier doesn't care whether text came from a
photographed receipt processed by Paperless OCR, a pasted URL
processed by trafilatura, or a wall of text the user pasted directly.
This module owns the "produce a SourceContent from a source" step;
the pipeline downstream operates on the result.

Extractors today:

    UrlExtractor    Fetch + trafilatura — pasted links to web articles.
    TextExtractor   Pass-through for pasted text. Surfaces the first
                    URL in the body as `source_uri` so a Reddit-style
                    paste keeps a pointer back to the original.

The Paperless flow keeps its own ingest path inside the bot (upload →
poll → OCR) because it's deeply Paperless-shaped; unifying it into a
single backend would be churn without a real second consumer.

`SourceContent` and the whole URL-reading ladder now live in
`lib/stack/web/`. They moved the moment a second consumer appeared:
the host CLI reads a URL with no archivist running. `UrlExtractor`
stays here as the bot's binding to it — an aiohttp session in, a
`SourceContent` out — so nothing upstream had to change.
"""

from __future__ import annotations

import re

import aiohttp
from loguru import logger

# Email parsing is framework plumbing (stack.email_message); re-exported
# here so existing `from extractors import parse_email, ParsedEmail`
# callers and tests keep working after the move.
from stack.email_message import ParsedEmail, parse_email  # noqa: F401
# Same arrangement for the capture types: the framework owns the shared
# shape, this module owns the docs-side mapping into it.
from stack.web import FetchOutcome, SourceContent  # noqa: F401
from stack.web.fetch import aiohttp_transport, fetch_url


# ── Shared helpers ───────────────────────────────────────────────────────

# A single `<title>` element with no nesting and no attribute parsing —
# regex is the right tool. Avoids depending on a specific trafilatura
# metadata API surface that varies across versions.

_TITLE_RE = re.compile(
    r"<title[^>]*>([^<]+)</title>", re.IGNORECASE | re.DOTALL,
)


def _html_title(html: str) -> str | None:
    match = _TITLE_RE.search(html)
    if not match:
        return None
    title = match.group(1).strip()
    return title or None


# Permissive URL pattern for finding a source link inside pasted prose.
# Excludes trailing sentence punctuation (`.` `,` `;` `:` `!` `?` `)`)
# so `https://example.com/x.` stops before the period; permissive
# enough to keep query strings and fragments.

_URL_IN_TEXT_RE = re.compile(
    r"https?://[^\s<>\"'\]]+[^\s<>\"'\]\.,;:!?\)]+", re.IGNORECASE,
)


def _first_url(text: str) -> str | None:
    match = _URL_IN_TEXT_RE.search(text)
    return match.group(0) if match else None


# ── UrlExtractor ─────────────────────────────────────────────────────────

class UrlExtractor:
    """The bot's binding to the framework's fetch ladder.

    The reading itself — canonicalize, structured data, HTTP,
    extraction, and the quality gate — is `stack.web.fetch`. This
    supplies the one thing the framework deliberately does not have: a
    transport. The bot already keeps an aiohttp session open for its
    lifetime, so it hands that in rather than opening a second one.

    Two ways out, for two callers. `fetch` returns the full outcome
    including the gate's verdict, which is what the capture pipeline
    needs to explain a refusal to the family. `extract` keeps the older
    "content or nothing" shape for callers that only care whether there
    was something to file.

    Tier 3 (a real browser, in the optional `web` stacklet) is not
    wired up yet. Until it is, a challenge page is terminal and the
    family is told the site blocked us — which is the honest answer,
    and a better one than the fabricated entry they used to get.
    """

    def __init__(self, http: aiohttp.ClientSession, *, timeout: int = 30):
        self.http = http
        self.timeout = timeout

    async def fetch(self, url: str) -> FetchOutcome:
        """Read a URL and return a judged outcome. Never raises."""
        outcome = await fetch_url(
            url, transport=aiohttp_transport(self.http, timeout=self.timeout),
        )
        if outcome.ok:
            logger.info(
                "[extractor] {} → tier {} ({}), {} chars",
                url, outcome.tier, outcome.profile, len(outcome.content.text),
            )
        else:
            logger.info(
                "[extractor] {} → {}: {}",
                url, outcome.verdict.name, outcome.verdict.detail,
            )
        return outcome

    async def extract(self, url: str) -> SourceContent | None:
        """The fetched body, or None when there was nothing to file."""
        return (await self.fetch(url)).content


# ── TextExtractor ────────────────────────────────────────────────────────

class TextExtractor:
    """Pass-through extractor for pasted text.

    The body becomes SourceContent verbatim (whitespace trimmed). The
    first URL in the body — if any — surfaces as `source_uri` so the
    mirror can link back to the source the user typically attaches at
    the end of a Reddit-style paste. The first content-bearing line
    (skipping bare URL lines) becomes the title hint.

    Returns None for empty/whitespace-only input. The caller's job is
    to gate by length so chat-shaped messages don't reach the
    extractor.
    """

    _TITLE_HINT_MAX = 120

    async def extract(self, text: str) -> SourceContent | None:
        stripped = (text or "").strip()
        if not stripped:
            return None

        title_hint = self._pick_title_hint(stripped)
        source_uri = _first_url(stripped)

        return SourceContent(
            text=stripped,
            mime="text/plain",
            title_hint=title_hint,
            source_uri=source_uri,
        )

    def _pick_title_hint(self, text: str) -> str | None:
        """First non-blank line that isn't just a URL. Truncated so the
        slug stays filesystem-friendly."""
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            # Pure URL line — skip; the next line usually carries the title.
            if _URL_IN_TEXT_RE.fullmatch(line):
                continue
            return line[: self._TITLE_HINT_MAX]
        return None


# ── Email ────────────────────────────────────────────────────────────────
#
# Email is just another source. The `mail` container's himalaya has already
# fetched the message; this is the pure mapping from its parts into the
# classifier's `SourceContent`. No fetching, no I/O — fully testable on its
# own, and the seam ADR-010 wants every new source to slot into.

def email_to_source(
    *, subject: str | None, body: str, thread_root: str | None = None,
) -> SourceContent:
    """Map a fetched email into a `SourceContent`.

    The body is the text the classifier reads; the subject is the title
    hint; the *thread root* Message-ID becomes the canonical pointer as an
    RFC 2392 ``mid:`` URI. It keys the thread file, not the individual
    message — every reply folds into the same entry (ADR-010). Angle
    brackets are stripped; a blank subject or a missing thread root
    collapse to ``None`` so a Dataview `where resource` filters cleanly,
    same convention as the other capture sources.

    Parsing of the raw RFC822 bytes into a `ParsedEmail` lives in the
    framework (`stack.email_message`) so the mail bot, the host CLI, and
    this docs-side mapping all share one parser; this function is the
    docs-specific step that turns a parsed email into the classifier's
    `SourceContent`.
    """
    mid = (thread_root or "").strip().strip("<>").strip()
    return SourceContent(
        text=body,
        mime="text/plain",
        title_hint=(subject or "").strip() or None,
        source_uri=f"mid:{mid}" if mid else None,
    )
