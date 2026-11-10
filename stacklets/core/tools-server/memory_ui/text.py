"""A card's markdown as safe HTML, for the memory's pages.

A card's text comes from outside: a pasted email, a web page's summary, a
message from the chat. So the text is escaped before anything else touches
it, and only the tags written here appear in the HTML. No HTML a card
carries reaches the page, and a link only becomes one when it goes to the
web, the memory's media or another card.

    html = to_html("**Buy** charcoal\\n\\n- [ ] Duff", here="homer/notes/grill.md")
    # '<p><strong>Buy</strong> charcoal</p><ul><li class="task">...'

What it knows: paragraphs, headings, bullet, numbered and task lists,
quotes, Obsidian callouts (a folded one as a closed `<details>`), tables,
bold, italics, code, links, and the media a record embeds
(`![[/media/2026/10/<event id>.png]]`, ADR-017 §3) as an image, a player
or a file link. It is the subset the card renderers write
(`stack.card_render`), the same the card browser's script drew before the
pages were rendered on the server.

Why its own renderer, not the `markdown` package: that package passes raw
HTML and `javascript:` links through, and the tools server carries no
dependency for a page this small.

Links: the web opens in a new tab; a link to another file of the memory
goes to `/card/<its path>`, which the server sends on to that card's
address (`pages.landing`); a link to the media archive goes to the
cards API's media route.

Pure: no I/O.
"""

from __future__ import annotations

import re
from html import escape, unescape

from memory_ui.addresses import card_address, media_address

__all__ = ["to_html", "vault_path"]


def to_html(text: str, *, here: str = "") -> str:
    """`text` (markdown) as HTML; `here` is the card's path in the memory,
    which relative links start from.

        >>> to_html("Hello <b>you</b>")
        '<p>Hello &lt;b&gt;you&lt;/b&gt;</p>'
        >>> to_html("[Duff](../people/homer.md)", here="family/documents/x.md")
        '<p><a class="internal" href="/card/family/people/homer.md">Duff</a></p>'
        >>> to_html("[x](javascript:alert)")
        '<p>x</p>'
    """
    text = re.sub(r"<!--.*?-->|<a id=\"[^\"]*\"></a>", "", str(text or ""), flags=re.DOTALL)
    return _Blocks(here).render(text.split("\n"))


def vault_path(href: str, here: str) -> str:
    """The memory path `href` points at from the card at `here`; "" for the
    web or an anchor.

        >>> vault_path("../../correspondents/duff.md", "family/documents/2026/x.md")
        'family/correspondents/duff.md'
        >>> vault_path("https://krusty.example", "x.md")
        ''
    """
    if re.match(r"^[a-z]+:", href, re.IGNORECASE) or href.startswith("#"):
        return ""
    path = href.split("#")[0]
    if not path:
        return ""
    if not path.startswith("/"):
        parts = here.split("/")[:-1]
        for piece in path.split("/"):
            if piece == "..":
                if parts:
                    parts.pop()
            elif piece != ".":
                parts.append(piece)
        path = "/".join(parts)
    return path.lstrip("/")


# ── Blocks ─────────────────────────────────────────────────────────────────

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*+]\s+(?:\[([ xX])\]\s+)?(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_ROW = re.compile(r"^\s*\|.*\|\s*$")
_RULE_CELL = re.compile(r"^:?-{2,}:?$")
_CALLOUT = re.compile(r"^\[!(\w+)\]([-+]?)\s*(.*)$")


class _Blocks:
    """One pass over the lines, collecting the open paragraph, list, table
    or quote, and writing each out when the next kind of line starts."""

    def __init__(self, here: str):
        self.here = here
        self.html: list[str] = []
        self.para: list[str] = []
        self.list = ""
        self.table: list[list[str]] = []
        self.quoted: list[str] = []

    def render(self, lines: list[str]) -> str:
        for raw in lines:
            self._line(raw.rstrip())
        self._paragraph()
        self._close_list()
        self._quote()
        self._table()
        return "".join(self.html)

    def _line(self, line: str) -> None:
        quote = _QUOTE.match(line)
        if not quote:
            self._quote()
        row = bool(_ROW.match(line))
        if not row:
            self._table()
        if not line.strip():
            self._paragraph()
            self._close_list()
        elif row:
            self._paragraph()
            self._close_list()
            self.table.append([c.strip() for c in line.strip().strip("|").split("|")])
        elif heading := _HEADING.match(line):
            self._paragraph()
            self._close_list()
            tag = "h3" if len(heading[1]) <= 2 else "h4"
            self.html.append(f"<{tag}>{_inline(heading[2], self.here)}</{tag}>")
        elif (bullet := _BULLET.match(line)) or _NUMBERED.match(line):
            self._item(bullet, _NUMBERED.match(line))
        elif quote:
            self._paragraph()
            self._close_list()
            self.quoted.append(quote[1])
        else:
            self._close_list()
            self.para.append(line.strip())

    def _item(self, bullet: re.Match | None, numbered: re.Match | None) -> None:
        self._paragraph()
        numbered = None if bullet else numbered
        want = "ul" if bullet else "ol"
        if self.list != want:
            self._close_list()
            self.html.append(f"<{want}>")
            self.list = want
        text = _inline(bullet[2] if bullet else numbered[1] if numbered else "", self.here)
        box = bullet[1] if bullet else None
        if box is None:
            self.html.append(f"<li>{text}</li>")
        else:
            done = "" if box == " " else " done"
            self.html.append(f'<li class="task{done}"><span class="box" aria-hidden="true"></span>'
                             f'<span class="t">{text}</span></li>')

    def _paragraph(self) -> None:
        if self.para:
            self.html.append(f"<p>{_inline(' '.join(self.para), self.here)}</p>")
        self.para = []

    def _close_list(self) -> None:
        if self.list:
            self.html.append(f"</{self.list}>")
        self.list = ""

    def _table(self) -> None:
        """A markdown table: its header row, the rule under it, then the rows."""
        rows = [r for r in self.table if not all(_RULE_CELL.match(c) for c in r)]
        self.table = []
        if not rows:
            return
        head, *body = rows
        cells = lambda row, tag: "".join(f"<{tag}>{_inline(c, self.here)}</{tag}>" for c in row)  # noqa: E731
        self.html.append('<div class="table"><table><thead><tr>' + cells(head, "th") + "</tr></thead>"
                         "<tbody>" + "".join(f"<tr>{cells(r, 'td')}</tr>" for r in body)
                         + "</tbody></table></div>")

    def _quote(self) -> None:
        """A quote, or an Obsidian callout (`> [!quote]- Original paste`): a
        labelled box whose `-` starts it folded and `+` open."""
        quoted, self.quoted = self.quoted, []
        if not quoted:
            return
        callout = _CALLOUT.match(quoted[0])
        if not callout:
            self.html.append(f"<blockquote>{_Blocks(self.here).render(quoted)}</blockquote>")
            return
        kind, fold, title = callout.groups()
        label = f'<span class="label">{_inline(title.strip(), self.here) if title.strip() else escape(kind)}</span>'
        inner = _Blocks(self.here).render(quoted[1:])
        if fold:
            opened = " open" if fold == "+" else ""
            self.html.append(f'<details class="callout"{opened}><summary>{label}</summary>{inner}</details>')
        else:
            self.html.append(f'<div class="callout">{label}{inner}</div>')


# ── Inline ─────────────────────────────────────────────────────────────────
# Media and links are held out of the text until the end, so the emphasis
# below never reads into an address (an event id's underscores are no
# emphasis).

_EMBED = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_WIKILINK = re.compile(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_AUTOLINK = re.compile(r"&lt;(https?://[^\s&]+(?:&amp;[^\s&]+)*)&gt;")
_HELD = re.compile("\x00(\\d+)\x00")
_IMAGES = {"png", "jpg", "jpeg", "gif", "webp", "heic"}
_AUDIO = {"m4a", "mp3", "wav", "ogg"}
_VIDEO = {"mp4", "mov", "webm"}


def _inline(text: str, here: str) -> str:
    """One line's markup as HTML: escaped first, then links, media and emphasis."""
    held: list[str] = []

    def hold(html: str) -> str:
        held.append(html)
        return f"\x00{len(held) - 1}\x00"

    out = escape(text, quote=True)
    out = _EMBED.sub(lambda m: hold(_media(f)) if (f := _media_file(_unescape(m[1].strip()), here))
                     else m[0], out)
    out = _IMAGE.sub(lambda m: f'<span class="figure">▣ {m[1] or "image"}</span>', out)
    out = _WIKILINK.sub(lambda m: hold(_card_link(_unescape(m[1].strip()), m[2] or m[1], here)), out)
    out = _LINK.sub(lambda m: hold(_link(m[1], _unescape(m[2]), here)), out)
    out = _AUTOLINK.sub(lambda m: hold(_web(m[1], re.sub(r"^https?://", "", m[1]))), out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*|__([^_]+)__", lambda m: f"<strong>{m[1] or m[2]}</strong>", out)
    out = re.sub(r"(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)", r"\1<em>\2</em>", out)
    return _HELD.sub(lambda m: held[int(m[1])], out)


def _link(label: str, href: str, here: str) -> str:
    """A markdown link: the web, the media archive or another card; else its label."""
    if re.match(r"^https?://", href):
        return _web(escape(href, quote=True), label)
    if file := _media_file(href, here):
        return f'<a href="{escape(media_address(file), quote=True)}" target="_blank" rel="noopener">{label}</a>'
    return _card_link(href, label, here) if vault_path(href, here) else label


def _web(href: str, label: str) -> str:
    return f'<a class="external" href="{href}" target="_blank" rel="noopener noreferrer">{label}</a>'


def _card_link(target: str, label: str, here: str) -> str:
    path = vault_path(target, here)
    if not path:
        return label
    if not path.endswith(".md") and "." not in path.rsplit("/", 1)[-1]:
        path += ".md"
    return f'<a class="internal" href="{card_address(path)}">{label}</a>'


def _media_file(href: str, here: str) -> str:
    """The file inside the media archive a link points at (`2026/10/x.png`), or ""."""
    path = vault_path(href, here)
    ok = path.startswith("media/") and re.fullmatch(r"[\w./-]+", path) and ".." not in path.split("/")
    return path.removeprefix("media/") if ok else ""


def _media(file: str) -> str:
    """A media file as the page shows it: an image, a player, or a link."""
    ext = file.rsplit(".", 1)[-1].lower()
    src = escape(media_address(file), quote=True)
    name = escape(file.rsplit("/", 1)[-1])
    if ext in _IMAGES:
        return f'<img class="media" src="{src}" alt="{name}" loading="lazy">'
    if ext in _AUDIO:
        return f'<audio class="media" controls preload="metadata" src="{src}"></audio>'
    if ext in _VIDEO:
        return f'<video class="media" controls preload="metadata" src="{src}"></video>'
    return f'<a class="file" href="{src}" target="_blank" rel="noopener">{name} ↗</a>'


def _unescape(text: str) -> str:
    """An address as written, from the escaped line it was found in."""
    return unescape(text)
