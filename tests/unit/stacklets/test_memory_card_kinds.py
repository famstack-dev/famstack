"""Every card's `kind` is one of its type's: migration memory/0006.

ADR-017 §6: `kind` is a fixed variant per type, checked like an enum. A
list's archive says `kind: archive`, so a calendar's archive no longer
reads as a to-do list with its past appointments open (review C3). A
diary card says what it was recorded as in `kind`, where it said
`medium`. The nightly move and the diary compile write new files that
way; this migration brings the files written before to the same shape.

A diary card is signed with a digest of its text (`stack.digest`). The
one the compile still owns is signed again, so the compile keeps owning
it and finds nothing to rewrite; one a person changed stays theirs.

The memory repo is a real git repository and Forgejo a real HTTP server
that applies each commit to it (`test_memory_card_links`), so a second
run reads what the first one wrote.
"""

from __future__ import annotations

import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
sys.path.insert(0, str(Path(__file__).parent))

import diary_card  # noqa: E402
from lib import BOT_USERNAME, give_card_kinds  # noqa: E402
from stack.cards import kind_problem  # noqa: E402
from stack.digest import signed  # noqa: E402
from stack.frontmatter import parse  # noqa: E402
from stack.lists import read_list  # noqa: E402
from test_memory_card_links import CONTENTS, _clone, _commits, _git, _read, forgejo  # noqa: E402, F401

TODOS = "family/lists/archive/einkaufsliste/2026-09.md"
DATES = "family/lists/archive/termine/2026-09.md"
DATES_NOW = "family/lists/archive/termine/2026-10.md"
PACKING = "family/lists/packliste.md"
KRUSTY = "family/correspondents/krusty-burger.md"
MEDIA = ("text", "voice", "image", "video", "file")


def _diary_card(medium: str, n: int) -> diary_card.Card:
    return diary_card.Card(
        entry_id=f"b48902f59{n}", on=date(2024, 3, 9), confidence="sent", kind=medium,
        sender="marge", at=1709985600000 + n, event_ids=[f"$event{n}"],
        media=[] if medium == "text" else [f"/media/2024/03/event{n}.bin"],
        body="Bart rode his skateboard to school.", title=f"Bart's skateboard, {medium}",
        persons=["Bart"], tags=["Person: Bart"], card_id=f"diary-3f9a0c1b{n}0")


def _diary_path(medium: str) -> str:
    return diary_card.card_path(_diary_card(medium, MEDIA.index(medium)), bucket="family")


def _written_before_kinds(card: diary_card.Card) -> str:
    """The card as the compile signed it while it still wrote `medium`."""
    today = diary_card.render(card, family_owned=True)
    return signed(today.replace(f"\nkind: {card.kind}\n", f"\nmedium: {card.kind}\n"))


EDITED = "family/diary/entries/2024/03/2024-03-10-c0ffee0000.md"
# Marge fixed a misheard word after the compile signed the card.
EDITED_TEXT = _written_before_kinds(replace(
    _diary_card("voice", 7), entry_id="c0ffee0000", on=date(2024, 3, 10))).replace(
    "skateboard to school", "skateboard to Springfield Elementary")

BART = "family/people/bart.md"

VAULT = {
    BART: "---\ntype: person\ncard_id: person-bart\ntitle: Bart Simpson\n---\n\n# Bart Simpson\n",
    TODOS: ("---\ntype: list\ncard_id: list-einkaufsliste-2026-09\ntitle: Einkaufsliste, 2026-09\n---\n\n"
            "## Obst\n- [x] Äpfel ✅ 2026-09-11\nVom Markt.\n"),
    # A calendar's month: the appointment was over, nobody ticked it.
    DATES: ("---\ntype: list\ncard_id: list-termine-2026-09\ntitle: Termine, 2026-09\n---\n\n"
            "- [ ] Zahnarzt 📅 2026-09-03\n"),
    DATES_NOW: ("---\ntype: list\ncard_id: list-termine-2026-10\ntitle: Termine, 2026-10\n"
                "kind: archive\n---\n\n- [ ] Elternabend 📅 2026-10-08\n"),
    PACKING: "---\ntype: list\ncard_id: list-packliste\ntitle: Packliste\nkind: packing\n---\n\n- [ ] Zelt\n",
    KRUSTY: ("---\ntype: correspondent\ncard_id: correspondent-krusty-burger\nkind: correspondent\n"
             "canonical: Krusty Burger\n---\n\nThe kids' favourite.\n"),
    EDITED: EDITED_TEXT,
    **{_diary_path(m): _written_before_kinds(_diary_card(m, n)) for n, m in enumerate(MEDIA)},
}


@pytest.fixture
def memory(tmp_path) -> Path:
    """The family/memory repository on Forgejo, holding the fabricated vault."""
    repo = tmp_path / "forgejo" / "memory"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    for path, text in VAULT.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed the vault")
    _git(repo, "update-server-info")
    return repo


class TestKindsOnce:
    TRAILER = "Migration: memory/0006"

    @pytest.fixture
    def found(self, memory, forgejo, tmp_path):  # noqa: F811
        found, sha = give_card_kinds(forgejo, _clone(memory, tmp_path, "run1"), trailer=self.TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory, httpserver):
        assert _commits(httpserver) == 1
        assert _git(memory, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: give 9 card(s) their kind\n\n{self.TRAILER}"

    def test_every_archive_says_it_is_one(self, found, memory):
        for path in (TODOS, DATES):
            assert parse(_read(memory, path))["kind"] == "archive", path
        assert _read(memory, DATES_NOW) == VAULT[DATES_NOW]

    def test_a_calendar_archive_holds_no_open_appointment(self, found, memory):
        """Before, the Zahnarzt read as open: the file looked like a to-do list."""
        archived = read_list(_read(memory, DATES), path=DATES)
        assert [i.text for i in archived.items] == ["Zahnarzt"]
        assert archived.open_items == []

    @pytest.mark.parametrize("medium", MEDIA)
    def test_a_diary_card_says_its_medium_as_its_kind(self, found, memory, medium):
        fields = parse(_read(memory, _diary_path(medium)))
        assert fields["kind"] == medium and "medium" not in fields

    @pytest.mark.parametrize("medium", MEDIA)
    def test_a_diary_card_the_compile_owns_is_what_the_compile_writes_today(self, found, memory, medium):
        """Signed again, so the next compile finds nothing to rewrite."""
        text = _read(memory, _diary_path(medium))
        assert text == diary_card.render(_diary_card(medium, MEDIA.index(medium)))
        assert not diary_card.edited_by_hand(text)

    def test_a_diary_card_a_person_changed_stays_theirs(self, found, memory):
        text = _read(memory, EDITED)
        assert text == EDITED_TEXT.replace("\nmedium: voice\n", "\nkind: voice\n")
        assert diary_card.edited_by_hand(text)

    def test_a_kind_that_only_repeats_a_type_without_kinds_goes(self, found, memory):
        assert _read(memory, KRUSTY) == VAULT[KRUSTY].replace("kind: correspondent\n", "")

    def test_a_kind_it_cannot_place_is_reported_and_kept(self, found, memory):
        assert _read(memory, PACKING) == VAULT[PACKING]
        assert found.left == [f"{PACKING}: `kind: packing` is not one of todo, checklist, calendar, archive"]

    def test_only_the_kind_lines_change(self, found, memory):
        assert _read(memory, TODOS) == VAULT[TODOS].replace("---\n\n", "kind: archive\n---\n\n")

    def test_the_kind_finding_holds_only_what_it_reported(self, found, memory):
        """The card browser's gauge (`stack memory check-format`, wrong kind)."""
        assert [path for path in VAULT if kind_problem(path, parse(_read(memory, path)))] == [PACKING]

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, forgejo,  # noqa: F811
                                                          httpserver, tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = give_card_kinds(forgejo, _clone(memory, tmp_path, "run2"), trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head



def test_stack_up_memory_runs_it_once_and_records_its_commit(memory, forgejo, tmp_path,  # noqa: F811
                                                             httpserver):
    """`0006_give_cards_their_kind.py`, run by the framework's runner the way
    `stack up memory` runs it: a fresh clone over HTTP, one commit."""
    from stack import Stack
    from stack.migrations import MigrationContext, Migrations
    from stack.output import CollectorOutput

    # 0001 finds no old todo lists to move; 0002 to 0004 find nothing to change.
    httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json([])
    stck = Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())
    stck.secrets.set("memory", "MEMORY_BOT_TOKEN", "bottoken")
    migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory", tmp_path / "data" / "memory")
    outcome = migrations.run(
        lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                   env={"CODE_URL": httpserver.url_for("")}),
        lambda msg: None)

    assert "memory/0006" in outcome["applied"]
    assert migrations.record().applied["0006"]["commit"] == _git(
        memory, "log", "-1", "--format=%H", "--grep=Migration: memory/0006")
    assert parse(_read(memory, DATES))["kind"] == "archive"
