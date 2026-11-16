"""A diary entry is dated by the day its photos were taken.

Matrix records only when a message reached the server, so photos sent
days after they were taken would land on the day they were sent. The
photo file can still say when it was taken: its EXIF `DateTimeOriginal`.
Element X on Android keeps it; on the iPhone only with "Optimise media"
off, and its default strips it. So the day a photo was taken dates an
entry whose words name no day, and the day it was sent stays the exact
fallback for every photo that carries none.

The first tests drive the compile as the admin does (`./stack memory
diary`), with the photo on the stand-in media store, and read the card
the memory kept. The others ask the dating step (`date_for`) and the
media store's reader (`stack.media_dates.capture_day`) directly. The family is
the Simpsons.
"""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)
from __future__ import annotations

import io
import json
import re
from datetime import date, datetime, timezone

import pytest
from PIL import Image

from stack import media_dates
from stack.message_groups import MessageGroup
from stacklets.memory.bot.diary.dating import basis_text, date_for
from stacklets.memory.bot.diary.entries import Reading
from stacklets.memory.bot.diary.messages import Message
from stacklets.memory.bot.diary.texts import configure_language
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401  (fixtures)
    ENTRIES, bot_command, bot_runner, brain, diary, memory, model, room,
)

# Sent on 10 November at noon UTC: the same day on the household's clock.
SENT_TS = int(datetime(2026, 11, 10, 12, tzinfo=timezone.utc).timestamp() * 1000)
HALLOWEEN = "2026:10:31 15:20:00"
# The EXIF tags `capture_day` reads: the Exif sub-IFD and its DateTimeOriginal.
EXIF_IFD, DATE_TIME_ORIGINAL = 0x8769, 0x9003


def jpeg(taken: str | None = None) -> bytes:
    """A small JPEG as a phone sends it, with `taken` as its
    DateTimeOriginal, or without EXIF as an optimising client sends it."""
    out = io.BytesIO()
    exif = Image.Exif()
    if taken:
        exif.get_ifd(EXIF_IFD)[DATE_TIME_ORIGINAL] = taken
    Image.new("RGB", (8, 8), "orange").save(out, "JPEG", exif=exif)
    return out.getvalue()


@pytest.fixture
def media_store(httpserver, monkeypatch, tmp_path):
    """The homeserver's media store and the archive the compile keeps files in.

    `serves(media_id, data)` puts a file behind `mxc://simpson/<media_id>`.
    """
    monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(tmp_path / "archive"))

    def serves(media_id: str, data: bytes) -> str:
        httpserver.expect_request(
            f"/_matrix/client/v1/media/download/simpson/{media_id}").respond_with_data(data)
        return f"mxc://simpson/{media_id}"
    return serves


def photo_sent(room, url: str, *, ts: int = SENT_TS, caption: str = "") -> None:
    """Marge sends one photo, with the filename as its body as clients do."""
    content = {"msgtype": "m.image", "body": "IMG_0031.jpg", "url": url,
               "info": {"mimetype": "image/jpeg"}}
    if caption:
        content["body"], content["filename"] = caption, "IMG_0031.jpg"
    room.events.append({"type": "m.room.message", "event_id": f"$p{len(room.events)}:simpson",
                        "sender": "@marge:simpson", "origin_server_ts": ts,
                        "content": content})


def dated(memory) -> list[tuple[str, str]]:
    """Each diary card's day, from its frontmatter, and how the day was
    found, from the first source in its sidecar."""
    out = []
    for path, text in memory.pages(ENTRIES).items():
        on = re.search(r'^date: "?([\d-]+)"?$', text, re.M).group(1)
        sidecar = json.loads(memory.forgejo.file(path.removesuffix(".md") + ".json"))
        out.append((on, sidecar["sources"][0]["date_basis"]))
    return out


# ── Through the compile ──────────────────────────────────────────────────

def test_a_photo_taken_before_it_was_sent_is_dated_by_the_day_it_was_taken(
        memory, room, diary, media_store):
    """Given a photo taken 31 October and sent 10 November without words,
    when the admin compiles the diary, then its card is on 31 October,
    dated from when the photo was taken."""
    photo_sent(room, media_store("halloween", jpeg(HALLOWEEN)))

    diary.runs()

    assert dated(memory) == [("2026-10-31", "taken")]


def test_a_photo_without_a_capture_day_is_dated_by_the_day_it_was_sent(
        memory, room, diary, media_store):
    """Given a photo an optimising client sent without EXIF, when the diary
    compiles, then its card is on the day it was sent, without a warning."""
    photo_sent(room, media_store("stripped", jpeg()))

    diary.runs()

    assert dated(memory) == [("2026-11-10", "sent")]


def test_a_file_whose_exif_cannot_be_read_counts_as_having_no_capture_day(
        memory, room, diary, media_store):
    """Given a photo whose bytes are no image at all, when the diary
    compiles, then the entry is still filed, on the day it was sent."""
    photo_sent(room, media_store("broken", b"\xff\xd8 not really a jpeg"))

    diary.runs()

    assert dated(memory) == [("2026-11-10", "sent")]


def test_a_capture_day_after_the_photo_was_sent_is_ignored(
        memory, room, diary, media_store):
    """Given a photo whose camera clock says 2099, sent 10 November, when
    the diary compiles, then its card is on the day it was sent.

    A photo cannot be taken after it was sent, so that day is a wrong
    clock, and a confidently wrong day is worse than the day it was sent."""
    photo_sent(room, media_store("future", jpeg("2099:01:01 10:00:00")))

    diary.runs()

    assert dated(memory) == [("2026-11-10", "sent")]


def test_a_card_on_file_keeps_its_day_until_a_rebuild(
        memory, room, diary, media_store, monkeypatch):
    """Given a card filed on the day its photo was sent, as before the
    diary read capture days, when the diary compiles again, then the card
    keeps its day; `--rebuild` dates it by the day the photo was taken.

    A compile writes a card again only when its sources change."""
    photo_sent(room, media_store("halloween", jpeg(HALLOWEEN)))
    monkeypatch.delenv("MEDIA_ARCHIVE_DIR")
    diary.runs()
    monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(memory.vault.parent / "archive"))

    diary.runs()
    kept = dated(memory)
    diary.runs("--rebuild")

    assert kept == [("2026-11-10", "sent")]
    assert dated(memory) == [("2026-10-31", "taken")]


# ── The dating step ──────────────────────────────────────────────────────

def _photo(event_id: str = "$p", *, taken: date | None = None, ts: int = SENT_TS,
           kind: str = "image", body: str = "") -> Message:
    return Message(event_id=event_id, sender="marge", ts=ts, kind=kind,
                                body=body, url=f"mxc://simpson/{event_id[1:]}", taken=taken)


def _group(*messages, queued: bool = False) -> MessageGroup:
    return MessageGroup(sender="marge", messages=messages, replies=(), queued=queued,
                        ended_by=messages[-1].event_id, closes_at_ms=messages[-1].ts)


def test_a_day_the_words_name_wins_over_the_day_the_photo_was_taken():
    """Given that photo with the line "Grampa visited us on Halloween" read
    as naming 31 October 2025, then the named day dates the entry."""
    photo = _photo(taken=date(2026, 10, 31))
    line = _photo("$l", kind="text", ts=SENT_TS + 60_000, body="Grampa visited us on Halloween")

    on, basis, _ = date_for(
        _group(photo, line), {"$l": Reading(spoken_date="2025-10-31")})

    assert (on, basis) == (date(2025, 10, 31), "spoken")


def test_photos_taken_on_two_days_date_the_entry_by_the_earliest():
    later = _photo("$a", taken=date(2026, 10, 31))
    earlier = _photo("$b", taken=date(2026, 10, 30), ts=SENT_TS + 1000)

    on, basis, _ = date_for(_group(later, earlier), {})

    assert (on, basis) == (date(2026, 10, 30), "taken")


def test_a_photo_of_a_late_queue_with_a_capture_day_is_dated_by_it():
    """A queue's arrival day is known to be off; a photo's own day is not."""
    on, basis, _ = date_for(
        _group(_photo(taken=date(2026, 10, 31)), queued=True), {})

    assert (on, basis) == (date(2026, 10, 31), "taken")


@pytest.mark.parametrize("language", ["en", "de"])
def test_the_basis_taken_has_its_own_sentence_in_each_language(language):
    configure_language(language)

    assert basis_text("taken") not in (
        basis_text("sent"), basis_text("spoken"))


# ── The media store's reader ─────────────────────────────────────────────

def test_capture_day_reads_the_day_a_photo_was_taken(tmp_path):
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpeg(HALLOWEEN))

    assert media_dates.capture_day(path) == date(2026, 10, 31)


@pytest.mark.parametrize("data", [jpeg(), b"not an image", jpeg("0000:00:00 00:00:00")])
def test_capture_day_is_none_for_a_file_without_a_readable_one(tmp_path, data):
    path = tmp_path / "photo.jpg"
    path.write_bytes(data)

    assert media_dates.capture_day(path) is None


def test_capture_day_is_none_for_a_missing_file(tmp_path):
    assert media_dates.capture_day(tmp_path / "gone.jpg") is None


def test_capture_days_read_the_original_when_a_derived_copy_is_shown(tmp_path):
    """Given a kept `.jpeg` original and its bounded `.jpg` copy, which a
    re-encode leaves without EXIF, when the days are read for the copy's
    link, then the day comes from the original beside it."""
    folder = tmp_path / "2026" / "11"
    folder.mkdir(parents=True)
    (folder / "p1.jpeg").write_bytes(jpeg(HALLOWEEN))
    (folder / "p1.jpg").write_bytes(jpeg())
    (folder / "p1.json").write_text("{}")

    days = media_dates.capture_days(tmp_path, {"$p1": "/media/2026/11/p1.jpg"})

    assert days == {"$p1": date(2026, 10, 31)}
