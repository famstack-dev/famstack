"""A note made from a file, as a phone on the home network reads its page.

A PDF, a text file or a photo saved in a room becomes a note. The capture
keeps the file in the memory's media store, with a `.json` beside it that
gives its name, type and size, and links or embeds it in the note's first
source; the source's quote holds the file's text after the link, or the
photo's caption after the embed. The card's own text is famstack's
summary of the file, or its description of the photo.

The page shows such a note like a bookmark: the kicker names the file's
kind with a paper clip, the summary, a panel that opens the file from
the media store, and "Full text" closed until it is opened. A photo shows
in its panel with "Open the image", then the caption and the description.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance, with its media store: a real curator over a bare
repository, which core reaches over HTTP on localhost.
"""

from __future__ import annotations


import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import (BENCH_PNG, FILE_NOTES, GARDEN_BENCH, MEDIA, PANCAKES,
                                              PANCAKES_MD, UNO_FULL_TEXT, UNO_PDF, UNO_RULES, VAULT)

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

UNO, BENCH, RECIPE = UNO_RULES, GARDEN_BENCH, PANCAKES
GRILL = "note-0b6e2d91aa"
RECIPE_MD, FULL_TEXT = PANCAKES_MD, UNO_FULL_TEXT


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("file-notes"), {**VAULT, **FILE_NOTES}) as running:
        for path, (data, record) in MEDIA.items():
            running.keeps_file(path, data, **record)
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return server.app


def phone(app, language: str = "en") -> Browser:
    return Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)


def opens(app, path: str, language: str = "en") -> Page:
    page = phone(app, language).opens(path)
    assert page.status == 200, page
    return page


# ── A note made from a PDF ────────────────────────────────────────────────

def test_a_note_from_a_pdf_shows_its_kind_its_facts_and_its_summary(tools):
    """Given a note made from a PDF, when its page opens, then the kicker
    names the kind with a paper clip, the facts say who saved it, in which
    room and when, and the text is the summary."""
    page = opens(tools, f"/card/{UNO}")

    assert page.texts("kicker") == ["Note · PDF"]
    assert 'data-icon="clip"' in page.body.split('class="kicker"', 1)[1].split("</p>", 1)[0]
    assert page.texts("facts") == ["Marge · Games · 7 November"]
    assert "The official UNO rules on two pages" in page.texts("body")[0]


def test_the_panel_opens_the_pdf_from_the_media_store(tools):
    """The panel names the file, its kind and size, and its link reads the
    file from the media store."""
    page = opens(tools, f"/card/{UNO}")

    [(text, href)] = page.links("target")
    assert text == "Open the PDFuno-rules.pdfPDF · 240 KB ↗"
    assert href == f"/card/api/media/{UNO_PDF}"
    assert phone(tools).opens(href).status == 200


def test_the_full_text_is_closed_and_not_on_the_page(tools):
    """"Full text" is closed and carries nothing but the loading line: the
    page names where to ask, and only that answer holds the text."""
    page = opens(tools, f"/card/{UNO}")

    assert page.texts("fulltext") == ["Full textLoading the full text…"]
    assert f'data-src="/card/{UNO}/text"' in page.body
    assert FULL_TEXT not in page.body

    asked = opens(tools, f"/card/{UNO}/text")
    assert FULL_TEXT in asked.text
    assert "uno-rules.pdf" not in asked.text


def test_another_file_kind_names_its_kind(tools):
    page = opens(tools, f"/card/{RECIPE}")

    assert page.texts("kicker") == ["Note · Markdown"]
    [(text, href)] = page.links("target")
    assert text.startswith("Open the filepancakes.md")
    assert href == f"/card/api/media/{RECIPE_MD}"


# ── A note made from an image ─────────────────────────────────────────────

def test_a_note_from_an_image_shows_the_image_the_caption_and_the_description(tools):
    """Given a note made from a photo, then the page shows the photo in its
    panel with a link to it at full size, the caption quoted, and
    famstack's description as the card's text."""
    page = opens(tools, f"/card/{BENCH}")

    assert page.texts("kicker") == ["Note · Image"]
    assert page.texts("facts") == ["Homer · Garden · 5 November"]
    assert page.attribute("img", "src") == f"/card/api/media/{BENCH_PNG}"
    assert page.links("photo") == [("Open the image ↗", f"/card/api/media/{BENCH_PNG}")]
    assert page.texts("caption") == ["This is the one I mean for under the apple tree."]
    assert "A wooden bench built in a ring" in page.texts("body")[0]
    assert page.links("target") == []


# ── Without its file, in a list, in German, and a written note ────────────

def test_without_its_file_the_page_leaves_the_panel_out(tools, memory):
    """A copy of the memory without its media store: no panel, no image,
    no link to a file that is not there."""
    kept = memory.media / UNO_PDF
    record = kept.with_suffix(".json")
    data, side = kept.read_bytes(), record.read_bytes()
    kept.unlink()
    record.unlink()
    try:
        page = opens(tools, f"/card/{UNO}")
    finally:
        kept.write_bytes(data)
        record.write_bytes(side)

    assert page.texts("kicker") == ["Note · PDF"]
    assert page.links("target") == []
    assert "/card/api/media/" not in page.body


def test_the_page_in_german(tools):
    page = opens(tools, f"/card/{UNO}", language="de")

    assert page.texts("kicker") == ["Notiz · PDF"]
    assert page.texts("fulltext") == ["VolltextVolltext wird geladen…"]
    assert page.links("target")[0][0].startswith("PDF öffnenuno-rules.pdfPDF · 240 KB")
    assert opens(tools, f"/card/{BENCH}", language="de").links("photo")[0][0] == "Bild öffnen ↗"


def test_a_written_note_looks_as_before(tools):
    page = opens(tools, f"/card/{GRILL}")

    assert page.texts("kicker") == ["Note"]
    assert page.texts("fulltext") == []
    assert page.links("target") == []
