"""Every card the stack writes meets its type's schema.

`test_memory_card_schemas.py` checks the schemas against a hand-made
memory repo whose cards copy what each writer produces. This file asks
the writers themselves. Each case below runs the code that renders a
card file (frontmatter and body) the way the stack runs it when a
household files something, and checks the frontmatter of the text that
comes out against the card type's schema in
`stacklets/memory/schemas/cards/`. A writer that drifts from its schema
fails here, not on the first card the write endpoint refuses.

Where a writer is an async method that talks to Forgejo, the case calls
the pure function that method builds its text with, given the arguments
the method gives it. Each case's docstring names the function and, where
the call is a stand-in for the method, says so.

The household is the Simpsons of `simpsons_cards.py`: its people (Homer,
Marge, Bart, Maggie) and its two topics (Medical, Itchy & Scratchy Land).
The values a schema checks against that the instance decides (who the
people are, which topics there are) are built from them, as the write
endpoint builds them from the memory repo.

A case marked `xfail` is a writer that does not meet its schema today.
That is the finding this file records: the assertion stays strict, and
the reason names the field and what the schema says against what the
writer writes. When the writer or the schema is fixed, the case passes
and the strict mark fails the run, so the mark is taken off.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "lib"))
sys.path.insert(0, str(_REPO / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(_REPO / "stacklets" / "docs" / "bot"))
# The memory stacklet as a package (`memory.lib`, `memory.note_card`), as the archivist reads it.
sys.path.insert(0, str(_REPO / "stacklets"))
sys.path.insert(0, str(Path(__file__).parent))

import diary  # noqa: E402
import diary_card  # noqa: E402
from card_schema import CardSchemas, VaultValues  # noqa: E402
from git_mirror import GitMirror  # noqa: E402
from simpsons_cards import ONTOLOGY, PEOPLE  # noqa: E402
from vault_context import VaultContext  # noqa: E402
from vault_entry import (  # noqa: E402
    capture_frontmatter,
    document_frontmatter,
    fold_email_message,
    identity_for,
    render_document,
    render_email_message_section,
)

from stack.cards.links import About, correspondent_card, topic_card, without_person_tags  # noqa: E402
from stack.cards.sources import Source  # noqa: E402
from stack.cards import with_card_id  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from stack.lists import (  # noqa: E402
    CALENDAR,
    CHECKLIST,
    Item,
    Link,
    action_item,
    archive_over,
    calendar_description,
    calendar_slug,
    calendar_title,
    insert_item,
    list_path,
    new_list,
    topic_list_description,
)
from stack.ontology import Ontology  # noqa: E402
from stack.people import Person, read_person, render_person  # noqa: E402
from stack.tags import card_tags  # noqa: E402

SCHEMAS = CardSchemas.load()
ONTO = Ontology.loads(ONTOLOGY)
VALUES = VaultValues(person_ids=[Path(p).stem for p in PEOPLE], ontology=ONTO)
DECLARED = [read_person(Path(p).stem, text) for p, text in PEOPLE.items()]
BUCKET = "family"


def _problems(text: str) -> list[dict]:
    """What the card's schema says is wrong with the frontmatter of `text`."""
    return SCHEMAS.problems(parse(text), VALUES)


# ── The archivist's filings ───────────────────────────────────────────────
#
# A capture (a note or a bookmark) goes through `GitMirror.capture_entry`,
# the method `publish_capture` writes the result of. It reads the people
# and topics from the local copy of the memory repo, so the mirror gets
# one: the Simpsons' person files and ontology in a temporary directory.


@pytest.fixture
def mirror(tmp_path, monkeypatch):
    """A GitMirror whose local memory repo holds the Simpsons' people and topics."""
    vault = tmp_path / "vault"
    (vault / BUCKET / "people").mkdir(parents=True)
    (vault / "ontology.toml").write_text(ONTOLOGY)
    for path, text in PEOPLE.items():
        (vault / path).write_text(text)
    monkeypatch.setenv("MEMORY_VAULT_DIR", str(vault))
    mirror = GitMirror(
        code_url="http://stack-code:3000", admin_user="stackadmin",
        admin_password="secret", admin_usernames=["homer"],
        data_dir=tmp_path, paperless_version="2.14.5",
    )
    mirror.vault = VaultContext(language="en", shared_bucket=BUCKET)
    return mirror


def note_from_the_chat(mirror: GitMirror) -> str:
    """Marge sends a line to the archivist, which files it as her note.

    `GitMirror.capture_entry(kind="note")`, the card `publish_capture`
    writes; its sidecar is no card and is not checked here."""
    return mirror.capture_entry(
        target_path="marge/notes/2026/10/art-supplies-for-next-time-7f3a9c.md",
        existing=None, kind="note", source_uri=None, title_hint=None,
        body_text="brushes + paper for next time",
        classification={"title": "Art supplies for next time", "persons": ["Marge"],
                        "summary": "Marge wants brushes and paper for the next trip.",
                        "facts": ["Brushes and paper are needed"], "action_items": []},
        captured_at="2026-10-13", model="qwen3",
        tags=["Itchy & Scratchy Land", "Watercolour"],
        capture_id="$r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4",
        submitter="@marge:springfield.example",
        sources=[Source("original", "marge", "2026-10-13T21:12:08+00:00", "chat", "text",
                        "$r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4",
                        quote="brushes + paper for next time")],
        made={"at": "2026-10-13T21:12:08Z", "model": "qwen3", "requested_by": "marge"},
    ).content


def bookmark_from_the_chat(mirror: GitMirror) -> str:
    """Bart sends a link to the Kwik-E-Mart's offers, which is filed as his bookmark.

    `GitMirror.capture_entry(kind="bookmark")`, the card `publish_capture` writes."""
    return mirror.capture_entry(
        target_path="bart/bookmarks/2026/10/kwik-e-mart-squishee-offers-5c4d3e.md",
        existing=None, kind="bookmark", source_uri="https://kwik-e-mart.example/squishee",
        title_hint="Squishee offers", body_text="",
        classification={"title": "Kwik-E-Mart Squishee offers", "persons": ["Bart"],
                        "summary": "Two Squishees for the price of one this week."},
        captured_at="2026-10-11", model="qwen3", tags=["Squishee", "Person: Bart"],
        capture_id="$Bk2LmNoPqRsTuVwXyZ0123456789",
        submitter="@bart:springfield.example",
    ).content


def document_from_paperless(mirror: GitMirror) -> str:
    """Homer's car insurance policy from Duff Insurance is mirrored from Paperless.

    `vault_entry.document_frontmatter` and `render_document`, the two
    calls `GitMirror.publish` builds the file with before it commits it,
    given the people and tags as `publish` gives them (declared ids,
    tags by the ontology's rule)."""
    path = "family/documents/2026/10/2026-10-02-duff-insurance-policy-p72.md"
    names = ["Homer"]
    fm = document_frontmatter(
        title="Duff Insurance policy", date="2026-10-02", correspondent="Duff Insurance",
        document_type="Policy", category="Medical",
        persons=About.of(names, DECLARED).ids,
        tags=card_tags(without_person_tags(["Medical", "Car insurance", "Person: Homer"]), ONTO),
        paperless_id=72, paperless_url="http://localhost:42020",
        processing="ai_formatted", model="qwen3", paperless_version="2.14.5",
        identity=identity_for(None, "document", path),
    )
    return render_document(
        frontmatter=fm, body="The policy covers the car and Homer's medical costs.",
        correspondent="Duff Insurance", persons=names, from_path=path,
        shared_bucket=BUCKET, summary="Homer's policy with Duff Insurance.",
        facts=["Covers the car"], action_items=[{"action": "Pay the premium", "due": "2026-11-01"}],
        source_link=("Show Document", "http://localhost:42020/documents/72/details"),
        people=DECLARED,
    )


_EMAIL_PATH = "family/emails/2026/10/2026-10-09-school-trip-to-the-plant-9e8d7c.md"
_THREAD = "mid:<trip-42@springfield-elementary.example>"


def _email_message(*, message_id: str, persons: list[str], tags: list[str], existing: str | None) -> str:
    """One message folded into its thread file, as `publish_email_message` folds it.

    `render_email_message_section`, `capture_frontmatter(kind="email")`
    and `fold_email_message`, with the arguments that method gives them.
    The mail path passes no capture id (`CapturePipeline.capture_email`)."""
    section = render_email_message_section(
        message_id=message_id, from_addr="skinner@springfield-elementary.example",
        captured_at="2026-10-09", body="The class visits the power plant on Friday.",
        summary="Bart's class visits the power plant.", facts=["Trip on Friday"],
        action_items=[{"action": "Sign the permission slip", "due": "2026-10-15"}],
    )
    fm = capture_frontmatter(
        title="School trip to the plant", captured_at="2026-10-09", kind="email",
        source_uri=_THREAD, persons=About.of(persons, DECLARED).ids,
        tags=card_tags(tags, ONTO), model="qwen3",
        identity=identity_for(existing, "email", _EMAIL_PATH),
    )
    return fold_email_message(
        existing, section=section, message_id=message_id, new_frontmatter=fm,
        title="School trip to the plant", captured_at="2026-10-09", source_uri=_THREAD,
        persons=persons, tags=card_tags(tags, ONTO), from_path=_EMAIL_PATH,
        shared_bucket=BUCKET, people=DECLARED,
    )


def email_thread_started(_mirror: GitMirror) -> str:
    """Principal Skinner writes about a school trip: the first message starts a thread file."""
    return _email_message(message_id="<trip-42@springfield-elementary.example>",
                          persons=["Bart"], tags=["School trip"], existing=None)


def email_thread_after_a_reply(_mirror: GitMirror) -> str:
    """A reply about Bart's checkup joins the thread: its people and tags join the file's."""
    first = email_thread_started(_mirror)
    return _email_message(message_id="<trip-43@springfield-elementary.example>",
                          persons=["Bart", "Marge"], tags=["Medical"], existing=first)


class TestTheArchivistsFilings:

    @pytest.mark.parametrize("write", [
        note_from_the_chat,
        bookmark_from_the_chat,
        document_from_paperless,
        email_thread_started,
        email_thread_after_a_reply,
    ])
    def test_a_filed_card_meets_its_types_schema(self, mirror, write):
        """Given a household member's message or a Paperless document,
        when the archivist renders the card it files it as,
        then the card's fields meet its type's schema."""
        assert _problems(write(mirror)) == []


# ── The cards the archivist declares ──────────────────────────────────────


def correspondent_with_aliases() -> str:
    """Duff Insurance is named on a document for the first time, with another name it goes by."""
    return correspondent_card("Duff Insurance", ["Duff", "duff insurance"], bucket=BUCKET)[1]


def correspondent_without_aliases() -> str:
    """The Kwik-E-Mart is named on a receipt for the first time."""
    return correspondent_card("Kwik-E-Mart", [], bucket=BUCKET)[1]


def topic_filed_into_first() -> str:
    """A record is filed into the Itchy & Scratchy Land folder for the first time."""
    return topic_card(BUCKET, "itchy-scratchy-land")[1]


class TestTheCardsTheArchivistDeclares:

    @pytest.mark.parametrize("write", [
        correspondent_with_aliases,
        correspondent_without_aliases,
        topic_filed_into_first,
    ])
    def test_a_declared_card_meets_its_types_schema(self, write):
        """Given a correspondent or a topic the memory has no card for yet,
        when the archivist declares it (`stack.cards.links`),
        then the new card meets its type's schema."""
        assert _problems(write()) == []


# ── The diary ─────────────────────────────────────────────────────────────
#
# The diary compile reads each entry with a model (`extraction_from`, held
# to the household's vocabulary), makes a card of it (`to_card`) and plans
# the writes (`plan`), which gives a new card its id and renders it. The
# cases run those three, with the model's answer fabricated.

_NAMES = {name.lower(): p.first_name for p in DECLARED for name in p.all_names()}
_ROOM = "!memories:springfield.example"


def _diary_card(entry: diary.Entry, answer: dict | None, *, template: str = "",
                media: dict[str, str] | None = None) -> str:
    extraction = diary_card.extraction_from(
        answer, ontology=ONTO, language="de", people=_NAMES, declared=DECLARED, model="qwen3")
    # The compile records which prompt the model read the entry under
    # (`cli/diary.py`), and nothing when the model gave no answer.
    extraction = replace(extraction, template=template)
    card = diary_card.to_card(entry, extraction, room_id=_ROOM, media=media or {})
    [text] = diary_card.plan({}, [card], bucket=BUCKET, complete=False).writes.values()
    return text


def diary_entry_the_model_could_not_read() -> str:
    """Homer writes a line to the memories room and the model gives no answer:
    the card has its words and no reading."""
    entry = diary.Entry(on=date(2026, 10, 12), confidence="sent", basis="", kind="text",
                        sender="homer", body="Mmm, donuts.", at=1760263200000,
                        event_ids=["$hD0nUtS0aBcDeFgHiJkLmNoPqRsTuVwX"])
    return _diary_card(entry, None)


def diary_entry_read_with_no_topic() -> str:
    """Homer writes about watching the game with Bart; the model reads it,
    and nothing in it is one of the household's topics."""
    entry = diary.Entry(on=date(2026, 10, 13), confidence="sent", basis="", kind="text",
                        sender="homer", body="Watched the game with Bart at Moe's.",
                        at=1760349600000, event_ids=["$gAmE0aBcDeFgHiJkLmNoPqRsTuVwXyZ01"])
    answer = {"title": "The game with Bart", "description": "Homer and Bart watch the game.",
              "persons": ["Homer", "Bart"], "topics": [], "summary": "", "facts": [],
              "quotes": []}
    return _diary_card(entry, answer, template="3f9a0c1b2d4e")


def diary_entry_read_by_the_model() -> str:
    """Marge records a voice message about Bart's checkup; the model reads
    out a title, the people, the topic and a summary."""
    first = "$vOiCe1aBcDeFgHiJkLmNoPqRsTuVwXyZ01"
    entry = diary.Entry(on=date(2026, 10, 14), confidence="sent", basis="", kind="voice",
                        sender="marge", body="Bart was at the doctor today and he was so brave.",
                        at=1760436000000, event_ids=[first], duration_ms=8400)
    answer = {"title": "Bart at the doctor", "description": "Marge on Bart's checkup.",
              "persons": ["Bart"], "topics": ["Medical"],
              "summary": "Bart had his checkup and was brave.", "facts": ["Checkup done"],
              "quotes": []}
    return _diary_card(entry, answer, template="3f9a0c1b2d4e",
                       media={first: "/media/2026/10/voice-checkup.m4a"})


class TestTheDiary:

    @pytest.mark.parametrize("write", [
        diary_entry_the_model_could_not_read,
        diary_entry_read_with_no_topic,
        diary_entry_read_by_the_model,
    ])
    def test_a_diary_card_meets_its_types_schema(self, write):
        """Given an entry in the memories room,
        when the diary compile writes its card,
        then the card's fields meet the diary schema."""
        assert _problems(write()) == []


# ── Lists ─────────────────────────────────────────────────────────────────
#
# A list file is started by `stack.lists.new_list`: by the archivist when
# a record brings action items for a topic or dates for the calendar, by
# `stack memory list` when a person starts one, and by the nightly move
# when a month's archive is first written.


def topic_todo_list() -> str:
    """A letter about Bart's checkup brings an action item for the Medical topic,
    which has no list yet (`GitMirror._fold_action_items_into_list`)."""
    path = list_path("medical", bucket=BUCKET)
    start = new_list("Medical", path=path, topics=["medical"],
                     description=topic_list_description("Medical", "en"))
    item = action_item({"action": "Book Bart's checkup", "due": "2026-11-03"},
                       source=Link("Checkup letter", "family/documents/2026/10/checkup-p73.md"))
    return insert_item(start, item, path=path)


def calendar_list() -> str:
    """A record names a date and the household has no calendar yet
    (`GitMirror._fold_document_dates`)."""
    path = list_path(calendar_slug("de"), bucket=BUCKET)
    start = new_list(calendar_title("de"), path=path,
                     description=calendar_description("de"), kind=CALENDAR)
    return insert_item(start, Item("Itchy & Scratchy Land trip", due="2026-11-14"), path=path)


def checklist_a_person_starts() -> str:
    """Marge starts a packing checklist with sections (`stack memory list create`)."""
    path = list_path("itchy-scratchy-land-packing", bucket=BUCKET)
    return new_list("Itchy & Scratchy Land packing", path=path,
                    description="What goes in the car.", topics=["itchy-scratchy-land"],
                    sections=["Tickets", "Snacks"], kind=CHECKLIST)


def archive_of_a_month() -> str:
    """The nightly move takes a done item off the Medical list into the month's archive."""
    path = list_path("medical", bucket=BUCKET)
    text = new_list("Medical", path=path, topics=["medical"])
    text = insert_item(text, Item("Pick up Maggie's prescription", done=True,
                                  done_on="2026-09-20"), path=path)
    _, archives, moved = archive_over(text, {}, path=path, today="2026-10-22", bucket=BUCKET)
    assert moved, "the done item was not over, so no archive was written"
    [archive] = archives.values()
    return archive


class TestLists:

    @pytest.mark.parametrize("write", [
        topic_todo_list,
        calendar_list,
        checklist_a_person_starts,
        archive_of_a_month,
    ])
    def test_a_new_list_meets_the_list_schema(self, write):
        """Given a list nobody has written yet,
        when the stack starts it and puts its first item on it,
        then the list card meets the list schema."""
        assert _problems(write()) == []


# ── People ────────────────────────────────────────────────────────────────


def member_with_an_account() -> str:
    """Homer has a chat account (`people_from_accounts` gives him a person file)."""
    return render_person(Person("homer", "Homer Simpson", account="homer"))


def member_with_aliases() -> str:
    """Bart has an account and goes by another name."""
    return render_person(Person("bart", "Bart Simpson", ("El Barto",), account="bart"))


def person_without_an_account() -> str:
    """Maggie is declared but has no account of her own."""
    return render_person(Person("maggie", "Maggie Simpson"))


class TestPeople:

    @pytest.mark.parametrize("write", [
        member_with_an_account,
        member_with_aliases,
        person_without_an_account,
    ])
    def test_a_person_file_meets_the_person_schema(self, write):
        """Given a person the household declares,
        when the stack first writes their file (`stack.people.render_person`),
        then it meets the person schema."""
        assert _problems(write()) == []


# ── stack memory write ────────────────────────────────────────────────────
#
# `stack memory write` commits the page it is given after one change of
# its own: `_keep_card_id`, which for a page that is not there yet is
# `stack.cards.with_card_id(text, path)`, and for a page that is there,
# the same call with the card id the page had. These cases run that call.


def note_written_by_hand() -> str:
    """Homer writes a note with no frontmatter at all: it becomes a note card."""
    return with_card_id("# Grill\n\nBuy charcoal at the Kwik-E-Mart.\n",
                        "homer/notes/grill.md")


def note_with_fields_and_no_type() -> str:
    """The agent writes a note with a title and tags but no `type`."""
    return with_card_id("---\ntitle: Donut run\ntags:\n  - donuts\n---\n\n# Donut run\n\n"
                        "Lard Lad on Saturday.\n", "homer/notes/donut-run.md")


def note_rewritten_without_its_id() -> str:
    """The agent rewrites Marge's note and drops its card id: it gets it back."""
    return with_card_id("---\ntype: note\ntitle: Art supplies for next time\npersons:\n"
                        "  - marge\ntags:\n  - itchy-scratchy-land\n---\n\n"
                        "# Art supplies for next time\n\nBrushes, paper and a blue pencil.\n",
                        "marge/notes/2026/10/art-supplies.md", "note-7f3a9c21e4")


class TestStackMemoryWrite:

    @pytest.mark.parametrize("write", [
        note_written_by_hand,
        note_with_fields_and_no_type,
        note_rewritten_without_its_id,
    ])
    def test_a_written_note_meets_the_note_schema(self, write):
        """Given a note a person or the agent hands to `stack memory write`,
        when the command gives it its card id before committing it,
        then the note meets the note schema."""
        assert _problems(write()) == []
