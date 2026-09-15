"""`SourceContent` — the classifier's input, normalized across sources.

Promoted here from `stacklets/docs/bot/extractors.py` because it stopped
being one stacklet's type the moment a second consumer needed it: the
host CLI (`stack web fetch`) builds one without the archivist running at
all. Same move as `stack.email_message`, same reason -- the framework
owns the shared shape, each stacklet owns its own mapping into it.

The archivist's classifier does not care whether text arrived from a
photographed receipt that Paperless OCR'd, a pasted URL that trafilatura
rendered, or a wall of text somebody typed. This is what all of those
agree to produce.
"""

from __future__ import annotations

from dataclasses import dataclass


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
