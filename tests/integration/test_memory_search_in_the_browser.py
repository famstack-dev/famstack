"""The memory's search in the browser: its results as a layer over the page.

Every memory page has one search in its header. With the pages' script,
its results open as a layer over the content section: the header and the
menu stay, the field shows a loading mark while the search runs and a
clear button while it holds words, and clearing it (or Escape) closes the
layer and gives back the page as it was, scroll position included. A
result opens as a link, and Back returns to the page with the layer open.

Opens core's tools server, served by uvicorn, in headless Chrome against
memory's test instance (a real curator over a bare repository holding the
Simpsons' cards). Nothing of a running instance is touched.

Needs the `browser` extra and Google Chrome:

    uv run --extra test --extra browser pytest tests/integration/test_memory_search_in_the_browser.py

On a phone (600 px and narrower) the header is one row: the mark with
"memory" under it, a search button and the menu button. The button slides
the field out between the mark and the menu button; clearing it, or
Escape, slides it back.

With `MEMORY_SHOTS=<dir>` it saves the loading state, the open layer at
1280 and 360 px, the page after clearing, and the phone's header closed
and open.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

playwright = pytest.importorskip("playwright.sync_api")

SHOTS = os.environ.get("MEMORY_SHOTS", "")
DESKTOP = {"width": 1280, "height": 900}
PHONE = {"width": 360, "height": 780}
# A word the Simpsons' memory answers with several groups of results.
WORDS = "insurance"


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("memory-search-browser"), VAULT) as running:
        yield running


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _family_page(chrome, pages, viewport):
    context = chrome.new_context(viewport=viewport, locale="en-US")
    page = context.new_page()
    page.goto(f"{pages}/memory/family")
    assert page.locator("#page-title").count() == 1
    # The page has drawn and the header's field may slide (search_toggle.js),
    # as by the time a person taps.
    page.wait_for_selector("html.search-motion", state="attached")
    return context, page


# ── The layer ─────────────────────────────────────────────────────────────

def test_the_results_open_over_the_page_and_clearing_returns_to_it(chrome, pages):
    """Given the family's page scrolled down, when the words are typed and
    the typing pauses, then the loading mark shows, then the results open
    over the content section with the header and the menu still there;
    when the clear button is pressed, then the layer closes, the field is
    empty and has the focus, and the page is back at its scroll position."""
    context, page = _family_page(chrome, pages, DESKTOP)
    page.evaluate("window.scrollTo(0, 400)")
    before = page.evaluate("window.scrollY")
    page.route("**/memory/search/results*", lambda route: (time.sleep(0.6), route.continue_()))

    clear = page.locator(".search-clear")
    assert clear.is_hidden()
    page.locator("#q").type(WORDS)
    assert clear.is_visible()
    assert clear.get_attribute("aria-label") == "Clear the search"
    page.wait_for_selector("form.search.busy")
    _shot(page, "01-loading")

    layer = page.locator(".search-layer")
    layer.locator(".results").first.wait_for()
    assert page.locator("form.search").evaluate("f => f.classList.contains('busy')") is False
    assert page.locator("main > .sheet").first.is_hidden()
    assert page.locator(".mhead").is_visible() and page.locator(".memory-menu").is_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _shot(page, "02-layer-desktop")

    clear.click()
    assert layer.is_hidden()
    assert page.locator("#q").input_value() == ""
    assert page.evaluate("document.activeElement.id") == "q"
    assert page.evaluate("window.scrollY") == before
    _shot(page, "04-after-clearing")
    context.close()


def test_the_search_on_a_card_page_opens_the_layer_over_the_card(chrome, pages):
    """Given a card's page, when the words are typed in its header, then
    the results open over the card, with the header and the menu still
    there, as on the memory's pages."""
    context = chrome.new_context(viewport=DESKTOP, locale="en-US")
    page = context.new_page()
    page.goto(f"{pages}/card/document-3f9a0c1b2d")

    page.locator("#q").type(WORDS)
    page.locator(".search-layer .results").first.wait_for()

    assert page.locator("main > .sheet").first.is_hidden()
    assert page.locator(".mhead").is_visible() and page.locator(".memory-menu").is_visible()
    _shot(page, "card-search-layer")
    context.close()


def test_enter_moves_the_focus_into_the_layer_and_escape_closes_it(chrome, pages):
    """Given words in the field, when Enter is pressed, then the layer opens
    with the focus on its heading and the address unchanged; when Escape
    is pressed, then the layer closes and the focus is back in the field."""
    context, page = _family_page(chrome, pages, DESKTOP)
    address = page.url
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    page.locator("#search-title:focus").wait_for()
    assert page.url == address

    page.keyboard.press("Escape")
    assert page.locator(".search-layer").is_hidden()
    assert page.evaluate("document.activeElement.id") == "q"
    context.close()


def test_escape_closes_the_layer_with_the_focus_in_the_menu(chrome, pages):
    """Given the layer open and the focus on a link of the menu, when
    Escape is pressed, then the layer closes and the page is back."""
    context, page = _family_page(chrome, pages, DESKTOP)
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    page.locator(".search-layer .results").first.wait_for()
    page.locator(".memory-menu a").first.focus()

    page.keyboard.press("Escape")
    assert page.locator(".search-layer").is_hidden()
    assert page.locator("main > .sheet").first.is_visible()
    context.close()


def test_a_results_preview_opens_inside_the_layer(chrome, pages):
    """Given the layer open, when a card result gets the focus, then its
    preview opens with the card's title, as on any memory page."""
    context, page = _family_page(chrome, pages, DESKTOP)
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    card = page.locator('.search-layer .results a[href^="/card/"]').first
    card.wait_for()
    title = card.inner_text()

    card.focus()
    box = page.locator(".card-preview")
    box.wait_for(state="visible")
    assert title in box.inner_text()
    context.close()


def test_deleting_the_words_closes_the_layer(chrome, pages):
    context, page = _family_page(chrome, pages, DESKTOP)
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    page.locator(".search-layer .results").first.wait_for()

    page.locator("#q").fill("")
    assert page.locator(".search-layer").is_hidden()
    assert page.locator("main > .sheet").first.is_visible()
    context.close()


def test_back_from_a_result_returns_to_the_layer(chrome, pages):
    """Given the layer open, when a result is opened and Back is pressed,
    then the page under it shows the layer again, with the words."""
    context, page = _family_page(chrome, pages, DESKTOP)
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    result = page.locator(".search-layer .results a").first
    result.wait_for()
    result.click()
    page.wait_for_url(lambda url: "/memory/family" not in url)

    page.go_back()
    page.locator(".search-layer .results").first.wait_for()
    assert page.locator("#q").input_value() == WORDS
    context.close()


def test_on_a_phone_the_layer_fits_the_screen(chrome, pages):
    """Given a phone 360 px wide, then the layer fills the content area
    below the header with no sideways scroll."""
    context, page = _family_page(chrome, pages, PHONE)
    page.locator(".search-open").click()
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    page.locator(".search-layer .results").first.wait_for()

    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    page.wait_for_timeout(300)                                      # the field's slide ends
    _shot(page, "03-layer-phone")
    context.close()


# ── The phone's header ────────────────────────────────────────────────────

# The header's background as the reader sees it on the paper.
HEADER_BACKGROUND = (0xEF, 0xEE, 0xE6)


def _contrast(css_colour: str, background: tuple[int, int, int]) -> float:
    """WCAG's contrast ratio of an `rgb(r, g, b)` colour on `background`."""
    def luminance(rgb):
        lin = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in (x / 255 for x in rgb)]
        return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]
    rgb = [int(part) for part in css_colour[css_colour.index("(") + 1:css_colour.index(")")].split(",")[:3]]
    lighter, darker = sorted((luminance(rgb), luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _box(page, selector: str) -> dict:
    return page.locator(selector).bounding_box()


def _field_shown(page) -> bool:
    """Whether the search field can be seen and reached: the closed field
    is hidden from sight and from the keyboard (visibility), not removed."""
    return page.locator("#q").evaluate(
        "f => getComputedStyle(f.form).visibility === 'visible' && getComputedStyle(f.form).display !== 'none'")


def _wait_until(page, check: str) -> None:
    """Poll `check` (a JavaScript expression) until it holds. The pages'
    CSP forbids eval, which `wait_for_function` needs for a predicate."""
    for _ in range(50):
        if page.evaluate(check):
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"never held: {check}")


def _slid_back(page) -> None:
    _wait_until(page, "getComputedStyle(document.querySelector('form.search')).visibility === 'hidden'")


def test_on_a_phone_the_header_is_one_row(chrome, pages):
    """Given a phone 360 px wide, then the mark, the search button and the
    menu button share one row, "memory" sits under the mark, the field is
    out of sight, and the search button has a name and a 44 px target."""
    context, page = _family_page(chrome, pages, PHONE)
    brand, word = _box(page, ".mhead .brand .fs-brand"), _box(page, ".mhead .brand b")
    button, menu = _box(page, ".search-open"), _box(page, ".menu-button")

    assert word["y"] >= brand["y"] + brand["height"] - 2          # under the mark
    centre = button["y"] + button["height"] / 2
    assert menu["y"] < centre < menu["y"] + menu["height"]          # one row
    assert brand["x"] + brand["width"] <= button["x"] <= menu["x"]
    assert button["width"] >= 44 and button["height"] >= 44
    assert page.locator(".search-open").get_attribute("aria-label") == "Search the memory"
    assert page.locator(".search-open").get_attribute("aria-expanded") == "false"
    assert not _field_shown(page)
    assert _box(page, ".mhead")["height"] < 70
    assert _contrast(page.locator(".mhead .brand b").evaluate("b => getComputedStyle(b).color"),
                     HEADER_BACKGROUND) >= 4.5                       # WCAG AA for small text
    _shot(page, "05-phone-header-closed")
    context.close()


def test_on_a_phone_the_search_button_slides_the_field_out(chrome, pages):
    """Given a phone, when the search button is tapped, then the field
    shows between the mark and the menu button, with the focus in it."""
    context, page = _family_page(chrome, pages, PHONE)
    page.locator(".search-open").click()

    _wait_until(page, "document.activeElement.id === 'q'")
    assert _field_shown(page)
    page.wait_for_timeout(300)                                      # the slide ends
    field, brand, menu = _box(page, "form.search"), _box(page, ".mhead .brand"), _box(page, ".menu-button")
    assert field["x"] >= brand["x"] + brand["width"]
    assert field["x"] + field["width"] <= menu["x"]
    assert page.locator(".search-open").get_attribute("aria-expanded") == "true"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    _shot(page, "06-phone-header-open")
    context.close()


def test_on_a_phone_clearing_or_escape_slides_the_field_back(chrome, pages):
    """Given the field slid out with the layer open, when × is tapped,
    then the layer closes and the field stays, empty; when × is tapped
    again, then the field slides back and the search button has the
    focus. Escape in an open field with no search slides it back too."""
    context, page = _family_page(chrome, pages, PHONE)
    page.locator(".search-open").click()
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    page.locator(".search-layer .results").first.wait_for()

    page.locator(".search-clear").click()
    assert page.locator(".search-layer").is_hidden()
    assert _field_shown(page) and page.locator("#q").input_value() == ""

    page.locator(".search-clear").click()
    _slid_back(page)
    assert page.evaluate("document.activeElement.className") == "search-open"
    assert page.locator(".search-open").get_attribute("aria-expanded") == "false"

    page.locator(".search-open").click()
    _wait_until(page, "document.activeElement.id === 'q'")
    page.keyboard.press("Escape")
    _slid_back(page)
    context.close()


def test_on_a_phone_escape_closes_the_layer_first(chrome, pages):
    """Given the field slid out with the layer open, when Escape is
    pressed, then the layer closes and the field stays out, empty, with
    the focus; a second Escape slides it back."""
    context, page = _family_page(chrome, pages, PHONE)
    page.locator(".search-open").click()
    page.locator("#q").fill(WORDS)
    page.locator("#q").press("Enter")
    page.locator(".search-layer .results").first.wait_for()

    page.keyboard.press("Escape")
    assert page.locator(".search-layer").is_hidden()
    assert _field_shown(page) and page.locator("#q").input_value() == ""
    assert page.evaluate("document.activeElement.id") == "q"

    page.keyboard.press("Escape")
    _slid_back(page)
    context.close()


def test_on_a_phone_the_field_that_slides_back_holds_no_words(chrome, pages):
    """Given the search page on a phone, its words in the field and the
    focus elsewhere, when Escape slides the field back and the search
    button is tapped, then the field opens empty: a field out of sight
    keeps no search."""
    context = chrome.new_context(viewport=PHONE, locale="en-US")
    page = context.new_page()
    page.goto(f"{pages}/memory/search?q={WORDS}")
    page.wait_for_selector("html.search-motion", state="attached")
    assert page.evaluate("document.activeElement.id") != "q"

    page.keyboard.press("Escape")
    _slid_back(page)
    page.locator(".search-open").click()
    assert page.locator("#q").input_value() == ""
    context.close()


def test_on_a_phone_the_search_page_shows_its_words(chrome, pages):
    """Given a phone, when the search page opens with words, then the field
    is out and holds them, so the words the page answers can be seen."""
    context = chrome.new_context(viewport=PHONE, locale="en-US")
    page = context.new_page()
    page.goto(f"{pages}/memory/search?q={WORDS}")
    assert _field_shown(page)
    assert page.locator("#q").input_value() == WORDS
    context.close()


def test_on_a_phone_with_reduced_motion_the_field_does_not_slide(chrome, pages):
    """Given a phone that asks for reduced motion, then the field appears
    without a transition; without that wish it slides."""
    calm = chrome.new_context(viewport=PHONE, locale="en-US", reduced_motion="reduce")
    moving = chrome.new_context(viewport=PHONE, locale="en-US")
    durations = []
    for context in (calm, moving):
        page = context.new_page()
        page.goto(f"{pages}/memory/family")
        page.wait_for_selector("html.search-motion", state="attached")
        durations.append(page.locator("form.search").evaluate(
            "f => getComputedStyle(f).transitionDuration.split(',').map(parseFloat)"))
        context.close()
    assert max(durations[0]) == 0
    assert max(durations[1]) > 0


def test_on_a_computer_the_header_keeps_its_field(chrome, pages):
    """Given a computer, then the field shows in the header at once and
    there is no search button."""
    context, page = _family_page(chrome, pages, DESKTOP)
    assert _field_shown(page)
    assert page.locator(".search-open").is_hidden()
    _shot(page, "07-desktop-header")
    context.close()
