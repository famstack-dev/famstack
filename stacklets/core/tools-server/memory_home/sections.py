"""The sections of the memory's pages, each drawn from one part of the overview.

The memory answers a page's overview with one part per section
(`stacklets/memory/overview/`, "The answer"); each function here draws one
of them, in the card pages' look:

    up_next(answer, context)       what comes up, soonest first
    lists(answer, context)         the lists with open items: title and how many are open
    topics(answer, context)        the person's topics as tiles
    cards(answer, context)         the page's cards, newest first
    changed(answer, context)       what people changed in the last two weeks
    bookmarks(answer, context)     the bookmarks, with their site
    documents(answer, context)     one chip per kind of document, most first
    links(answer, context)         the people, topics and senders the page's cards name
    type_chips(answer, context)    how many of the page's cards are of each type

Every section is a `<section>` named by its part (`data-section="lists"`),
with its heading, and says so in one line when it has nothing: a page keeps
its shape, so a person learns where things will be. The links section is
the one exception: it has nothing to say when the cards name no one.

The sections draw what the memory answered and count nothing themselves:
a count on the page is the memory's count, so it agrees with the page it
links to.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape
from urllib.parse import urlsplit

from memory_home import addresses
from memory_home.items import Context, by_line, card_link, person_name, row, type_icon
from memory_home.words import counted, words
from memory_ui.addresses import card_address
from memory_ui.words import day_of, plain_day

__all__ = ["up_next", "lists", "topics", "cards", "changed", "bookmarks", "documents", "links",
           "type_chips", "section", "rows", "listed"]

# How many lines "What changed" shows; its heading counts them all.
CHANGED_SHOWN = 8
# How many bookmarks a page shows; the documents page and search show the rest.
BOOKMARKS_SHOWN = 8


# ── The sections ─────────────────────────────────────────────────────────

def up_next(answer: dict, context: Context) -> str:
    """What comes up: a card's own date ahead, or a list item's due day,
    soonest first. A list item names its list in the quiet line."""
    part = answer.get("up_next") or {}
    items = part.get("items", [])
    body = (rows(row(i, context, line=escape(i.get("list", ""))) for i in items)
            if items else "")
    return section("up_next", context, body, part.get("count"))


def lists(answer: dict, context: Context) -> str:
    """The lists with open items, as small cards: the title and how many
    items are open, never the items, because a long list would break the
    page. The items are on the list's own card page."""
    found = answer.get("lists") or []
    w = words(context.language)
    cards = "".join(
        f'<a class="listcard" data-type="list" href="{escape(card_address(item["id"]))}">'
        f'<span class="tt">{type_icon("list")}<span>{escape(item.get("title", ""))}</span></span>'
        f'<span class="lo">{escape(counted(item.get("open", 0), w["open_items"]))}</span></a>'
        for item in found)
    body = (f'<div class="listgrid">{cards}</div><p class="fl-meta">{escape(w["card"]["chat_line"])}</p>'
            if found else "")
    return section("lists", context, body, len(found) if found else None)


def topics(answer: dict, context: Context) -> str:
    """The person's topics as tiles, most cards about them first: how many
    cards the topic has, how many are about them, its newest card, and
    "only you" for a topic in their own folder. On another person's page
    the section is "Topics" and the tiles name that person ("2 about Lisa")."""
    found = answer.get("topics") or []
    w = words(context.language)
    whose = "" if context.me == context.looker else person_name(context.me, context)
    tiles = "".join(_topic_tile(t, w, whose) for t in found)
    return section("topics", context, f'<div class="tiles">{tiles}</div>' if found else "",
                    len(found) if found else None, key="their_topics" if whose else "topics")


def cards(answer: dict, context: Context, *, key: str = "cards") -> str:
    """The page's cards, newest first, each a row to its card's page: one
    part of them, whose page links the others (`paging`). `key` names the
    section's words: "cards", or "sent" on a sender's page."""
    part = answer.get("cards") or {}
    return section("cards", context, listed(part, context), part.get("count") or None, key=key)


def listed(part: dict, context: Context) -> str:
    """A part of a long list (`{"count": 52, "items": [...], "part": 1,
    "parts": 2}`) as rows; "" when it lists none. Its page links the
    other parts (`paging`), so no line here says how many are left."""
    items = part.get("items", [])
    return rows(row(i, context) for i in items) if items else ""


def changed(answer: dict, context: Context) -> str:
    """What people changed in the last two weeks, newest first, each with
    the day and who did it. It shows the first lines only, and the
    heading's count says how many there are; no "and N more" line, because
    it would lead nowhere."""
    part = answer.get("changed") or {}
    items = part.get("items", [])[:CHANGED_SHOWN]
    count = part.get("count", 0)
    lines = [row(i, context, when=_day(i.get("on"), context), line=by_line(i, context)) for i in items]
    return section("changed", context, rows(lines) if items else "", count or None)


def bookmarks(answer: dict, context: Context) -> str:
    """The bookmarks, newest first: the title links the card, the site is
    named under it."""
    part = answer.get("bookmarks") or {}
    items = part.get("items", [])[:BOOKMARKS_SHOWN]
    marks = "".join(
        f'<li>{type_icon("bookmark")}<span>{card_link(b)}'
        f'<span class="site">{escape(_site(b.get("resource", "")))}</span></span></li>'
        for b in items)
    return section("bookmarks", context, f'<ul class="marks">{marks}</ul>' if items else "",
                    part.get("count") or None)


def documents(answer: dict, context: Context) -> str:
    """One chip per kind of document, most documents first, each opening
    the documents page of that kind; "All documents" opens them all."""
    part = answer.get("documents") or {}
    kinds = part.get("kinds", [])
    w = words(context.language)
    chips = "".join(
        f'<a class="chip" href="{escape(addresses.documents(k["kind"]))}">'
        f'{type_icon("document", kind=k["kind"], size=12)}{escape(k["kind"])}'
        f' <span class="n">{k.get("count", 0)}</span></a>'
        for k in kinds if k.get("kind"))
    more = (f'<a class="more-link" href="{escape(addresses.documents())}">{escape(w["all_documents"])} ›</a>'
            if kinds else "")
    return section("documents", context, f'<div class="kinds">{chips}</div>' if kinds else "",
                    part.get("count") or None, more)


def links(answer: dict, context: Context) -> str:
    """The people, topics and senders the page's cards name, most first,
    each a chip to its memory page with how many cards name it; "" when
    the cards name none. On a person's page the people are "Often with"."""
    part = answer.get("links") or {}
    w = words(context.language)
    fields = [
        (w["often_with"] if context.me else w["people"], "person", addresses.person,
         part.get("people", [])),
        (w["topics"], "topic", addresses.topic, part.get("topics", [])),
        (w["senders"], "correspondent", addresses.sender, part.get("senders", [])),
    ]
    body = "".join(_field(label, kind, address, found, context) for label, kind, address, found in fields
                   if found)
    return section("links", context, body) if body else ""


def type_chips(answer: dict, context: Context) -> str:
    """How many of the page's cards are of each type, as a row of chips,
    most first; "" for a page with no card."""
    counts = answer.get("counts") or {}
    w = words(context.language)
    chips = "".join(
        f'<span class="chip type-chip" data-type="{escape(t)}">{type_icon(t, size=12)}'
        f'{escape(w["types"].get(t, t))} <span class="n">{n}</span></span>'
        for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
    return (f'<div class="filters" role="group" aria-label="{escape(w["show_only"])}">{chips}</div>'
            if chips else "")


# ── Parts ────────────────────────────────────────────────────────────────

def section(name: str, context: Context, body: str, count: int | None = None, more: str = "", *,
            key: str = "") -> str:
    """A section named `name` (`data-section`): its heading (with the
    count when there is one, and `more`, a link to the rest), then `body`,
    or its empty line when `body` is "". `key` picks other words for the
    heading and the empty line than the name's own ("their_topics")."""
    w = words(context.language)
    key = key or name
    number = f'<span class="fl-count">{count}</span>' if count else ""
    inside = body or f'<p class="empty-text">{escape(w["empty"].get(key, ""))}</p>'
    return (f'<section class="section fl-section" data-section="{name}" aria-labelledby="s-{name}">'
            f'<h2 id="s-{name}"><span class="section-title">{escape(w["sections"][key])}</span>'
            f'{number}{more}</h2>{inside}</section>')


def rows(lines) -> str:
    """Rows (`items.row`) as the list a section shows."""
    return f'<ul class="fl-rows">{"".join(lines)}</ul>'


def _topic_tile(topic: dict, w: dict, whose: str) -> str:
    """One topic as a tile; `whose` names the page's person when that is
    not the person looking ("" on their own page, which says "you")."""
    total = counted(topic.get("cards", 0), w["cards"])
    about = topic.get("about", 0)
    about_line = w["about_name"].format(n=about, name=whose) if whose else w["about_you"].format(n=about)
    line = total + (", " + about_line if about < topic.get("cards", 0) else "")
    only = w["only_name"].format(name=whose) if whose else w["only_you"]
    lock = f' · <span class="lock">{escape(only)}</span>' if topic.get("own") else ""
    latest = topic.get("latest")
    newest = (f'<span>{escape(w["latest"].format(title=latest.get("title", "")))}</span>'
              if latest else "")
    return (f'<a class="qtile" data-type="topic" href="{escape(addresses.topic(topic["id"]))}">'
            f'<span class="tt">{type_icon("topic")}{escape(topic.get("title", ""))}</span>'
            f'<span>{escape(line)}{lock}</span>{newest}</a>')


def _field(label: str, kind: str, address, found: list[dict], context: Context) -> str:
    chips = "".join(
        f'<a class="chip" href="{escape(address(f["id"]))}">{type_icon(kind, size=11)}'
        f'{escape(_link_title(f, kind, context))} <span class="n">{f.get("count", 0)}</span></a>'
        for f in found)
    return f'<div class="field"><p class="label">{escape(label)}</p><div class="chiprow">{chips}</div></div>'


def _link_title(found: dict, kind: str, context: Context) -> str:
    """A person by the first name the page uses everywhere; a topic or a
    sender by its card's title."""
    if kind == "person":
        return context.names.get(found["id"]) or (found.get("title") or found["id"]).split()[0]
    return found.get("title") or found["id"]


def _day(value: object, context: Context) -> str:
    day = day_of(value)
    return plain_day(day, today=context.today, language=context.language) if day else ""


def _site(url: str) -> str:
    """A bookmark's site as a reader names it: `pie.example/recipe`."""
    parts = urlsplit(url)
    return (parts.netloc + parts.path).rstrip("/") if parts.netloc else url
