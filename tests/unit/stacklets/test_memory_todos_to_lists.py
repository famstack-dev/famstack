"""Each topic's old `todos.md` moves to `lists/<topic>.md`, by migration memory/0001.

Families have been ticking items in these files for months, so the
move is held to what a family would check afterwards: every item is
there, every tick is kept, the due dates still say the same day, and
a second start finds nothing left to move. The move runs against the
real memory: the curator's clone it reads, and its API it writes through,
in front of a bare repository as Forgejo (`running_memory`, memory's test instance).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets"))
sys.path.insert(0, str(Path(__file__).parent))

from stacklets.memory.lib import move_todos_to_lists, todos_as_list  # noqa: E402
from stacklets.memory.memory_api import MemoryAPI, Refused  # noqa: E402
from tests.memory.unit.instance import TOKEN, clone_of, commit_as, git, running_memory  # noqa: E402

from stack.lists import read_list  # noqa: E402

CAMPING = "family/itchy-scratchy-land/todos.md"
CAMPING_LIST = "family/lists/itchy-scratchy-land.md"
DASH = "—"

CAMPING_TODOS = (
    "# Itchy Scratchy Land\n\n"
    "- [ ] Book the hotel before the prices jump\n"
    f"- [ ] Pay the deposit {DASH} 2026-10-30\n"
    "- [x] Pack sunscreen for Bart\n"
)


@pytest.fixture
def memory(tmp_path, request):
    """The memory with the files a test names (`indirect`)."""
    with running_memory(tmp_path / "data", request.param, seeded_by="marge",
                        seeded_as="todos as the family kept them") as memory:
        yield memory


def _move(memory):
    """The move as migration 0001 makes it: from the curator's clone,
    through the curator."""
    api = MemoryAPI.from_host(memory.config)
    api.sync()
    return move_todos_to_lists(api, memory.vault, trailer="Migration: memory/0001")


class TestAnOldTodoList:
    def test_keeps_its_title_its_items_and_its_ticks(self):
        lst = read_list(todos_as_list(CAMPING_TODOS, "itchy-scratchy-land"),
                        path="family/lists/itchy-scratchy-land.md")
        assert lst.title == "Itchy Scratchy Land" and lst.topics == ["itchy-scratchy-land"]
        assert [(i.text, i.done, i.due) for i in lst.items] == [
            ("Book the hotel before the prices jump", False, ""),
            ("Pay the deposit", False, "2026-10-30"),
            ("Pack sunscreen for Bart", True, ""),
        ]

    def test_writes_its_due_dates_as_list_fields(self):
        assert "- [ ] Pay the deposit 📅 2026-10-30\n" in todos_as_list(CAMPING_TODOS, "camping")

    def test_says_what_it_is_for_in_the_household_language(self):
        lst = read_list(todos_as_list(CAMPING_TODOS, "camping", language="de"),
                        path="family/lists/camping.md")
        assert lst.description == "Was für Itchy Scratchy Land noch zu tun ist"

    def test_without_a_title_heading_takes_the_topics_name(self):
        lst = read_list(todos_as_list("- [ ] Ask the vet\n", "pet"), path="family/lists/pet.md")
        assert lst.title == "Pet" and [i.text for i in lst.items] == ["Ask the vet"]


class TestTheMoveOnStart:
    @pytest.mark.parametrize("memory", [{CAMPING: CAMPING_TODOS,
                                         "family/people/homer.md": "---\ntype: person\n---\n",
                                         "family/finance/budget.md": "# Budget\n"}], indirect=True)
    def test_moves_every_topics_list_in_one_commit(self, memory):
        moved, commit = _move(memory)

        assert moved == ["itchy-scratchy-land"]
        assert memory.forgejo.changed() == [CAMPING, CAMPING_LIST]
        assert memory.text(CAMPING) == ""
        assert "- [x] Pack sunscreen for Bart" in memory.text(CAMPING_LIST)
        assert memory.forgejo.new_commits() == [
            "memory-bot <memory-bot@local> | memory-bot <memory-bot@local> | "
            "lists: move each topic's todo list to lists/"]
        assert memory.forgejo.message().endswith("\n\nMigration: memory/0001")
        assert commit == git(memory.forgejo.repo, "rev-parse", "main")

    @pytest.mark.parametrize("memory", [{CAMPING: CAMPING_TODOS, CAMPING_LIST: (
        "---\ntype: list\ncard_id: list-itchy-scratchy-land\ntitle: Itchy Scratchy Land\n---\n\n- [x] Pay the deposit ✅ 2026-10-01\n")}],
        indirect=True)
    def test_a_topic_with_a_list_already_gains_only_the_missing_items(self, memory):
        _move(memory)

        items = read_list(memory.text(CAMPING_LIST), path=CAMPING_LIST).items
        assert [(i.text, i.done) for i in items] == [
            ("Pay the deposit", True),
            ("Book the hotel before the prices jump", False),
            ("Pack sunscreen for Bart", True),
        ]

    @pytest.mark.parametrize("memory", [{CAMPING_LIST: "---\ntype: list\ntitle: Camping\n---\n",
                                         "family/camping/notes.md": "# Camping\n"}], indirect=True)
    def test_a_second_start_finds_nothing_to_move(self, memory):
        assert _move(memory) == ([], None)
        assert memory.forgejo.new_commits() == []

    @pytest.mark.parametrize("memory", [{CAMPING: CAMPING_TODOS}], indirect=True)
    def test_a_list_edited_meanwhile_is_not_moved_and_nothing_is_written(self, memory):
        """The curator refuses a commit made from the list as it was; the
        move runs again on the next start."""
        edited = CAMPING_TODOS + "- [ ] Find the swimsuits\n"
        # Marge edits it in Forgejo after the move read the curator's clone.
        marge = clone_of(memory.forgejo, memory.forgejo.repo.parent / "marge")
        commit_as(marge, "marge", "edit the todos", {CAMPING: edited})

        with pytest.raises(Refused):
            move_todos_to_lists(MemoryAPI.from_host(memory.config), memory.vault)
        assert memory.text(CAMPING) == edited
        assert memory.text(CAMPING_LIST) == ""


class TestTheMigration:
    """`stacklets/memory/migrations/0001_move_todos_to_lists.py`, run by the
    framework's runner the way `stack up memory` runs it."""

    @pytest.mark.parametrize("memory", [{CAMPING: CAMPING_TODOS}], indirect=True)
    def test_its_commit_carries_the_trailer_and_is_its_record(self, memory, tmp_path):
        from stack import Stack
        from stack.migrations import MigrationContext, Migrations
        from stack.output import CollectorOutput

        port = memory.config["stack"]["core"]["cards_port"]
        (tmp_path / "stack.toml").write_text(f"[core]\ncards_port = {port}\n", encoding="utf-8")
        stck = Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())
        stck.secrets.set("memory", "CARDS_WRITE_TOKEN", TOKEN)
        migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory", tmp_path / "data" / "memory")

        outcome = migrations.run(
            lambda m: MigrationContext(m, stack=stck, stacklet_id="memory", env={}),
            lambda msg: None)

        assert "memory/0001" in outcome["applied"]
        assert migrations.record().applied["0001"]["commit"] == git(
            memory.forgejo.repo, "log", "-1", "--format=%H", "--grep=Migration: memory/0001", "main")
