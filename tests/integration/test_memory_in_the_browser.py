"""The memory's pages in the browser: a card's history and a note's full
text load only when they are opened.

Opens core's tools server, served by uvicorn, in headless Chrome against
memory's test instance (a real curator over a bare repository holding the
Simpsons' cards). A card's page asks the memory for its versions only
when "History" is opened; then the timeline shows the latest three
events, and "Show all" unfolds the rest. A note made from a PDF asks for
its full text only when "Full text" is opened. A diary entry's photos
open one by one from their mosaic, with "3 of 7", and "All photos" leads
back.

Needs the `browser` extra and Google Chrome:

    uv run --extra test --extra browser pytest tests/integration/test_memory_in_the_browser.py

With `MEMORY_SHOTS=<dir>` it saves a page of each card type,
in English and German, at 390 and 1280 px wide.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

from tests.memory.unit.instance import running_memory
from tests.unit.surface.conftest import tools_server
from tests.memory.unit.simpsons_cards import (DIARY_ENTRIES, ENTRY_MEDIA, FILE_NOTES, GARDEN_BENCH,
                                              GRANDMA, GRANDMA_PHOTOS, LAKE, MEDIA, PLAY, SAX,
                                              UNO_FULL_TEXT, UNO_RULES, VAULT)

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

SHOTS = os.environ.get("MEMORY_SHOTS", "")
WIDTHS = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 900}}

POLICY = "document-3f9a0c1b2d"
DIARY = "diary-5b0c1b2d3e"
CARDS = {
    **VAULT,
    **FILE_NOTES,
    **DIARY_ENTRIES,
    "homer/diary/entries/2026-11-03-abe.md": (
        f"---\ntype: diary\ncard_id: {DIARY}\ntitle: Abe's birthday lunch\ndate: \"2026-11-03\"\n"
        "date_basis: spoken\npersons:\n  - homer\nkind: voice\nduration_ms: 41000\n"
        "media:\n  - /media/2026/11/Ev1AbCdEf.m4a\ntimestamp: \"2026-11-03T12:00:00Z\"\n---\n"
        "# Abe's birthday lunch\n\nWe took Grampa to lunch. He told the onion story again.\n"),
}
# A page of each type, for the screenshots.
PAGES = {"document": POLICY, "bookmark": "bookmark-5c4d3e2f1a", "note": "note-7f3a9c21e4",
         "email": "email-9e8d7c6b5a", "diary": DIARY, "list": "list-packliste",
         "person": "person-bart", "correspondent": "correspondent-duff-insurance",
         "topic": "topic-itchy-scratchy-land", "pdfnote": UNO_RULES, "imagenote": GARDEN_BENCH,
         "entry-grandma": GRANDMA, "entry-sax": SAX, "entry-lake": LAKE, "entry-play": PLAY}
# Four changes after the seed, so the timeline has more than three events.
EDITS = [("marge", ["medical"]), ("homer", ["medical", "car-insurance"]), ("bart", ["car-insurance"]),
         ("marge", ["medical"])]


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("memory-browser"), CARDS) as running:
        for path, (data, record) in {**MEDIA, **ENTRY_MEDIA}.items():
            running.keeps_file(path, data, **record)
        for who, tags in EDITS:
            _, version = running.api.card(POLICY)
            running.api.edit(POLICY, {"tags": tags}, version=version, by=who)
        yield running


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pages(memory):
    """Core's tools server on a loopback port, reading the memory."""
    patch = pytest.MonkeyPatch()
    patch.setenv("MEMORY_CARDS_URL", memory.curator)
    patch.setenv("TIMEZONE", "UTC")
    server = tools_server("server")

    port = _free_port()
    running = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    for _ in range(100):
        if running.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    running.should_exit = True
    thread.join(timeout=5)
    patch.undo()


@pytest.fixture(scope="module")
def chrome():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # no Chrome on this Mac
            pytest.skip(f"Google Chrome is needed: {e}")
        yield browser
        browser.close()


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def test_history_is_read_only_when_it_is_opened(chrome, pages):
    """Given a card's page, when it opens, then the memory is not asked for
    the card's history; when History is opened, then it is, the latest
    three events show, and "Show all" unfolds the fifth."""
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="en-US")
    page = context.new_page()
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url))

    page.goto(f"{pages}/card/{POLICY}")
    page.wait_for_load_state("networkidle")
    assert not [u for u in asked if u.endswith("/history")]
    assert page.locator("details.history").get_attribute("open") is None

    page.locator("details.history > summary").click()
    page.locator(".timeline li").first.wait_for()

    assert [u for u in asked if u.endswith(f"/card/{POLICY}/history")]
    assert page.locator(".timeline li:visible").count() == 3
    assert page.locator(".timeline li").first.inner_text().startswith("Marge changed it")
    _shot(page, "1280-en-history")

    page.get_by_role("button", name="Show all").click()
    assert page.locator(".timeline li:visible").count() == 5
    context.close()


def test_the_full_text_is_read_only_when_it_is_opened(chrome, pages):
    """Given a note made from a PDF, when its page opens, then its full text
    is neither on the page nor asked for; when "Full text" is opened, then
    it is asked for and shows."""
    context = chrome.new_context(viewport=WIDTHS["390"], locale="en-US")
    page = context.new_page()
    asked: list[str] = []
    page.on("request", lambda r: asked.append(r.url))

    page.goto(f"{pages}/card/{UNO_RULES}")
    page.wait_for_load_state("networkidle")
    assert not [u for u in asked if u.endswith("/text")]
    assert UNO_FULL_TEXT not in page.content()

    page.locator("details.fulltext > summary").click()
    page.locator(".fulltext-body").wait_for()

    assert [u for u in asked if u.endswith(f"/card/{UNO_RULES}/text")]
    assert UNO_FULL_TEXT in page.locator(".fulltext-body").inner_text()
    _shot(page, "390-en-fulltext")
    context.close()


def test_a_diary_entrys_photos_open_one_by_one_and_lead_back_to_the_mosaic(chrome, pages):
    """Given an entry with seven photos, when the third tile is tapped, then
    it shows full size with "3 of 7"; the arrow steps to "4 of 7", the
    arrow key back to "3 of 7", and "All photos" shows the mosaic again."""
    context = chrome.new_context(viewport=WIDTHS["390"], locale="en-US")
    page = context.new_page()
    page.goto(f"{pages}/card/{GRANDMA}")
    viewer, mosaic = page.locator(".viewer"), page.locator(".mosaic")
    assert mosaic.is_visible() and not viewer.is_visible()

    page.locator(".mosaic .tile").nth(2).click()
    assert viewer.locator(".count").inner_text() == "3 of 7"
    assert viewer.locator("img.shown").get_attribute("src").endswith(GRANDMA_PHOTOS[2])
    assert not mosaic.is_visible()
    _shot(page, "390-en-entry-photo")

    viewer.locator(".nav.next").click()
    assert viewer.locator(".count").inner_text() == "4 of 7"
    page.keyboard.press("ArrowLeft")
    assert viewer.locator(".count").inner_text() == "3 of 7"

    page.locator(".allphotos").click()
    assert mosaic.is_visible() and not viewer.is_visible()
    context.close()


@pytest.mark.skipif(not SHOTS, reason="screenshots only with MEMORY_SHOTS=<dir>")
@pytest.mark.parametrize("language", ["en", "de"])
@pytest.mark.parametrize("width", list(WIDTHS))
def test_the_pages_as_people_see_them(chrome, pages, width, language):
    context = chrome.new_context(viewport=WIDTHS[width], locale="de-DE" if language == "de" else "en-US",
                                 extra_http_headers={"accept-language": language})
    page = context.new_page()
    for kind, card in PAGES.items():
        page.goto(f"{pages}/card/{card}")
        assert page.locator(".kicker").count() == 1, kind
        _shot(page, f"{width}-{language}-{kind}")
    context.close()
