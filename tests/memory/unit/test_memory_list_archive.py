"""The nightly archive: yesterday's ticks leave every list, into one commit.

What a family sees the morning after a shop: the list holds only what
they did not get, and what they got is in the month's archive.

What leaves a list and when is decided from the files alone
(`archive_lists`), so those rules are stated with fixed dates. The command
around it runs as the curator runs it on the nightly sweep, against the
real memory: the curator's API over its card store, pushing to a bare
repository as Forgejo (`running_memory`, memory's test instance).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]

from stack.memory import File  # noqa: E402
from tests.loopback import a_closed_port
from tests.memory.unit.instance import running_memory  # noqa: E402

from stack.lists import read_list  # noqa: E402



from stacklets.memory.bot.cli import lists as nightly  # noqa: E402

MEMORY_BOT = "memory-bot <memory-bot@local>"
SHOPPING = "family/lists/einkaufsliste.md"
SHOPPING_ARCHIVE = "family/lists/archive/einkaufsliste/2026-10.md"
LIST = (
    "---\ntype: list\ncard_id: list-einkaufsliste\ntitle: Einkaufsliste\nsections:\n  - Obst\n  - Milch\n---\n\n"
    "## Obst\n- [x] Äpfel ✅ 2026-10-11\n- [ ] Birnen\n\n"
    "## Milch\n- [x] Joghurt\n"
)


def _texts(changes: list[dict]) -> dict[str, str]:
    return {c["path"]: c["text"] for c in changes}


# ── What leaves a list, and when ─────────────────────────────────────────

def test_the_morning_after_the_list_holds_what_is_open():
    """Given apples ticked yesterday and yoghurt ticked by hand today, when
    the night is over, then the apples are in October's archive and the list
    holds the pears and the yoghurt, which stays until tomorrow.

    "Did we get yoghurt?" still has an answer on the day it was bought.
    """
    changes, moved = nightly.archive_lists([File(SHOPPING, LIST, "v-list")],
                                           bucket="family", today="2026-10-12")

    assert moved == 1, "Joghurt was ticked by hand today and stays until tomorrow"
    kept = read_list(_texts(changes)[SHOPPING], path=SHOPPING)
    assert [(i.text, i.done, i.done_on) for i in kept.items] == \
        [("Birnen", False, ""), ("Joghurt", True, "2026-10-12")]
    assert "## Obst\n- [x] Äpfel ✅ 2026-10-11" in _texts(changes)[SHOPPING_ARCHIVE]


def test_each_file_is_written_on_the_version_it_was_read_at():
    """The list is written on the version it was read at, and a month's
    archive that is there already on its own, so a tick made meanwhile
    stops the commit instead of being lost; a new archive is created."""
    october = File(SHOPPING_ARCHIVE, "---\ntype: list\ntitle: Einkaufsliste, 2026-10\nkind: archive\n"
                                     "---\n\n- [x] Brot ✅ 2026-10-02\n", "v-archive")

    changes, _ = nightly.archive_lists([File(SHOPPING, LIST, "v-list"), october],
                                       bucket="family", today="2026-10-12")

    assert {c["path"]: c["base"] for c in changes} == {SHOPPING: "v-list", SHOPPING_ARCHIVE: "v-archive"}
    assert "Brot" in _texts(changes)[SHOPPING_ARCHIVE] and "Äpfel" in _texts(changes)[SHOPPING_ARCHIVE]


def test_a_quiet_night_changes_nothing():
    packing = File("family/lists/packliste.md", "---\ntype: list\ntitle: Packliste\n---\n\n- [ ] Zelt\n", "v")

    assert nightly.archive_lists([packing], bucket="family", today="2026-10-12") == ([], 0)


def test_the_morning_after_an_appointment_it_is_off_the_calendar():
    calendar = File("family/lists/termine.md", "---\ntype: list\ntitle: Termine\nkind: calendar\n---\n\n"
                    "- [ ] Theater 📅 2026-11-03\n- [ ] Elternabend 📅 2026-11-17\n", "v")

    changes, moved = nightly.archive_lists([calendar], bucket="family", today="2026-11-04")

    assert moved == 1
    left = read_list(_texts(changes)["family/lists/termine.md"], path="family/lists/termine.md")
    assert [i.text for i in left.items] == ["Elternabend"]
    assert "family/lists/archive/termine/2026-11.md" in _texts(changes)


def test_an_archive_is_no_list_of_its_own():
    """Only the bucket's lists are archived: the archives below them are
    what they move into."""
    archived = File(SHOPPING_ARCHIVE, LIST, "v-archive")

    assert nightly.archive_lists([archived], bucket="family", today="2026-10-12") == ([], 0)


# ── The command, as the curator runs it ──────────────────────────────────

@pytest.fixture
def memory(tmp_path, monkeypatch):
    """The memory holding the shopping list, and the curator's environment."""
    with running_memory(tmp_path / "data", {SHOPPING: LIST}, seeded_by="marge",
                        seeded_as="chore(lists): marge changed Einkaufsliste") as memory:
        for key, value in {**memory.environ, "SHARED_BUCKET": "family", "TIMEZONE": "UTC"}.items():
            monkeypatch.setenv(key, value)
        yield memory


def test_the_night_moves_what_is_over_in_one_commit_by_the_memory_bot(memory, capsys):
    """Given apples ticked on a day that is over, when the nightly archive
    runs, then Forgejo has them in October's archive and off the list, in
    one commit by the memory bot, and the curator's log names them."""
    assert asyncio.run(nightly.run(None, [])) == 0

    assert memory.forgejo.new_commits() == [
        f"{MEMORY_BOT} | {MEMORY_BOT} | lists: archive 1 item(s) that are over"]
    assert "Äpfel" not in memory.text(SHOPPING) and "Birnen" in memory.text(SHOPPING)
    assert "Äpfel" in memory.text(SHOPPING_ARCHIVE)
    assert "einkaufsliste: Äpfel" in capsys.readouterr().out


def test_a_dry_run_names_what_would_move_and_writes_nothing(memory, capsys):
    assert asyncio.run(nightly.run(None, ["--dry-run"])) == 0

    assert memory.forgejo.new_commits() == []
    assert "einkaufsliste: Äpfel" in capsys.readouterr().out


def test_without_the_curator_nothing_is_archived_and_the_log_says_what_to_start(monkeypatch, capsys):
    port = a_closed_port()
    monkeypatch.setenv("MEMORY_API_URL", f"http://127.0.0.1:{port}")

    assert asyncio.run(nightly.run(None, [])) == 1
    err = capsys.readouterr().err
    assert "lists not archived" in err and "stack up memory" in err


# ── A person's own lists ─────────────────────────────────────────────────

HOMERS_CALENDAR = "homer/lists/dates-homer.md"
HOMERS_CALENDAR_TEXT = ("---\ntype: list\ncard_id: list-dates-homer\ntitle: Homer's Dates\nkind: calendar\n"
                        "---\n\n- [ ] Old checkup 📅 2026-10-01\n- [ ] Dentist 📅 2099-12-02\n")
HOMER = "---\ntype: person\ncard_id: person-homer\ntitle: Homer Simpson\naccount: homer\n---\n\n# Homer\n"
# A list Homer made by hand in his own folder, and a folder that is nobody's:
# both with an item ticked on a day that is over.
SURPRISE = "homer/lists/surprise.md"
TEMPLATES = "templates/lists/x.md"
# A calendar in a folder that is no declared person's, with a date that is over.
NOBODYS_CALENDAR = "templates/lists/dates-template.md"
NOBODYS_CALENDAR_TEXT = ("---\ntype: list\ncard_id: list-dates-template\ntitle: Template dates\n"
                         "kind: calendar\n---\n\n"
                         "- [ ] Old checkup 📅 2026-10-01\n")
TICKED = "---\ntype: list\ntitle: {title}\n---\n\n- [x] Buy cake ✅ 2026-10-01\n- [ ] Hide cake\n"


@pytest.fixture
def homers_calendar(tmp_path, monkeypatch):
    """The memory holding the shopping list and Homer's own calendar."""
    with running_memory(tmp_path / "data", {SHOPPING: LIST, HOMERS_CALENDAR: HOMERS_CALENDAR_TEXT,
                                           "family/people/homer.md": HOMER,
                                           SURPRISE: TICKED.format(title="Surprise"),
                                           TEMPLATES: TICKED.format(title="Template"),
                                           NOBODYS_CALENDAR: NOBODYS_CALENDAR_TEXT},
                        seeded_by="homer", seeded_as="chore(lists): homer changed Homer's Dates") as memory:
        for key, value in {**memory.environ, "SHARED_BUCKET": "family", "TIMEZONE": "UTC"}.items():
            monkeypatch.setenv(key, value)
        yield memory


def test_the_night_takes_a_past_date_off_a_persons_own_calendar(homers_calendar):
    """Given a checkup that is over on Homer's own calendar, when the nightly
    archive runs, then it is in his own archive and off his calendar, his
    date ahead stays, and nothing of his reaches the family's folder: a
    calendar keeps only what is ahead, in whichever bucket it is."""
    assert asyncio.run(nightly.run(None, [])) == 0

    memory = homers_calendar
    assert "Old checkup" not in memory.text(HOMERS_CALENDAR)
    assert "Dentist" in memory.text(HOMERS_CALENDAR)
    assert "Old checkup" in memory.text("homer/lists/archive/dates-homer/2026-10.md")
    assert "Old checkup" not in memory.text("family/lists/archive/dates-homer/2026-10.md")
    assert "Äpfel" in memory.text(SHOPPING_ARCHIVE)


def test_the_night_leaves_a_persons_other_lists_and_nobodys_folder_as_they_are(homers_calendar):
    """Given a list Homer made by hand in his own folder and a folder that is
    no person's, each with an item ticked on a day that is over, when the
    nightly archive runs, then both files stay byte for byte, while his past
    private date still moves: only calendars keep only what is ahead, and no
    rule was decided for the rest."""
    assert asyncio.run(nightly.run(None, [])) == 0

    memory = homers_calendar
    assert memory.text(SURPRISE) == TICKED.format(title="Surprise")
    assert memory.text(TEMPLATES) == TICKED.format(title="Template")
    assert memory.text("homer/lists/archive/surprise/2026-10.md") == ""
    assert "Old checkup" in memory.text("homer/lists/archive/dates-homer/2026-10.md")


def test_the_night_leaves_a_calendar_in_nobodys_folder_as_it_is(homers_calendar):
    """Given a calendar with a date that is over in a folder that is no
    declared person's, when the nightly archive runs, then the file stays
    byte for byte and no archive is made: only the shared folder and the
    family's people have their calendars kept to what is ahead."""
    assert asyncio.run(nightly.run(None, [])) == 0

    memory = homers_calendar
    assert memory.text(NOBODYS_CALENDAR) == NOBODYS_CALENDAR_TEXT
    assert memory.text("templates/lists/archive/dates-template/2026-10.md") == ""
