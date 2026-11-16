# ruff: noqa: F811
"""A photo sent without words is shown as just the photo.

The diary used to write "Nothing was written alongside this one." under
an upload without a caption, and asked the model to title it from
"(no words)". Both read like a placeholder. These tests drive the compile
as the admin runs it (`stack memory diary`) against the compile test's
stand-in room and model, and the month page as `render_month` writes it.
"""
from __future__ import annotations

import re
from datetime import date

from stacklets.memory.bot import diary as diary_pages
# The room, the model and the fixtures the compile tests share.
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401
    ENTRIES, Model, Room, bot_command, bot_runner, brain, diary, memory, room)
import pytest

from stacklets.memory.bot import diary_card


class NoModelModel(Model):
    """Fails the test when it is asked to read an entry for a card."""

    def __init__(self):
        super().__init__()
        self.reads_allowed = False
        self.summary_prompts: list[str] = []

    async def complete(self, role, prompt: str, **kw) -> str:
        # The month summary is the one prompt that carries the entries'
        # evidence lines, "- [1 October] Marge: ...".
        if re.search(r"^- \[[^\]]+\] ", prompt, re.M):
            self.summary_prompts.append(prompt)
        assert self.reads_allowed or not prompt.startswith("You are filing one entry"), (
            "an entry without words must not be read by the model")
        return await super().complete(role, prompt, **kw)


@pytest.fixture
def model() -> NoModelModel:
    return NoModelModel()


def a_photo(room: Room, who: str = "marge", caption: str = "") -> str:
    return room._post(who, {"msgtype": "m.image", "body": caption or "IMG_0001.png",
                            "filename": "IMG_0001.png"})


def test_a_photo_without_words_gets_its_kind_and_day_as_title_and_no_model_call(
        memory, room, diary):
    a_photo(room)
    diary.runs()

    texts = [t for _, t in sorted(memory.pages(ENTRIES).items())]
    assert len(texts) == 1
    assert re.search(r"^title: .*(Photo|Foto), ", texts[0], re.M)
    assert diary_card.parse(texts[0]).summary == ""


def test_a_second_compile_reports_no_card_made_under_older_prompts(
        memory, room, diary):
    a_photo(room)
    diary.runs()

    diary.runs()

    assert "older prompts" not in diary.progress + diary.printed


def test_a_reply_to_a_photo_without_words_makes_the_next_compile_read_it(
        memory, room, diary, model):
    """The reply is words: the entry is read once, by the compile after it."""
    model_reads = model.entries_read
    photo = a_photo(room)
    diary.runs()
    assert model_reads == []
    model.reads_allowed = True
    room.answers("marge", photo, "So cute!")

    diary.runs()
    diary.runs()

    assert len(model_reads) == 1


def test_the_month_summary_reads_a_photo_without_words_as_a_photo(
        memory, room, diary, model):
    a_photo(room)
    diary.runs()

    prompts = model.summary_prompts

    assert prompts
    assert all("(a photo)" in p and "no caption" not in p for p in prompts)


def test_a_photo_without_words_shows_only_the_photo_on_the_month_page():
    page = diary_pages.render_month([diary_pages.Entry(
        on=date(2026, 4, 3), confidence="sent", basis="b", kind="image", sender="homer",
        body="")])

    assert "Nothing was written" not in page
    assert "Hierzu wurde nichts geschrieben" not in page


def test_an_untranscribable_recording_keeps_its_sentence():
    page = diary_pages.render_month([diary_pages.Entry(
        on=date(2026, 9, 14), confidence="sent", basis="b", kind="voice", sender="homer",
        body="")])

    assert "This recording could not be transcribed." in page
