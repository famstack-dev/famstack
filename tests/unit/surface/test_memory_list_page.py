"""A list's page, as a phone on the home network reads it.

A list is a card (`list-<slug>`): its sections in the family's walking
order, one checkbox line per item, the day it is due, the person it is
assigned to, and the day it was ticked. Ticked items move to the list's
archive at night, one card per month (`list-<slug>-<YYYY-MM>`).

The page shows the sections with their items, an item ticked today
greyed with who ticked it, the archive's months below, and the line that
says where items are added and ticked: the chat. `/go/list/<slug>` lands
on that page, for the family's lists and for a person's own.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance: a real curator over a bare repository, which core reaches
over HTTP on localhost. Marge ticks an item the way the chat does, with
`stack memory list tick`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import VAULT

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

TODAY = date.today()
SOON = TODAY + timedelta(days=30)

GROCERIES = "family/lists/groceries.md"
LISTS = {
    GROCERIES: (
        "---\ntype: list\ncard_id: list-groceries\ntitle: Groceries\n"
        "sections:\n  - Fruit & Veg\n  - Dairy\n  - Drugstore\n---\n\n"
        "## Fruit & Veg\n- [ ] Apples\n- [ ] Bananas\n\n"
        f"## Dairy\n- [ ] Milk [Homer](../people/homer.md) 📅 {SOON.isoformat()}\n"
        "- [x] Butter ✅ 2026-10-01\n\n"
        "## Drugstore\n- [ ] Toothpaste\n"),
    "family/lists/archive/groceries/2026-09.md": (
        "---\ntype: list\ncard_id: list-groceries-2026-09\ntitle: \"Groceries, 2026-09\"\n"
        "kind: archive\n---\n\n- [x] Flour ✅ 2026-09-12\n"),
    "family/lists/archive/groceries/2026-10.md": (
        "---\ntype: list\ncard_id: list-groceries-2026-10\ntitle: \"Groceries, 2026-10\"\n"
        "kind: archive\n---\n\n- [x] Eggs ✅ 2026-10-02\n- [x] Cheese ✅ 2026-10-09\n"),
    "family/lists/dates.md": (
        "---\ntype: list\ncard_id: list-dates\ntitle: Dates\nkind: calendar\n---\n\n"
        f"- [ ] Parents' evening 📅 {SOON.isoformat()}\n"),
    # Homer's own list, which only he has.
    "homer/lists/garage.md": (
        "---\ntype: list\ncard_id: list-garage\ntitle: Garage\n---\n\n"
        "- [ ] Fix the bike\n- [ ] Oil the hinges\n"),
}
# Homer's own list with the family's slug, made in Forgejo after the tick:
# the chat's commands ask which bucket is meant once two lists share a slug.
SNACKS = {"homer/lists/groceries.md": (
    "---\ntype: list\ncard_id: list-groceries-2\ntitle: Homer's snacks\n---\n\n- [ ] Donuts\n")}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    """The Simpsons' memory, after Marge ticked the bananas in the chat today."""
    with running_memory(tmp_path_factory.mktemp("list-page"), {**VAULT, **LISTS}) as running:
        ticked = running.person("marge").runs("memory", "list",
                                              ["tick", "groceries", "Bananas", "--as", "marge"])
        assert ticked.get("committed"), ticked
        running.edited_in_forgejo(SNACKS, by="homer", subject="a list of his own")
        yield running


@pytest.fixture
def tools(memory, monkeypatch):
    """Core's tools server, reading the memory as it does on an instance."""
    server = tools_server("server")

    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    monkeypatch.setattr(server, "BRAIN_DIR", memory.vault)
    monkeypatch.setattr(server, "VAULT_DIR", memory.vault)
    return server.app


def opens(app, path: str, language: str = "en") -> Page:
    page = Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000",
                   language=language).opens(path)
    assert page.status == 200, page
    return page


def lands(app, path: str) -> str:
    page = Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000").opens(path)
    assert page.status == 302, page
    return page.location


# ── A list's page ─────────────────────────────────────────────────────────

def test_a_list_shows_its_sections_in_order_with_their_items(tools):
    """Given the groceries list, when its page opens, then its sections
    show in the list's order, each with its items, and the head says the
    kind and how many items are open."""
    page = opens(tools, "/card/list-groceries")

    assert page.texts("kicker") == ["List"]
    assert page.texts("facts") == ["To-do list"]
    assert page.texts("extra") == ["3 open"]
    assert [h for h in page.texts("section-title")] == ["Fruit & Veg", "Dairy", "Drugstore"]
    assert page.texts("item-text") == ["Apples", "Bananas", "Milk", "Toothpaste"]


def test_an_open_item_shows_who_it_is_for_and_its_due_day(tools):
    page = opens(tools, "/card/list-groceries")

    assert page.texts("item-due") == [f"{SOON.day} {SOON.strftime('%B')}"
                                      + ("" if SOON.year == TODAY.year else f" {SOON.year}")]
    assert page.texts("item-for") == ["Homer"]


def test_an_item_ticked_today_shows_ticked_with_who_ticked_it(tools):
    """Bananas was ticked today by Marge: greyed, with "ticked by Marge"."""
    page = opens(tools, "/card/list-groceries")

    [done] = page.texts("done")
    assert "Bananas" in done and done.endswith("ticked by Marge")
    assert page.texts("by") == ["ticked by Marge"]


def test_an_item_ticked_before_today_is_not_on_the_page(tools):
    """Resting state: Butter was ticked in October; it belongs to the
    archive's months, not to the list."""
    assert "Butter" not in opens(tools, "/card/list-groceries").text


def test_the_archive_months_are_listed_newest_first(tools):
    page = opens(tools, "/card/list-groceries")

    assert page.texts("subhead")[0] == "Earlier months"
    assert page.links("archive") == [("October 2026 · 2 items", "/card/list-groceries-2026-10"),
                                     ("September 2026 · 1 item", "/card/list-groceries-2026-09")]


def test_the_page_says_where_items_are_added_and_ticked(tools):
    """Nothing on the page adds or ticks an item: the chat does."""
    page = opens(tools, "/card/list-groceries")

    assert page.texts("chatline") == ["Add or tick items in the chat: 'Stacky, we need milk.'"]
    sheet = page.body.split('class="sheet', 1)[1]
    assert "<input" not in sheet and "<button" not in sheet


def test_a_list_without_sections_shows_its_items_under_no_heading(tools):
    page = opens(tools, "/card/list-garage")

    assert page.texts("section-title") == []
    assert page.texts("item-text") == ["Fix the bike", "Oil the hinges"]


def test_a_calendar_list_says_its_next_date(tools):
    page = opens(tools, "/card/list-dates")

    assert page.texts("facts") == ["Calendar list"]
    assert page.texts("extra")[0].startswith("Next: Parents' evening, ")


def test_a_list_page_in_german(tools):
    page = opens(tools, "/card/list-groceries", language="de")

    assert page.texts("by") == ["abgehakt von Marge"]
    assert page.links("archive")[0] == ("Oktober 2026 · 2 Punkte", "/card/list-groceries-2026-10")


def test_opening_a_list_page_writes_nothing(tools, memory):
    """Resting state: no list file changes when its page is read."""
    before = memory.forgejo.head()
    opens(tools, "/card/list-groceries")
    assert memory.forgejo.head() == before


# ── /go/list/<slug> ───────────────────────────────────────────────────────

def test_a_list_link_of_a_persons_own_list_lands_on_its_page(tools):
    """Given Homer's own garage list, `/go/list/garage` lands on its page, by its id."""
    assert lands(tools, "/go/list/garage") == "/card/list-garage"


def test_a_list_link_prefers_the_familys_list(tools):
    """The family and Homer both have a list `groceries`: the family's wins."""
    assert lands(tools, "/go/list/groceries") == "/card/list-groceries"


def test_a_list_link_without_a_list_says_no_card_has_it(tools):
    page = Browser(tools, listener=PUBLIC_PORT, host="192.168.1.20:42000").opens("/go/list/nothing")
    assert page.status == 404
