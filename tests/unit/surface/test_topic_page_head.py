"""A topic's page opens with a compact head, two chips and, for a topic of
mostly links or documents, that list as its main list.

Marge's phone opens `/memory/topic/<id>` of two fabricated topics: her quilt
(3 bookmarks, 1 document, 5 notes, one summarised by a model) and the
family's camper van (12 bookmarks, 2 documents, 7 notes). The memory is
memory's test instance, core's tools server answers over HTTP, and the page
is read the way a browser without scripts reads it.
"""

from __future__ import annotations

import json

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .test_memory_graph_pages import opens, phone, sections

QUILT = "topic-quilt"
CAMPER = "topic-camper"
BOWLING = "topic-plain"


def _bookmark(folder: str, n: int, who: str = "marge") -> dict[str, str]:
    return {f"{folder}/bookmarks/b{n}.md": (
        f"---\ntype: bookmark\ncard_id: bookmark-{folder.split('/')[-1]}-{n}\ntitle: Place {n}\n"
        f"date: \"2026-10-{n + 10:02}\"\npersons:\n  - {who}\n"
        f"resource: \"https://www.site{n}.example/page\"\n---\n\n# Place {n}\n\n"
        f"> **About** [Marge](../../people/marge.md)\n\nWhy it was kept: number {n}.\n")}


def _document(folder: str, n: int, kind: str) -> dict[str, str]:
    return {f"{folder}/documents/d{n}.md": (
        f"---\ntype: document\ncard_id: document-{folder.split('/')[-1]}-{n}\ntitle: Paper {n}\n"
        f"date: \"2026-09-{n + 1:02}\"\ndocument_type: {kind}\npersons:\n  - marge\n---\n\n# Paper {n}\n")}


def _note(folder: str, n: int, who: str = "marge") -> dict[str, str]:
    return {f"{folder}/notes/n{n}.md": (
        f"---\ntype: note\ncard_id: note-{folder.split('/')[-1]}-{n}\ntitle: Note {n}\n"
        f"date: \"2026-09-{n + 1:02}\"\npersons:\n  - {who}\nfiled_by: {who}\n---\n\n# Note {n}\n\n"
        f"Text of note {n}.\n\n## Sources\n\n> words {n}\n")}


def _topic(folder: str, card_id: str, title: str) -> dict[str, str]:
    return {f"{folder}/topic.md": f"---\ntype: topic\ncard_id: {card_id}\ntitle: {title}\n---\n\n# {title}\n"}


def _files() -> dict[str, str]:
    files = dict(PEOPLE)
    files |= _topic("marge/quilt", QUILT, "Quilt for Lisa")
    for n in range(3):
        files |= _bookmark("marge/quilt", n)
    files |= _document("marge/quilt", 0, "Receipt")
    for n in range(5):
        files |= _note("marge/quilt", n, who="marge" if n < 4 else "homer")
    files["marge/quilt/notes/n0.json"] = json.dumps({
        "card_id": "note-quilt-0", "filed_by": "marge",
        "sources": [{"role": "original", "person": "marge", "time": "2026-09-01T10:00:00Z",
                     "channel": "chat", "kind": "text", "id": "$E0"}],
        "made": {"model": "qwen3", "prompt_template": "abc123"}})
    files |= _topic("family/camper", CAMPER, "Camper van")
    for n in range(12):
        files |= _bookmark("family/camper", n)
    for n, kind in enumerate(("Policy", "Receipt")):
        files |= _document("family/camper", n, kind)
    for n in range(7):
        files |= _note("family/camper", n)
    files |= _topic("family/plain", BOWLING, "Plain topic")
    files |= _note("family/plain", 0)
    return files


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("topic-head"), _files()) as running:
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
    """Core's tools server, reaching the memory's curator, in UTC."""
    monkeypatch.setenv("MEMORY_CARDS_URL", memory.curator)
    monkeypatch.setenv("TIMEZONE", "UTC")
    return tools_server("server").app


def marge(tools, topic):
    return opens(phone(tools, person="marge"), f"/memory/topic/{topic}")


# ── The head ──────────────────────────────────────────────────────────────

def test_the_head_is_the_kicker_the_title_the_facts_and_the_key(tools):
    """Given the quilt, when its page opens, then the head says whose topic
    it is, its title, how many cards and how many are about Marge, and the
    key; today's row of type chips is gone."""
    page = marge(tools, QUILT)

    assert page.texts("kicker") == ["Own topic"]
    assert page.texts("title") == ["Quilt for Lisa"]
    assert page.texts("facts") == ["9 cards · 9 about you"]
    assert page.texts("key") == ["✦ beside a date: an AI summarised the entry. "
                                 "Everything else is taken from the cards as they are."]
    assert page.texts("filters") == []


def test_the_key_is_left_out_when_no_entry_was_summarised(tools):
    page = marge(tools, BOWLING)

    assert page.texts("key") == []


# ── The chips ─────────────────────────────────────────────────────────────

def test_two_chips_name_the_bookmarks_and_the_documents_and_both_start_closed(tools):
    page = marge(tools, QUILT)

    assert page.texts("topic-chip") == ["3 bookmarks", "1 document"]
    assert page.attributes("topic-chip", "aria-expanded") == ["false", "false"]
    assert page.attributes("topic-chip", "data-opens") == ["bookmarks", "documents"]
    assert "main-bookmarks" not in sections(page)


def test_a_chip_with_nothing_behind_it_is_left_out(tools):
    page = marge(tools, BOWLING)

    assert page.texts("topic-chip") == []


def test_a_bookmark_row_names_its_title_its_site_who_and_when(tools):
    page = marge(tools, QUILT)
    rows = page.within("data-panel", "bookmarks")

    assert ("Place 2", "https://www.site2.example/page") in rows.links()
    assert rows.texts("mark-site")[0] == "site2.example"
    assert "Marge" in rows.texts("mark-by")[0]
    assert "Oct" in rows.texts("mark-by")[0]


def test_a_document_row_names_its_title_its_kind_who_and_when(tools):
    page = marge(tools, QUILT)
    rows = page.within("data-panel", "documents")

    assert [t for t, _ in rows.links()] == ["Paper 0"]
    assert rows.texts("mark-site") == ["Receipt"]
    assert "Marge" in rows.texts("mark-by")[0]
    assert page.within("data-panel", "documents").attributes("ti", "data-type") == ["document"]


# ── The main list ─────────────────────────────────────────────────────────

def test_a_topic_of_mostly_bookmarks_opens_on_them_as_its_main_list(tools):
    """Given 12 of 21 cards are bookmarks, then the bookmarks chip starts
    open, and "Bookmarks 12" is a section after the others and before the
    entries: the newest 5 with their words, then "All 12 ›"."""
    page = marge(tools, CAMPER)

    assert page.attributes("topic-chip", "aria-expanded") == ["true", "false"]
    assert "main-bookmarks" in sections(page)
    main = page.within("data-section", "main-bookmarks")
    assert main.texts("section-title") == ["Bookmarks"]
    assert main.texts("fl-count") == ["12"]
    assert len(main.texts("mark-title")) == 12, "the rest is in the page, folded behind All"
    assert len(main.texts("mark-more")) == 7, "all but the newest 5 are folded"
    assert "Why it was kept: number 11." in main.texts("mark-said")[0]
    assert main.texts("all-rows") == ["All 12 ›"]


def test_a_topic_of_a_few_bookmarks_has_no_main_list(tools):
    page = marge(tools, QUILT)

    assert "main-bookmarks" not in sections(page)
    assert "main-documents" not in sections(page)


def test_the_main_list_comes_before_the_entries(tools):
    order = sections(marge(tools, CAMPER))

    assert order.index("main-bookmarks") < order.index("cards")
