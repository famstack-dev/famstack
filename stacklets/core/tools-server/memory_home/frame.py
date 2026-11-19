"""The frame of the memory's pages: the header, the menu, and the phone's menu button.

Every memory page, and every card page (`card_pages.py`), sits in the
card pages' frame (`memory_ui.frame.document`:
the wiki's look, the column on the left, the page as a paper sheet) and fills
it with its own parts:

    html = document(sheet, title="My memory", language="en", here=addresses.home(),
                    person=("marge", "Marge"), menu=Menu(topics=[...], kinds=[...], senders=2))

- **The header** above the frame: the famstack mark with "memory", which
  leads to "My memory", and the one search at full width. On a computer it
  names the person the device is paired to, so nobody wonders whose page it
  is.
- **The menu** in the column: the way back to the dashboard, the person's
  own page and the family's, then "Your topics": the person's topics, each
  with how many cards it holds, the count its page shows. Then
  "Everything": each kind of card the device may read (Documents, Notes,
  Diary, Emails, Bookmarks, Lists), each with its count, and "Senders"
  with how many there are (`menu.py` reads both). The link to the page
  that is open carries `aria-current="page"`. A household device has no
  topics of its own, so its menu has no such group, but it has
  "Everything".
- **On a phone** (600 px and narrower, `static/home.css`) the header is
  one row, so the page starts higher: the mark with "memory" under it,
  small and grey, a search button and a menu button. The search button
  slides the field out over the space between the mark and the menu
  button; the menu folds away behind its button. Without the scripts both
  buttons stay hidden, the search sits on its own line under the mark and
  the menu stays open, so every link works. Between 601 and 900 px the
  search keeps its own line under the mark.

The script (`static/home.js`) moves the focus to the page's heading when a
page opens, so a screen reader starts at the new page, and opens and
closes the menu on a phone. A second one (`static/preview.js`) shows a
card link's preview on hover and on focus. A third
(`static/search_layer.js`) shows the search's results as a layer over the
page, with a loading mark and a clear button in the field; without it the
search sends its words to the search page. A fourth
(`static/search_toggle.js`) slides the field out and back on a phone.

The colours of the card types come from the dashboard's tokens
(`dashboard/tokens.css`, `--t-*`), which the page loads as they are.

Pure: no I/O.
"""

from __future__ import annotations

from html import escape

from access import DASHBOARD_PREFIX
from memory_home import addresses
from memory_home.items import type_icon
from memory_home.menu import Menu
from memory_home.words import words
from memory_ui.frame import document as card_frame
from memory_ui.frame import icon, wordmark

__all__ = ["document", "avatar", "page_head"]

# The heading a page opens with, which takes the focus (`static/home.js`).
HEADING_ID = "page-title"
# The menu the phone's button opens and closes.
MENU_ID = "memory-menu"
# The dashboard's tokens: the card types' colours (`--t-*`), not copied.
TOKENS = f"{DASHBOARD_PREFIX}/static/tokens.css"


def document(sheet: str, *, title: str, language: str, here: str,
             person: tuple[str, str] | None = None, menu: Menu | None = None,
             query: str = "", description: str = "") -> str:
    """A whole memory page: the header, the menu and `sheet` (HTML) in the
    card pages' frame. `title` names the browser tab, `here` is the address
    of the page (its menu link is marked current), `person` the device's
    person as (id, first name), None for a household device, `menu` the
    menu's groups for the device (`menu.menu_of`; none when None), and
    `query` the words the search field holds (the search page's own), and
    `description` the preview a chat app shows for a link (a card's page).

        >>> "aria-current" in document("<p>x</p>", title="My memory", language="en",
        ...                            here="/memory", person=("marge", "Marge"))
        True
    """
    return card_frame(sheet, title=title, language=language, description=description,
                      header=_header(language, person, query),
                      side=_menu(language, here, person, menu or Menu()),
                      stylesheets=(TOKENS, addresses.asset("home.css")),
                      scripts=(addresses.asset("home.js"), addresses.asset("preview.js"),
                               addresses.asset("search_layer.js"), addresses.asset("search_toggle.js")))


def avatar(person_id: str, name: str) -> str:
    """The person's initial on their colour, as the dashboard draws people."""
    return (f'<span class="avatar" data-person="{escape(person_id)}" aria-hidden="true">'
            f'{escape(name[:1].upper())}</span>')


def page_head(card_type: str, kicker: str, title: str, facts: str = "", *, kind: str = "") -> str:
    """The head of a page's sheet: the kicker with the type's icon, the
    heading that takes the focus when the page opens, and `facts` (HTML,
    already escaped) under it. `kind` draws a document by its kind's icon.

        page_head("topic", "The family's topic", "Camping", '<span class="count-line">2 cards</span>')
    """
    return (f'<header class="family-head"><p class="kicker" data-type="{escape(card_type)}">'
            f'{type_icon(card_type, kind=kind)}{escape(kicker)}</p>'
            f'<h1 class="title" id="{HEADING_ID}" tabindex="-1">{escape(title)}</h1>'
            + (f'<p class="facts">{facts}</p>' if facts else "") + "</header>")


# ── The header ───────────────────────────────────────────────────────────

def _header(language: str, person: tuple[str, str] | None, query: str) -> str:
    w = words(language)
    me = (f'<span class="me">{avatar(*person)}{escape(person[1])} '
          f'<small>· {escape(w["this_device"])}</small></span>' if person else "")
    return (f'<a class="skip" href="#main">{escape(w["skip"])}</a>'
            f'<header class="mhead">'
            f'<a class="brand" href="{addresses.home()}" aria-label="{escape(w["brand"])}">'
            f'{wordmark()}<b>{escape(w["memory"])}</b></a>'
            f'<button class="search-open" type="button" aria-label="{escape(w["search"])}" '
            f'aria-controls="q" aria-expanded="false" hidden>{icon("search", 20)}</button>'
            f'<button class="menu-button" type="button" aria-expanded="false" aria-controls="{MENU_ID}" hidden>'
            f'{icon("menu", 20)}<span class="menu-label">{escape(w["menu"])}</span></button>'
            f'{_search(w, query)}{me}</header>')


def _search(w: dict, query: str) -> str:
    """The one search: a form that sends its words to the search page,
    holding `query`, so a search with no match keeps its words. The clear
    button and the loading mark are for the layer's script
    (`static/search_layer.js`); the clear button is hidden while the field
    is empty, and the mark until a search runs."""
    clear_hidden = "" if query else " hidden"
    return (f'<form class="search" role="search" action="{addresses.search()}" method="get">'
            f'<label class="sr" for="q">{escape(w["search"])}</label>{icon("search", 16)}'
            f'<input type="search" id="q" name="q" value="{escape(query)}" '
            f'placeholder="{escape(w["search_hint"])}" '
            'autocomplete="off" enterkeyhint="search">'
            f'<span class="search-busy" role="status" data-label="{escape(w["searching"])}"></span>'
            f'<button class="search-clear" type="button" aria-label="{escape(w["clear_search"])}"'
            f'{clear_hidden}>×</button></form>')


# ── The menu ─────────────────────────────────────────────────────────────

def _menu(language: str, here: str, person: tuple[str, str] | None, menu: Menu) -> str:
    """The column's links: back to the dashboard, the person's page, the
    family's page, the person's topics, every kind of card and the
    senders. Only the link that leads to `here`,
    the address shown, is marked current: on a household device `/memory`
    shows the family's page, and no link of the menu leads there."""
    w = words(language)

    def link(href: str, label: str, mark: str = "", kind: str = "fn-link", count: str = "") -> str:
        current = ' aria-current="page"' if href == here else ""
        return f'<a class="{kind}" href="{escape(href)}"{current}>{mark}{escape(label)}{count}</a>'

    own = link(addresses.home(), person[1], avatar(*person)) if person else ""
    family = link(addresses.family(), w["the_family"],
                  f'<span class="ti" data-type="family">{icon("family")}</span>')
    return (f'<nav class="memory-menu" id="{MENU_ID}" aria-label="{escape(w["menu_label"])}">'
            f'<a class="fn-link back" href="{DASHBOARD_PREFIX}">{escape(w["card"]["dashboard"])}</a>'
            f'{own}{family}{_topic_group(w, menu.topics, link)}{_everything_group(w, menu, link)}</nav>')


def _topic_group(w: dict, topics, link) -> str:
    """"Your topics" in the menu, each topic with its count; "" with none."""
    if not topics:
        return ""
    found = "".join(
        link(addresses.topic(t["id"]), t.get("title") or t["id"],
             f'<span class="ti" data-type="topic">{icon("topic", 12)}</span>', kind="fn-sub",
             count=f'<span class="n">{int(t.get("cards", 0))}</span>')
        for t in topics)
    return _group("topics", w["your_topics"], found)


def _everything_group(w: dict, menu: Menu, link) -> str:
    """"Everything" in the menu: each kind of card with its count, in the
    memory's order, then the senders with theirs; "" with none of them."""
    found = "".join(
        link(addresses.kind(k["type"]), w["types"].get(k["type"], k["type"]),
             type_icon(k["type"], size=12), kind="fn-sub", count=f'<span class="n">{int(k["count"])}</span>')
        for k in menu.kinds)
    if menu.senders:
        found += link(addresses.senders(), w["senders"], type_icon("correspondent", size=12), kind="fn-sub",
                      count=f'<span class="n">{menu.senders}</span>')
    return _group("everything", w["everything"], found) if found else ""


def _group(name: str, label: str, links: str) -> str:
    """A group of the menu under its label; `name` (`data-group`) says which."""
    return f'<div class="fn-group" data-group="{name}"><p class="label">{escape(label)}</p>{links}</div>'
