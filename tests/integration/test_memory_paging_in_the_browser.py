"""The memory's long lists in the browser: "Older" and "Newer" between their parts.

A person's, a topic's, a sender's, a kind's and the documents page show
their cards 40 at a time. Below the cards, "Older" opens the next part at
an address of its own (`?part=2`) and "Newer" the part before, so Back
returns to the part a person came from. On a phone 360 px wide the links
fit without a sideways scroll, and each is at least as tall as a finger's
tap target (44 px).

Opens core's tools server, served by uvicorn, in headless Chrome against
memory's test instance (a real curator over a bare repository) holding 45
notes in a topic and 45 receipts from one sender, all naming Marge.
Nothing of a running instance is touched.

Needs the `browser` extra and Google Chrome:

    uv run --extra test --extra browser pytest tests/integration/test_memory_paging_in_the_browser.py

With `MEMORY_SHOTS=<dir>` it saves each page's second part at 360 and
1280 px, in English and German, and the person page's "What changed".
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.memory.unit.instance import running_memory

playwright = pytest.importorskip("playwright.sync_api")

SHOTS = os.environ.get("MEMORY_SHOTS", "")
WIDTHS = {"360": {"width": 360, "height": 780}, "1280": {"width": 1280, "height": 900}}
# The smallest tap target a link may have, in CSS pixels.
TAP_PX = 44
MANY = 45

PAGES = {"person": "/memory/person/marge", "topic": "/memory/topic/topic-garden",
         "sender": "/memory/sender/correspondent-kwik-e-mart", "documents": "/memory/documents",
         "notes": "/memory/kind/note"}


def _day(n: int) -> str:
    return f"2026-0{7 + n // 30}-{1 + n % 30:02d}"


def _card(kind: str, n: int, title: str, extra: str = "") -> str:
    return (f"---\ntype: {kind}\ncard_id: {kind}-{n:02d}\ntitle: {title}\ndate: \"{_day(n)}\"\n"
            f"persons:\n  - marge\n{extra}---\n\n# {title}\n")


CARDS = {
    "family/people/marge.md": ("---\ntype: person\ncard_id: person-marge\ntitle: Marge Simpson\n"
                               "account: marge\n---\n\n# Marge Simpson\n"),
    "family/garden/topic.md": "---\ntype: topic\ncard_id: topic-garden\ntitle: Garden\n---\n\n# Garden\n",
    "family/correspondents/kwik-e-mart.md": ("---\ntype: correspondent\ncard_id: correspondent-kwik-e-mart\n"
                                             "title: Kwik-E-Mart\n---\n\n# Kwik-E-Mart\n"),
    **{f"family/garden/notes/{n:02d}.md": _card("note", n, f"Roses, week {n}") for n in range(MANY)},
    **{f"family/documents/{n:02d}.md": _card("document", n, f"Receipt {n}",
                                             "correspondent: Kwik-E-Mart\ndocument_type: Receipt\n")
       for n in range(MANY)},
}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("memory-paging-browser"), CARDS) as running:
        yield running


def _shot(page, name: str, *, full_page: bool = True) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=full_page)


def _fits(page) -> bool:
    """The page does not scroll sideways."""
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


def _tall_enough(link) -> bool:
    box = link.bounding_box()
    return box is not None and box["height"] >= TAP_PX and box["width"] >= TAP_PX


@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("width", list(WIDTHS))
@pytest.mark.parametrize("name", list(PAGES))
def test_older_and_newer_lead_between_the_parts_and_back_returns(chrome, pages, name, width, language):
    """Given a page with more than 40 cards, at 360 or 1280 px, in English
    or German, when "Older" is tapped, then the second part opens at its
    own address with "Newer", and "Older" only on Marge's page, whose 90
    cards have three parts; when Back is pressed,
    then the first part is back. The page never scrolls sideways, and
    each link is at least 44 px in both directions."""
    context = chrome.new_context(viewport=WIDTHS[width], locale="de-DE" if language == "de" else "en-US",
                                 extra_http_headers={"accept-language": language})
    page = context.new_page()
    page.goto(f"{pages}{PAGES[name]}")
    older = page.locator(".pager-older")

    assert _fits(page)
    if name == "person":
        # "What changed" ends with its last line, no "and N more".
        page.locator('[data-section="changed"]').evaluate("s => s.scrollIntoView({block: 'center'})")
        page.mouse.wheel(0, 250)
        _shot(page, f"{width}-{language}-{name}-what-changed", full_page=False)
    older.scroll_into_view_if_needed()
    assert older.is_visible() and _tall_enough(older)
    older.click()
    page.wait_for_url(f"**{PAGES[name]}?part=2")
    newer = page.locator(".pager-newer")
    assert _fits(page)
    assert newer.is_visible() and _tall_enough(newer)
    assert page.locator(".pager-older").count() == (1 if name == "person" else 0)
    newer.scroll_into_view_if_needed()
    _shot(page, f"{width}-{language}-{name}-part-2")
    page.go_back()
    page.wait_for_url(f"**{PAGES[name]}")
    assert page.locator(".pager-older").count() == 1
    context.close()
