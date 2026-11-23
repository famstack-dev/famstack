"""A topic's answer: everything the topic's page draws, in one reply.

Driven over real HTTP against memory's test instance (`running_memory`), as
`test_memory_overview.py` is; the shared builders and reads are in
`overview_world.py`. What to assert: the bookmarks, documents, lists, what
changed and the entries a topic answers, and that only its first part
carries the entries.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest

from tests.memory.unit.instance import RunningMemory, running_memory
from tests.memory.unit.overview_world import SEEDED, TODAY, card, overview, person, topic


# ── A topic's answer: everything its page draws ───────────────────────────

def _trip_files() -> dict[str, str]:
    """A topic "Trip" with 12 bookmarks, 3 documents, 4 notes (the oldest made by a
    model, with its sources), a packing list tagged with the topic with 7 open
    items, and a list that only sits in the topic's folder."""
    files = {"family/people/marge.md": person("marge", "Marge Simpson"),
             "family/people/homer.md": person("homer", "Homer Simpson"),
             "family/trip/topic.md": topic("trip", "Trip")}
    for n in range(12):
        files[f"family/trip/bookmarks/b{n:02}.md"] = (
            f"---\ntype: bookmark\ncard_id: bookmark-{n:02}\ntitle: Place {n}\n"
            f"date: \"2026-09-{n + 10:02}\"\npersons:\n  - marge\n"
            f"resource: \"https://www.site{n}.example/page\"\n---\n\n# Place {n}\n\n"
            f"> **About** [Marge](../../people/marge.md)\n\nWhy it was kept: number {n}.\n")
    for n, kind in enumerate(("Receipt", "Receipt", "Policy")):
        files[f"family/trip/documents/d{n}.md"] = card(
            "document", f"document-{n}", f"Paper {n}", f"2026-09-0{n + 1}", ["marge"], document_type=kind)
    for n in range(4):
        files[f"family/trip/notes/n{n}.md"] = (
            f"---\ntype: note\ncard_id: note-trip-{n}\ntitle: Note {n}\ndate: \"2026-09-0{n + 1}\"\n"
            f"persons:\n  - marge\nfiled_by: marge\n---\n\n# Note {n}\n\nText of note {n}.\n\n"
            f"## Sources\n\n> words {n}\n")
    files["family/trip/notes/n0.json"] = json.dumps({
        "card_id": "note-trip-0", "filed_by": "marge",
        "sources": [{"role": "original", "person": "marge", "time": "2026-09-01T10:00:00Z",
                     "channel": "chat", "kind": "text", "id": "$E0"}],
        "made": {"at": "2026-09-01T10:30:00Z", "model": "qwen3", "prompt_template": "abc123"}})
    items = "".join(f"- [ ] Item {i}\n" for i in range(7)) + "- [x] Done\n"
    files["family/trip/lists/packing.md"] = (
        "---\ntype: list\ncard_id: list-packing\ntitle: Packing\ntags:\n  - trip\n---\n\n" + items)
    files["family/trip/lists/other.md"] = (
        "---\ntype: list\ncard_id: list-other\ntitle: Other\n---\n\n- [ ] One\n- [ ] Two\n")
    return files


@pytest.fixture(scope="module")
def trip(tmp_path_factory) -> Iterator[RunningMemory]:
    """The trip topic. The archivist filed a letter for Marge a week ago (she is the
    author, the archivist the committer); Homer changed a note a month ago."""
    with pytest.MonkeyPatch.context() as clock, \
            running_memory(tmp_path_factory.mktemp("trip"), _trip_files(), seeded_at=SEEDED) as memory:
        clock.setenv("STACK_TODAY", TODAY)
        memory.edited_in_forgejo({"family/trip/documents/d0.md": _trip_files()["family/trip/documents/d0.md"] + "\nx\n"},
                                 by="marge", committer="archivist-bot", subject="capture: Paper 0",
                                 at="2026-09-24T10:00:00")
        yield memory


def test_a_topics_answer_holds_bookmarks_newest_first_documents_by_kind_and_counts(trip):
    """Given a topic with 12 bookmarks, 3 documents and 4 notes, then the answer has
    the bookmarks newest first with site and words, the documents by kind, and 12, 3, 4."""
    page = overview(trip, "topic/topic-trip")

    assert page["bookmarks"]["count"] == 12
    first = page["bookmarks"]["items"][0]
    assert (first["id"], first["site"], first["words"]) == (
        "bookmark-11", "site11.example", "Why it was kept: number 11.")
    assert [(k["kind"], k["count"]) for k in page["documents"]["kinds"]] == [("Receipt", 2), ("Policy", 1)]
    assert page["counts"] == {"bookmark": 12, "note": 4, "document": 3, "list": 2}


def test_a_topics_to_do_list_carries_its_first_five_open_items_and_another_list_its_count(trip):
    """Given a list tagged with the topic and one that only sits in it, then the first
    names 5 of its 7 open items, and the other its count alone."""
    lists = {lst["id"]: lst for lst in overview(trip, "topic/topic-trip")["lists"]}

    assert lists["list-packing"]["open"] == 7
    assert [i["title"] for i in lists["list-packing"]["items"]] == [f"Item {i}" for i in range(5)]
    assert lists["list-other"]["open"] == 2 and "items" not in lists["list-other"]


def test_what_the_archivist_filed_for_marge_is_marges_change_in_the_topic(trip):
    """Given a letter the archivist filed for Marge in the last 14 days, then the topic's
    changed lists it as Marge's."""
    changed = overview(trip, "topic/topic-trip")["changed"]

    assert [(i["id"], i["by"]) for i in changed["items"]] == [("document-0", "marge")]


def test_the_newest_ten_entries_come_in_full_and_older_notes_stay_in_cards(trip):
    """Given 4 notes, then `entries` holds them newest first with text, title, date,
    sender, sources and `made`, the sidecar's record of the model that wrote the
    text: the page decides ✦ from it by the card page's rule, so the two agree."""
    page = overview(trip, "topic/topic-trip")

    assert [e["id"] for e in page["entries"]] == ["note-trip-3", "note-trip-2", "note-trip-1", "note-trip-0"]
    oldest = page["entries"][-1]
    assert (oldest["title"], oldest["day"], oldest["sender"]) == ("Note 0", "2026-09-01", "marge")
    assert "Text of note 0." in oldest["text"]
    assert [s["quote"] for s in oldest["sources"]] == ["words 0"]
    assert oldest["made"] == {"at": "2026-09-01T10:30:00Z", "model": "qwen3", "prompt_template": "abc123"}
    assert page["entries"][0]["made"] == {} and page["entries"][0]["sources"] == []
    assert page["cards"]["count"] == 21


def test_only_ten_entries_are_in_the_answer(tmp_path, monkeypatch):
    """Given 12 notes, then `entries` holds the newest 10 and `cards` all 12."""
    monkeypatch.setenv("STACK_TODAY", TODAY)
    files = {"family/people/marge.md": person("marge", "Marge Simpson"),
             "family/trip/topic.md": topic("trip", "Trip")}
    for n in range(12):
        files[f"family/trip/notes/n{n:02}.md"] = card("note", f"note-{n:02}", f"N{n}", f"2026-09-{n + 1:02}", ["marge"])
    with running_memory(tmp_path, files) as memory:
        page = overview(memory, "topic/topic-trip")

    assert len(page["entries"]) == 10 and page["entries"][0]["id"] == "note-11"
    assert page["cards"]["count"] == 12


def test_only_the_first_part_of_a_long_topic_carries_entries(tmp_path, monkeypatch):
    """Given 50 notes, when the page asks for part 2, then `entries` is empty:
    the entries are the newest notes, which part 1 already shows in full,
    and part 2 lists the older notes as its cards."""
    monkeypatch.setenv("STACK_TODAY", TODAY)
    files = {"family/people/marge.md": person("marge", "Marge Simpson"),
             "family/trip/topic.md": topic("trip", "Trip")}
    for n in range(50):
        files[f"family/trip/notes/n{n:02}.md"] = card("note", f"note-{n:02}", f"N{n}",
                                                      f"2026-0{7 + n // 28}-{1 + n % 28:02}", ["marge"])
    with running_memory(tmp_path, files) as memory:
        first = overview(memory, "topic/topic-trip")
        second = overview(memory, "topic/topic-trip?part=2")

    assert len(first["entries"]) == 10 and second["entries"] == []
    assert len(second["cards"]["items"]) == 10


def test_a_topic_with_no_cards_answers_empty_parts(tmp_path, monkeypatch):
    """Given a topic without cards, then every part is empty and the counts are 0."""
    monkeypatch.setenv("STACK_TODAY", TODAY)
    files = {"family/people/marge.md": person("marge", "Marge Simpson"),
             "family/trip/topic.md": topic("trip", "Trip")}
    with running_memory(tmp_path, files) as memory:
        empty = overview(memory, "topic/topic-trip")

    assert empty["entries"] == [] and empty["lists"] == [] and empty["counts"] == {}
    assert empty["bookmarks"] == {"count": 0, "items": []}
    assert empty["changed"] == {"count": 0, "items": []}
    assert empty["documents"]["total"] == 0
