"""What the archivist files into the family memory, and how it lands there.

The archivist files a document Paperless read, a note or a link someone
sent to a room, and an email, each as a card in the family memory. It
writes through the memory's curator, its one writer (ADR-019): each filing
is one commit, by the person it is for and through the archivist's
account, on the version of every file it was made from. What the filing
names is linked: its people, its correspondent and its topic, whose cards
the archivist writes the first time something names them. Its action
items go on its topic's list, its dates on the family's calendar.

These tests drive the archivist as its pipelines do (`publish`,
`publish_capture`, `publish_email_message`) against the real path: the
curator's API over its card store, which pushes to a bare repository as
Forgejo (`running_memory`, memory's test instance). What a test asserts is what Forgejo
then holds and what its commits say. The household is the Simpsons.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO_ROOT / "lib"))
# `memory.lib`, for the ontology a capture's tags are read through.
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))

from stacklets.docs.bot.diary_room import household_now  # noqa: E402
from stacklets.docs.bot.git_mirror import GitMirror  # noqa: E402
from stacklets.docs.bot.memory_client import MemoryClient  # noqa: E402
from tests.memory.unit.instance import git, running_memory  # noqa: E402
from stacklets.docs.bot.vault_context import VaultContext  # noqa: E402

from stack.cards.sources import Source  # noqa: E402
from stack.frontmatter import parse  # noqa: E402

ARCHIVIST = "archivist-bot <archivist-bot@local>"
PEOPLE = {f"family/people/{pid}.md": f"---\ntype: person\ncard_id: person-{pid}\ntitle: {name}\n---\n"
          for pid, name in (("homer", "Homer Simpson"), ("bart", "Bart Simpson"),
                            ("lisa", "Lisa Simpson"))}


def _day(offset: int) -> str:
    """The household's date `offset` days from today."""
    return (household_now().date() + timedelta(days=offset)).isoformat()


@pytest.fixture
def memory(tmp_path, request, monkeypatch):
    """The Simpsons' memory with their people, or the files a test names with
    `@pytest.mark.parametrize("memory", [files], indirect=True)`."""
    files = {**PEOPLE, **(getattr(request, "param", None) or {})}
    with running_memory(tmp_path / "data", files, seeded_by="memory-bot",
                        seeded_as="seed: the Simpsons") as memory:
        # The archivist reads people, lists and the ontology from the
        # curator's clone, as the bot runner mounts it.
        monkeypatch.setenv("MEMORY_VAULT_DIR", str(memory.vault))
        monkeypatch.setenv("LANGUAGE", "en")
        yield memory


@pytest.fixture
def archivist(memory, tmp_path) -> GitMirror:
    """The archivist's mirror, as the bot builds it in the bot runner."""
    return GitMirror(data_dir=tmp_path / "archivist",
                     vault=VaultContext(language="en", shared_bucket="family"),
                     memory=MemoryClient.from_environ(memory.environ))


def files_in(memory, pattern: str) -> list[str]:
    """The paths in Forgejo matching `pattern`, a regular expression."""
    listed = git(memory.forgejo.repo, "ls-tree", "-r", "--name-only", "main").splitlines()
    return [p for p in listed if re.search(pattern, p)]


async def a_document(archivist, paperless_id: int = 247, *, title: str = "Duff Beer invoice",
                     summary: str | None = None, model: str | None = None, **classification):
    """Paperless document `paperless_id`, filed as the document pipeline files it."""
    return await archivist.publish(
        paperless_id=paperless_id, body_text="Two cases of Duff.", processing="ai_formatted",
        model=model, paperless_url="http://docs.home.test", summary=summary,
        tags=classification.pop("tags", None),
        classification={"title": title, "date": "2026-10-13", **classification})


async def a_note(archivist, words: str, *, entity: str = "marge", **kwargs):
    """A note `words`, as the capture pipeline files what someone sent to a room."""
    capture = {"entity": entity, "kind": "note", "source_uri": None, "title_hint": None,
               "body_text": words, "classification": {"title": words, "summary": "s"},
               "captured_at": "2026-10-13", "model": None, **kwargs}
    return await archivist.publish_capture(**capture)


async def a_link(archivist, uri: str, *, title: str, entity: str = "homer", kind: str = "bookmark",
                 **kwargs):
    return await archivist.publish_capture(
        entity=entity, kind=kind, source_uri=uri, title_hint=None, body_text="",
        classification={"title": title, "summary": "s"}, captured_at="2026-10-13", model=None,
        **kwargs)


# ── A document ───────────────────────────────────────────────────────────

async def test_a_document_is_one_commit_by_the_archivist_saying_what_was_learnt(archivist, memory):
    """Given a Duff Insurance letter Paperless read, when the archivist files
    it, then the memory has its card, in one commit by the archivist that
    says what was learnt, the classifier's summary, and which Paperless
    document it is and how it was read.

    `git log` on the memory reads as the household's archive, and its
    trailers find a document's commits by its Paperless id.
    """
    summary = "## Summary\nDuff Insurance car insurance EUR 340/year."
    assert await a_document(archivist, title="Duff Insurance", summary=summary, model="qwen2.5:14b")

    assert memory.forgejo.new_commits() == [f"{ARCHIVIST} | {ARCHIVIST} | learn: Duff Insurance"]
    assert memory.forgejo.message() == (
        f"learn: Duff Insurance\n\n{summary}\n\n"
        "Paperless-Id: 247\nProcessing: ai_formatted\nModel: qwen2.5:14b")
    [card] = files_in(memory, r"-p247\.md$")
    assert card.startswith("family/documents/2026/10/2026-10-13-duff-insurance")


async def test_a_document_filed_again_is_an_update_of_its_card(archivist, memory):
    """Given a filed document, when a refile reads more from it, then its
    card is updated in a commit that says so, with the new summary."""
    await a_document(archivist)
    await a_document(archivist, summary="Now with a summary.", facts=["Two cases of Duff."])

    assert [c.rsplit(" | ", 1)[-1] for c in memory.forgejo.new_commits()] == [
        "learn: Duff Beer invoice", "update: Duff Beer invoice"]
    assert memory.forgejo.message() == ("update: Duff Beer invoice\n\nNow with a summary.\n\n"
                                        "Paperless-Id: 247\nProcessing: ai_formatted")


async def test_a_document_whose_title_changed_moves_in_one_commit_and_keeps_its_id(archivist, memory):
    """Given a filed document, when a refile gives it a new title, then its
    card moves to the path the title names, in one commit that removes the
    old path, and keeps its id: links and the chat name the card by it,
    and its history follows the move."""
    await a_document(archivist)
    [first] = files_in(memory, r"-p247\.md$")
    card_id = parse(memory.text(first))["card_id"]
    assert re.fullmatch(r"document-[0-9a-f]{10}", card_id)

    await a_document(archivist, title="Duff Beer invoice, October")

    [moved] = files_in(memory, r"-p247\.md$")
    assert moved != first and parse(memory.text(moved))["card_id"] == card_id
    assert memory.written[-1] == sorted([first, moved])


async def test_a_document_names_its_people_by_id_and_links_their_cards(archivist, memory):
    await a_document(archivist, 31, persons=["Homer", "Bart"], tags=["Leisure", "Person: Homer"])

    text = memory.text(files_in(memory, r"-p31\.md$")[0])
    assert parse(text)["persons"] == ["homer", "bart"]
    assert parse(text)["tags"] == ["leisure"]
    assert "**About:** [Homer](../../../people/homer.md), [Bart](../../../people/bart.md)" in text


async def test_a_name_nobody_goes_by_stays_a_plain_name(archivist, memory):
    await a_document(archivist, 32, persons=["Homer", "Mr. Burns"])

    text = memory.text(files_in(memory, r"-p32\.md$")[0])
    assert parse(text)["persons"] == ["homer"]
    assert "**About:** [Homer](../../../people/homer.md), Mr. Burns" in text
    assert files_in(memory, r"/people/") == sorted(PEOPLE)


async def test_a_new_correspondent_gets_its_card_once_and_keeps_it(archivist, memory):
    """Given a tab from Moe's Tavern, when it is filed, then the tavern gets
    its card, by the archivist; a second letter from Moe's leaves the card
    as it is, whatever aliases it names: from then on it is the family's."""
    moe = "family/correspondents/moe-s-tavern.md"
    await a_document(archivist, 33, correspondent="Moe's Tavern", correspondent_aliases=["Moe's"])

    text = memory.text(files_in(memory, r"-p33\.md$")[0])
    assert "**From:** [Moe's Tavern](../../../correspondents/moe-s-tavern.md)" in text
    card = memory.text(moe)
    assert parse(card) == {"type": "correspondent", "card_id": "correspondent-moe-s-tavern",
                           "title": "Moe's Tavern", "aliases": ["Moe's"]}

    await a_document(archivist, 34, correspondent="Moe's Tavern", correspondent_aliases=["Moe Szyslak"])

    assert memory.text(moe) == card
    declared = [c for c in memory.forgejo.new_commits() if c.endswith(f"declare: {moe}")]
    assert declared == [f"{ARCHIVIST} | {ARCHIVIST} | declare: {moe}"]


async def test_a_document_tags_its_topics_by_id_and_its_keywords_in_lowercase(archivist, memory):
    """On a German instance a document's Paperless tags are German labels;
    the card holds the topic's id, and a reader shows the label from the
    ontology. With no `ontology.toml` in the memory the shipped seed is
    the ontology, as on a new instance (ADR-017 §4)."""
    archivist.vault = VaultContext(language="de", shared_bucket="family")
    await a_document(archivist, 41, title="Impfpass Bart",
                     tags=["Gesundheit", "Cropper Paper", "Medical", "Person: Bart", "Moe's"])

    # `Medical` is the same topic in English: one tag. `Moe's` is no keyword
    # as it stands, so the writer slugs it.
    assert parse(memory.text(files_in(memory, r"-p41\.md$")[0]))["tags"] == \
        ["medical", "cropper-paper", "moe-s"]


# ── Action items on the topic's list ─────────────────────────────────────

CAMPING_LIST = "family/lists/camping.md"


async def test_a_document_starts_its_topics_list_with_its_action_items(archivist, memory):
    """Given a campsite booking with two things to do, when it is filed,
    then the topic's list is started with them, each linking the booking,
    in a commit by the archivist."""
    due = _day(30)
    await a_document(archivist, 12, title="Campsite booking", topics=["Camping"],
                     action_items=[{"action": "book pitch", "due": due}, "pack stove"])

    written = memory.text(CAMPING_LIST)
    assert written.startswith("---\ntype: list\ncard_id: list-camping\ntitle: Camping\n"
                              "description: What is still to do for Camping\n"
                              "topics:\n  - camping\n")
    booking = files_in(memory, r"-p12\.md$")[0].removeprefix("family/")
    assert f"- [ ] book pitch [Campsite booking](../{booking}) 📅 {due}" in written
    assert "- [ ] pack stove [Campsite booking]" in written
    assert f"{ARCHIVIST} | {ARCHIVIST} | lists: add action items to camping" in \
        memory.forgejo.new_commits()


@pytest.mark.parametrize("memory", [{CAMPING_LIST: (
    "---\ntype: list\ncard_id: list-camping\ntitle: Camping\n---\n\n- [x] book pitch ✅ 2026-03-03\n")}],
    indirect=True)
async def test_a_ticked_item_is_never_reopened_and_a_notes_items_are_its_authors(archivist, memory):
    """Given the camping list with "book pitch" ticked, when Homer files a
    packing note that names it again, then it stays ticked, the new item
    goes on, and the list's commit is Homer's, whose note it is."""
    await a_note(archivist, "Packing", entity="family/camping", submitter="@homer:simpson",
                 classification={"title": "Packing", "summary": "s",
                                 "action_items": ["pack stove", "Book pitch"]})

    written = memory.text(CAMPING_LIST)
    assert "- [x] book pitch ✅ 2026-03-03" in written
    assert written.lower().count("book pitch") == 1
    assert re.search(r"- \[ \] pack stove \[Packing\]\(\.\./camping/notes/2026/10/packing-\w+\.md\)", written)
    assert "homer <homer@simpson> | " + ARCHIVIST + " | lists: add action items to camping" in \
        memory.forgejo.new_commits()


async def test_a_refiled_record_adds_nothing_and_its_items_follow_a_rename(archivist, memory):
    """The model words a refile's items differently; the list keeps the family's."""
    due = _day(14)
    await a_document(archivist, 72, title="Permission slip", topics=["Education"],
                     action_items=[{"action": "Sign the slip", "due": due}])
    await a_document(archivist, 72, title="School notice", topics=["Education"],
                     action_items=[{"action": "Sign and return the slip", "due": due}])

    written = memory.text("family/lists/education.md")
    renamed = files_in(memory, r"-p72\.md$")[0].removeprefix("family/")
    assert "school-notice" in renamed
    assert "Sign and return" not in written
    assert f"- [ ] Sign the slip [Permission slip](../{renamed}) 📅 {due}" in written


async def test_an_item_already_past_its_due_date_stays_off_the_list(archivist, memory):
    """An old letter filed late, or last year's bill refiled, has nothing
    left to do. The record's summary still names the item; only the list
    leaves it out."""
    await a_document(archivist, 40, title="Werkstatt Rechnung", topics=["Fahrzeug"], action_items=[
        {"action": "Rechnung überweisen", "due": _day(-60)},
        {"action": "Unterlagen abholen", "due": _day(0)},
        "Reifen einlagern"])

    written = memory.text("family/lists/fahrzeug.md")
    assert "Rechnung überweisen" not in written
    assert "Unterlagen abholen" in written, "due today is still ahead of the family"
    assert "Reifen einlagern" in written, "an item without a date never runs out"


async def test_a_record_with_only_past_items_starts_no_list(archivist, memory):
    await a_document(archivist, 40, title="Werkstatt Rechnung", topics=["Fahrzeug"],
                     action_items=[{"action": "Rechnung überweisen", "due": _day(-60)}])

    assert files_in(memory, r"/lists/") == []


async def test_a_new_list_is_titled_with_the_topics_name(archivist, memory):
    await a_document(archivist, 9, title="Hecke", topics=["Haus & Garten"],
                     action_items=["Hecke schneiden"])

    assert "title: Haus & Garten\n" in memory.text("family/lists/haus-garten.md")


async def test_a_personal_capture_adds_to_no_list(archivist, memory):
    await a_note(archivist, "Training", entity="homer/gravel",
                 classification={"title": "Training", "summary": "s", "action_items": ["pump tires"]})

    assert files_in(memory, r"/lists/") == []


# ── Dates on the family's calendar ───────────────────────────────────────

async def a_school_letter(archivist, dates, *, persons=("Lisa",), paperless_id: int = 80):
    await a_document(archivist, paperless_id, title="Elternbrief Grundschule",
                     persons=list(persons), dates=dates)


@pytest.fixture
def german(archivist, monkeypatch):
    monkeypatch.setenv("LANGUAGE", "de")
    archivist.vault = VaultContext(language="de", shared_bucket="family")


async def test_a_school_letter_starts_the_calendar_with_its_dates(archivist, memory, german):
    """A letter's dates go on the calendar: in date order, for the child,
    linked back; a date already past stays off."""
    soon, later = _day(21), _day(35)
    await a_school_letter(archivist, [{"what": "Elternabend", "date": later},
                                      {"what": "Theater", "date": soon},
                                      {"what": "Interkulturelles Familienfest", "date": _day(-3)}])

    written = memory.text("family/lists/termine.md")
    letter = files_in(memory, r"-p80\.md$")[0].removeprefix("family/")
    assert "title: Termine\n" in written and "kind: calendar\n" in written
    assert written.index("Theater") < written.index("Elternabend"), "next date on top"
    assert (f"- [ ] Theater [Lisa](../people/lisa.md) [Elternbrief Grundschule]"
            f"(../{letter}) 📅 {soon}") in written
    assert "Familienfest" not in written


@pytest.mark.parametrize("memory", [{"family/lists/kalender.md": (
    "---\ntype: list\ncard_id: list-kalender\ntitle: Kalender\nkind: calendar\n---\n\n- [ ] Zahnarzt 📅 2099-11-05\n")}],
    indirect=True)
async def test_dates_join_the_calendar_the_family_already_has(archivist, memory, german):
    await a_school_letter(archivist, [{"what": "Theater", "date": _day(10)}])

    assert files_in(memory, r"termine\.md$") == []
    written = memory.text("family/lists/kalender.md")
    assert written.index("Theater") < written.index("Zahnarzt")


async def test_someone_the_family_has_not_declared_is_left_off_the_entry(archivist, memory, german):
    await a_school_letter(archivist, [{"what": "Theater", "date": _day(10)}],
                          persons=("Lisa", "Frau Krabappel"))

    written = memory.text("family/lists/termine.md")
    assert "[Lisa](../people/lisa.md)" in written and "Krabappel" not in written


async def test_the_same_event_next_year_is_a_new_entry(archivist, memory, german):
    """Unlike a to-do, an appointment is told apart by its date as much as its name."""
    await a_school_letter(archivist, [{"what": "Elternabend", "date": _day(20)}])
    await a_school_letter(archivist, [{"what": "Elternabend", "date": _day(385)}], paperless_id=91)

    assert memory.text("family/lists/termine.md").count("Elternabend") == 2


async def test_a_letter_without_dates_touches_no_list(archivist, memory, german):
    await a_school_letter(archivist, [])
    await a_school_letter(archivist, [{"what": "Theater", "date": "im November"}], paperless_id=81)

    assert files_in(memory, r"/lists/") == []


# ── A link and a note someone sent ───────────────────────────────────────

async def test_a_link_saved_again_under_a_new_title_updates_the_one_capture(archivist, memory):
    """The same link re-saved with new framing updates the capture it was
    filed as, where it is: the capture's identity is the link, not its title."""
    first = await a_link(archivist, "https://duff.test/recall", title="Old framing")
    again = await a_link(archivist, "https://duff.test/recall", title="New framing")

    assert again.path == first.path
    assert files_in(memory, r"^homer/bookmarks/") == [first.path]
    assert "# New framing" in memory.text(first.path)


async def test_the_same_link_from_someone_else_or_as_a_note_is_its_own_capture(archivist, memory):
    homers = await a_link(archivist, "https://duff.test/recall", title="Recall")
    marges = await a_link(archivist, "https://duff.test/recall", title="Recall", entity="marge")
    as_note = await a_link(archivist, "https://duff.test/recall", title="Recall", kind="note")

    assert len({homers.path, marges.path, as_note.path}) == 3


BART_DRAWING = "family/painting/bookmarks/2026/10/bart-s-drawing-454811.md"


@pytest.mark.parametrize("memory", [{BART_DRAWING: "---\ntype: bookmark\ntitle: Bart's drawing\n---\n"}],
                         indirect=True)
@pytest.mark.parametrize("entity, title", [("family/painting", "Bart's watercolour, wet on wet"),
                                           ("marge", "Bart's drawing")])
async def test_a_capture_keeps_its_path_whatever_its_new_title_or_who_replied(
        archivist, memory, entity, title):
    """A capture's path is its address: the topic page, a list item and the
    link in the chat point at it. A correction that gives it a new title,
    or comes from someone else in the room, changes what the page says,
    never where it is."""
    filed = await a_link(archivist, "https://springfield.example/drawing", title=title, entity=entity,
                         existing_path=BART_DRAWING, capture_id="$x")

    assert filed.path == BART_DRAWING
    assert files_in(memory, r"/bookmarks/") == [BART_DRAWING]
    assert f"# {title}" in memory.text(BART_DRAWING)


async def test_a_new_capture_is_named_after_its_title(archivist, memory):
    filed = await a_link(archivist, "https://springfield.example/tent", title="Tent", entity="family/painting")

    assert filed.path.startswith("family/painting/bookmarks/2026/10/tent-")


CAMPSITE = "family/camping/notes/2026/10/campsite-3a338e.md"
CAMPSITE_SIDECAR = CAMPSITE.removesuffix(".md") + ".json"


async def a_campsite_note(archivist, **kwargs):
    """Marge's note about the campsite, filed as its own words (ADR-018)."""
    return await archivist.publish_capture(**{
        "entity": "family/camping", "kind": "note", "source_uri": None, "title_hint": None,
        "body_text": "call the campsite b4 fri",
        "classification": {"title": "Campsite", "summary": "", "facts": [],
                           "action_items": ["Call the campsite"]},
        "captured_at": "2026-10-13", "model": "m", "capture_id": "$x", "existing_path": CAMPSITE,
        "submitter": "@marge:simpson", "clean": "Call the campsite before Friday.",
        "sources": [Source("original", "marge", "2026-10-13T08:00:00+00:00", "chat", "text",
                           "$x", quote="call the campsite b4 fri")],
        "made": {"at": "2026-10-13T08:00:05+00:00", "model": "m", "code_commit": "9a5bdc9",
                 "prompt_template": "3f9a1c2b7d4e", "requested_by": "marge"},
        **kwargs})


async def test_a_note_is_its_card_and_its_sidecar_in_one_commit_by_its_sender(archivist, memory):
    """A note is two files in one commit (ADR-018): the card with what it
    is about, and the sidecar with where its sources came from and how
    the card was made. The commit is Marge's, who sent it."""
    filed = await a_campsite_note(archivist)

    assert memory.written[0] == sorted([CAMPSITE, CAMPSITE_SIDECAR])
    assert memory.forgejo.new_commits()[0] == f"marge <marge@simpson> | {ARCHIVIST} | capture: Campsite"
    sidecar = json.loads(memory.text(CAMPSITE_SIDECAR))
    assert sidecar["card_id"] == filed.card_id
    assert sidecar["filed_by"] == "marge"
    assert sidecar["sources"] == [{"role": "original", "person": "marge",
                                   "time": "2026-10-13T08:00:00+00:00", "channel": "chat",
                                   "kind": "text", "id": "$x"}]
    assert (sidecar["made"]["code_commit"], sidecar["made"]["prompt_template"]) == \
        ("9a5bdc9", "3f9a1c2b7d4e")


async def test_a_notes_card_has_its_clean_version_then_its_words_and_no_machine_fields(
        archivist, memory):
    await a_campsite_note(archivist)

    card = memory.text(CAMPSITE)
    assert card.index("Call the campsite before Friday.") < \
        card.index("## Sources\n\n> call the campsite b4 fri")
    assert "[!summary]" not in card
    assert not {"capture_id", "model", "timestamp", "filed_by", "code_commit",
                "prompt_template"} & set(parse(card))


async def test_a_notes_to_dos_are_on_the_list_and_not_on_the_card(archivist, memory):
    await a_campsite_note(archivist, clean="")

    assert "Action items" not in memory.text(CAMPSITE)
    assert "- [ ] Call the campsite" in memory.text(CAMPING_LIST)


@pytest.mark.parametrize("memory", [{CAMPSITE: (
    "---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Campsite\ncapture_id: \"$x\"\n---\n\n# Campsite\n")}],
    indirect=True)
async def test_a_note_in_the_old_format_gets_its_sidecar_and_keeps_its_id(archivist, memory):
    filed = await a_campsite_note(archivist)

    assert filed.card_id == "note-0b6e2d91aa"
    assert memory.written[0] == sorted([CAMPSITE, CAMPSITE_SIDECAR])


class _MargeEditsAfterTheRead(MemoryClient):
    """The archivist's client, while Marge edits a note in Forgejo's web
    editor just after the archivist first read it for a refile."""

    def __init__(self, memory, path: str, to: str):
        super().__init__(memory.environ["MEMORY_API_URL"], token=memory.environ["CARDS_WRITE_TOKEN"])
        self._memory, self._edit = memory, (path, to)

    def file(self, path: str):
        found = super().file(path)
        if self._edit and path == self._edit[0]:
            self._memory.edited_in_forgejo({path: self._edit[1]}, by="marge")
            self._edit = None
        return found


@pytest.mark.parametrize("memory", [{CAMPSITE: (
    "---\ntype: note\ncard_id: note-0b6e2d91aa\ntitle: Campsite\n---\n\n# Campsite\n")}], indirect=True)
async def test_a_note_edited_while_it_is_refiled_is_not_overwritten(archivist, memory):
    """Given a note Marge edits in Forgejo after the archivist read it for a
    refile, when the refile is written, then nothing is written and nothing
    is filed: her edit stays. A refile made from the card before her edit
    would undo it."""
    archivist.memory = _MargeEditsAfterTheRead(memory, CAMPSITE, "Marge's edit\n")

    assert await a_campsite_note(archivist) is None
    assert memory.text(CAMPSITE) == "Marge's edit\n"
    assert memory.written == []


async def test_a_note_filed_into_a_topic_gives_the_topic_its_card_once(archivist, memory):
    topic = "family/itchy-scratchy-land/topic.md"
    for words in ("Book the hotel", "Pack sunscreen"):
        await a_note(archivist, words, entity="family/itchy-scratchy-land")

    assert parse(memory.text(topic)) == {
        "type": "topic", "card_id": "topic-itchy-scratchy-land", "title": "Itchy Scratchy Land"}
    assert [c for c in memory.forgejo.new_commits() if c.endswith(f"declare: {topic}")] == \
        [f"{ARCHIVIST} | {ARCHIVIST} | declare: {topic}"]


async def test_a_note_keeps_its_keywords_as_keywords(archivist, memory):
    archivist.vault = VaultContext(language="de", shared_bucket="family")
    filed = await a_note(archivist, "Lisa paints the park.", entity="family/itchy-scratchy-land",
                         tags=["itchy-scratchy-land", "Watercolour", "Reise", "watercolour"])

    assert parse(memory.text(filed.path))["tags"] == ["itchy-scratchy-land", "watercolour", "travel"]


async def test_a_capture_rebuilt_from_its_source_keeps_its_id(archivist, memory):
    capture = {"body_text": "Brushes and paper for the next trip.", "capture_id": "$r-Plk"}
    first = await a_note(archivist, "Art supplies", **capture)
    assert re.fullmatch(r"note-[0-9a-f]{10}", first.card_id)

    again = await a_note(archivist, "Art supplies", existing_path=first.path,
                         classification={"title": "Art supplies for Lisa"}, **capture)

    assert again == first and parse(memory.text(first.path))["card_id"] == first.card_id


@pytest.mark.parametrize("memory", [{"marge/notes/2026/10/bart-s-drawing-454811.md": (
    "---\ntype: note\ncard_id: note-0b6e2d91aa\nformer_card_ids:\n  - bookmark-7f3a9c21e4\n"
    "title: Bart's drawing\n---\n")}], indirect=True)
async def test_a_note_refiled_from_a_bookmark_keeps_its_former_id(archivist, memory):
    """A correction rebuilds the card from its source; a link to the id it
    had as a bookmark must still find it afterwards."""
    path = "marge/notes/2026/10/bart-s-drawing-454811.md"
    await a_link(archivist, "https://springfield.example/drawing", title="Bart's watercolour", entity="marge",
                 kind="note", existing_path=path, capture_id="$x")

    fields = parse(memory.text(path))
    assert (fields["card_id"], fields["former_card_ids"]) == ("note-0b6e2d91aa", ["bookmark-7f3a9c21e4"])


async def test_a_notes_history_holds_the_edit_a_person_made_in_forgejo(archivist, memory):
    """Given Marge's note, edited by Homer in Forgejo's web editor, when a
    refile reads the note's history, then Homer's edit is in it: the refile
    applies it again instead of losing it (ADR-018 §7)."""
    filed = await a_campsite_note(archivist)
    card = memory.text(CAMPSITE)
    memory.edited_in_forgejo({CAMPSITE: card.replace("before Friday", "before Thursday")},
                             by="homer", subject="edit: Campsite")

    history = await archivist.card_history(filed.card_id)

    assert [e.person for e in history.edits] == ["homer"]
    assert await archivist.card_history("note-0000000000") is None


async def test_a_capture_reads_back_as_the_memory_has_it(archivist, memory):
    filed = await a_note(archivist, "Pack sunscreen")

    assert await archivist.read_capture(filed.path) == memory.text(filed.path)
    assert await archivist.read_capture("marge/notes/2026/10/gone.md") is None


# ── An email thread ──────────────────────────────────────────────────────

async def test_an_email_thread_is_one_card_its_replies_fold_into(archivist, memory):
    """Given a field trip email from Springfield Elementary, when it and its
    reply are filed, then they are one card with one id, the reply folded
    in, each commit saying which thread and message it is."""
    message = {"entity": "marge", "thread_uri": "mid:abc@springfield-elementary.test",
               "from_addr": "skinner@springfield-elementary.test", "title_hint": None,
               "captured_at": "2026-10-13", "model": None}
    first = await archivist.publish_email_message(
        message_id="abc@springfield-elementary.test", body_text="Field trip on Friday.",
        classification={"title": "Field trip"}, **message)
    assert re.fullmatch(r"email-[0-9a-f]{10}", first.card_id)

    again = await archivist.publish_email_message(
        message_id="def@springfield-elementary.test", body_text="Bring a lunch.",
        classification={"title": "Field trip lunch"}, **message)

    assert again == first and parse(memory.text(first.path))["card_id"] == first.card_id
    assert "Bring a lunch." in memory.text(first.path)
    assert memory.forgejo.message() == ("update: Field trip lunch\n\n"
                                        "Thread: mid:abc@springfield-elementary.test\n"
                                        "Message-Id: def@springfield-elementary.test")


# ── While Forgejo is down, and without the curator ───────────────────────

async def test_a_filing_made_while_forgejo_is_down_is_kept_and_pushed_once_it_is_back(
        archivist, memory):
    """Given Forgejo down, when Marge sends a note, then it is filed all the
    same, in the curator's clone, and reaches Forgejo with the curator's
    next cycle once Forgejo is back. A restart of Forgejo loses nothing
    the family sent meanwhile."""
    memory.forgejo.goes_down()

    filed = await a_note(archivist, "Pack sunscreen")

    assert filed is not None
    assert [p.subject for p in memory.store.pending()] == ["capture: Pack sunscreen"]
    memory.forgejo.comes_back()
    assert memory.store.sync().state == "ok"
    assert "Pack sunscreen" in memory.text(filed.path)


async def test_without_the_curator_nothing_is_filed_and_nothing_raised(memory, tmp_path):
    """The archivist answers in the chat either way: a filing that cannot
    reach the memory is logged and reported as not filed."""
    offline = GitMirror(data_dir=tmp_path / "offline",
                        memory=MemoryClient("http://127.0.0.1:9", token="t"))

    assert await a_note(offline, "Pack sunscreen") is None
    assert await a_document(offline) is False
