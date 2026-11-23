"""A note's card page shows the messages it was filed from.

A note filed from a chat keeps each message as a source (ADR-018). Its
page shows them under the cleaned-up text, as a diary entry's page shows
its own: a text quoted, a photo with its caption, photos in a row as one
mosaic, a file with "Open", a later reply as "Added by Marge, 16
November". A note the AI wrote carries ✦ beside its date, a key line that
says so, and "Something wrong?" with how to correct it in the room. A
note the AI did not write, and a diary card, look as they did.

Driven through the tools server's FastAPI app (the Browser DSL), as the
diary entry page's tests are: memory's test instance with its media
store, a real curator over a bare repository.
"""

from __future__ import annotations

import json
import re

import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import (DIARY_ENTRIES, ENTRY_MEDIA, GRANDMA, PLAY_PROGRAMME,
                                              VAULT, VISIT_PHOTOS)

from .conftest import tools_server
from .dsl import Browser, Page

CARDS_PORT = tools_server("access").CARDS_PORT
QUILT, AS_SENT, SENT_ONLY = "note-9a1c3e5f70", "note-2b4d6f8a10", "note-3c5e7a9b21"
SENT = "2026-11-15T20:31:00+00:00"


def _source(n: int, kind: str, *, role: str = "original", when: str = SENT, channel: str = "chat") -> dict:
    return {"role": role, "person": "marge", "time": when, "channel": channel, "kind": kind,
            "id": f"$msg{n}"}


def _note(card_id: str, title: str, text: str, quotes: list[str], sources: list[dict],
          made: dict) -> dict[str, str]:
    """A note and its sidecar by path: the text, then `## Sources` with one quote per source."""
    shown = "\n\n".join("\n".join(f"> {line}" for line in q.split("\n")) for q in quotes)
    card = (f"---\ntype: note\ncard_id: {card_id}\ntitle: {title}\ndate: \"2026-11-15\"\n"
            f"persons:\n  - marge\ntags: []\n---\n\n# {title}\n\n{text}\n\n## Sources\n\n{shown}\n")
    stem = f"marge/notes/2026/11/{title.lower().replace(' ', '-')}"
    sidecar = json.dumps({"card_id": card_id, "filed_by": "marge", "sources": sources,
                          "made": made}, indent=2) + "\n"
    return {f"{stem}.md": card, f"{stem}.json": sidecar}


# Six messages: two lines, three photos in a row, a PDF; and a reply the next day.
_QUILT = _note(
    QUILT, "Binding corners",
    "Cut the binding strips 2.5 inches wide and mitre each corner.",
    ["I started on the binding today.", f"![[/media/{VISIT_PHOTOS[0]}]]\nThe first corner",
     f"![[/media/{VISIT_PHOTOS[1]}]]", f"![[/media/{VISIT_PHOTOS[2]}]]",
     f"[programme.pdf](/media/{PLAY_PROGRAMME})", "The strips were 2.5 inches.",
     "Bart says the corners need a second pass."],
    [_source(1, "text"), _source(2, "image"), _source(3, "image"), _source(4, "image"),
     _source(5, "file"), _source(6, "text"),
     _source(7, "text", role="reply", when="2026-11-16T09:00:00+00:00")],
    {"model": "qwen3", "prompt_template": "7c2e91a4"})

# Filed while the AI was down: sources, no prompt template.
_AS_SENT = _note(AS_SENT, "Lunch list", "Bread and cheese.", ["Bread and cheese."],
                 [_source(1, "text")], {"model": ""})

NOTES = {**_QUILT, **_AS_SENT}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("note-entries"),
                        {**VAULT, **DIARY_ENTRIES, **NOTES}) as running:
        for path, (data, record) in ENTRY_MEDIA.items():
            running.keeps_file(path, data, **record)
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    server = tools_server("server")
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return server.app


def opens(app, card_id: str, language: str = "en") -> Page:
    page = Browser(app, listener=CARDS_PORT, host="127.0.0.1:42002",
                   language=language).opens(f"/card/{card_id}")
    assert page.status == 200, page
    return page


def sections(page: Page) -> list[str]:
    return re.findall(r'data-section="([a-z]+)"', page.body)


# ── The messages, as sent ─────────────────────────────────────────────────

def test_a_note_shows_its_messages_in_the_order_sent_below_its_text(tools):
    """Given a note filed from 6 messages with 3 photos and a PDF, when its page opens,
    then below the text the messages show in the order sent: a text quoted, the photos
    as one mosaic with the first caption, the file with "Open", a text."""
    page = opens(tools, QUILT)

    assert sections(page) == ["text", "images", "file", "text", "added"]
    assert page.body.index("mitre each corner") < page.body.index('data-section="text"')
    assert page.texts("words")[0] == "I started on the binding today."
    assert page.texts("caption") == ["The first corner"]
    assert len(re.findall(r'class="tile"', page.body)) == 3
    assert "programme.pdf" in page.text and "Open" in page.texts("inlinebtn")
    assert "Sources" not in page.texts("body")[0]


def test_a_reply_in_the_thread_shows_who_added_it_and_the_day(tools):
    """Given a reply added after filing, then it reads "Added by Marge, 16 November"."""
    page = opens(tools, QUILT)

    assert page.texts("added-by") == ["Added by Marge, 16 November"]
    assert page.texts("words")[-1] == "Bart says the corners need a second pass."


# ── The AI's mark ─────────────────────────────────────────────────────────

def test_a_note_the_ai_wrote_has_a_star_beside_its_date_and_a_key_line(tools):
    """Given a note whose sidecar names a prompt template, then ✦ stands beside the
    date with its hover text, and the key says who wrote what, from how many messages."""
    page = opens(tools, QUILT)

    assert re.search(r'Sun 15 Nov\s*<span class="ai-mark"[^>]*title="Summarised by famstack\'s AI"',
                     page.body) or re.search(r'15 November\s*<span class="ai-mark"', page.body)
    assert ("The title and the text are famstack's AI's, written once at filing from 6 messages. "
            "The messages are below, as sent.") in page.text


def test_a_note_filed_while_the_ai_was_down_has_no_star_and_no_key(tools):
    """Given sources and no prompt template, then no ✦ and no key line, and the messages show."""
    page = opens(tools, AS_SENT)

    assert "ai-mark" not in page.body and "written once at filing" not in page.text
    assert sections(page) == ["text"]


# ── How to correct it ─────────────────────────────────────────────────────

def test_a_note_filed_from_a_room_says_how_to_correct_it_with_its_own_words(tools):
    page = opens(tools, QUILT)

    assert "Something wrong?" in page.text
    assert re.search(r"Reply in the thread of its notice in the room, for example: .{0,5}Cut the "
                     r"binding strips", page.text)


# ── The resting state ─────────────────────────────────────────────────────

def test_a_diary_card_and_a_note_without_sources_look_as_before(tools):
    diary = opens(tools, GRANDMA)
    grill = opens(tools, "note-0b6e2d91aa")

    assert "ai-mark" not in diary.body and "Something wrong?" not in diary.text
    assert sections(grill) == [] and "Something wrong?" not in grill.text
