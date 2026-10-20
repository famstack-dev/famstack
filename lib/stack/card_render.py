"""One layout per card type, written as markdown (ADR-016 §4).

A card type has one renderer, and it writes markdown. Each surface shows
that markdown its own way: the chat as the HTML Matrix allows
(`stack.matrix.html`), the card browser as HTML, the agent as it is. So a
note, a document or a diary entry reads the same wherever it is shown,
and a new card type needs one renderer instead of one per surface.

Every rendered card has the same shape:

    # <title>

    <one line of the facts that matter for its type: date · people · ...>

    <the type's own sections: summary, facts, to-dos, media, its text>

    <the person's own words, quoted, then the replies>

Each field appears once. The file carries more, for the tools that edit
it (ADR-017 §5): a heading, a meta block of links derived from the
fields, Obsidian callouts. The renderer reads past what repeats a field
and never changes the card. A type without a renderer of its own gets the
generic one: the title, the fields as a list, then the body.

    rendered = render(fields, body, names={"homer": "Homer"}, language="en")
    rendered.markdown      # the whole card
    rendered.below_title   # for a surface that shows the title itself

Stdlib only: the host CLI, the bots and the cards API all import it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .frontmatter import FrontmatterError, parse

Fields = Mapping[str, object]

# ── The rendered card ─────────────────────────────────────────────────


@dataclass(frozen=True)
class Rendered:
    """A card as markdown, in its three parts.

    `facts` is the one line under the title, "" when the type has none to
    show; `body` is everything after it.

        >>> r = Rendered("Zelt ist kaputt", "2026-10-13 · Homer", "Das Zelt ist gerissen.")
        >>> print(r.markdown, end="")
        # Zelt ist kaputt
        <BLANKLINE>
        2026-10-13 · Homer
        <BLANKLINE>
        Das Zelt ist gerissen.
    """

    title: str
    facts: str
    body: str

    @property
    def markdown(self) -> str:
        return _blocks(f"# {self.title}" if self.title else "", self.facts, self.body) + "\n"

    @property
    def below_title(self) -> str:
        return _blocks(self.facts, self.body)


def render(fields: Fields, body: str, *, names: Mapping[str, str] | None = None,
           language: str = "en") -> Rendered:
    """The card with frontmatter `fields` and markdown `body`, as its type shows it.

    `names` maps a person id to the name a reader knows them by
    (`{"homer": "Homer"}`); a person it does not name is shown as written.
    `language` is the household's, for the one label a card shows.
    """
    title = str(fields.get("title") or "").strip() or _first_heading(body)
    renderer = _RENDERERS.get(str(fields.get("type") or "").strip())
    if renderer is None:
        return Rendered(title, "", _blocks(_field_list(fields), _without_heading(body)))
    facts_of, layout = renderer
    card = _Card(fields, {str(k).lower(): v for k, v in (names or {}).items()}, _label(language))
    facts = " · ".join(p for p in facts_of(fields, card.names) if p)
    return Rendered(title, facts, layout(body, card))


def render_file(text: str, *, names: Mapping[str, str] | None = None,
                language: str = "en") -> Rendered:
    """The card file `text` (frontmatter and body), rendered.

    A file whose frontmatter does not parse is shown with the generic
    renderer and no fields, as the text it is.
    """
    try:
        fields = parse(text)
    except FrontmatterError:
        fields = {}
    return render(fields, split_body(text), names=names, language=language)


def split_body(text: str) -> str:
    """The body of a card file: what follows its frontmatter.

    >>> split_body("---\\ntype: note\\n---\\n\\nHello\\n")
    '\\nHello\\n'
    """
    if text.startswith("---\n") and (end := text.find("\n---\n", 3)) >= 0:
        return text[end + len("\n---\n"):]
    return text


# ── The facts line, per type ──────────────────────────────────────────
#
# Only what a reader needs to place the card: when, who, from whom, what
# kind. Tags are not here; a surface shows them as tags.

Names = Mapping[str, str]
FactsOf = Callable[[Fields, Names], list[str]]

# What a diary entry was recorded as, shown without a word in any language.
_MEDIUM = {"voice": "🎤", "image": "📷", "video": "🎬", "file": "📎"}


def _document_facts(f: Fields, names: Names) -> list[str]:
    paperless = str(f.get("paperless_id") or "").strip()
    resource = _text(f, "resource")
    number = f"[#{paperless}]({resource})" if paperless and resource else ""
    return [_text(f, "date"), _people(f, names), _text(f, "correspondent"),
            _text(f, "document_type"), number]


def _capture_facts(f: Fields, names: Names) -> list[str]:
    return [_text(f, "date"), _people(f, names)]


def _bookmark_facts(f: Fields, names: Names) -> list[str]:
    url = _text(f, "resource") or _text(f, "url")
    host = urlsplit(url).netloc.removeprefix("www.") if url.startswith("http") else ""
    return [_text(f, "date"), _people(f, names), f"[{host}]({url})" if host else ""]


def _diary_facts(f: Fields, names: Names) -> list[str]:
    medium = _text(f, "kind") or _text(f, "medium")
    return [_text(f, "date"), _people(f, names), _MEDIUM.get(medium, "")]


def _list_facts(f: Fields, _names: Names) -> list[str]:
    return [_text(f, "kind")]


def _declared_facts(f: Fields, _names: Names) -> list[str]:
    """A person's, a correspondent's or a topic's: the other names it goes by."""
    aliases = [*_strings(f.get("aliases")), *_strings(f.get("synonyms"))]
    account = _text(f, "account")
    return [", ".join(aliases), f"@{account}" if account else ""]


def _people(f: Fields, names: Names) -> str:
    """The people a card is about, by name: `persons` holds ids (ADR-017 §4)."""
    return ", ".join(_name(p, names) for p in _strings(f.get("persons")))


def _name(person: str, names: Names) -> str:
    """A person by the name a reader knows; an id nobody declared, capitalised."""
    if known := names.get(person.lower()):
        return known
    return person[:1].upper() + person[1:] if person.islower() else person


# ── The body, per kind of card ────────────────────────────────────────
#
# A record's file, as the writers lay it out: its heading, a meta block
# quoting its fields, the briefing callout (summary, facts, action items),
# media, its text, then the words people wrote (a caption, a transcript,
# the paste) and the replies. The layouts below read that and write the
# card's sections without what repeats a field.

@dataclass(frozen=True)
class _Card:
    """What a layout reads besides the body: the fields, who is who, the label."""

    fields: Fields
    names: Names
    todo: str


Layout = Callable[[str, _Card], str]

# Headings a capture or a diary card files the person's own words under.
_WORDS = ("Caption", "Transcript", "Text")
_REPLIES = "Replies"
_QUOTES = "Quotes"

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SECTION = re.compile(r"^##\s+(.+?)\s*$")
_COMMENT = re.compile(r"<!--.*?-->\n?|<a id=\"[^\"]*\"></a>", re.DOTALL)
_CALLOUT = re.compile(r"^\[!(\w+)\][-+]?\s*(.*)$")
_META_LINE = re.compile(r"^\*\*[^*\n]+\*\*")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_ONLY_LINK = re.compile(r"^\[[^\]]+\]\(([^)\s]+)\)$")
_MEDIA = re.compile(r"(?:^|/)media/")
_EMBED = re.compile(r"!\[\[[^\]]+\]\]")
_AUTHOR = re.compile(r"^\*\*(.+)\*\*$")


def _record_layout(body: str, card: _Card) -> str:
    """A document or an email: its sections in the order the file has them."""
    preamble, sections = _sections(_without_meta(_without_heading(_COMMENT.sub("", body))))
    blocks = [_unwrapped(preamble, card)]
    for heading, lines in sections:
        blocks += [f"## {heading}", _unwrapped(lines, card)]
    return _blocks(_file_links(body), *blocks)


def _captured_layout(body: str, card: _Card) -> str:
    """A note, a bookmark or a diary entry: the person's own words last.

    The words (a caption, a transcript, the paste) are quoted so that
    nothing in them reads as one of the card's sections, and the replies
    follow them, each under the name of who wrote it.
    """
    preamble, sections = _sections(_without_meta(_without_heading(_COMMENT.sub("", body))))
    own: list[str] = []
    rest = _unwrapped(preamble, card, words=own)
    blocks, replies = [rest], []
    for heading, lines in sections:
        if heading in _WORDS:
            own.append(_quoted(lines))
        elif heading == _REPLIES:
            replies.append(_by_name(lines, card.names))
        elif heading == _QUOTES:
            blocks.append(_trimmed(lines))
        else:
            blocks += [f"## {heading}", _unwrapped(lines, card, words=own)]
    has_media = bool(_EMBED.search(body))
    return _blocks(*blocks, "" if has_media else _file_links(body), *own, *replies)


def _list_layout(body: str, card: _Card) -> str:
    """A list: what it is for, then its items as the file has them."""
    return _blocks(_text(card.fields, "description"), _without_heading(_COMMENT.sub("", body)))


def _declared_layout(body: str, _card: _Card) -> str:
    """A person, a correspondent or a topic: what the family wrote about it."""
    return _without_heading(_COMMENT.sub("", body)).strip()


_RENDERERS: dict[str, tuple[FactsOf, Layout]] = {
    "document": (_document_facts, _record_layout),
    "email": (_capture_facts, _record_layout),
    "note": (_capture_facts, _captured_layout),
    "bookmark": (_bookmark_facts, _captured_layout),
    "diary": (_diary_facts, _captured_layout),
    "list": (_list_facts, _list_layout),
    "person": (_declared_facts, _declared_layout),
    "correspondent": (_declared_facts, _declared_layout),
    "topic": (_declared_facts, _declared_layout),
}


# ── Reading the file's blocks ─────────────────────────────────────────

def _without_heading(body: str) -> str:
    """`body` without its first line when that is a heading: the title is a field."""
    lines = body.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None and (m := _HEADING.match(lines[first])) and len(m[1]) == 1:
        del lines[first]
    return "\n".join(lines)


def _without_meta(body: str) -> str:
    """`body` without the meta block a record opens with (`> **About** …`)."""
    lines = body.split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is None or not lines[first].startswith(">"):
        return body
    end = first
    while end < len(lines) and lines[end].startswith(">"):
        end += 1
    quoted = [ln.lstrip(">").strip() for ln in lines[first:end]]
    if all(_META_LINE.match(q) for q in quoted if q):
        del lines[first:end]
    return "\n".join(lines)


def _file_links(body: str) -> str:
    """The meta block's links to files in the media archive, one per line.

    They are the one part of the meta block that is no field: a record
    keeps its upload there when the file is not shown in the page.
    """
    lines = _without_heading(body).split("\n")
    first = next((i for i, ln in enumerate(lines) if ln.strip()), len(lines))
    found = []
    for ln in lines[first:]:
        if not ln.startswith(">"):
            break
        found += [f"[{label}]({href})" for label, href in _LINK.findall(ln) if _MEDIA.search(href)]
    return "\n".join(found)


def _sections(body: str) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """The lines before the first `## ` heading, and each section after it."""
    preamble: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    for ln in body.split("\n"):
        if m := _SECTION.match(ln):
            sections.append((m[1], []))
        elif sections:
            sections[-1][1].append(ln)
        else:
            preamble.append(ln)
    return preamble, sections


def _unwrapped(lines: list[str], card: _Card, *, words: list[str] | None = None) -> str:
    """`lines` with each Obsidian callout written as plain markdown.

    The briefing (`> [!summary]`) becomes its summary, facts and to-dos.
    Any other callout is a quote; when `words` is given it is the person's
    own (a note's paste) and moves there, to be shown last.
    """
    out: list[str] = []
    i = 0
    while i < len(lines):
        callout = _CALLOUT.match(lines[i].removeprefix(">").strip()) \
            if lines[i].startswith(">") else None
        if callout is None:
            out.append(lines[i])
            i += 1
            continue
        end = i + 1
        while end < len(lines) and lines[end].startswith(">"):
            end += 1
        inner = [ln[2:] if ln.startswith("> ") else ln[1:] for ln in lines[i + 1:end]]
        if callout[1].lower() == "summary":
            out += ["", _briefing(inner, card), ""]
        elif words is not None:
            words.append(_quoted(inner))
        else:
            out += ["", _quoted(inner), ""]
        i = end
    return _trimmed(out)


def _briefing(inner: list[str], card: _Card) -> str:
    """The briefing callout's content as the card's sections.

    A link that only repeats the card's `resource` (the document in
    Paperless) is left out: the facts line carries it.
    """
    summary: list[str] = []
    facts: list[str] = []
    tasks: list[str] = []
    for paragraph in "\n".join(inner).split("\n\n"):
        head, _, rest = paragraph.strip().partition("\n")
        if head == "**Facts**":
            facts += _bullets(rest)
        elif head == "**Action items**":
            tasks += _bullets(rest)
        elif (link := _ONLY_LINK.match(head)) and not rest and link[1] == _text(card.fields, "resource"):
            continue
        elif paragraph.strip():
            summary.append(paragraph.strip())
    return _blocks(*summary, "\n".join(facts),
                   f"**{card.todo}**\n\n" + "\n".join(tasks) if tasks else "")


def _by_name(lines: list[str], names: Names) -> str:
    """Replies with each author's line (`**homer**`) naming them as a reader knows them."""
    return _trimmed([f"**{_name(m[1], names)}**" if (m := _AUTHOR.match(ln.strip())) else ln
                     for ln in lines])


def _bullets(text: str) -> list[str]:
    return [ln.strip() for ln in text.split("\n") if ln.strip().startswith("- ")]


def _quoted(lines: list[str]) -> str:
    """Words as a quote, blank lines included, so it stays one quote."""
    text = _trimmed(lines)
    return "\n".join(f"> {ln}" if ln.strip() else ">" for ln in text.split("\n")) if text else ""


def _field_list(fields: Fields) -> str:
    """Every field but the title, one per line, for the generic renderer."""
    rows = []
    for key, value in fields.items():
        shown = ", ".join(_strings(value)) if isinstance(value, list) else str(value).strip()
        if key != "title" and shown:
            rows.append(f"- **{key}**: {shown}")
    return "\n".join(rows)


def _first_heading(body: str) -> str:
    for ln in body.split("\n"):
        if m := _HEADING.match(ln):
            return m[2]
    return ""


# ── Small parts ───────────────────────────────────────────────────────

# The one label a card shows: over the to-dos, which would otherwise run
# into the facts list above them. In the household's language.
_TODO = {"en": "To do", "de": "Zu erledigen"}


def _label(language: str) -> str:
    return _TODO.get((language or "").strip().lower()[:2], _TODO["en"])


def _text(fields: Fields, key: str) -> str:
    value = fields.get(key)
    return "" if value is None or isinstance(value, (list, dict)) else str(value).strip()


def _strings(value: object) -> list[str]:
    values = value if isinstance(value, list) else [value] if value else []
    return [str(v).strip() for v in values if str(v).strip()]


def _trimmed(lines: list[str]) -> str:
    """`lines` joined, without blank lines at either end or more than one in a row."""
    text = re.sub(r"\n{3,}", "\n\n", "\n".join(ln.rstrip() for ln in lines))
    return text.strip("\n")


def _blocks(*blocks: str) -> str:
    """The non-empty `blocks`, one blank line between each."""
    return "\n\n".join(b.strip("\n") for b in blocks if b and b.strip())


# ── For the chat ──────────────────────────────────────────────────────
#
# The chat shows no images from outside the room, and a link into the
# memory repo opens nothing there. So a rendered card goes to the chat
# with its media as a link and its links to other cards as their names.

_WIKI_EMBED = re.compile(r"!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]")
# A link or an image (`![alt](src)`), which the chat shows as a link too.
_ANY_LINK = re.compile(r"(!?)\[([^\]]+)\]\(([^)\s]+)\)")
_WEB = re.compile(r"^(?:https?|mailto):")


def for_chat(markdown: str, *, card_link: str = "") -> str:
    """`markdown` as the chat can show it.

    A media embed becomes a link to the card it is shown in (`card_link`),
    or its file name when there is none; a link to a file of the memory
    repo becomes its label. Web links stay.

        >>> for_chat("![[/media/2026/10/a.png]]", card_link="https://home.example/go/note-1")
        '📎 [a.png](https://home.example/go/note-1)'
        >>> for_chat("**About** [Marge](../../people/marge.md)")
        '**About** Marge'
    """
    def media(name: str) -> str:
        return f"📎 [{name}]({card_link})" if card_link else f"📎 {name}"

    text = _WIKI_EMBED.sub(lambda m: media(m[1].strip().rsplit("/", 1)[-1]), markdown)

    def link(m: re.Match[str]) -> str:
        label, href = m[2], m[3]
        if _WEB.match(href):
            return m[0]
        return media(label) if m[1] or _MEDIA.search(href) else label

    return _ANY_LINK.sub(link, text)
