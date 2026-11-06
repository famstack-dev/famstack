"""An instance older than 0.3.0-beta.2 keeps its cards writable: migration memory/0011.

The archivist's old format reached 0.5 in shapes the earlier migrations read
wrongly or skip. The curator refuses a later write of such a card that has a
`type`: a reply folded into an email thread, an edit, a reprocess. The bundle
`tests/fixtures/memory/memory-0.4.0.bundle` is the memory of a 0.4 instance.
`tests/fixtures/memory/pre_june_cards.json` holds the old shapes on top of it,
all fabricated Simpsons data: documents, bookmarks and a note in the old
writer's format, and emails, a bookmark and a note as 0.4 wrote them, two names
in them that only `persons` holds.

Two instances run these tests. The bundle's goes through every migration once
for the file (`updated`), because that chain takes seconds, and its tests read
what it left. A small memory (`small`) holds one case each for what a card can
do to the run, with only 0011 still pending. Everything goes through the
framework's runner, as `stack up memory` runs it, and a real curator with its
real card check.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from stack.cards import card_id_from, former_card_ids
from stack.cards.lookup import find_card
from stack.cards.commits import MEMORY_BOT, MEMORY_BOT_EMAIL
from stack.frontmatter import parse
from stack.memory import Refused, put
from stack.migrations import MigrationContext, Migrations
from stacklets.memory.format_check import format_report
from stacklets.memory.graph import load_graph
from tests.memory.unit.instance import RunningMemory, git, running_memory, version_of

_REPO = Path(__file__).resolve().parents[3]
HISTORY = _REPO / "tests" / "fixtures" / "memory" / "memory-0.4.0.bundle"
MEMORY_STACKLET = _REPO / "stacklets" / "memory"
OLD_CARDS: dict[str, str] = json.loads(
    (_REPO / "tests" / "fixtures" / "memory" / "pre_june_cards.json").read_text(encoding="utf-8"))

POWER_BILL = "family/documents/2026/05/2026-05-12-stromrechnung-mai-2026-p9017.md"
DENTIST_BILL = "family/documents/2026/06/2026-06-02-zahnarzt-rechnung-p9018.md"
DONUTS = "homer/bookmarks/2026/06/donut-recipes-02c7ea.md"
SAXOPHONE = "lisa/bookmarks/2026/05/saxophone-practice-tips-0fbdb4.md"
CAKES = "marge/notes/2026/06/kuchen-fuer-das-schulfest-ee507f.md"
ORDER = "family/emails/2026/10/2026-10-22-bestellbestaetigung-kwik-e-mart-lieferung-73c659.md"
PLOW_OFFER = "family/emails/2026/09/2026-09-14-schneeraeumung-angebot-8c21d0.md"
TRICKS = "bart/bookmarks/2026/08/skateboard-tricks-4d1e77.md"
AUTUMN_BOOKS = "lisa/notes/2026/09/buecher-fuer-den-herbst-b70a3c.md"

REPAIR = "memory/0011"


# ── Running the migrations ────────────────────────────────────────────────

def _up(memory: RunningMemory, records: Path, step=None) -> tuple[dict, list[str]]:
    """Whatever is pending, run by the framework's runner as `stack up memory`
    runs it, its record under `records`. The outcome, and what the migrations
    said; `step` hears each line as it is said."""
    said: list[str] = []

    def say(line: str) -> None:
        said.append(line)
        if step:
            step(line)

    migrations = Migrations("memory", MEMORY_STACKLET, records)
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=memory.stack, stacklet_id="memory", env={}, step_fn=say),
        say)
    return outcome, said


def _naming(said: list[str], *facts: str) -> list[str]:
    """The lines of the output that carry each of `facts`."""
    return [line for line in said if all(fact in line for fact in facts)]


def _repair_commit(memory: RunningMemory) -> str:
    return git(memory.forgejo.repo, "log", "-1", "--format=%H", f"--grep=^Migration: {REPAIR}$", "main")


def _files_of(memory: RunningMemory, commit: str) -> dict[str, str]:
    """Each file the commit touched, with git's letter for what it did."""
    out = git(memory.forgejo.repo, "show", "--no-renames", "--name-status", "--format=", commit)
    return {line.split("\t")[1]: line.split("\t")[0] for line in out.splitlines() if line}


def _before_repair(memory: RunningMemory, path: str) -> str:
    return git(memory.forgejo.repo, "show", f"{_repair_commit(memory)}^:{path}")


def _body(text: str) -> str:
    """A card's text, without the line end `git` strips from what it prints."""
    return text.partition("\n---\n")[2].rstrip("\n")


# ── A 0.4 memory with the old cards, after every migration ────────────────

@dataclass
class Updated:
    """The instance after its update, with what the update left in it. The
    cards are kept as they stood then, because one test writes to the memory."""

    memory: RunningMemory
    outcome: dict
    said: list[str]
    cards: dict[str, str]
    report: dict

    def fields(self, path: str) -> dict:
        return parse(self.cards[path])


@pytest.fixture(scope="module")
def updated(tmp_path_factory) -> Iterator[Updated]:
    """Built once for the file, because the chain over the 0.4 bundle takes seconds."""
    root = tmp_path_factory.mktemp("older-cards")
    with running_memory(root / "data", {}, history=HISTORY) as memory:
        memory.edited_in_forgejo(OLD_CARDS, by="homer", subject="the older cards")
        outcome, said = _up(memory, root / "records")
        yield Updated(memory, outcome, said, {path: memory.text(path) for path in OLD_CARDS},
                      format_report(load_graph(memory.vault)))


def test_every_migration_passes_on_the_older_cards(updated):
    assert "failed" not in updated.outcome, "\n".join(updated.said)
    assert REPAIR in updated.outcome["applied"]


def test_a_document_taken_for_a_note_is_a_document_again(updated):
    """Given a document migration 0002 made a note, when the update runs, then
    it has a document's type and id, and a link to its note id still finds it."""
    bill = updated.fields(POWER_BILL)
    note_id = card_id_from(parse(_before_repair(updated.memory, POWER_BILL)))

    assert note_id.startswith("note-")
    assert bill["type"] == "document" and card_id_from(bill).startswith("document-")
    assert former_card_ids(bill) == [note_id]
    assert updated.memory.api.card(note_id)[0]["path"] == POWER_BILL


def test_a_document_has_todays_field_names_with_the_same_values(updated):
    bill = updated.fields(POWER_BILL)
    assert bill["timestamp"] == "2026-06-10T08:00:00Z" and "added" not in bill


@pytest.mark.parametrize("path", [DONUTS, SAXOPHONE])
def test_an_old_bookmark_has_its_type_an_id_and_todays_field_names(updated, path):
    bookmark = updated.fields(path)
    assert bookmark["type"] == "bookmark" and card_id_from(bookmark).startswith("bookmark-")
    assert bookmark["resource"].startswith("https://") and "source_uri" not in bookmark
    assert "kind" not in bookmark and "added" not in bookmark


def test_an_old_note_has_its_type_an_id_and_its_sources(updated):
    note = updated.fields(CAKES)
    assert note["type"] == "note" and card_id_from(note).startswith("note-")
    assert "## Sources" in updated.cards[CAKES] and "Zwei Kuchen backen" in updated.cards[CAKES]
    sidecar = json.loads(updated.memory.text(CAKES.removesuffix(".md") + ".json"))
    assert sidecar["card_id"] == card_id_from(note)


def test_an_email_from_0_4_has_the_tags_of_today_and_no_oldparse(updated):
    order = updated.fields(ORDER)
    assert order["tags"] == ["shopping", "sender-orders-kwik-e-mart-example",
                             "mailbox-simpsonsmailbox", "folder-inbox"]
    assert "capture_id" not in order and "filed_by" not in order


@pytest.mark.parametrize("path, name, kept", [
    (POWER_BILL, "Mr. Burns", ["homer"]),
    (PLOW_OFFER, "Mr. Plow", ["homer"]),
    (TRICKS, "Evergreen", ["bart"]),
    (AUTUMN_BOOKS, "Evergreen", ["lisa"]),
])
def test_a_name_nobody_goes_by_leaves_persons_and_the_output_names_it(updated, path, name, kept):
    """Given a record of any age with a name in `persons`, when the update
    runs, then a name someone goes by is their id, a name nobody goes by is
    gone, and the output names the card and the name. Three of the four hold
    the name in `persons` alone, so nothing else on the card keeps it."""
    assert updated.fields(path)["persons"] == kept
    assert _naming(updated.said, path, name)


def test_a_tag_with_no_letter_goes_and_the_output_names_it(updated):
    assert updated.fields(TRICKS)["tags"] == ["skateboard"]
    assert _naming(updated.said, TRICKS, "2026")


def test_a_tag_the_rule_cannot_read_becomes_a_keyword(updated):
    assert updated.fields(DENTIST_BILL)["tags"] == ["medical", "moe-s"]


def test_the_repair_is_one_commit_by_the_memory_bot_and_deletes_nothing(updated):
    memory = updated.memory
    commit = _repair_commit(memory)
    author = git(memory.forgejo.repo, "log", "-1", "--format=%an <%ae>", commit)
    files = _files_of(memory, commit)

    assert author == f"{MEMORY_BOT} <{MEMORY_BOT_EMAIL}>"
    assert set(files.values()) <= {"M", "A"}
    assert set(OLD_CARDS) <= set(files)
    assert files[CAKES.removesuffix(".md") + ".json"] == "A"
    assert "family/correspondents/dr-wolfe-dental.md" in files


@pytest.mark.parametrize("path", [POWER_BILL, DENTIST_BILL, DONUTS, SAXOPHONE, ORDER, PLOW_OFFER, TRICKS])
def test_the_repair_changes_no_words_on_a_card_outside_its_sources(updated, path):
    assert _body(updated.cards[path]) == _body(_before_repair(updated.memory, path))


def test_the_memory_meets_the_format_afterwards(updated):
    assert {key: value for key, value in updated.report.items()
            if key not in ("cards", "findings") and value} == {}


@pytest.mark.parametrize("path", sorted(OLD_CARDS))
def test_an_old_card_can_be_written_after_the_update(updated, path):
    """Given an old card of each shape, when a person's change writes it
    after the update, with no `Migration:` trailer, then the curator accepts
    it, which means the card meets its schema. Each one is checked, also the
    ones with no `type` before the repair, which got no schema check then."""
    text = updated.memory.text(path)

    written = updated.memory.api.commit(
        [put(path, text + "\nA line added later.\n", base=version_of(text))],
        by="marge", kind="cards", title="a later change")

    assert written.commit
    assert "A line added later." in updated.memory.text(path)


def test_a_chat_link_to_an_old_bookmark_still_finds_it(updated):
    capture_id = parse(OLD_CARDS[DONUTS])["capture_id"]
    assert find_card("capture", capture_id, root=updated.memory.vault) == DONUTS


def test_a_chat_link_to_an_old_note_still_finds_it(updated):
    capture_id = parse(OLD_CARDS[CAKES])["capture_id"]
    assert find_card("capture", capture_id, root=updated.memory.vault) == CAKES


@pytest.mark.xfail(strict=True, reason="`/go/capture/<id>` looks up `capture_id` or a sidecar's sources, and "
                                       "an email has neither after the repair: its email schema has no `capture_id`")
def test_a_chat_link_to_an_email_from_0_4_still_finds_it(updated):
    """A memory search answer in 0.4 linked a hit by its capture id, and an
    email it filed could carry one. Such a link in the chat's history answers
    404 once the email has lost the field."""
    capture_id = parse(OLD_CARDS[ORDER])["capture_id"]
    assert find_card("capture", capture_id, root=updated.memory.vault) == ORDER


def test_running_it_again_makes_no_commit(updated, tmp_path):
    """As after restoring an older backup of the record: every migration runs
    again, on a memory it has repaired already."""
    head = updated.memory.forgejo.head()

    outcome, said = _up(updated.memory, tmp_path / "restored")

    assert "failed" not in outcome, "\n".join(said)
    assert updated.memory.forgejo.head() == head


# ── A small memory with only the repair pending ───────────────────────────

MARGE = "family/people/marge.md"
PEOPLE = {
    f"family/people/{name}.md": (f"---\ntype: person\ncard_id: person-{name}\ntitle: {name.title()} Simpson\n"
                                 f"account: {name}\n---\n\n# {name.title()} Simpson\n")
    for name in ("homer", "marge", "bart", "lisa")
}
DIARY = "family/diary/2026/05/2026-05-02-der-ausflug-a1b2c3.md"
A_DIARY_CARD = ("---\ntype: diary\ntitle: Der Ausflug\nmedium: text\ndate: \"2026-05-02\"\n"
                "persons:\n  - Homer\n  - Evergreen\ntags:\n  - \"2026\"\n---\n\n# Der Ausflug\n\nAm See.\n")
ODD_KIND = "family/notes/2026/06/a-recipe.md"
ODD_FIELDS = "homer/bookmarks/2026/06/added-and-timestamp.md"
ODD_TYPE = "family/notes/2026/06/odd-type.md"
A_CARD_OF_ODD_KIND = "---\ntitle: A recipe\nkind: recipe\npersons:\n  - Evergreen\n---\n\n# A recipe\n"
A_CARD_WITH_BOTH_TIMES = (
    "---\ntype: bookmark\ncard_id: bookmark-0a1b2c3d4e\ntitle: Both times\npersons:\n  - Homer\n  - Evergreen\n"
    "tags:\n  - Moe's\nresource: https://example.com/both\nadded: \"2026-06-01T08:00:00Z\"\n"
    "timestamp: \"2026-06-02T08:00:00Z\"\n---\n\n# Both times\n")
A_MARGE_FILE_THAT_DOES_NOT_READ = "---\ntype: person\ntitle: Marge: the mum\naccount: marge\n---\n\n# Marge\n"
MARGES_BOOKMARK = "marge/bookmarks/2026/08/quilting-5e6f70.md"
A_BOOKMARK_ABOUT_MARGE = (
    "---\ntype: bookmark\ncard_id: bookmark-5e6f705e6f\ntitle: Quilting\npersons:\n  - marge\n  - Homer\n  - Evergreen\n"
    "tags:\n  - Handarbeit\n  - \"Person: Marge\"\nresource: https://example.com/quilt\n"
    "timestamp: \"2026-08-02T08:00:00Z\"\n---\n\n# Quilting\n")
RETYPED = "family/documents/2026/06/retyped-p77.md"
A_NOTE_WITH_A_DOCUMENTS_ID = (
    "---\ntype: note\ncard_id: document-77aa77aa77\ntitle: Retyped by hand\npaperless_id: 77\n"
    "source: paperless\ntimestamp: \"2026-06-02T08:00:00Z\"\n---\n\n# Retyped by hand\n")


A_BOOKMARK_WITH_A_NAME = OLD_CARDS[DONUTS].replace("- Homer\n", "- Homer\n- Evergreen\n")


def _only_the_repair_pending(records: Path) -> None:
    migrations = Migrations("memory", MEMORY_STACKLET, records)
    record = migrations.record()
    for migration in migrations.shipped():
        if migration.key != REPAIR:
            record.mark_applied(migration, None)


@pytest.fixture
def small(tmp_path) -> Iterator:
    """A running memory holding `files`, every migration but the repair applied."""

    def holding(files: dict[str, str]):
        return running_memory(tmp_path / "data", files)

    _only_the_repair_pending(tmp_path / "records")
    yield holding


@pytest.mark.parametrize("pulled", [True, False], ids=["the-curator-pulled-it", "still-in-forgejo"])
def test_an_edit_made_while_it_runs_is_never_overwritten(small, tmp_path, pulled):
    """Given a person's edit in Forgejo after the migration read the cards,
    then the curator refuses the commit and nothing is written, also when the
    curator has pulled the edit by the time of the write. The next `stack up
    memory` converts every card and keeps the edit."""
    edited = A_BOOKMARK_WITH_A_NAME + "\nMarge added a line meanwhile.\n"
    with small({**PEOPLE, DONUTS: A_BOOKMARK_WITH_A_NAME, SAXOPHONE: OLD_CARDS[SAXOPHONE]}) as memory:
        heads: list[str] = []

        # The repair says what it found before it writes, so the edit arrives
        # between its read and its commit.
        def edit_once(line: str) -> None:
            if DONUTS in line and not heads:
                heads.append(memory.edited_in_forgejo({DONUTS: edited}, pulled=pulled))

        outcome, said = _up(memory, tmp_path / "records", step=edit_once)

        assert heads, "the edit was never made: " + "\n".join(said)
        assert REPAIR in outcome["failed"]
        assert memory.forgejo.head() == heads[0]
        assert memory.text(DONUTS) == edited
        assert "kind: bookmark" in memory.text(SAXOPHONE)

        again, said = _up(memory, tmp_path / "records")

        assert "failed" not in again, "\n".join(said)
        assert "Marge added a line meanwhile." in memory.text(DONUTS)
        assert parse(memory.text(DONUTS))["type"] == "bookmark"
        assert parse(memory.text(SAXOPHONE))["type"] == "bookmark"


def test_no_person_files_stop_it_and_no_name_leaves_persons(small, tmp_path):
    """As migration 0003 stops: with nobody declared, every name reads as
    unknown, and acting on that would empty every card's `persons`."""
    with small({TRICKS: OLD_CARDS[TRICKS]}) as memory:
        head = memory.forgejo.head()

        outcome, _ = _up(memory, tmp_path / "records")

        assert REPAIR in outcome["failed"]
        assert memory.forgejo.head() == head
        assert parse(memory.text(TRICKS))["persons"] == ["Bart", "Evergreen"]


@pytest.mark.parametrize("path, text", [
    (ODD_KIND, A_CARD_OF_ODD_KIND),
    (ODD_FIELDS, A_CARD_WITH_BOTH_TIMES),
    (ODD_TYPE, "---\ntitle: Odd\ntype:\n---\n\n# Odd\n"),
    (ODD_TYPE, "---\ntitle: Odd\ntype: []\n---\n\n# Odd\n"),
    (ODD_TYPE, "---\ntitle: Odd\ntype: [note]\n---\n\n# Odd\n"),
], ids=["kind-with-no-type", "both-times", "blank-type", "empty-list-type", "list-type"])
def test_a_card_the_rules_cannot_read_stays_and_is_named(small, tmp_path, path, text):
    """Given a card the rules cannot read, then it stays byte for byte, the
    output names it, and the migration passes: a failed migration holds the
    curator and the bots, so one odd card must not stop the family's memory.
    The other cards are repaired all the same."""
    with small({**PEOPLE, path: text, DONUTS: OLD_CARDS[DONUTS]}) as memory:
        outcome, said = _up(memory, tmp_path / "records")

        assert outcome == {"applied": [REPAIR]}, "\n".join(said)
        assert memory.text(path) == text
        assert _naming(said, path)
        assert parse(memory.text(DONUTS))["type"] == "bookmark"


def test_a_note_that_already_carries_a_documents_id_passes(small, tmp_path):
    """Given a hand-retyped card, a note with a Paperless id and a document's
    `card_id`, then it is a document again under the id it has, with no
    former id to list, and the migration passes."""
    with small({**PEOPLE, RETYPED: A_NOTE_WITH_A_DOCUMENTS_ID}) as memory:
        outcome, said = _up(memory, tmp_path / "records")

        assert outcome == {"applied": [REPAIR]}, "\n".join(said)
        card = parse(memory.text(RETYPED))
        assert card["type"] == "document" and card_id_from(card) == "document-77aa77aa77"


@pytest.mark.parametrize("ontology", [
    "[topic.broken\n",
    'topic = "x"\n',
    '[topic.a]\nnames = "Foo"\n',
    "[topic.a]\nnames = 3\n",
], ids=["no-toml", "topic-no-table", "names-text", "names-number"])
def test_an_ontology_that_cannot_be_read_leaves_the_tags_and_is_named(small, tmp_path, ontology):
    """Given a hand edit that broke `ontology.toml`, then no tag can be read,
    so the tags stay and the output says why. The rest of the repair is written."""
    with small({**PEOPLE, "ontology.toml": ontology, DONUTS: OLD_CARDS[DONUTS]}) as memory:
        outcome, said = _up(memory, tmp_path / "records")

        assert outcome == {"applied": [REPAIR]}, "\n".join(said)
        assert _naming(said, "ontology.toml")
        card = parse(memory.text(DONUTS))
        assert card["type"] == "bookmark" and card["tags"] == ["Kochen", "Donuts"]


def test_a_person_file_that_cannot_be_read_changes_no_persons(small, tmp_path):
    """Given one person file that does not parse, then that person's id reads
    as a name nobody goes by, and acting on that would take them off every
    card. So no card's `persons` changes, the output names the file, and the
    migration passes. The rest of the repair is written, and a `Person:` tag
    still goes rather than become a keyword."""
    files = {**PEOPLE, MARGE: A_MARGE_FILE_THAT_DOES_NOT_READ,
             MARGES_BOOKMARK: A_BOOKMARK_ABOUT_MARGE, DONUTS: OLD_CARDS[DONUTS]}
    with small(files) as memory:
        outcome, said = _up(memory, tmp_path / "records")

        assert outcome == {"applied": [REPAIR]}, "\n".join(said)
        assert _naming(said, MARGE)
        assert not _naming(said, "leaves `persons`")
        card = parse(memory.text(MARGES_BOOKMARK))
        assert card["persons"] == ["marge", "Homer", "Evergreen"]
        assert card["tags"] == ["handarbeit"]
        assert parse(memory.text(DONUTS))["persons"] == ["Homer"]
        assert parse(memory.text(DONUTS))["type"] == "bookmark"


def test_a_diary_card_stays_byte_for_byte(small, tmp_path):
    """Diary cards are signed, and an edit from outside the compile would make
    them the family's for good. This one has what every pass looks for: no
    id, `medium` in place of `kind`, a name nobody goes by, a tag with no letter."""
    with small({**PEOPLE, DIARY: A_DIARY_CARD, DONUTS: OLD_CARDS[DONUTS]}) as memory:
        outcome, said = _up(memory, tmp_path / "records")

        assert outcome == {"applied": [REPAIR]}, "\n".join(said)
        assert memory.text(DIARY) == A_DIARY_CARD
        assert DIARY not in _files_of(memory, _repair_commit(memory))


REFUSED_WITHOUT_THE_REPAIR = [POWER_BILL, DENTIST_BILL, ORDER, PLOW_OFFER, TRICKS, AUTUMN_BOOKS]


def test_without_the_repair_the_curator_refuses_a_card_with_a_type(tmp_path):
    """The reason for the migration: every other migration leaves these cards
    in a state their schema refuses. Bookmarks and notes with no `type` are not
    among them, because a card with no `type` gets no schema check. Runs the
    earlier migrations on a small memory, the repair marked as done, which
    takes about a second."""
    records = tmp_path / "records"
    migrations = Migrations("memory", MEMORY_STACKLET, records)
    record = migrations.record()
    record.mark_applied(next(m for m in migrations.shipped() if m.key == REPAIR), None)
    cards = {path: OLD_CARDS[path] for path in REFUSED_WITHOUT_THE_REPAIR}
    with running_memory(tmp_path / "data", {**PEOPLE, **cards}) as memory:
        outcome, said = _up(memory, records)
        assert "failed" not in outcome, "\n".join(said)
        for path in REFUSED_WITHOUT_THE_REPAIR:
            text = memory.text(path)
            with pytest.raises(Refused):
                memory.api.commit([put(path, text + "\nA line added later.\n", base=version_of(text))],
                                  by="marge", kind="cards", title="a later change")


def test_a_memory_with_nothing_to_repair_gets_no_commit(small, tmp_path):
    with small(PEOPLE) as memory:
        head = memory.forgejo.head()

        outcome, said = _up(memory, tmp_path / "records")

        assert outcome == {"applied": [REPAIR]}, "\n".join(said)
        assert memory.forgejo.head() == head
