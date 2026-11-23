"""A topic's page shows its newest notes in full, their messages one tap away.

Marge's phone opens `/memory/topic/<id>` of three fabricated topics: her
quilt (four notes: three written by the AI, one filed as sent, one of the
three with a reply added after its text was written), a topic of fourteen
notes, and a topic with a bookmark and no note. The memory is memory's test
instance, core's tools server answers over HTTP, and the page is read the
way a browser without scripts reads it.
"""

from __future__ import annotations

import json

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .test_memory_graph_pages import opens, phone, sections

QUILT, MANY, LINKS = "topic-quilt", "topic-many", "topic-links"
FILED = "2026-09-04T10:00:00Z"
MADE = "2026-09-04T10:30:00Z"
LATER = "2026-09-05T08:00:00Z"


def _source(n: int, role: str = "original", when: str = FILED) -> dict:
    return {"role": role, "person": "marge", "time": when, "channel": "chat", "kind": "text", "id": f"$m{n}"}


def _note(folder: str, slug: str, title: str, day: str, text: str, quotes: list[str],
          made: dict | None, replies: int = 0) -> dict[str, str]:
    """A note and its sidecar: the text, `## Sources` with one quote per message
    (replies last), and `made` the way the filing writes it."""
    shown = "\n\n".join(f"> {q}" for q in quotes)
    card = (f"---\ntype: note\ncard_id: note-{slug}\ntitle: {title}\ndate: \"{day}\"\n"
            f"persons:\n  - marge\nfiled_by: marge\n---\n\n# {title}\n\n{text}\n\n## Sources\n\n{shown}\n")
    originals = len(quotes) - replies
    sources = [_source(n) for n in range(originals)] + [_source(originals + n, "reply", LATER)
                                                         for n in range(replies)]
    side = json.dumps({"card_id": f"note-{slug}", "filed_by": "marge", "sources": sources,
                       "made": made if made is not None else {"model": ""}})
    return {f"{folder}/notes/{slug}.md": card, f"{folder}/notes/{slug}.json": side}


def _topic(folder: str, card_id: str, title: str) -> dict[str, str]:
    return {f"{folder}/topic.md": f"---\ntype: topic\ncard_id: {card_id}\ntitle: {title}\n---\n\n# {title}\n"}


AI = {"at": MADE, "model": "qwen3", "prompt_template": "abc123"}


def _files() -> dict[str, str]:
    files = dict(PEOPLE)
    files |= _topic("marge/quilt", QUILT, "Quilt for Lisa")
    files |= _note("marge/quilt", "binding", "Binding corners", "2026-09-04",
                   "## Steps\n\n- Cut the strips 2.5 inches wide\n- Mitre each corner\n",
                   ["I started on the binding.", "The strips were 2.5 inches.", "Bart says a second pass."],
                   AI, replies=1)
    files |= _note("marge/quilt", "plan", "Quilting plan", "2026-09-03", "Stitch in the ditch first.",
                   ["Stitch in the ditch first."], AI)
    files |= _note("marge/quilt", "fabric", "Fabric run", "2026-09-02", "Spare fat quarters are in the blue box.",
                   ["Spare fat quarters are in the blue box."], None)
    files |= _note("marge/quilt", "batting", "Batting", "2026-09-01", "Wool batting, 90 inches.",
                   ["Wool batting, 90 inches."], AI)
    files |= _topic("marge/many", MANY, "Many notes")
    for n in range(14):
        files |= _note("marge/many", f"m{n:02}", f"Entry {n}", f"2026-08-{n + 1:02}", f"Text {n}.",
                       [f"words {n}"], AI)
    files |= _topic("marge/links", LINKS, "Only links")
    files["marge/links/bookmarks/b.md"] = (
        "---\ntype: bookmark\ncard_id: bookmark-one\ntitle: A place\ndate: \"2026-10-01\"\n"
        "persons:\n  - marge\nresource: \"https://site.example/page\"\n---\n\n# A place\n")
    return files


@pytest.fixture(scope="module")
def memory(tmp_path_factory):
    with running_memory(tmp_path_factory.mktemp("topic-entries"), _files()) as running:
        yield running


@pytest.fixture
def host(tmp_path, monkeypatch):
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


def marge(tools, topic, language="en"):
    return opens(phone(tools, person="marge", language=language), f"/memory/topic/{topic}")


def entry(page, slug):
    return page.within("data-entry", f"note-{slug}")


# ── Entries in full ───────────────────────────────────────────────────────

def test_the_quilts_notes_read_in_full_newest_first(tools):
    """Criterion 1: "Entries 4" lists the notes newest first, each with who, when,
    the title and its text as it is."""
    page = marge(tools, QUILT)
    entries = page.within("data-section", "entries")

    assert "entries" in sections(page)
    assert entries.texts("section-title") == ["Entries"] and entries.texts("fl-count") == ["4"]
    assert entries.texts("entry-title") == ["Binding corners", "Quilting plan", "Fabric run", "Batting"]
    first = entry(page, "binding")
    assert first.texts("entry-who") == ["Marge"] and first.texts("entry-date") == ["4 September"]
    assert "Cut the strips 2.5 inches wide" in first.texts("entry-text")[0]
    assert "Steps" in first.texts("entry-text")[0], "the heading stays a heading of the text"


def test_a_note_the_ai_wrote_has_the_mark_beside_its_date(tools):
    """Criteria 2 and 5: ✦ with the card page's words beside each AI note's date; none for the
    note filed as sent."""
    page = marge(tools, QUILT)

    assert entry(page, "binding").attributes("ai-mark", "title") == ["Summarised by famstack's AI"]
    assert entry(page, "batting").texts("ai-mark") == ["✦"]
    assert entry(page, "fabric").texts("ai-mark") == []


def test_each_foot_folds_the_messages_and_opens_the_entry(tools):
    """Criterion 3: "show the original messages" opens the messages in place, "Open the entry ›"
    links the card's page."""
    page = marge(tools, QUILT)
    plan = entry(page, "plan")

    assert plan.texts("show-messages") == ["show the original messages"]
    assert [href for text, href in plan.links("entry-foot") if text == "Open the entry ›"] == ["/card/note-plan"]
    assert plan.texts("words") == ["Stitch in the ditch first."], "the message as sent, folded in the entry"
    assert entry(page, "fabric").texts("show-messages") == ["show the original messages"]


def test_a_reply_after_the_text_was_written_is_counted_above_the_text(tools):
    """Criterion 4: "↩ 1 message added after this text" stands above the text; the text is as written."""
    page = marge(tools, QUILT)
    bound = entry(page, "binding")

    assert bound.texts("entry-later") == ["↩ 1 message added after this text"]
    assert "Cut the strips" in bound.texts("entry-text")[0]
    assert "Added by Marge" in bound.texts("added-by")[0]
    assert entry(page, "plan").texts("entry-later") == []


# ── Many notes, none ──────────────────────────────────────────────────────

def test_fourteen_notes_show_the_newest_ten_in_full_and_the_older_four_as_rows(tools):
    """Criterion 6."""
    page = marge(tools, MANY)
    entries = page.within("data-section", "entries")

    assert entries.texts("fl-count") == ["14"]
    assert len(entries.texts("entry-title")) == 10 and entries.texts("entry-title")[0] == "Entry 13"
    assert [href for _, href in entries.links("row") if href.startswith("/card/")] == [f"/card/note-m{n:02}" for n in (3, 2, 1, 0)]


def test_a_topic_without_notes_has_no_entries_section(tools):
    """Criterion 7."""
    page = marge(tools, LINKS)

    assert "entries" not in sections(page)
    assert page.attributes("entry", "data-entry") == []


def test_the_entries_are_german_in_german(tools):
    page = marge(tools, QUILT, language="de")

    assert page.within("data-section", "entries").texts("section-title") == ["Einträge"]
    assert entry(page, "plan").texts("show-messages") == ["Originalnachrichten anzeigen"]
