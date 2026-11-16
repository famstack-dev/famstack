"""The diary forms one entry from each message group.

What a person sends in one go is one memory: photos and a few lines sent
within minutes of each other are one diary entry. A pause of 5 minutes, or
a message "--", starts the next one. A reply in an entry's thread adds to
it, at any time. The rule is `stack.message_groups`; these tests pin what
the diary makes of it, as the family sees it on the cards.

Most tests drive the compile as the admin runs it (`./stack memory
diary`), with the room on the stand-in Synapse, and read the cards the
memory kept. The report the archivist posts from comes from the command
in the bot runner with `--json`, as the archivist calls it. The tests on
voice messages read the room through the diary's own steps (`resolve`,
`group_room`, `compile_entries`), because the compile would first send
each recording to Whisper, which the unit lane has no stand-in for.

The stand-in model reads a day out of the words it is given in `NAMED`
and an addressee out of `ADDRESSED`, and nothing else. The family is the
Simpsons.
"""

# ruff: noqa: F811  (the imported fixtures are used as test parameters)
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import re
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, timezone

import pytest

from stack.cards import new_card_id
from stack.frontmatter import parse
from stacklets.memory.bot.diary.card import DIARY_TYPE, card_path, files as card_files, to_card
from stacklets.memory.bot.diary.entries import Entry, Part, Reading, compile_entries
from stacklets.memory.bot.diary.extraction import Extraction
from stacklets.memory.bot.diary.grouping import filed_groups, group_room
from stacklets.memory.bot.diary.messages import resolve
from tests.memory.unit.instance import RunningMemory
from tests.memory.unit.test_memory_diary_compile import (  # noqa: F401  (fixtures)
    ENTRIES, Model, bot_command, bot_runner, brain, commits_since, diary, head, memory, room,
)

BUCKET = "family"
# The evening of 10 November, when Homer sends the Halloween evening.
EVENING = int(datetime(2026, 11, 10, 18, tzinfo=timezone.utc).timestamp() * 1000)
SECOND = 1000
MINUTE = 60 * SECOND
DAY = 24 * 60 * MINUTE

HALLOWEEN_LINE = "Grampa visited us on Halloween"
FUN_LINE = "It was a nice visit and the kids had a lot of fun"

# What the stand-in model hears in a message's words.
NAMED = {"Halloween": "2026-10-31", "Monday": "2026-11-02", "Friday": "2026-11-06",
         "Easter": "2026-04-05"}
ADDRESSED = {"for Lisa": "Lisa", "for Bart": "Bart"}


class ReadingModel(Model):
    """The stand-in model, reading a day and an addressee out of the words."""

    async def complete(self, role, prompt: str, **kw) -> str:
        answer = await super().complete(role, prompt, **kw)
        if not prompt.startswith("You are reading a family's private memories room"):
            return answer
        blocks = re.split(r"^\[(\d+)\] ", prompt, flags=re.M)[1:]
        words = dict(zip(map(int, blocks[::2]), blocks[1::2]))
        rows = json.loads(answer)["messages"]
        for row in rows:
            text = words[row["n"]].split("\n\n", 1)[0]
            row["spoken_date"] = next((d for w, d in NAMED.items() if w in text), None)
            row["addressee"] = next((a for w, a in ADDRESSED.items() if w in text), None)
        return json.dumps({"messages": rows})


@pytest.fixture
def model() -> Model:
    return ReadingModel()


@pytest.fixture(autouse=True)
def household_in_utc(monkeypatch):
    """The household's days are UTC days, so a test's times name its days."""
    monkeypatch.setenv("TIMEZONE", "UTC")


# ── The room ─────────────────────────────────────────────────────────────

class Sends:
    """What people send to the memories room, at the times a test names."""

    def __init__(self, room):
        self.room = room

    def _post(self, who: str, at: int, content: dict) -> str:
        event_id = f"$e{len(self.room.events) + 1}:simpson"
        self.room.events.append({"type": "m.room.message", "event_id": event_id,
                                 "sender": f"@{who}:simpson", "origin_server_ts": at,
                                 "content": content})
        return event_id

    def photo(self, who: str, at: int) -> str:
        n = len(self.room.events) + 1
        return self._post(who, at, {"msgtype": "m.image", "body": f"IMG_{n:04}.jpg",
                                    "filename": f"IMG_{n:04}.jpg",
                                    "url": f"mxc://simpson/p{n}",
                                    "info": {"mimetype": "image/jpeg"}})

    def text(self, who: str, at: int, words: str) -> str:
        return self._post(who, at, {"msgtype": "m.text", "body": words})

    def in_thread(self, who: str, at: int, root: str, words: str) -> str:
        return self._post(who, at, {"msgtype": "m.text", "body": words,
                                    "m.relates_to": {"rel_type": "m.thread", "event_id": root}})

    def card_notice(self, root: str, at: int) -> str:
        """The archivist's card notice in the thread of `root`."""
        return self._post("archivist-bot", at, {
            "msgtype": "m.notice", "body": "Filed in the diary.",
            "dev.famstack.event": {"type": "diary.filed"},
            "m.relates_to": {"rel_type": "m.thread", "event_id": root}})


@pytest.fixture
def sends(room) -> Sends:
    return Sends(room)


def the_halloween_evening(sends: Sends) -> list[str]:
    """Three photos, the Halloween line, two photos, a line, within 5 minutes."""
    return [sends.photo("homer", EVENING), sends.photo("homer", EVENING + 20 * SECOND),
            sends.photo("homer", EVENING + MINUTE),
            sends.text("homer", EVENING + 2 * MINUTE, HALLOWEEN_LINE),
            sends.photo("homer", EVENING + 3 * MINUTE),
            sends.photo("homer", EVENING + 3 * MINUTE + 10 * SECOND),
            sends.text("homer", EVENING + 4 * MINUTE, FUN_LINE)]


# ── Reading what the memory kept ─────────────────────────────────────────

class Filed:
    """A diary card as the memory holds it: its fields and its sidecar's sources."""

    def __init__(self, path: str, text: str, sidecar: dict):
        self.path, self.fields, self.sources = path, parse(text), sidecar["sources"]

    @property
    def date(self) -> str:
        return str(self.fields["date"])

    @property
    def basis(self) -> str:
        return self.sources[0]["date_basis"]

    def ids(self, role: str = "original") -> list[str]:
        return [s["id"] for s in self.sources if s["role"] == role]


def filed(memory: RunningMemory) -> list[Filed]:
    """Every diary card in the memory, in the order its first message was sent."""
    out = [Filed(path, text, json.loads(memory.forgejo.file(path.removesuffix(".md") + ".json")))
           for path, text in memory.pages(ENTRIES).items()]
    return sorted(out, key=lambda card: card.sources[0]["time"])


def every_event_on_one_card(memory: RunningMemory) -> Counter:
    """How many cards each event id is on."""
    return Counter(s["id"] for card in filed(memory) for s in card.sources)


def report(bot_command, model) -> dict:
    """The compile's report, as the archivist asks the bot runner for it."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = asyncio.run(bot_command.run(model, ["--json"]))
    assert rc == 0
    return json.loads(out.getvalue())


def filed_one_card_per_message(memory: RunningMemory, events: list[dict], *,
                               corrected: str = "") -> None:
    """The room's messages as the diary filed them before message groups:
    one card per message, dated the day sent, written by the compile. The
    card of `corrected` is the family's, as a correction leaves it."""
    writes: dict[str, str] = {}
    for message in resolve(events):
        entry = Entry(
            on=message.sent_on, confidence="sent", basis="", kind=message.kind,
            sender=message.sender, body=message.body, at=message.ts,
            event_ids=[message.event_id], parts=[Part(message)])
        card = to_card(entry, Extraction(title="Filed before"), media={})
        card = replace(card, card_id=new_card_id(
            DIARY_TYPE, card_path(card, bucket=BUCKET)))
        writes.update(card_files(card, bucket=BUCKET,
                                       family_owned=message.event_id == corrected))
    memory.edited_in_forgejo(writes, by="stackadmin", subject="the cards filed before")


# ── The Halloween evening ────────────────────────────────────────────────

def test_photos_and_lines_sent_in_one_go_are_one_entry_on_the_day_the_words_name(
        memory, diary, sends):
    """Given Homer's three photos, the line "Grampa visited us on
    Halloween", two photos and another line, all within 5 minutes on
    10 November, when the admin compiles the diary, then one card holds
    all seven, in the order sent, each as Homer's own, on 31 October,
    dated by the words and without a warning.

    A day the words name dates the moment. It is no sign that the
    messages arrived late, so none of them is marked uncertain."""
    sent = the_halloween_evening(sends)

    diary.runs()

    [card] = filed(memory)
    assert card.ids() == sent and card.ids("reply") == []
    assert [s["person"] for s in card.sources] == ["homer"] * 7
    assert card.date == "2026-10-31" and card.basis == "spoken"


def test_the_card_notice_goes_in_the_thread_of_the_last_line(
        memory, sends, bot_command, model):
    """Given the Halloween evening, when the archivist asks for the compile's
    report, then it names one card, not posted yet, whose thread is the
    last line's: the message that ended the group."""
    sent = the_halloween_evening(sends)

    answer = report(bot_command, model)

    [card] = answer["cards"]
    assert card["root"] == sent[-1] and card["posted"] is False
    assert card["date"] == "2026-10-31"


def test_entries_filed_one_per_message_before_stay_as_they_are(memory, room, diary, sends):
    """Given the Halloween evening filed before as seven cards, one per message,
    when the admin compiles the diary, then the seven cards stay, nothing
    is written, and no message moves to another card.

    A card on file keeps its grouping: regrouping it is a separate
    command that asks first."""
    the_halloween_evening(sends)
    filed_one_card_per_message(memory, room.events)
    mark = head(memory)

    diary.runs()

    assert commits_since(memory, mark) == []
    assert [len(card.ids()) for card in filed(memory)] == [1] * 7


# ── Two people at once ───────────────────────────────────────────────────

def test_two_people_writing_at_once_make_one_entry_each(memory, diary, sends):
    """Given Homer's photos at 18:00, 18:02 and 18:03 and Marge's line at
    18:01, when the admin compiles the diary, then Homer's three photos
    are one entry and Marge's line is another: her message in between
    ends nothing of his."""
    homer = [sends.photo("homer", EVENING)]
    marge = sends.text("marge", EVENING + MINUTE, "Lisa lost her first tooth")
    homer += [sends.photo("homer", EVENING + 2 * MINUTE),
              sends.photo("homer", EVENING + 3 * MINUTE)]

    diary.runs()

    assert [card.ids() for card in filed(memory)] == [homer, [marge]]


# ── A pause or "--" ──────────────────────────────────────────────────────

def test_a_pause_of_5_minutes_or_dashes_start_a_new_entry(memory, diary, sends):
    """Given a line and a photo, then "--", then a photo, then a photo 17
    minutes later, when the admin compiles the diary, then there are three
    entries, and the dashes are on none of them."""
    first = [sends.text("homer", EVENING, "First swim in the lake"),
             sends.photo("homer", EVENING + 2 * MINUTE)]
    dashes = sends.text("homer", EVENING + 3 * MINUTE, "--")
    second = sends.photo("homer", EVENING + 3 * MINUTE + 5 * SECOND)
    third = sends.photo("homer", EVENING + 20 * MINUTE)

    diary.runs()

    assert [card.ids() for card in filed(memory)] == [first, [second], [third]]
    assert dashes not in every_event_on_one_card(memory)


@pytest.mark.parametrize("marker", ["--", "—", "–", "---"])
def test_the_card_notice_of_an_entry_ended_by_dashes_goes_in_their_thread(
        memory, sends, bot_command, model, marker):
    """Given a line, then the dashes as typed or as autocorrect changes
    them, then a photo, when the archivist asks for the report, then the
    first entry's notice goes in the thread of its dashes, and the second
    entry's in the thread of its last message."""
    sends.text("homer", EVENING, "First swim in the lake")
    dashes = sends.text("homer", EVENING + MINUTE, marker)
    photo = sends.photo("homer", EVENING + MINUTE + 5 * SECOND)

    answer = report(bot_command, model)

    assert [card["root"] for card in answer["cards"]] == [dashes, photo]


def test_the_window_is_a_parameter(memory, diary, sends):
    """Given two lines 2 seconds apart, when the admin compiles with
    `--group-window 1`, then they are two entries: a replayed corpus
    compresses hours into seconds and runs with a small window."""
    first = sends.text("homer", EVENING, "Breakfast at the lake")
    second = sends.text("homer", EVENING + 2 * SECOND, "Lunch at the lake")

    diary.runs("--group-window", "1")

    assert [card.ids() for card in filed(memory)] == [[first], [second]]


# ── A go still open waits for its end ────────────────────────────────────

def _the_moment(monkeypatch, at: int) -> None:
    """The compile runs at `at` (epoch milliseconds)."""
    moment = datetime.fromtimestamp(at / 1000, timezone.utc)
    monkeypatch.setenv("STACK_NOW", moment.isoformat())


def test_a_compile_in_the_middle_of_a_go_files_it_once_the_go_ended(
        memory, diary, sends, monkeypatch):
    """Given Homer's two photos at 18:00 and 18:01, when the admin compiles
    at 18:02, then nothing is filed; and when Homer sends two more photos
    at 18:03 and 18:04 and the admin compiles after 18:09, then one card
    holds all four.

    A card takes no new message of its own, so filing a go before it
    ended would store the family's one go as two memories for good."""
    first = [sends.photo("homer", EVENING), sends.photo("homer", EVENING + MINUTE)]
    _the_moment(monkeypatch, EVENING + 2 * MINUTE)
    diary.runs()
    assert filed(memory) == []

    rest = [sends.photo("homer", EVENING + 3 * MINUTE), sends.photo("homer", EVENING + 4 * MINUTE)]
    _the_moment(monkeypatch, EVENING + 10 * MINUTE)
    diary.runs()

    [card] = filed(memory)
    assert card.ids() == first + rest


def test_a_go_ended_by_dashes_is_filed_at_once(memory, diary, sends, monkeypatch):
    """Given two photos and "--" 10 seconds later, when the admin compiles
    a minute after the dashes, then the card is filed: the sender ended
    the go, so there is no window to wait out."""
    photos = [sends.photo("homer", EVENING), sends.photo("homer", EVENING + 10 * SECOND)]
    sends.text("homer", EVENING + 20 * SECOND, "--")
    _the_moment(monkeypatch, EVENING + 80 * SECOND)

    diary.runs()

    [card] = filed(memory)
    assert card.ids() == photos


def test_a_photo_a_minute_after_a_filed_entry_is_an_entry_of_its_own(memory, diary, sends):
    """Given the Halloween evening with Marge's reply, filed as one card, when a
    photo of Homer's from one minute after its last line reaches the room
    and the admin compiles, also with `--rebuild`, then the card stays as
    it was, the photo is a card of its own, and every message is on one
    card.

    A card on file never takes a new message of its own: it would change
    a memory the family may already have read and corrected."""
    sent = the_halloween_evening(sends)
    reply = sends.in_thread("marge", EVENING + 10 * MINUTE, sent[1], "So cute!")
    diary.runs()
    [before] = filed(memory)
    kept = memory.pages(ENTRIES)[before.path]

    late = sends.photo("homer", EVENING + 5 * MINUTE)
    for args in [(), ("--rebuild",)]:
        diary.runs(*args)

        cards = filed(memory)
        assert [card.ids() for card in cards] == [sent, [late]]
        assert cards[0].ids("reply") == [reply]
        assert memory.pages(ENTRIES)[before.path] == kept
        assert every_event_on_one_card(memory) == Counter(
            {event_id: 1 for event_id in [*sent, reply, late]})


# ── A phone's late queue, proven by the length of its voice messages ─────

def _voice(event_id: str, at: int, seconds: int, words: str) -> dict:
    return {"type": "m.room.message", "event_id": event_id, "sender": "@homer:simpson",
            "origin_server_ts": at,
            "content": {"msgtype": "m.audio", "body": words, "url": f"mxc://simpson/{event_id[1:]}",
                        "info": {"duration": seconds * SECOND}}}


def _entries(events: list[dict], spoken: dict[str, str],
             on_file: dict[str, str] | None = None) -> list[Entry]:
    """The entries the compile makes of `events`, whose voice messages say
    `body` (Whisper's words) and of which `spoken` names a day, by event id.
    `on_file` is the diary tree the cards on file are read from."""
    messages = resolve(events)
    readings = {m.event_id: Reading(spoken_date=spoken.get(m.event_id))
                for m in messages}
    filed = filed_groups(on_file or {}, messages)
    groups = group_room(messages, readings, filed=filed)
    return compile_entries(groups, readings)


def _on_file(entries: list[Entry]) -> dict[str, str]:
    """The diary tree after the compile filed `entries`, one card each."""
    tree: dict[str, str] = {}
    for entry in entries:
        card = to_card(entry, Extraction(title="Filed"), media={})
        card = replace(card, card_id=new_card_id(
            DIARY_TYPE, card_path(card, bucket=BUCKET)))
        tree.update(card_files(card, bucket=BUCKET))
    return tree


def _seen(entries: list[Entry]) -> list[tuple]:
    return [(e.event_ids, e.on, e.confidence, e.ended_by) for e in entries]


def test_voice_messages_arriving_faster_than_they_last_are_an_entry_each():
    """Given three voice messages of 40 seconds that reach the room on
    Saturday 2 seconds apart, the first naming Monday, the third Friday,
    when the diary compiles them, then each is its own entry: on Monday,
    on Saturday as "uncertain", and on Friday.

    Nobody records 40 seconds in 2: a phone that was offline sent its
    queue, so the day it arrived says nothing about the second one."""
    saturday = int(datetime(2026, 11, 7, 9, tzinfo=timezone.utc).timestamp() * 1000)
    events = [_voice("$v1", saturday, 40, "Monday, Lisa played the saxophone"),
              _voice("$v2", saturday + 2 * SECOND, 40, "Bart rode his bike"),
              _voice("$v3", saturday + 4 * SECOND, 40, "Friday, Maggie's tooth")]

    entries = _entries(events, {"$v1": "2026-11-02", "$v3": "2026-11-06"})

    assert [(e.event_ids, e.on, e.confidence) for e in entries] == [
        (["$v1"], date(2026, 11, 2), "spoken"),
        (["$v3"], date(2026, 11, 6), "spoken"),
        (["$v2"], date(2026, 11, 7), "uncertain")]


def test_voice_messages_sent_live_with_a_photo_are_one_entry():
    """Given a voice message of 30 seconds, a photo 50 seconds later and a
    voice message of 20 seconds 100 seconds after the first, when the
    diary compiles them, then they are one entry dated the day sent: the
    second recording came later than it lasts."""
    events = [_voice("$v1", EVENING, 30, "We are at the lake"),
              {"type": "m.room.message", "event_id": "$p1", "sender": "@homer:simpson",
               "origin_server_ts": EVENING + 50 * SECOND,
               "content": {"msgtype": "m.image", "body": "IMG_1.jpg", "filename": "IMG_1.jpg",
                           "url": "mxc://simpson/p1"}},
              _voice("$v2", EVENING + 100 * SECOND, 20, "The water is cold")]

    [entry] = _entries(events, {})

    assert entry.event_ids == ["$v1", "$p1", "$v2"]
    assert (entry.on, entry.confidence) == (date(2026, 11, 10), "sent")


def test_a_queued_entry_keeps_its_reply_and_its_doubt_once_filed():
    """Given three voice messages of a phone's late queue, the first naming
    Monday, and Marge's reply in the thread of the second, when the diary
    compiles them, then the second entry holds the reply and says it does
    not know its day; and when the diary compiles them again with their
    cards on file, then every entry is the same.

    A card on file keeps its grouping, so its doubt about the day must
    come back from the card, not from the room's timing."""
    saturday = int(datetime(2026, 11, 7, 9, tzinfo=timezone.utc).timestamp() * 1000)
    events = [_voice("$v1", saturday, 40, "Monday, Lisa played the saxophone"),
              _voice("$v2", saturday + 2 * SECOND, 40, "Bart rode his bike"),
              _voice("$v3", saturday + 4 * SECOND, 40, "Maggie lost a tooth"),
              {"type": "m.room.message", "event_id": "$r", "sender": "@marge:simpson",
               "origin_server_ts": saturday + DAY,
               "content": {"msgtype": "m.text", "body": "He was so proud!",
                           "m.relates_to": {"rel_type": "m.thread", "event_id": "$v2"}}}]
    spoken = {"$v1": "2026-11-02"}

    entries = _entries(events, spoken)

    assert _seen(entries) == [
        (["$v1"], date(2026, 11, 2), "spoken", "$v1"),
        (["$v2", "$r"], date(2026, 11, 7), "uncertain", "$v2"),
        (["$v3"], date(2026, 11, 7), "uncertain", "$v3")]
    assert _seen(_entries(events, spoken, on_file=_on_file(entries))) == _seen(entries)


# ── A reply joins its entry, whenever it comes ───────────────────────────

def test_a_reply_in_an_entry_s_thread_days_later_joins_it_with_its_person_and_day(
        memory, diary, sends):
    """Given the Halloween evening and Marge's reply three days later in the
    thread of its second photo, when the admin compiles the diary, then
    the one card holds her reply as hers, on its own day."""
    sent = the_halloween_evening(sends)
    reply = sends.in_thread("marge", EVENING + 3 * DAY, sent[1], "So cute!")

    diary.runs()

    [card] = filed(memory)
    [source] = [s for s in card.sources if s["role"] == "reply"]
    assert source["id"] == reply and source["person"] == "marge"
    assert source["time"].startswith("2026-11-13")


# ── Two named days never share an entry ──────────────────────────────────

def test_two_messages_that_name_different_days_are_two_entries(memory, diary, sends):
    """Given Homer's line about Easter and, a minute later, his line about
    Halloween, when the admin compiles the diary, then they are two
    entries, each on the day it names."""
    easter = sends.text("homer", EVENING, "The egg hunt at Easter")
    halloween = sends.text("homer", EVENING + MINUTE, HALLOWEEN_LINE)

    diary.runs()

    cards = sorted(filed(memory), key=lambda card: card.date)
    assert [(card.ids(), card.date) for card in cards] == [
        ([easter], "2026-04-05"), ([halloween], "2026-10-31")]


# ── The first addressee decides ──────────────────────────────────────────

def test_the_first_message_that_names_an_addressee_decides(memory, diary, sends):
    """Given a photo, a line for Lisa and a line for Bart, in one go, when
    the admin compiles the diary, then the one entry is for Lisa."""
    sends.photo("homer", EVENING)
    sends.text("homer", EVENING + MINUTE, "This one is for Lisa")
    sends.text("homer", EVENING + 2 * MINUTE, "And this for Bart")

    diary.runs()

    [card] = filed(memory)
    assert str(card.fields["addressee"]).lower() == "lisa"


# ── The resting state ────────────────────────────────────────────────────

def test_a_room_with_nothing_new_writes_no_card_also_on_rebuild(memory, diary, sends):
    """Given the Halloween evening compiled, when the admin compiles again and
    then with `--rebuild`, then no card is written again."""
    the_halloween_evening(sends)
    diary.runs()
    mark = head(memory)

    diary.runs()
    diary.runs("--rebuild")

    assert commits_since(memory, mark) == []


@pytest.mark.parametrize("args", [(), ("--rebuild",)])
def test_every_message_stays_on_exactly_one_card_next_to_a_corrected_one(
        memory, room, diary, sends, args):
    """Given the Halloween evening filed before as one card per message, the
    Halloween line's card corrected by the family, when the admin compiles
    the diary, also with `--rebuild`, then every message is on exactly one
    card and each card keeps its one message.

    A rebuild that regrouped around a corrected card would lose a card's
    photos or show them twice."""
    sent = the_halloween_evening(sends)
    filed_one_card_per_message(memory, room.events, corrected=sent[3])

    diary.runs(*args)

    assert every_event_on_one_card(memory) == Counter({event_id: 1 for event_id in sent})
    assert [card.ids() for card in filed(memory)] == [[event_id] for event_id in sent]


def test_a_card_filed_before_counts_as_posted_by_the_notice_on_its_first_message(
        memory, room, sends, bot_command, model):
    """Given a card filed before whose notice is in the thread of its first
    message, and a new photo since, when the archivist asks for the report,
    then the old card counts as posted and only the new one is to post."""
    first = sends.photo("homer", EVENING)
    filed_one_card_per_message(memory, room.events)
    sends.card_notice(first, EVENING + DAY)
    new = sends.photo("homer", EVENING + 2 * DAY)

    answer = report(bot_command, model)

    posted = {card["root"]: card["posted"] for card in answer["cards"]}
    assert posted == {first: True, new: False}


def test_a_card_counts_as_posted_by_the_notice_in_its_dashes_thread(
        memory, sends, bot_command, model):
    """Given a line ended by "--" and the card notice in the thread of the
    dashes, when the archivist asks for the report again, then the card
    counts as posted."""
    sends.text("homer", EVENING, "First swim in the lake")
    dashes = sends.text("homer", EVENING + MINUTE, "--")
    sends.card_notice(dashes, EVENING + DAY)

    answer = report(bot_command, model)

    assert [(card["root"], card["posted"]) for card in answer["cards"]] == [(dashes, True)]
