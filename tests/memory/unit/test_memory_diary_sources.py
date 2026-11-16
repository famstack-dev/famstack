"""A diary card keeps its sources (ADR-018): one quote per message and per reply.

A diary entry gathers messages: photos, a line under them, a reply the
next day. Its card keeps what the entry is about, then `## Sources`, one
blockquote per message and per reply in the order sent, and the sidecar
`<stem>.json` beside it keeps who sent each one, when, and what it is.
Quote n belongs to `sources[n]`.

Two ways reach that shape, and both are driven here:

- The compile (`./stack memory diary`), as the admin runs it, with the
  room on the stand-in Synapse and its photos on the stand-in media
  download. It writes new cards in the format.
- Migration memory/0012, for the cards written before: their sources
  were in their frontmatter and their body. It converts each without a
  model call and without reading the room, in one commit by the memory
  bot, through the curator. What the old card did not record (a reply's
  time) is kept as `unknown` and marked `reconstructed`.

The memory is memory's test instance (`running_memory`): Forgejo, a bare
repository, and the curator in front of it. The family is the Simpsons.
"""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from stacklets.memory.format_check import format_report
from stacklets.memory.graph import load_graph
from stacklets.memory.lib import BOT_USERNAME
from stacklets.memory.old_diary_cards import give_diary_cards_their_sources
from stack.cards.sources import quotes_of, sources_of
from stack.digest import changed_by_hand, signed
from stack.frontmatter import parse
from tests.memory.unit.instance import RunningMemory, git, running_memory
from tests.memory.unit.diary_cards import with_parts
from tests.memory.unit.simpsons_cards import ONTOLOGY
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401  (fixtures)
    BASE_TS, ENTRIES, HOUR_MS, Room, bot_command, bot_runner, brain, commits_since, model,
    diary, head, room,
)
from stacklets.memory.bot.diary.card import files as card_files, parse as parse_card, to_card, to_entry
from stacklets.memory.bot.diary.entries import Entry
from stacklets.memory.bot.diary.extraction import Extraction
from stacklets.memory.bot.diary.sources import media_of

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DAY_MS = 24 * HOUR_MS


def _side(path: str) -> str:
    return path.removesuffix(".md") + ".json"


# ── The compile writes a card with its sources ───────────────────────────

@pytest.fixture
def memory(tmp_path) -> Iterator[RunningMemory]:
    """The memory as a fresh install leaves it, holding the ontology."""
    with running_memory(tmp_path, {"ontology.toml": ONTOLOGY}, seeded_by="stackadmin",
                        seeded_as="seed: the ontology") as running:
        yield running


PHOTOS = {"gate": (b"photo of the gate", "Grampa at the gate"),
          "cake": (b"photo of the cake", "The cake was enormous"),
          "car": (b"photo of the car", "Waving goodbye")}
LINE = "Grampa came for lunch today."
REPLY = "He left his hat here."


def _photo(room: Room, n: int, media_id: str, caption: str, ts: int) -> str:
    """Homer's photo with its caption, as a client sends one: the file name
    in `filename`, the caption in `body`."""
    event_id = f"${media_id}:simpson"
    room.events.append({"type": "m.room.message", "event_id": event_id,
                        "sender": "@homer:simpson", "origin_server_ts": ts,
                        "content": {"msgtype": "m.image", "body": caption,
                                    "filename": f"IMG_00{n}.jpg",
                                    "url": f"mxc://simpson/{media_id}",
                                    "info": {"mimetype": "image/jpeg"}}})
    return event_id


@pytest.fixture
def grampas_visit(room, httpserver, monkeypatch, tmp_path) -> dict[str, str]:
    """Homer's three photos, each with its caption, and his line under them,
    on 10 November; Marge's reply to the first photo on 11 November. The
    media store keeps what the compile archives. Returns the event ids."""
    monkeypatch.setenv("MEDIA_ARCHIVE_DIR", str(tmp_path / "archive"))
    monkeypatch.setenv("TIMEZONE", "UTC")
    ten_november = BASE_TS + 40 * DAY_MS + 9 * HOUR_MS
    ids = {}
    for n, (media_id, (data, caption)) in enumerate(PHOTOS.items(), 1):
        httpserver.expect_request(
            f"/_matrix/client/v1/media/download/simpson/{media_id}").respond_with_data(data)
        ids[media_id] = _photo(room, n, media_id, caption, ten_november + n * 1000)
    ids["line"] = "$line:simpson"
    room.events.append({"type": "m.room.message", "event_id": ids["line"],
                        "sender": "@homer:simpson", "origin_server_ts": ten_november + 4000,
                        "content": {"msgtype": "m.text", "body": LINE}})
    ids["reply"] = "$reply:simpson"
    room.events.append({"type": "m.room.message", "event_id": ids["reply"],
                        "sender": "@marge:simpson", "origin_server_ts": ten_november + DAY_MS,
                        "content": {"msgtype": "m.text", "body": REPLY,
                                    "m.relates_to": {"m.in_reply_to": {"event_id": ids["gate"]}}}})
    return ids


def the_card(memory: RunningMemory) -> tuple[str, str, dict]:
    """The one diary card in the memory: its path, its text and its sidecar."""
    [(path, text)] = memory.pages(ENTRIES).items()
    return path, text, json.loads(memory.forgejo.file(_side(path)))


def test_three_photos_a_line_and_a_reply_make_five_quotes_and_five_sources(
        memory, diary, grampas_visit):
    """Given Homer's three photos with their captions and a line on 10
    November, and Marge's reply on 11 November, when the admin compiles the
    diary, then the card quotes each photo with its caption, the line and
    the reply, in that order, and the sidecar names Homer for the first
    four and Marge for the reply, each with its time and event id. Homer
    sent the photos and the line in one go, so the line is the entry's
    own, not a reply.

    The card is rebuilt from its sources; a photo's caption that is not
    its own quote is lost to every rebuild."""
    diary.runs()

    path, card, sidecar = the_card(memory)
    quotes = quotes_of(card)
    assert len(quotes) == 5
    for quote, (media_id, (_, caption)) in zip(quotes, PHOTOS.items()):
        embed, words = quote.split("\n", 1)
        assert re.fullmatch(rf"!\[\[/media/\d{{4}}/\d{{2}}/{media_id}-simpson\.jpg\]\]", embed)
        assert words == caption
    assert quotes[3:] == [LINE, REPLY]
    records = sidecar["sources"]
    assert [(r["role"], r["person"], r["kind"], r["id"]) for r in records] == [
        ("original", "homer", "image", grampas_visit["gate"]),
        ("original", "homer", "image", grampas_visit["cake"]),
        ("original", "homer", "image", grampas_visit["car"]),
        ("original", "homer", "text", grampas_visit["line"]),
        ("reply", "marge", "text", grampas_visit["reply"])]
    assert records[4]["time"].startswith("2026-11-11T")
    assert records[0]["media"] == f"sha256:{hashlib.sha256(PHOTOS['gate'][0]).hexdigest()}"


def test_a_compiled_card_keeps_only_what_it_is_about_in_its_frontmatter(
        memory, diary, grampas_visit):
    """Where the card came from is in its sidecar, not in the fields a
    person reads and edits."""
    diary.runs()

    _, card, sidecar = the_card(memory)
    assert set(parse(card)) <= {"type", "card_id", "title", "date", "description", "persons",
                                "tags", "addressee", "kind", "digest"}
    assert sidecar["card_id"] == parse(card)["card_id"] and sidecar["filed_by"] == "homer"
    assert sidecar["made"]["model"] == "stand-in" and sidecar["made"]["prompt_template"]


def test_an_unchanged_room_writes_nothing_on_the_next_compile(memory, diary, grampas_visit):
    """The resting state: a compile of a room nobody wrote in since changes
    no card and makes no commit, and the format check finds the card in
    shape."""
    diary.runs()
    mark = head(memory)

    diary.runs()

    assert commits_since(memory, mark) == []
    assert format_report(load_graph(memory.vault))["sources"] == []


def test_a_compile_that_knows_more_than_the_sidecar_writes_the_sidecar(
        memory, diary, grampas_visit):
    """Given a card whose sidecar lost the id of Marge's reply, as a card
    converted from the format before can, and whose text is as the
    compile writes it, when the diary compiles the room, then the sidecar
    gets the reply's id back, in one commit: a change only the sidecar
    shows is still a write."""
    diary.runs()
    path, _, sidecar = the_card(memory)
    sidecar["sources"][4]["id"] = "unknown"
    memory.edited_in_forgejo({_side(path): json.dumps(sidecar, indent=2) + "\n"})
    mark = head(memory)

    diary.runs()

    _, _, after = the_card(memory)
    assert after["sources"][4]["id"] == grampas_visit["reply"]
    assert len(commits_since(memory, mark)) == 1


# ── Migration 0012: the cards written before ─────────────────────────────

TRAILER = "Migration: memory/0012"
OWNED = "family/diary/entries/2026/10/2026-10-01-{}.md"
EDITED = "family/diary/entries/2026/09/2026-09-20-5a6b7c8d9e.md"
NOTE = "marge/notes/2026/10/grill-0b6e2d.md"
LAKE_PHOTO = "/media/2026/09/lake-simpson.jpg"
LAKE_SHA = "4168fe9973e0031ca1a1b8c445d653bc3f7c56a985eea2093c070ccc5cd8f008"
TREEHOUSE = "We built a treehouse in the backyard."


def _entry_id(event_id: str) -> str:
    return hashlib.sha256(event_id.encode()).hexdigest()[:10]


def _owned_card(event_id: str, reply_id: str) -> tuple[str, str]:
    """A card the compile wrote before, signed: Homer's line of 1 October
    at nine and Marge's reply to it, as an older release laid it out."""
    path = OWNED.format(_entry_id(event_id))
    text = signed(f"""---
type: diary
card_id: diary-1c2d3e4f5a
title: We built a treehouse in the backyard
date: "2026-10-01"
date_basis: sent
description: Answered by marge.
persons: []
tags: []
filed_by: homer
addressee: ""
kind: text
mode: note
resource: "https://matrix.to/#/!memories:simpson/{event_id}"
entry_id: {_entry_id(event_id)}
event_ids:
  - "{event_id}"
  - "{reply_id}"
media: []
model: stand-in
timestamp: "2026-10-01T09:00:00.000Z"
---

# We built a treehouse in the backyard

> **From** homer · **Date** 2026-10-01 (sent)

## Replies

**marge**
> Nice work, Homie.

## Text

{TREEHOUSE}
""")
    return path, text


EDITED_TEXT = """---
type: diary
card_id: diary-9e8d7c6b5a
title: A day at the lake
date: "2026-09-20"
date_basis: spoken
description: Lisa swam across the lake.
persons:
  - lisa
tags: []
filed_by: marge
addressee: Lisa
kind: image
mode: monologue
resource: "https://matrix.to/#/!memories:simpson/$lake:simpson"
entry_id: 5a6b7c8d9e
event_ids:
  - "$lake:simpson"
  - "$lake-line:simpson"
  - "$lake-answer:simpson"
media:
  - /media/2026/09/lake-simpson.jpg
model: stand-in
timestamp: "2026-09-20T15:04:05.000Z"
digest: 0123456789abcdef
---

# A day at the lake

> **From** marge · **For** Lisa · **Date** 2026-09-20 (spoken)

Lisa says she will swim it again next year.

![[/media/2026/09/maggie-boat.jpg]]

![[/media/2026/09/lake-simpson.jpg]]

## Replies

**homer**
> Woohoo, a swimmer!

## Caption

Lisa swam all the way across, Maggie watched from the boat.
"""

PEOPLE = {f"family/people/{who}.md": f"---\ntype: person\ncard_id: person-{who}\n"
          f"title: {who.capitalize()} Simpson\n---\n\n# {who.capitalize()} Simpson\n"
          for who in ("homer", "marge", "bart", "lisa")}

NOTE_TEXT = """---
type: note
card_id: note-0b6e2d91aa
title: Grill
date: "2026-10-03"
---

# Grill

Buy charcoal.
"""


@pytest.fixture
def media_root(tmp_path) -> Path:
    """The media store, with the record its capture wrote for the lake photo."""
    root = tmp_path / "media"
    (root / "2026" / "09").mkdir(parents=True)
    (root / "2026" / "09" / "lake-simpson.json").write_text(json.dumps(
        {"event_id": "$lake:simpson", "sha256": LAKE_SHA, "mime": "image/jpeg"}))
    return root


@pytest.fixture
def older(tmp_path) -> Iterator[RunningMemory]:
    """The memory as an older release left it: a card the compile owns, a
    card Marge changed by hand, and a note."""
    owned_path, owned = _owned_card("$m1:simpson", "$m2:simpson")
    vault = {"ontology.toml": ONTOLOGY, **PEOPLE, owned_path: owned, EDITED: EDITED_TEXT,
             NOTE: NOTE_TEXT}
    with running_memory(tmp_path / "data", vault, seeded_by="marge",
                        seeded_as="seed the vault") as running:
        running.owned_path = owned_path
        yield running


def migrate(memory: RunningMemory, media_root: Path):
    return give_diary_cards_their_sources(memory.api, memory.vault, media_root=media_root,
                                          trailer=TRAILER)


class TestTheCardsWrittenBeforeGetTheirSourcesOnce:

    @pytest.fixture
    def found(self, older, media_root):
        found, sha = migrate(older, media_root)
        assert sha == older.forgejo.head()
        return found

    def test_one_commit_by_the_memory_bot_with_every_card_and_sidecar(self, found, older):
        assert git(older.forgejo.repo, "log", "-1", "--format=%an|%s").strip() == \
            f"{BOT_USERNAME}|cards: give 2 diary card(s) their sources and a sidecar"
        assert TRAILER in git(older.forgejo.repo, "log", "-1", "--format=%B")
        changed = set(git(older.forgejo.repo, "show", "--name-only", "--format=", "HEAD").split())
        assert changed == {p for c in (older.owned_path, EDITED) for p in (c, _side(c))}

    def test_a_card_keeps_its_words_title_and_fields(self, found, older):
        """Every field that held a value; an empty list or text is left out,
        as the compile leaves it out of a new card."""
        card = older.page(older.owned_path)
        fields = {k: v for k, v in parse(card).items() if k != "digest"}
        assert fields == {"type": "diary", "card_id": "diary-1c2d3e4f5a",
                          "title": "We built a treehouse in the backyard",
                          "date": "2026-10-01", "description": "Answered by marge.",
                          "kind": "text"}
        assert "> **From** homer · **Date** 2026-10-01 (sent)" in card
        assert quotes_of(card) == [TREEHOUSE, "Nice work, Homie."]
        assert "## Replies" not in card and "## Text" not in card

    def test_its_messages_become_sources_from_its_old_fields(self, found, older):
        sidecar = json.loads(older.page(_side(older.owned_path)))
        assert (sidecar["card_id"], sidecar["filed_by"]) == ("diary-1c2d3e4f5a", "homer")
        original, reply = sidecar["sources"]
        assert original == {"role": "original", "person": "homer",
                            "time": "2026-10-01T09:00:00+00:00", "channel": "chat",
                            "kind": "text", "id": "$m1:simpson", "mode": "note",
                            "date_basis": "sent"}
        assert sidecar["made"] == {"model": "stand-in"}
        assert sources_of(older.page(older.owned_path), sidecar)

    def test_a_reply_keeps_its_id_and_gets_an_unknown_time_marked_and_reported(
            self, found, older):
        reply = json.loads(older.page(_side(older.owned_path)))["sources"][1]
        assert (reply["role"], reply["person"], reply["id"], reply["time"],
                reply["reconstructed"]) == ("reply", "marge", "$m2:simpson", "unknown", True)
        assert any(older.owned_path in line and "without a time" in line for line in found.left)

    def test_a_card_the_compile_owned_stays_the_compiles(self, found, older):
        assert not changed_by_hand(older.page(older.owned_path))

    def test_a_card_a_person_changed_keeps_their_words_and_stays_theirs(self, found, older):
        """Marge added a line of her own and the photo's caption. The words,
        the line and the photo stay; the card still reads as hers, so no
        compile rewrites it."""
        card = older.page(EDITED)
        assert changed_by_hand(card)
        assert "Lisa says she will swim it again next year." in card
        assert quotes_of(card)[0] == (f"![[{LAKE_PHOTO}]]\n"
                                      "Lisa swam all the way across, Maggie watched from the boat.")
        original = json.loads(older.page(_side(EDITED)))["sources"][0]
        assert (original["kind"], original["media"], original["date_basis"]) == (
            "image", f"sha256:{LAKE_SHA}", "spoken")

    def test_a_photo_a_person_embedded_by_hand_stays_on_their_card(self, found, older):
        """Marge embedded a second photo by hand, one the old card did not
        list as its media. It is no source of the entry, so it stays where
        she put it, and the card still shows it."""
        card = older.page(EDITED)
        assert "![[/media/2026/09/maggie-boat.jpg]]" in card.split("## Sources")[0]

    def test_a_reply_on_a_changed_card_keeps_its_own_id(self, found, older):
        """The changed card names three messages: the photo, a line without
        a file and Homer's reply. The ids split by the one reply the card
        shows, so the reply keeps the last id and the line is the entry's."""
        sources = json.loads(older.page(_side(EDITED)))["sources"]
        assert [(s["role"], s["id"]) for s in sources] == [
            ("original", "$lake:simpson"), ("original", "$lake-line:simpson"),
            ("reply", "$lake-answer:simpson")]
        assert sources[-1]["person"] == "homer"
        assert any(EDITED in line and "stays theirs" in line for line in found.left)

    def test_the_diary_pages_read_the_same_entries_from_the_migrated_cards(self, found, older):
        """The month pages are drawn from each card's entry: who, the day,
        the words, the replies and the files, as the old card had them."""

        def entry(path):
            return to_entry(parse_card(older.page(path), older.page(_side(path))))

        owned, edited = entry(older.owned_path), entry(EDITED)
        assert (owned.sender, owned.on.isoformat(), owned.body, owned.comments, owned.event_ids) == (
            "homer", "2026-10-01", TREEHOUSE, [("marge", "Nice work, Homie.")],
            ["$m1:simpson", "$m2:simpson"])
        assert (edited.sender, edited.confidence, edited.addressee, edited.kind) == (
            "marge", "spoken", "Lisa", "image")
        assert edited.body == "Lisa swam all the way across, Maggie watched from the boat."
        assert media_of([parse_card(older.page(EDITED), older.page(_side(EDITED)))]) \
            == {"$lake:simpson": LAKE_PHOTO}

    def test_every_other_card_stays_byte_for_byte(self, found, older):
        assert older.page(NOTE) == NOTE_TEXT

    def test_afterwards_the_format_check_finds_no_diary_card_without_a_sidecar(
            self, found, older):
        assert [f for f in format_report(load_graph(older.vault))["sources"]
                if "/diary/" in f["path"]] == []

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, older, media_root):
        mark = older.forgejo.head()
        again, sha = migrate(older, media_root)
        assert (again.changes, sha) == ({}, None)
        assert older.forgejo.head() == mark


def test_a_card_written_by_hand_without_a_sidecar_keeps_its_words_on_the_pages():
    """Given a diary card Marge wrote by hand, with a quote under
    `## Sources` and no sidecar, when the pages read it, then its words
    are shown, and the compile does not take it for one of its entries."""

    card = parse_card(NOTE_TEXT.replace("type: note", "type: diary").replace(
        "Buy charcoal.", "## Sources\n\n> Homer grilled the burgers."))

    assert to_entry(card).body == "Homer grilled the burgers."
    assert card.entry_id == ""


def test_a_correction_id_on_an_old_card_reads_as_a_message_of_the_entry():
    """Given an old card whose ids end with a correction's, which the card
    shows no words for, when its sources are rebuilt from its fields, then
    the correction's id reads as the entry's own message, or with a reply
    as the reply's id. Nothing on an old card tells a correction from a
    text line under a photo, and the line is the commoner case; no words
    and no file are lost either way."""
    from stacklets.memory.old_diary_cards import old_sources

    def ids(event_ids, replies):
        return [(s.role, s.id) for s in old_sources(
            event_ids=event_ids, sender="homer", time="2026-10-05T18:00:00+00:00",
            kind="image", body="At the lake", duration_ms=None, replies=replies,
            media=["/media/2026/10/$photo.jpg"])]

    assert ids(["$photo", "$fix"], []) == [("original", "$photo"), ("original", "$fix")]
    assert ids(["$photo", "$reply", "$fix"], [("marge", "So cute!")]) == [
        ("original", "$photo"), ("original", "$reply"), ("reply", "$fix")]


# ── A recording's length ─────────────────────────────────────────────────

VOICE_OLD = "family/diary/entries/2026/10/2026-10-05-7d8e9f0a1b.md"
VOICE_OLD_TEXT = """---
type: diary
card_id: diary-7d8e9f0a1b
title: Bart's band practice
date: "2026-10-05"
date_basis: spoken
persons: []
tags: []
filed_by: homer
kind: voice
mode: monologue
entry_id: 7d8e9f0a1b
event_ids:
  - "$band:simpson"
duration_ms: 41000
media: []
timestamp: "2026-10-05T18:00:00.000Z"
---

# Bart's band practice

## Transcript

Bart's band played in the garage until the neighbours called.
"""


def test_a_compiled_voice_entry_meets_the_format(tmp_path):
    """Given a voice message of 41 seconds, when the diary writes its card,
    then the format check finds nothing wrong with the card or its sidecar:
    a recording's length is a whole number of milliseconds, and the check
    reads every rule the sidecar's schema has."""

    entry = with_parts(Entry(on=date(2026, 10, 5), confidence="spoken", basis="", kind="voice",
                        sender="homer", body="Bart's band played in the garage.",
                        at=1_791_230_400_000, event_ids=["$band:simpson"], duration_ms=41_000))
    card = to_card(entry, Extraction(title="Band practice"), media={})
    card = replace(card, card_id=f"diary-{card.entry_id}")    # as the compile's plan names it
    vault = {"ontology.toml": ONTOLOGY, **PEOPLE, **card_files(card, bucket="family")}

    with running_memory(tmp_path / "data", vault) as memory:
        report = format_report(load_graph(memory.vault))

    assert report["invalid_sidecars"] == [] and report["sources"] == []


def test_a_migrated_voice_card_meets_the_format(tmp_path, media_root):
    """Given a voice card written before, whose length was in its
    frontmatter, when migration 0012 converts it, then the format check
    finds nothing wrong: the length moved into its source."""
    vault = {"ontology.toml": ONTOLOGY, **PEOPLE, VOICE_OLD: VOICE_OLD_TEXT}
    with running_memory(tmp_path / "data", vault) as memory:
        migrate(memory, media_root)
        sidecar = json.loads(memory.page(_side(VOICE_OLD)))
        report = format_report(load_graph(memory.vault))

    assert sidecar["sources"][0]["duration_ms"] == 41000
    assert report["invalid_sidecars"] == [] and report["sources"] == []


def test_before_the_migration_the_format_check_names_the_older_cards(older):
    found = [f["path"] for f in format_report(load_graph(older.vault))["sources"]]
    assert sorted(found) == sorted([older.owned_path, EDITED])


def test_an_edit_pushed_after_the_migration_read_makes_the_curator_refuse(
        older, media_root):
    """Given Marge's edit of a card, pushed to Forgejo after the migration
    read the curator's clone, when the migration commits, then the curator
    refuses, nothing of the migration lands and Marge's edit stays; the
    next `stack up memory` reads again."""
    from stack.memory import Refused

    edited = EDITED_TEXT.replace("next year", "next summer")
    mark = older.edited_in_forgejo({EDITED: edited}, by="marge", subject="the lake, next summer",
                                   pulled=False)
    with pytest.raises(Refused):
        migrate(older, media_root)
    assert older.forgejo.head() == mark
    assert older.page(EDITED) == edited


def test_an_edit_pulled_between_the_read_and_the_commit_makes_the_curator_refuse(
        older, media_root):
    """Given Marge's edit of a card, pulled into the curator's clone after
    the migration read the card and before it commits, when it commits,
    then the curator refuses: each card is written on the version the
    migration read, not on the one in the clone at commit time."""
    from stack.memory import Refused
    from stacklets.memory.lib import commit_found
    from stacklets.memory.old_diary_cards import diary_sources_for

    found = diary_sources_for(older.vault, media_root=media_root)
    edited = EDITED_TEXT.replace("next year", "next summer")
    mark = older.edited_in_forgejo({EDITED: edited}, by="marge", subject="the lake, next summer")

    with pytest.raises(Refused):
        commit_found(older.api, older.vault, found, title="give diary cards their sources",
                     trailer=TRAILER)
    assert older.forgejo.head() == mark
    assert older.page(EDITED) == edited


def test_a_card_with_windows_line_endings_converts_with_all_its_words(tmp_path, media_root):
    """Given Marge's changed card saved with Windows line endings (CRLF), as
    Obsidian on Windows or a paste into Forgejo's editor writes it, when
    migration 0012 converts it, then the curator takes the commit and the
    card keeps its title, her line, the caption and the reply, as the
    same card with LF does."""
    crlf = EDITED_TEXT.replace("\n", "\r\n")
    with running_memory(tmp_path / "data", {"ontology.toml": ONTOLOGY, EDITED: crlf}) as memory:
        assert b"\r\n" in (memory.vault / EDITED).read_bytes()
        _, sha = migrate(memory, media_root)
        card = memory.page(EDITED)

    assert sha
    assert parse(card)["title"] == "A day at the lake"
    assert "Lisa says she will swim it again next year." in card
    assert quotes_of(card)[0] == (f"![[{LAKE_PHOTO}]]\n"
                                  "Lisa swam all the way across, Maggie watched from the boat.")
    assert quotes_of(card)[-1] == "Woohoo, a swimmer!"


def test_stack_up_memory_runs_it_once_and_records_its_commit(older, media_root, tmp_path):
    """`0012_give_diary_cards_their_sources.py`, run by the framework's
    runner the way `stack up memory` runs it: through the curator, one
    commit."""
    from stack.migrations import MigrationContext, Migrations

    stck = older.stack
    migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory",
                            tmp_path / "data" / "memory")
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                   env={"MEDIA_ARCHIVE_DIR": str(media_root)}),
        lambda msg: None)

    assert "memory/0012" in outcome["applied"], outcome
    assert migrations.record().applied["0012"]["commit"] == git(
        older.forgejo.repo, "log", "-1", "--format=%H", "--grep=Migration: memory/0012")
    assert json.loads(older.page(_side(EDITED)))["sources"][0]["media"] == f"sha256:{LAKE_SHA}"


# ── After the migration, the compile writes nothing ──────────────────────

@pytest.fixture
def treehouse(room) -> Room:
    """The room the older card was made from: Homer's line at nine on
    1 October and Marge's reply to it an hour later."""
    line = room.says("homer", TREEHOUSE)
    room.answers("marge", line, "Nice work, Homie.")
    return room


def test_after_the_migration_a_compile_of_the_same_room_writes_no_card(
        older, media_root, treehouse, monkeypatch, bot_runner, bot_command):
    """Given the card an older release wrote for Homer's line and Marge's
    reply, migrated, when the diary compiles the same room, then no card
    is written: the migrated card has the entry's messages, words and
    reply, and a reply's unknown time is no change."""
    from tests.memory.unit.test_memory_diary_compile import Diary

    monkeypatch.setenv("MEMORY_API_URL", older.curator)
    monkeypatch.setenv("MEMORY_VAULT_DIR", str(older.vault))
    migrate(older, media_root)
    mark = head(older)

    compile_ = Diary(older.stack, bot_command, monkeypatch)
    compile_.runs()

    assert "error" not in compile_.answer and "exit" not in compile_.answer, compile_.progress
    assert commits_since(older, mark) == []
    assert sorted(older.pages(ENTRIES)) == sorted([older.owned_path, EDITED])
