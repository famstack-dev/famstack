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

trafilatura is imported lazily so a Python test environment that
doesn't exercise URL extraction doesn't need the dep installed. In
production the bot-runner image always carries it (declared in
`stacklets/core/bot-runner/requirements.txt`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import message_from_bytes
from email.policy import default as _email_policy
from email.utils import parseaddr, parsedate_to_datetime

import aiohttp
from loguru import logger


# ── SourceContent ────────────────────────────────────────────────────────

@dataclass
class SourceContent:
    """The classifier's input, normalized across source types.

    `text` is the body the classifier reads — Markdown when the
    extractor can produce it, plain text otherwise. `title_hint` is
    whatever the source advertised as a title (HTML `<title>`, first
    body line, filename); the classifier may overwrite it with
    something more useful. `source_uri` is the canonical pointer
    back to the origin (`https://...`, `paperless://42`,
    `matrix:<event-id>` — caller decides the scheme), captured into
    the mirror's frontmatter for round-tripping. None means the
    capture has no upstream pointer (a pure pasted note).
    """
    text: str
    mime: str = "text/plain"
    title_hint: str | None = None
    source_uri: str | None = None


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
    """Fetch a URL and convert the HTML body to Markdown via trafilatura.

    Failure paths return None, never raise — the caller renders a
    single "couldn't capture this" reply regardless of whether the
    server 500'd, the host was unreachable, or trafilatura couldn't
    find a body. Logging surfaces the distinction for debugging.

    Non-HTML content types are rejected at the gate. PDFs reach the
    archivist through a separate path (`_handle_url`) that uploads to
    Paperless; this extractor's job is web articles only.
    """

    def __init__(self, http: aiohttp.ClientSession, *, timeout: int = 30):
        self.http = http
        self.timeout = timeout

    async def extract(self, url: str) -> SourceContent | None:
        html = await self._fetch_html(url)
        if html is None:
            return None

        try:
            import trafilatura
        except ImportError:
            logger.error(
                "[extractor] trafilatura not installed — "
                "URL captures require the bot-runner image",
            )
            return None

        body = trafilatura.extract(
            html, output_format="markdown",
            include_links=True, include_images=False,
            include_tables=True,
            favor_precision=True,
        )
        if not body or not body.strip():
            logger.info("[extractor] {} → trafilatura returned no body", url)
            return None

        return SourceContent(
            text=body.strip(),
            mime="text/html",
            title_hint=_html_title(html),
            source_uri=url,
        )

    async def _fetch_html(self, url: str) -> str | None:
        """GET the URL. Returns the body text on success, None on any
        failure (non-200, non-HTML, transport error)."""
        try:
            async with self.http.get(
                url,
                timeout=aiohttp.ClientTimeout(total=self.timeout),
                allow_redirects=True,
                headers={"User-Agent": "famstack-archivist/1.0"},
            ) as resp:
                if resp.status != 200:
                    logger.info(
                        "[extractor] {} → HTTP {}", url, resp.status,
                    )
                    return None
                ctype = (resp.content_type or "").lower()
                if not (
                    ctype.startswith("text/html")
                    or ctype.startswith("application/xhtml")
                ):
                    logger.info(
                        "[extractor] {} → non-HTML content_type={}",
                        url, ctype,
                    )
                    return None
                return await resp.text()
        except (aiohttp.ClientError, OSError) as e:
            logger.warning("[extractor] {} → fetch failed: {}", url, e)
            return None


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
    """
    mid = (thread_root or "").strip().strip("<>").strip()
    return SourceContent(
        text=body,
        mime="text/plain",
        title_hint=(subject or "").strip() or None,
        source_uri=f"mid:{mid}" if mid else None,
    )


@dataclass
class ParsedEmail:
    """The fields the capture pipeline needs from one email message."""

    subject: str | None
    from_name: str | None
    from_addr: str | None
    message_id: str | None
    date: str | None   # captured_at, YYYY-MM-DD
    body: str
    references: list[str] = field(default_factory=list)
    in_reply_to: str | None = None

    @property
    def thread_root(self) -> str | None:
        """The Message-ID the conversation is keyed by.

        A reply carries `References` (ancestors, oldest first); its first
        entry is the thread root. Failing that, the immediate parent
        (`In-Reply-To`). A message that starts a thread has neither, so it
        is its own root. This is the vault entry's identity — every message
        in a conversation folds into the file keyed by this id (ADR-010).
        """
        if self.references:
            return self.references[0]
        if self.in_reply_to:
            return self.in_reply_to
        return self.message_id


def parse_email(raw: bytes) -> ParsedEmail:
    """Parse an RFC822 message (a Maildir file's bytes) into a `ParsedEmail`.

    himalaya syncs IMAP into a Maildir of plain RFC822 files; the mail bot
    reads those *as bytes* and parses here rather than via himalaya's
    rendered JSON, so the contract is the standard email format + stdlib
    `email` (modern `default` policy), not himalaya's output quirks. Bytes
    in (not str) so declared charsets decode correctly — a UTF-8 body
    round-trips. The plain-text body is preferred, falling back to HTML
    content. Missing headers collapse to None / "" — the classifier copes.
    """
    msg = message_from_bytes(raw, policy=_email_policy)

    from_name, from_addr = parseaddr(msg["from"] or "")
    mid = (msg["message-id"] or "").strip().strip("<>").strip() or None

    captured_at = None
    if msg["date"]:
        try:
            captured_at = parsedate_to_datetime(str(msg["date"])).date().isoformat()
        except (TypeError, ValueError):
            captured_at = None

    in_reply = _parse_msgids(msg["in-reply-to"])
    return ParsedEmail(
        subject=(msg["subject"] or "").strip() or None,
        from_name=(from_name or "").strip() or None,
        from_addr=(from_addr or "").strip() or None,
        message_id=mid,
        date=captured_at,
        body=_email_body(msg),
        references=_parse_msgids(msg["references"]),
        in_reply_to=in_reply[0] if in_reply else None,
    )


_MSGID_RE = re.compile(r"<([^<>]+)>")


def _parse_msgids(header) -> list[str]:
    """Bare Message-IDs from a References / In-Reply-To header (angle-bracketed)."""
    if not header:
        return []
    return [m.strip() for m in _MSGID_RE.findall(str(header)) if m.strip()]


def _email_body(msg) -> str:
    """Best-effort plain-text body from an EmailMessage (default policy)."""
    part = msg.get_body(preferencelist=("plain", "html"))
    target = part if part is not None else msg
    try:
        content = target.get_content()
    except (KeyError, LookupError):
        payload = target.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else (payload or "")
    return (content or "").strip()
