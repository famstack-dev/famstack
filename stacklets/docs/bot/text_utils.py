"""Pure text utilities — filename cleaning, reply stripping, URL detection.

No I/O, no env vars, no Matrix knowledge. Just bytes and strings.

Extracted from archivist.py so each function can be unit-tested in
isolation and reused by the deriver (Phase 2) without pulling in the
bot framework."""


import re

# Regex to detect a message that is just a URL (no other text)
URL_PATTERN = re.compile(r'^https?://[^\s/]+\.[^\s/]+(/\S*)?$')

# Google Docs/Sheets/Slides URL patterns → export as PDF
GOOGLE_DOC_PATTERNS = {
    re.compile(r'https://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)'): "document",
    re.compile(r'https://docs\.google\.com/spreadsheets/d/([a-zA-Z0-9_-]+)'): "spreadsheets",
    re.compile(r'https://docs\.google\.com/presentation/d/([a-zA-Z0-9_-]+)'): "presentation",
}


def clean_filename(raw_filename: str, msgtype: str = "") -> str:
    """Strip UUIDs, tildes, and other noise from filenames for display.

    Handles Element's auto-UUID filenames (e.g.
    ``a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.jpg``) and replaces them
    with a sensible fallback based on the extension.

    >>> clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890~1.jpg")
    'photo.jpg'
    >>> clean_filename("a1b2c3d4-e5f6-7890-abcd-ef1234567890.pdf")
    'document.pdf'
    >>> clean_filename("Rechnung_ADAC_Marz2025.pdf")
    'Rechnung_ADAC_Marz2025.pdf'
    >>> clean_filename("IMG_1234.HEIC")
    'photo.HEIC'
    >>> clean_filename("document")
    'document'
    >>> clean_filename("note.md")
    'note.md'
    """
    clean = re.sub(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}~?\d*\.?',
        '',
        raw_filename,
    )
    # No noise detected → return as-is.
    if clean == raw_filename:
        return clean
    # UUID was stripped. clean may be empty or just the extension.
    # Map to a sensible display name.
    ext = raw_filename.rsplit(".", 1)[-1] if "." in raw_filename else ""
    if not clean or clean == ext:
        if ext.lower() in ("jpg", "jpeg", "png", "tiff", "heic"):
            return f"photo.{ext}"
        elif ext.lower() == "pdf":
            return "document.pdf"
        elif ext:
            return f"file.{ext}"
        else:
            return "document"
    return clean


def strip_reply_fallback(body: str) -> str:
    """Drop Matrix's reply-quote fallback from a message body.

    A reply in Element/most clients arrives as:

        > <@homer:test.local> first line of the quoted message
        > second line of the quoted message

        the user's actual reply

    The leading ``>``-prefixed block is the fallback Matrix injects so
    clients without rich-reply support still show the context. We
    want only the user's real text — what comes after the first
    blank line that follows the quoted block.

    >>> strip_reply_fallback("> <@homer:test.local> first line\\n> second line\\n\\nthe user's reply")
    "the user's reply"
    >>> strip_reply_fallback("just regular text, no reply")
    'just regular text, no reply'
    >>> strip_reply_fallback("> quoted line\\n\\n")
    ''
    >>> strip_reply_fallback("")
    ''
    >>> strip_reply_fallback("> line 1\\n> line 2\\n> line 3")
    ''
    """
    lines = body.split("\n")
    idx = 0
    while idx < len(lines) and lines[idx].startswith(">"):
        idx += 1
    while idx < len(lines) and not lines[idx].strip():
        idx += 1
    return "\n".join(lines[idx:]).strip()


def looks_like_paste(text: str) -> bool:
    """Heuristic: distinguish a paste from short chat in a capture room.

    Length-based threshold (~100 stripped chars). Deliberate
    undershoot: users who want short notes captured will pad them
    with context. Chat ("ok", "thanks!", "yes\\nno") falls below
    the gate and is ignored. A bare URL is handled by the URL
    path before this predicate runs.

    >>> looks_like_paste("ok")
    False
    >>> looks_like_paste("thanks!")
    False
    >>> looks_like_paste("A" * 100)
    True
    >>> looks_like_paste("A" * 99)
    False
    >>> looks_like_paste("   ")
    False
    >>> looks_like_paste("")
    False
    """
    return len(text.strip()) >= 100


def google_docs_export_url(url: str) -> tuple[str, str] | None:
    """If URL is a Google Docs link, return (export_url, doc_type). None otherwise.

    >>> google_docs_export_url("https://docs.google.com/document/d/abc123/edit")
    ('https://docs.google.com/document/d/abc123/export?format=pdf', 'document')
    >>> google_docs_export_url("https://docs.google.com/document/d/abc123")
    ('https://docs.google.com/document/d/abc123/export?format=pdf', 'document')
    >>> google_docs_export_url("https://docs.google.com/spreadsheets/d/xyz789/edit")
    ('https://docs.google.com/spreadsheets/d/xyz789/export?format=pdf', 'spreadsheets')
    >>> google_docs_export_url("https://docs.google.com/presentation/d/pres001")
    ('https://docs.google.com/presentation/d/pres001/export?format=pdf', 'presentation')
    >>> google_docs_export_url("https://example.com/file.pdf")
    None
    >>> google_docs_export_url("not a url")
    None
    >>> google_docs_export_url("")
    None
    """
    for pattern, doc_type in GOOGLE_DOC_PATTERNS.items():
        match = pattern.search(url)
        if match:
            doc_id = match.group(1)
            export_url = (
                f"https://docs.google.com/{doc_type}/d/{doc_id}/export?format=pdf"
            )
            return export_url, doc_type
    return None


def is_just_url(text: str) -> bool:
    """True when the text is a bare URL with no other content.

    Used to distinguish a URL paste (archive it) from a chat message
    that happens to contain a link (ignore it).

    >>> is_just_url("https://example.com/file.pdf")
    True
    >>> is_just_url("https://example.com/path?q=1#frag")
    True
    >>> is_just_url("Check this out: https://example.com")
    False
    >>> is_just_url("https://example.com but with text")
    False
    >>> is_just_url("hello world")
    False
    >>> is_just_url("")
    False
    """
    return bool(URL_PATTERN.match(text))
