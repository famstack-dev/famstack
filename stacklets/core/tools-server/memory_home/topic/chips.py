"""The two chips under a topic's head and the lists they open.

    chips(answer, context)        # <div class="topic-chips">3 bookmarks, 1 document</div>
    panels(answer, context)       # the lists that sit under the chips
    main_list(answer, context)    # the list that is the page's main section, or ""

A chip names a kind and how many of it the topic holds; a kind with none
has no chip. A tap opens that kind's list and closes the other one
(`static/topic.js`). A kind that holds more than half of the topic's cards
is its main list: it starts open, sits after Up next instead of under the
chips, shows the newest `MAIN_SHOWN` rows with the words sent with each, and
folds the rest behind "All n ›". The same rule serves bookmarks and documents.

Pure: no I/O. `answer` is the memory's overview of the topic.
"""

from __future__ import annotations

from html import escape

from memory_home import sections
from memory_home.items import Context, card_link, person_name, type_icon
from memory_home.words import counted, words
from memory_ui.words import day_of, plain_day

__all__ = ["chips", "panels", "main_list", "main_kind"]

KINDS = ("bookmarks", "documents")
# The newest rows a main list shows before "All n ›".
MAIN_SHOWN = 5


def main_kind(answer: dict) -> str:
    """"bookmarks" or "documents" when that kind is more than half of the
    topic's cards, else ""."""
    total = (answer.get("cards") or {}).get("count", 0)
    for kind in KINDS:
        if _count(answer, kind) * 2 > total:
            return kind
    return ""


def chips(answer: dict, context: Context) -> str:
    """The chips for the kinds the topic holds; "" when it holds neither."""
    w = words(context.language)
    main = main_kind(answer)
    found = "".join(
        f'<button class="chip topic-chip" type="button" data-opens="{kind}" aria-controls="p-{kind}" '
        f'aria-expanded="{"true" if kind == main else "false"}">{type_icon(kind[:-1], size=12)}'
        f'{escape(counted(_count(answer, kind), w["chip_" + kind]))}</button>'
        for kind in KINDS if _count(answer, kind))
    return (f'<div class="topic-chips" role="group" aria-label="{escape(w["in_topic"])}">{found}</div>'
            if found else "")


def panels(answer: dict, context: Context) -> str:
    """The lists that sit under the chips: every kind but the main one."""
    main = main_kind(answer)
    return "".join(_panel(answer, context, kind, main=False) for kind in KINDS
                   if kind != main and _count(answer, kind))


def main_list(answer: dict, context: Context) -> str:
    """The main list as a section of the page; "" when no kind is main."""
    main = main_kind(answer)
    return _panel(answer, context, main, main=True) if main else ""


# ── One list ──────────────────────────────────────────────────────────────

def _panel(answer: dict, context: Context, kind: str, *, main: bool) -> str:
    items = (answer.get(kind) or {}).get("items", [])
    count = _count(answer, kind)
    cut = main and len(items) > MAIN_SHOWN
    rows = "".join(_row(i, context, said=main, folded=cut and n >= MAIN_SHOWN) for n, i in enumerate(items))
    more = (f'<button class="all-rows" type="button">'
            f'{escape(words(context.language)["all_n"].format(n=count))}</button>') if cut else ""
    body = f'<ul class="marks">{rows}</ul>{more}'
    if main:
        body = sections.section(f"main-{kind}", context, body, count, key=f"main_{kind}")
    hidden = "" if main else " hidden"
    return f'<div class="topic-panel" id="p-{kind}" data-panel="{kind}"{hidden}>{body}</div>'


def _row(item: dict, context: Context, *, said: bool, folded: bool) -> str:
    """A bookmark (its title opens the site, then the site) or a document
    (its kind's icon, its title, its kind), then who and when."""
    if item.get("type") == "bookmark":
        icon = type_icon("bookmark")
        title = (f'<a class="external mark-title" href="{escape(item.get("resource", ""))}">'
                 f'{escape(item.get("title") or item["id"])}</a>')
        under = item.get("site", "")
    else:
        icon = type_icon("document", kind=item.get("kind", ""))
        title = card_link(item).replace('class="internal"', 'class="internal mark-title"')
        under = item.get("kind", "")
    words_sent = (f'<blockquote class="said"><p class="mark-said">{escape(item["words"])}</p></blockquote>'
                  if said and item.get("words") else "")
    return (f'<li class="mark{" mark-more" if folded else ""}"{" hidden" if folded else ""}>{icon}<div>'
            f'{title}<span class="site mark-site">{escape(under)}</span>{words_sent}'
            f'<span class="by mark-by">{escape(_by(item, context))}</span></div></li>')


def _by(item: dict, context: Context) -> str:
    """"Marge · Oct 11": who the card names, and its day in words."""
    day = day_of(item.get("day"))
    when = plain_day(day, today=context.today, language=context.language) if day else ""
    who = ", ".join(person_name(p, context) for p in item.get("persons", []))
    return " · ".join(part for part in (who, when) if part)


def _count(answer: dict, kind: str) -> int:
    return (answer.get(kind) or {}).get("count", 0)
