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
import time

import pytest

from tests.dashboard_host import DashboardHost
from tests.memory.unit.instance import running_memory
from tests.memory.unit.simpsons_cards import PEOPLE

from .conftest import tools_server
from .test_memory_graph_pages import opens, phone, sections

QUILT, MANY, LINKS, LONG = "topic-quilt", "topic-many", "topic-links", "topic-long"
UNPROMPTED = "topic-unprompted"
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
    files |= _topic("marge/long", LONG, "Fifty notes")
    for n in range(50):
        files[f"marge/long/notes/l{n:02}.md"] = (
            f"---\ntype: note\ncard_id: note-l{n:02}\ntitle: Long {n}\ndate: \"2026-0{6 + n // 28}-{1 + n % 28:02}\"\n"
            f"persons:\n  - marge\n---\n\n# Long {n}\n\nText {n}.\n")
    files |= _topic("marge/unprompted", UNPROMPTED, "Model only")
    files |= _note("marge/unprompted", "loose", "Loose note", "2026-09-06", "Sent as it was.",
                   ["Sent as it was."], {"at": MADE, "model": "qwen3"})
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
    """Criteria 2 and 5: ✦ beside each AI note's date; none for the note filed as sent."""
    page = marge(tools, QUILT)

    assert entry(page, "binding").texts("ai-mark") == entry(page, "batting").texts("ai-mark") == ["✦"]
    assert entry(page, "fabric").texts("ai-mark") == []


@pytest.mark.parametrize("slug", ["binding", "plan", "fabric", "batting"])
def test_the_topic_page_and_the_card_page_mark_the_same_notes(tools, slug):
    """Given notes the AI wrote and one filed as sent, then each note has ✦ on
    the topic page exactly when its own card page has it: both pages decide
    by one rule from the sidecar, so they never disagree."""
    device = phone(tools, person="marge")
    on_topic = bool(entry(opens(device, f"/memory/topic/{QUILT}"), slug).texts("ai-mark"))
    on_card = bool(opens(device, f"/card/note-{slug}").texts("ai-mark"))

    assert on_topic == on_card == (slug != "fabric")


AI_ROUTES = {
    "managed": '[ai]\nprovider = "managed"\n',
    "external": '[ai]\nprovider = "external"\nopenai_url = "http://192.168.1.30:11434/v1"\ndefault = "qwen3:8b"\n',
    "unknown": "",
}


@pytest.mark.parametrize("route, hint, key", [
    ("managed", "Summarised by famstack's AI", "famstack's AI summarised the entry"),
    ("external", "Summarised by your family's AI server", "your family's AI server summarised the entry"),
    ("unknown", "Summarised by AI", "an AI summarised the entry"),
])
def test_the_mark_names_the_ai_the_stack_uses(tools, host, route, hint, key):
    """Given `[ai] provider` managed, external or not set, then ✦ and the key
    name famstack's AI, the family's own AI server, or just an AI: a family
    with its own AI server is not told famstack's AI wrote its notes."""
    with (host.root / "stack.toml").open("a") as toml:
        toml.write("\n" + AI_ROUTES[route])
    page = marge(tools, QUILT)

    assert entry(page, "binding").attributes("ai-mark", "title") == [hint]
    assert key in page.texts("key")[0]


def test_the_mark_names_just_an_ai_when_the_stack_does_not_answer(tools, host):
    """Given a host API that fails `ai recommended show`, then ✦ says
    "Summarised by AI" and the page still opens."""
    host.failing.add("ai_recommended")
    page = marge(tools, QUILT)

    assert entry(page, "binding").attributes("ai-mark", "title") == ["Summarised by AI"]


@pytest.mark.parametrize("route, hint", [
    ("managed", "Summarised by famstack's AI"),
    ("external", "Summarised by your family's AI server"),
    ("unknown", "Summarised by AI"),
])
def test_the_card_page_names_the_same_ai_as_the_topic_page(tools, host, route, hint):
    """Given `[ai] provider` managed, external or not set, then a note's own
    card page names the same AI beside its date as the topic page: both
    pages take the words of one rule."""
    with (host.root / "stack.toml").open("a") as toml:
        toml.write("\n" + AI_ROUTES[route])
    device = phone(tools, person="marge")
    on_card = opens(device, "/card/note-binding")
    on_topic = entry(opens(device, f"/memory/topic/{QUILT}"), "binding")

    # The card page has ✦ twice, beside the date and before its key line.
    assert set(on_card.attributes("ai-mark", "title")) == set(on_topic.attributes("ai-mark", "title")) == {hint}


# The page waits this long at most for a host API that hangs: its own read
# of the AI's route gives up after about 2 s, far below the 30 s of other reads.
HANGING_HOST_SECS = 5


@pytest.mark.parametrize("address", [f"/memory/topic/{QUILT}", "/card/note-binding"])
def test_a_host_api_that_never_answers_names_just_an_ai_and_keeps_the_page_fast(tools, host, address):
    """Given a host API that takes `ai recommended show` and never answers,
    then the topic page and the card page still answer within seconds, and
    ✦ says "Summarised by AI": the AI's route is a detail of the words, so
    it never holds a page."""
    host.stalling.add("ai_recommended")
    started = time.monotonic()
    page = opens(phone(tools, person="marge"), address)
    took = time.monotonic() - started

    assert took < HANGING_HOST_SECS
    assert page.attributes("ai-mark", "title")[0] == "Summarised by AI"
    assert any(r.get("cmd") == "ai_recommended" for r in host.asked)


def test_a_model_without_a_prompt_template_gets_no_mark_and_no_key(tools):
    """Given a topic whose one note has a sidecar `made` that names a model
    and no prompt template, then the entry has no ✦ and the head no key: the
    head decides by the card page's rule, not by `made` being there."""
    page = marge(tools, UNPROMPTED)

    assert entry(page, "loose").texts("ai-mark") == []
    assert page.texts("key") == []


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


def test_a_later_part_of_a_long_topic_draws_no_entry(tools):
    """Given 50 notes, when Marge opens part 2, then no entry is drawn in
    full again: part 1 shows the newest ten, part 2 lists its ten older
    notes as rows."""
    device = phone(tools, person="marge")
    first = opens(device, f"/memory/topic/{LONG}")
    second = opens(device, f"/memory/topic/{LONG}?part=2")

    assert len(first.attributes("entry", "data-entry")) == 10
    assert second.attributes("entry", "data-entry") == []
    assert [h for _, h in second.within("data-section", "entries").links("row")
            if h.startswith("/card/")] == [f"/card/note-l{n:02}" for n in range(9, -1, -1)]


def test_a_topic_without_notes_has_no_entries_section(tools):
    """Criterion 7."""
    page = marge(tools, LINKS)

    assert "entries" not in sections(page)
    assert page.attributes("entry", "data-entry") == []


def test_the_entries_are_german_in_german(tools):
    page = marge(tools, QUILT, language="de")

    assert page.within("data-section", "entries").texts("section-title") == ["Einträge"]
    assert entry(page, "plan").texts("show-messages") == ["Originalnachrichten anzeigen"]
