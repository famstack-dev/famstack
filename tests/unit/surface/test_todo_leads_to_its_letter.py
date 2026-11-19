"""A to-do the archivist took from a letter leads back to that letter.

The archivist files a letter's to-dos onto a list and writes the letter
as the item's last link (`stack.lists`: a link into the vault that is not
a person names the record the item came from):

    - [ ] Pay entry fee of 40.00 USD [Bowling league registration](../documents/...) 📅 2026-11-30

A person who reads the to-do wants the letter. So "Up next" on the
memory's pages opens the letter's card for such a to-do, and the list's
page links the letter beside the item. A to-do a person added in the
chat has no such link and stays as it is: Up next opens its list, the
list's page shows no link. A letter the device may not read is not
linked either, because the memory answers only with what the device's
audience may read.

Driven through the tools server's FastAPI app (the Browser DSL), read the
way a browser without JavaScript reads the page. The memory is memory's
test instance: a real curator over a bare repository, which core reaches
over HTTP on localhost. The host API is the dashboard's stand-in.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .dsl import Browser, Page

PUBLIC_PORT = tools_server("access").PUBLIC_PORT

# Due days that "Up next" holds: it looks 60 days ahead of today.
DUE = (date.today() + timedelta(days=10)).isoformat()
LATER = (date.today() + timedelta(days=20)).isoformat()

LETTER = "document-b0b11e9a01"
LISAS_LETTER = "document-5a7e11a02"
SPORT = "list-sport"

CARDS = {
    **PEOPLE,
    "family/documents/2026/11/bowling-league.md": (
        f"---\ntype: document\ncard_id: {LETTER}\ntitle: Bowling league registration\n"
        "date: \"2026-11-02\"\ncorrespondent: Barney's Bowlarama\ndocument_type: Letter\n"
        "persons:\n  - homer\n---\n\n# Bowling league registration\n"),
    # In Lisa's own folder: Homer's phone may not read it.
    "lisa/documents/2026/11/band-camp.md": (
        f"---\ntype: document\ncard_id: {LISAS_LETTER}\ntitle: Band camp\n"
        "date: \"2026-11-03\"\ndocument_type: Letter\npersons:\n  - lisa\n---\n\n# Band camp\n"),
    "family/lists/sport.md": (
        f"---\ntype: list\ncard_id: {SPORT}\ntitle: Sport\n---\n\n"
        "- [ ] Pay entry fee of 40.00 USD "
        f"[Bowling league registration](../documents/2026/11/bowling-league.md) 📅 {DUE}\n"
        f"- [ ] New bowling shoes 📅 {LATER}\n"
        "- [ ] Pack the saxophone [Band camp](../../lisa/documents/2026/11/band-camp.md)\n"
        # A letter that was deleted or moved: its path names no card.
        "- [ ] Return the rental ball [Old letter](../documents/2026/10/gone.md)\n"),
}


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("todo-letter"), CARDS) as running:
        yield running


@pytest.fixture
def tools(memory, tmp_path, monkeypatch):
    """Core's tools server, reaching the memory's curator, in UTC, with the
    host API answering for the Simpsons."""
    host = DashboardHost.in_state("services", root=tmp_path / "instance")
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(host.serve()))
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    yield tools_server("server").app
    host.close()


def opens(app, path: str, *, person: str) -> Page:
    page = Browser(app, listener=PUBLIC_PORT, host="192.168.1.20:42000", person=person).opens(path)
    assert page.status == 200, page
    return page


def up_next(app, *, person: str) -> dict[str, tuple[str, str]]:
    """The rows of Up next on `person`'s /memory: the to-do's text -> (where
    it leads, the quiet line under it)."""
    part = opens(app, "/memory", person=person).within("data-section", "up_next")
    return {text: (href, quiet) for (text, href), quiet in zip(part.links("change-line"), part.texts("fl-meta"))}


# ── Up next ───────────────────────────────────────────────────────────────

def test_a_todo_from_a_letter_opens_the_letter_in_up_next(tools):
    """Given the letter's to-do on the Sport list, when Homer clicks it in
    Up next, then the letter's card opens, not the list."""
    lines = up_next(tools, person="homer")

    assert lines["Pay entry fee of 40.00 USD"] == (f"/card/{LETTER}", "Bowling league registration")


def test_a_todo_added_in_the_chat_opens_its_list_in_up_next(tools):
    """Resting state: a to-do with no letter behind it opens its list, as
    before, and the quiet line names the list."""
    assert up_next(tools, person="homer")["New bowling shoes"] == (f"/card/{SPORT}", "Sport")


# ── The list's page ───────────────────────────────────────────────────────

def test_the_list_page_links_the_letter_a_todo_came_from(tools):
    """Given the same to-do on the Sport list's page, then the item links
    the letter it came from, by its title."""
    page = opens(tools, f"/card/{SPORT}", person="homer")

    assert page.links("item-from") == [("Bowling league registration", f"/card/{LETTER}")]


def test_a_letter_the_device_may_not_read_is_not_linked(tools):
    """Lisa's own letter is not Homer's to read: the item shows, without a link."""
    page = opens(tools, f"/card/{SPORT}", person="homer")

    assert "Pack the saxophone" in page.texts("item-text")
    assert all(href != f"/card/{LISAS_LETTER}" for _, href in page.links())


def test_a_letter_no_card_has_any_more_leaves_a_plain_todo(tools):
    """Given a to-do whose letter was deleted or moved, so its link names a
    path no card has, then the list's page shows it as a plain to-do: no
    link and no error."""
    page = opens(tools, f"/card/{SPORT}", person="homer")

    assert "Return the rental ball" in page.texts("item-text")
    assert all("gone" not in href for _, href in page.links())
