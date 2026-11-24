"""A date a person asks Stacky for just for themselves stays theirs.

Homer tells Stacky: "put my dentist appointment on the 2nd at 9, just for
me". The date goes on Homer's own calendar, in his own folder of the
memory, and on no shared list. A person's own folder is read only by that
person alone (`stack.cards.audience.readable`), so the date shows on
Homer's devices, next to the family's dates, and on none of Marge's: not
on the Dates list, not in Up next, not in a search.

Driven from the person's side. Stacky's calendar tool runs as the agent
runs it, through the real `stack` shim and the host API's own checks
(`stacky_in_a_room`), on a fabricated memory (the Simpsons,
`running_memory`). What a device shows is the curator's overview API, which
core's pages render for the paired person (`GET /overview/<view>?audience=`).

What holds:

  1. Just for me: in the person's own calendar, in no shared list.
  2. Another person's device and chat: the date on none of their pages.
  3. The person's own device and chat: the date next to the family's,
     marked private, with the family's count unchanged.
  4. For the whole family, and without such words: the shared list, as
     before; the reply names the list the date went on.
  5. When it goes wrong: a room with others in it, a date already on the
     shared list, a speaker outside the room's people, an own calendar
     that does not read. None of them puts the date on a shared list.
"""

from __future__ import annotations

import contextlib
import io
import json
import urllib.request
from datetime import date, timedelta

import pytest

from tests.memory.unit.instance import running_memory
from tests.unit.stacklets.stacky_in_a_room import Room

# Dates ahead of today, inside "Up next"'s window, so every page shows them.
IN_TEN_DAYS = (date.today() + timedelta(days=10)).isoformat()
IN_FIVE_DAYS = (date.today() + timedelta(days=5)).isoformat()

HOMERS_CHAT = Room("!homer-chat:simpson", ("homer",), "homer")
MARGES_CHAT = Room("!marge-chat:simpson", ("marge",), "marge")
FAMILY_ROOM = Room("!family:simpson", ("homer", "marge", "bart", "lisa"), "homer", "Simpsons")
BARTS_CHAT = Room("!bart-chat:simpson", ("bart",), "bart")


def _person(slug: str, title: str) -> str:
    return (f"---\ntype: person\ncard_id: person-{slug}\ntitle: {title}\n"
            f"account: {slug}\n---\n\n# {title}\n")


# The family's calendar, as the archivist starts it, with the fair for
# everyone and a date someone once put there for Homer alone.
FAMILY_DATES = ("---\ntype: list\ncard_id: list-dates\ntitle: Dates\nkind: calendar\n---\n\n"
                f"- [ ] Springfield fair 📅 {IN_FIVE_DAYS}\n"
                f"- [ ] Moe's tab due [Homer](../people/homer.md) 📅 {IN_FIVE_DAYS}\n")

SEED = {**{f"family/people/{slug}.md": _person(slug, f"{slug.title()} Simpson")
           for slug in ("homer", "marge", "bart", "lisa")},
        "family/lists/dates.md": FAMILY_DATES}

DENTIST = "dentist appointment"
HOMERS_CALENDAR = "homer/lists/dates-homer.md"


@pytest.fixture
def memory(tmp_path):
    """The Simpsons' memory: their people and the family's calendar."""
    with running_memory(tmp_path, SEED) as running:
        yield running


def homer_keeps_his_dentist(stacky, room: Room = HOMERS_CHAT) -> str:
    """Homer, in `room`, asks Stacky for the dentist just for him; Stacky's answer."""
    return stacky.in_room(room).asks_calendar(op="add", what=DENTIST, day=IN_TEN_DAYS,
                                              time="09:00", just_for_me=True)


def holding(memory, words: str, under: str) -> list[str]:
    """The paths of every page below `under` in Forgejo whose text holds `words`."""
    return [path for path, text in memory.pages(under).items() if words in text]


def title_of(memory, path: str) -> str:
    """The `title:` of the list at `path`, as Forgejo holds it."""
    return next(line.split(":", 1)[1].strip() for line in memory.page(path).splitlines()
                if line.startswith("title:"))


def calendar_answer(memory, person: str) -> dict:
    """What `stack memory list show calendar` answers as data when `person` alone listens."""
    with contextlib.redirect_stdout(io.StringIO()):
        return memory.stack.run_cli_command(
            "memory", "list", ["show", "calendar", "--days", "30", "--audience", person])


def up_next(memory, person: str, view: str = "me") -> list[str]:
    """The titles in Up next of the page `view`, as `person`'s paired device asks for it."""
    url = f"{memory.curator}/overview/{view}?audience={person}"
    with urllib.request.urlopen(url) as answer:
        return [item["title"] for item in json.loads(answer.read())["up_next"]["items"]]


# ── 1. Just for me ───────────────────────────────────────────────────

def test_a_date_just_for_homer_is_in_his_own_folder_and_on_no_shared_list(stacky, memory):
    """Given Homer alone with Stacky, when he asks for the dentist just for
    him, then the date is on a list in his own folder and on no list of the
    family's: the family's folder is what everyone reads."""
    homer_keeps_his_dentist(stacky)

    assert holding(memory, DENTIST, "homer/lists")
    assert holding(memory, DENTIST, "family") == []


def test_his_own_calendar_is_made_by_his_first_private_date(stacky, memory):
    """Given Homer has no calendar of his own, when his first date just for
    him is added, then it is made in his folder, as a calendar, and the
    family's calendar is untouched."""
    homer_keeps_his_dentist(stacky)

    [path] = holding(memory, DENTIST, "homer/lists")
    assert "kind: calendar" in memory.page(path)
    assert memory.page("family/lists/dates.md") == FAMILY_DATES


# ── 2. Not on anyone else's pages ────────────────────────────────────

def test_marges_device_shows_the_date_in_no_up_next(stacky, memory):
    """Given Homer's private dentist date, when Marge's device asks for her
    page and the family's page, then neither Up next holds it, while the
    family's fair is there."""
    homer_keeps_his_dentist(stacky)

    for view in ("me", "family"):
        titles = up_next(memory, "marge", view)
        assert "Springfield fair" in titles
        assert not any(DENTIST in title for title in titles)


@pytest.mark.parametrize("line", ["memory find dentist", "memory search dentist",
                                  "memory list show calendar --days 30"])
def test_in_marges_chat_no_read_finds_the_date(stacky, memory, line):
    """Given Homer's private dentist date, when Stacky reads for Marge (a
    search, the calendar), then the answer does not hold it."""
    homer_keeps_his_dentist(stacky)

    assert DENTIST not in stacky.in_room(MARGES_CHAT).execs(line)


def test_in_marges_chat_the_calendar_tool_shows_the_fair_without_homers_date(stacky, memory):
    """Given Homer's private dentist date, when Marge asks Stacky what is
    planned, then she hears the family's fair and not Homer's dentist."""
    homer_keeps_his_dentist(stacky)

    answer = stacky.in_room(MARGES_CHAT).asks_calendar(op="show", days=30)
    assert "Springfield fair" in answer
    assert DENTIST not in answer


# ── 3. On the person's own pages, next to the family's ───────────────

def test_homers_device_shows_his_date_next_to_the_familys(stacky, memory):
    """Given Homer's private dentist date, when Homer's device asks for his
    page, then Up next holds his dentist and the family's fair."""
    homer_keeps_his_dentist(stacky)

    titles = up_next(memory, "homer")
    assert "Springfield fair" in titles
    assert any(DENTIST in title for title in titles)


def test_in_homers_chat_the_calendar_tool_shows_his_date_next_to_the_familys(stacky, memory):
    """Given Homer's private dentist date, when Homer asks Stacky what is
    planned, then he hears his dentist and the family's fair."""
    homer_keeps_his_dentist(stacky)

    answer = stacky.in_room(HOMERS_CHAT).asks_calendar(op="show", days=30)
    assert "Springfield fair" in answer
    assert DENTIST in answer


def test_in_his_own_answer_his_dates_are_marked_private_and_the_familys_count_stays(
        stacky, memory):
    """Given Homer's private dentist date, when the calendar is read for Homer
    alone, then the dentist is marked private, the family's dates are not,
    and the family's calendar still counts its own two dates: the agent then
    knows which date to remove as his own, and reads out the right count."""
    homer_keeps_his_dentist(stacky)

    answer = calendar_answer(memory, "homer")

    assert {i["text"]: i.get("private", False) for i in answer["items"]} == {
        "Springfield fair": False, "Moe's tab due": False, f"09:00 {DENTIST}": True}
    assert answer["own"]["open"] == 1
    assert answer["open"] == 2


def test_in_homers_chat_he_removes_his_private_date(stacky, memory):
    """Given Homer's private dentist date, when he asks Stacky to take it off
    again, then his own calendar no longer holds it."""
    homer_keeps_his_dentist(stacky)

    stacky.in_room(HOMERS_CHAT).asks_calendar(op="remove", what=DENTIST, just_for_me=True)

    assert holding(memory, DENTIST, "homer/lists") == []


# ── 4. and 5. Family dates, and dates without such words ─────────────

@pytest.mark.parametrize("just_for_me", [False, None], ids=["for the family", "no such words"])
def test_a_date_not_asked_for_one_person_goes_on_the_familys_calendar(stacky, memory, just_for_me):
    """Given Homer asks for a date for the family, or names nobody, then it
    goes on the family's calendar, as before, and nothing into his folder."""
    stacky.in_room(HOMERS_CHAT).asks_calendar(op="add", what="Barbecue", day=IN_TEN_DAYS,
                                              just_for_me=just_for_me)

    assert holding(memory, "Barbecue", "family/lists") == ["family/lists/dates.md"]
    assert holding(memory, "Barbecue", "homer") == []


def test_the_reply_names_the_list_each_date_went_on(stacky, memory):
    """Given Homer adds one date for himself and one for the family, then
    each reply names the list it went on, so he can correct a wrong place."""
    private = homer_keeps_his_dentist(stacky)
    family = stacky.in_room(HOMERS_CHAT).asks_calendar(op="add", what="Barbecue", day=IN_TEN_DAYS)

    assert title_of(memory, HOMERS_CALENDAR) in private
    assert title_of(memory, "family/lists/dates.md") in family
    assert title_of(memory, HOMERS_CALENDAR) not in family


# ── Failure modes ────────────────────────────────────────────────────

def test_in_the_family_room_a_private_date_goes_to_his_folder_and_is_not_read_back(
        stacky, memory):
    """Given Homer in the family room, when he asks for the dentist just for
    him, then the date is written only to his own folder, and the family
    room's calendar does not show it."""
    homer_keeps_his_dentist(stacky, FAMILY_ROOM)

    assert holding(memory, DENTIST, "homer/lists")
    assert holding(memory, DENTIST, "family") == []
    assert DENTIST not in stacky.in_room(FAMILY_ROOM).asks_calendar(op="show", days=30)


def test_a_date_already_on_the_shared_list_for_homer_stays_where_it_is(stacky, memory):
    """Given Moe's tab on the family's calendar, assigned to Homer before
    private dates existed, when Homer adds a private date, then the tab stays
    on the family's calendar: nothing moves on its own."""
    homer_keeps_his_dentist(stacky)

    assert holding(memory, "Moe's tab", "family/lists") == ["family/lists/dates.md"]
    assert holding(memory, "Moe's tab", "homer") == []


def test_in_barts_chat_no_date_goes_into_homers_folder(stacky, memory):
    """Given Bart alone with Stacky, when a line asks for a private date as
    Homer, then it is refused and nothing is written: a private date goes
    only into the folder of someone in the room."""
    answer = stacky.in_room(BARTS_CHAT).execs(
        f"memory list add calendar Moe --due {IN_TEN_DAYS} --as homer --private")

    assert "Moe" not in "".join(memory.pages("homer").values())
    assert holding(memory, "Moe ", "family") == []
    assert answer  # the refusal says why


def test_when_his_own_calendar_does_not_read_the_date_is_saved_nowhere(stacky, memory):
    """Given Homer's own calendar file broken in Forgejo, when he asks for a
    date just for him, then it lands on no list of the family's, and the
    answer names the file that kept it from being saved: a private date is
    never written to the shared list instead."""
    memory.edited_in_forgejo({HOMERS_CALENDAR: "not a list\n"}, by="homer")

    answer = homer_keeps_his_dentist(stacky)

    assert holding(memory, DENTIST, "family") == []
    assert holding(memory, DENTIST, "homer") == []
    assert HOMERS_CALENDAR in answer


def test_when_his_own_calendar_does_not_read_his_chat_says_so(stacky, memory):
    """Given Homer's own calendar file broken in Forgejo, when he asks what
    is planned, then he hears the family's dates and which file keeps his
    own from showing, instead of silently missing them."""
    memory.edited_in_forgejo({HOMERS_CALENDAR: "not a list\n"}, by="homer")

    answer = stacky.in_room(HOMERS_CHAT).asks_calendar(op="show", days=30)

    assert "Springfield fair" in answer
    assert HOMERS_CALENDAR in answer
