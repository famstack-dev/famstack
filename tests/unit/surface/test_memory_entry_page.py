"""A diary entry's page, as a phone on the home network reads it.

A diary entry is a diary card. It can hold a voice message, photos, a
video, a file and replies. Its page shows them as sections, in the order
they were sent: the first message's medium with its words under it (a
voice message with its transcript, a photo with its caption, or the
text), then the other media (2 to 10 photos in a row as a mosaic, a
video, a file), then each reply under "Added by <name>". The facts line
says for whom, the day and what the entry holds ("Voice and 7 photos").
The title and the summary are famstack's narrative and never look like a
quote; the family's words are quoted.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance with its media store: a real curator over a bare
repository, which core reaches over HTTP on localhost.
"""

from __future__ import annotations

import re

import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import (DIARY_ENTRIES, ENTRY_MEDIA, GRANDMA, GRANDMA_PHOTOS,
                                              GRANDMA_WORDS, LAKE, PLAY, SAX, VAULT)

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("entries"), {**VAULT, **DIARY_ENTRIES}) as running:
        for path, (data, record) in ENTRY_MEDIA.items():
            running.keeps_file(path, data, **record)
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return server.app


def opens(app, path: str, language: str = "en") -> Page:
    page = Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000",
                   language=language).opens(path)
    assert page.status == 200, page
    return page


def sections(page: Page) -> list[str]:
    """The kinds of the entry's sections, in page order."""
    return re.findall(r'data-section="([a-z]+)"', page.body)


# ── A voice message and seven photos ──────────────────────────────────────

def test_a_voice_message_and_seven_photos_show_as_a_voice_section_and_a_mosaic(tools):
    """Given a diary card with a voice message and seven photos, when its
    page opens, then the facts say what it holds, the Voice section has
    its player and transcript, and the seven photos form one mosaic."""
    page = opens(tools, f"/card/{GRANDMA}")

    assert page.texts("facts") == ["For Lisa · 3 November · Voice and 7 photos"]
    assert page.texts("extra") == ["Date as said in the recording"]
    assert sections(page) == ["voice", "images"]
    assert page.texts("play") == ["▶ 1:12"]
    assert page.texts("words")[0] == GRANDMA_WORDS
    assert 'class="mosaic mosaic-7"' in page.body
    assert page.links("mosaic") == [("", f"/card/api/media/{p}") for p in GRANDMA_PHOTOS]


def test_the_voice_section_plays_the_recording_from_the_media_store(tools):
    page = opens(tools, f"/card/{GRANDMA}")

    assert '<audio preload="none" src="/card/api/media/2026/11/GrandmaVoiceEvent.m4a">' in page.body
    assert "Play the recording" not in page.texts("target")


def test_a_reply_shows_under_who_added_it_with_its_words_quoted(tools):
    """The card stores no reply's day, so the line names only who added it."""
    page = opens(tools, f"/card/{PLAY}")

    assert page.texts("added-by") == ["Added by Marge"]
    assert page.texts("words")[-1] == "You were the best tree."


def test_the_summary_is_narrative_and_the_family_words_are_quoted(tools):
    """The summary stays famstack's text under the title; the transcript is
    quoted once, in its section, and nowhere in the narrative."""
    page = opens(tools, f"/card/{GRANDMA}")
    body = page.texts("body")[0]

    assert "Grandma Bouvier came for the day" in body
    assert GRANDMA_WORDS not in body
    assert not any("Grandma Bouvier" in w for w in page.texts("words"))
    assert "Quotes" not in page.text and "Transcript" not in page.text


# ── The other kinds ───────────────────────────────────────────────────────

def test_an_entry_of_one_text_shows_one_text_section_and_no_mosaic(tools):
    page = opens(tools, f"/card/{SAX}")

    assert page.texts("facts") == ["For Lisa · 28 October · Text"]
    assert sections(page) == ["text"]
    assert page.texts("words") == ["I played the whole piece without a mistake today!"]
    assert "mosaic" not in page.body


def test_a_photo_the_media_store_lacks_shows_what_it_was_without_a_broken_image(tools):
    """Given three photos of which the store keeps two, then the mosaic has
    three tiles, two of them photos and one that says what is missing."""
    page = opens(tools, f"/card/{LAKE}")

    assert page.texts("facts") == ["For Marge · 15 August · 3 photos"]
    assert sections(page) == ["images"]
    assert len(page.links("mosaic")) == 2
    assert page.texts("missing") == ["Not in the memory's files"]
    assert "LakeFireEvent.png" not in page.body
    assert page.texts("caption") == ["The lake was warm enough to swim."]


def test_a_video_a_file_and_a_reply_each_get_their_section(tools):
    page = opens(tools, f"/card/{PLAY}")

    assert page.texts("facts") == ["For Bart · 1 April · Video, a file and a reply"]
    assert sections(page) == ["video", "file", "added"]
    assert page.texts("playover") == ["▶ 0:12"]
    assert page.texts("filename") == ["programme.pdf"]
    assert page.texts("filekind") == ["PDF"]
    assert page.links("filerow") == [("Open", "/card/api/media/2026/04/PlayProgrammeEvent.pdf")]
    assert page.texts("added-by") == ["Added by Marge"]


def test_the_entry_reads_in_german(tools):
    page = opens(tools, f"/card/{GRANDMA}", language="de")

    assert page.texts("facts") == ["Für Lisa · 3. November · Sprachnachricht und 7 Fotos"]
    assert page.texts("allphotos") == ["Alle Fotos"]
    assert opens(tools, f"/card/{PLAY}", language="de").texts("added-by") == ["Ergänzt von Marge"]


# ── Resting state ─────────────────────────────────────────────────────────

def test_opening_an_entry_changes_no_diary_card(tools, memory):
    _, before = memory.api.card(GRANDMA)
    opens(tools, f"/card/{GRANDMA}")
    _, after = memory.api.card(GRANDMA)

    assert after == before
