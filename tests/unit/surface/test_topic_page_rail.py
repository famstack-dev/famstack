"""A topic's page shows its lists and what changed lately in a rail.

Marge's phone opens `/memory/topic/<id>` of three fabricated topics: the
family's camper van (a to-do list with 4 open items, a pack list with 9,
three notes that changed in the last days), her quilt (no list, nothing
new) and a plain topic with ten new notes. The memory is memory's test
instance, core's tools server answers over HTTP, and the page is read the
way a browser without scripts reads it. Where the rail stands, beside the
entries or after the chips, is the stylesheet's; the page holds one rail,
before the sections, so a phone shows it after the chips.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .test_memory_graph_pages import opens, phone, sections
from .test_topic_page_head import _note, _topic

CAMPER = "topic-camper"
QUILT = "topic-quilt"
PLAIN = "topic-plain"
OLD = "2026-09-01T10:00:00"


def _days_ago(n: int) -> str:
    return (datetime.now() - timedelta(days=n)).strftime("%Y-%m-%dT%H:%M:%S")


def _list(folder: str, card_id: str, title: str, items: list[str], *, tagged: bool) -> dict[str, str]:
    tags = f"tags:\n  - {folder.split('/')[-1]}\n" if tagged else ""
    body = "".join(f"- [ ] {item}\n" for item in items)
    return {f"{folder}/lists/{card_id}.md":
            f"---\ntype: list\ncard_id: {card_id}\ntitle: \"{title}\"\n{tags}---\n\n{body}"}


def _files() -> dict[str, str]:
    files = dict(PEOPLE)
    files |= _topic("family/camper", CAMPER, "Camper van")
    files |= _list("family/camper", "list-van-todo", "Van: to do",
                   ["Check the battery", "Buy storm straps", "Drain the water tank", "Fix the awning"],
                   tagged=True)
    files |= _list("family/camper", "list-van-pack", "Pack list", [f"Thing {n}" for n in range(9)], tagged=False)
    for n in range(3):
        files |= _note("family/camper", n)
    files |= _topic("marge/quilt", QUILT, "Quilt for Lisa")
    files |= _note("marge/quilt", 0)
    files |= _topic("family/plain", PLAIN, "Plain topic")
    return files


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    """Everything filed long ago, then the last days' changes: Marge files
    note 0, Homer adds a reply to note 1 and files note 2, and ten notes
    of the plain topic are filed."""
    with running_memory(tmp_path_factory.mktemp("topic-rail"), _files(), seeded_at=OLD,
                        seeded_as="capture: seeded") as running:
        note = lambda n: _note("family/camper", n)  # noqa: E731
        for n, (who, kind, when) in enumerate([("marge", "capture", 3), ("homer", "reply", 2),
                                               ("homer", "capture", 1)]):
            path, text = next(iter(note(n).items()))
            running.edited_in_forgejo({path: text + "\nMore.\n"}, by=who, at=_days_ago(when),
                                      subject=f"{kind}: Note {n}")
        plain = {}
        for n in range(10):
            plain |= _note("family/plain", n)
        running.edited_in_forgejo(plain, by="marge", at=_days_ago(1), subject="capture: ten notes")
        yield running


@pytest.fixture
def host(tmp_path, monkeypatch):
    """The host API's stand-in, answering `stack family` for the Simpsons."""
    stand_in = DashboardHost.in_state("services", root=tmp_path / "instance")
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(stand_in.serve()))
    yield stand_in
    stand_in.close()


@pytest.fixture
def tools(memory, host, monkeypatch):
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return tools_server("server").app


def marge(tools, topic):
    return opens(phone(tools, person="marge"), f"/memory/topic/{topic}")


# ── Lists ─────────────────────────────────────────────────────────────────

def test_the_rail_names_the_to_do_list_with_its_open_items_and_the_other_list_as_a_link(tools):
    """Given the van's to-do list with 4 open items and a pack list, then
    the rail shows "Lists 2": the to-do list's title, "4 open" and its
    items, then "Pack list 9" as a link."""
    page = marge(tools, CAMPER)
    lists = page.within("data-section", "lists")

    assert lists.texts("fl-count")[:1] == ["2"]
    assert lists.texts("todo-title") == ["Van: to do4 open"]
    assert lists.texts("item-text") == ["Check the battery", "Buy storm straps",
                                        "Drain the water tank", "Fix the awning"]
    assert lists.texts("rail-list") == ["Pack list9"]
    assert lists.links()[0] == ("Van: to do", "/card/list-van-todo")   # the to-do list comes first
    assert ("Pack list", "/card/list-van-pack") in lists.links()
    assert ("Van: to do", "/card/list-van-todo") in lists.links()


def test_on_a_phone_the_to_do_list_marks_the_items_past_the_third_and_offers_all(tools):
    """The page marks the items past the first 3 (`rail-more`) and links
    "All 4 ›"; the stylesheet shows them in the rail and hides them, and
    the link, on a phone."""
    lists = marge(tools, CAMPER).within("data-section", "lists")

    assert lists.texts("rail-more") == ["Fix the awning"]
    assert lists.texts("all-items") == ["All 4 ›"]
    assert ("All 4 ›", "/card/list-van-todo") in lists.links()


def test_the_rail_stands_before_the_sections_so_a_phone_shows_it_after_the_chips(tools):
    page = marge(tools, CAMPER)

    assert sections(page)[:2] == ["lists", "recent"]
    assert "up_next" in sections(page)


def test_a_topic_with_no_open_list_says_so(tools):
    lists = marge(tools, QUILT).within("data-section", "lists")

    assert lists.texts("empty-text") == ["No open list."]


# ── Recent ────────────────────────────────────────────────────────────────

def test_recent_lists_what_changed_newest_first_with_what_happened_and_who(tools):
    """Given Marge filed note 0 three days ago, Homer added a reply to
    note 1 two days ago and filed note 2 yesterday, then Recent lists them
    newest first, with "by you" for Marge looking."""
    recent = marge(tools, CAMPER).within("data-section", "recent")

    assert recent.texts("fl-count")[:1] == ["3"]
    assert recent.texts("change-line") == ["Note 2", "Added to Note 1", "Note 0"]
    quiet = [line.split(" · ", 1)[1] for line in recent.texts("fl-meta")]
    assert quiet == ["New entry · by Homer", "by Homer", "New entry · by you"]


def test_a_reply_added_after_filing_reads_added_to_the_entry(tools):
    recent = marge(tools, CAMPER).within("data-section", "recent")

    assert ("Note 1", "/card/note-camper-1") in recent.links()
    assert "Added to Note 1" in recent.texts("change-line")


def test_recent_shows_the_first_eight_rows_and_counts_all(tools):
    recent = marge(tools, PLAIN).within("data-section", "recent")

    assert recent.texts("fl-count")[:1] == ["10"]
    assert len(recent.texts("change-line")) == 8


def test_a_topic_nothing_changed_in_says_so(tools):
    recent = marge(tools, QUILT).within("data-section", "recent")

    assert recent.texts("empty-text") == ["Nothing new in the last two weeks."]
