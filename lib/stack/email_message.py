"""Email parsing — RFC822 bytes into the fields the brain needs.

Framework plumbing, not bot-specific. Email is an ingestion channel like the
Matrix interface itself, so the primitives live in `stack.*` where every
surface can import them: the mail bot (core), the archivist's capture pipeline
(docs), the host CLI, and the tests. Pure stdlib `email`, no I/O.

`parse_email` takes *bytes* (a Maildir file, an IMAP `RFC822` fetch) so declared
charsets decode correctly. Thread identity is resolved here: `thread_root` is
the Message-ID every reply folds into, the vault entry's identity per ADR-010.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import message_from_bytes
from email.policy import default as _email_policy
from email.utils import parseaddr, parsedate_to_datetime


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
    # The IMAP UID this message was fetched at, when known. The fetcher sets
    # it so the bot can advance its per-folder watermark; it is None for
    # transports without UIDs (a Maildir file) and irrelevant to parsing.
    uid: int | None = None

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

    The mail bot reads messages *as bytes* and parses here against the
    standard email format + stdlib `email` (modern `default` policy), not any
    mail client's rendered output. Bytes in (not str) so declared charsets
    decode correctly — a UTF-8 body round-trips. The plain-text body is
    preferred, falling back to HTML content. Missing headers collapse to
    None / "" — the classifier copes.
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
    """Best-effort readable body from an EmailMessage (default policy).

    Prefers the plain-text part when present. An HTML-only message is
    converted to Markdown (`html2text`) so the room and the vault get clean,
    link-preserving text rather than a wall of tags — most non-trivial mail
    is HTML-only. Conversion failures degrade to the raw HTML, never an
    exception.
    """
    part = msg.get_body(preferencelist=("plain", "html"))
    target = part if part is not None else msg
    try:
        content = target.get_content()
    except (KeyError, LookupError):
        payload = target.get_payload(decode=True)
        content = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else (payload or "")
    content = content or ""
    ctype = target.get_content_type() if hasattr(target, "get_content_type") else "text/plain"
    if ctype == "text/html":
        content = _html_to_markdown(content)
    return content.strip()


_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\((https?://[^)\s]+)\)")
_BARE_URL_RE = re.compile(r"(?<![`(])\bhttps?://[^\s)`<>\]]+")


def defang_links(text: str) -> str:
    """Reveal URLs as non-clickable plaintext (anti-phishing).

    A phishing email hides a hostile URL behind friendly link text
    ("[Your bank](https://evil.example)"). Defanging surfaces the *real*
    URL next to the label and wraps every URL in a Markdown code span, so
    neither Matrix nor Obsidian auto-links it — the reader sees where it
    actually points and can't click it by reflex.

    `[label](url)` -> ``label (`url`)``; a bare `url` -> `` `url` ``. The URL
    text is preserved (still copyable, reproducible), only its clickability
    is removed. Applied at render surfaces; the verbatim `raw_content` kept
    on the source event stays untouched.
    """
    if not text:
        return text

    def _link(m: "re.Match") -> str:
        label, url = m.group(1).strip(), m.group(2)
        return f"{label} (`{url}`)" if label else f"`{url}`"

    text = _MD_LINK_RE.sub(_link, text)
    return _BARE_URL_RE.sub(lambda m: f"`{m.group(0)}`", text)


def _html_to_markdown(html: str) -> str:
    """Convert an HTML email body to Markdown.

    `html2text` keeps headings, links, and emphasis while dropping styling
    and (configured here) images — email signatures and tracking pixels are
    noise. Falls back to the raw HTML if the library is unavailable, so the
    body is never lost.
    """
    try:
        import html2text
    except ImportError:
        return html
    h = html2text.HTML2Text()
    h.body_width = 0       # don't hard-wrap; let the renderer reflow
    h.ignore_images = True  # drop logos, tracking pixels, layout images
    return h.handle(html)
