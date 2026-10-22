"""Each topic's old `todos.md` moves to `lists/<topic>.md`, by migration memory/0001.

Families have been ticking items in these files for months, so the
move is held to what a family would check afterwards: every item is
there, every tick is kept, the due dates still say the same day, and
a second start finds nothing left to move. Forgejo is a real HTTP
server here (`pytest-httpserver`), answering the calls the move makes.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

from lib import REPO_NAME, REPO_OWNER, move_todos_to_lists, todos_as_list  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402
from stack.lists import read_list  # noqa: E402

# Every write names its committer, which the client asks Forgejo for.
pytestmark = pytest.mark.usefixtures("forgejo_accounts")

CONTENTS = f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents"
DASH = "—"

CAMPING_TODOS = (
    "# Itchy Scratchy Land\n\n"
    "- [ ] Book the hotel before the prices jump\n"
    f"- [ ] Pay the deposit {DASH} 2026-10-30\n"
    "- [x] Pack sunscreen for Bart\n"
)


def _file(text: str, sha: str) -> dict:
    return {"type": "file", "content": base64.b64encode(text.encode()).decode(), "sha": sha}


def _commit(httpserver) -> dict:
    post = next(r for r, _ in httpserver.log if r.method == "POST")
    return json.loads(post.data)


def _files(commit: dict) -> dict[str, tuple[str, str]]:
    """Path to (operation, content) for every file in the commit."""
    return {f["path"]: (f["operation"],
                        base64.b64decode(f["content"]).decode() if f.get("content") else "")
            for f in commit["files"]}


def _client(httpserver) -> ForgejoClient:
    return ForgejoClient(url=httpserver.url_for(""), token="bottoken")


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
    def test_moves_every_topics_list_in_one_commit(self, httpserver):
        httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json([
            {"type": "dir", "path": "family/itchy-scratchy-land"},
            {"type": "dir", "path": "family/people"},
            {"type": "dir", "path": "family/finance"},
        ])
        httpserver.expect_request(f"{CONTENTS}/family/itchy-scratchy-land/todos.md",
                                  method="GET").respond_with_json(_file(CAMPING_TODOS, "old-sha"))
        httpserver.expect_request(f"{CONTENTS}/family/finance/todos.md",
                                  method="GET").respond_with_data("", status=404)
        httpserver.expect_request(f"{CONTENTS}/family/lists/itchy-scratchy-land.md",
                                  method="GET").respond_with_data("", status=404)
        httpserver.expect_request(CONTENTS, method="POST").respond_with_json({}, status=201)

        assert move_todos_to_lists(_client(httpserver))[0] == ["itchy-scratchy-land"]

        commit = _commit(httpserver)
        files = _files(commit)
        assert files["family/itchy-scratchy-land/todos.md"][0] == "delete"
        operation, content = files["family/lists/itchy-scratchy-land.md"]
        assert operation == "create" and "- [x] Pack sunscreen for Bart" in content
        deleted = next(f for f in commit["files"] if f["operation"] == "delete")
        assert deleted["sha"] == "old-sha", "a todo list edited meanwhile is not deleted"

    def test_a_topic_with_a_list_already_gains_only_the_missing_items(self, httpserver):
        existing = ("---\ntype: list\ntitle: Itchy Scratchy Land\n---\n\n"
                    "- [x] Pay the deposit ✅ 2026-10-01\n")
        httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json(
            [{"type": "dir", "path": "family/itchy-scratchy-land"}])
        httpserver.expect_request(f"{CONTENTS}/family/itchy-scratchy-land/todos.md",
                                  method="GET").respond_with_json(_file(CAMPING_TODOS, "old-sha"))
        httpserver.expect_request(f"{CONTENTS}/family/lists/itchy-scratchy-land.md",
                                  method="GET").respond_with_json(_file(existing, "list-sha"))
        httpserver.expect_request(CONTENTS, method="POST").respond_with_json({}, status=201)

        move_todos_to_lists(_client(httpserver))

        content = _files(_commit(httpserver))["family/lists/itchy-scratchy-land.md"][1]
        items = read_list(content, path="family/lists/itchy-scratchy-land.md").items
        assert [(i.text, i.done) for i in items] == [
            ("Pay the deposit", True),
            ("Book the hotel before the prices jump", False),
            ("Pack sunscreen for Bart", True),
        ]

    def test_a_second_start_finds_nothing_to_move(self, httpserver):
        httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json(
            [{"type": "dir", "path": "family/lists"}, {"type": "dir", "path": "family/camping"}])
        httpserver.expect_request(f"{CONTENTS}/family/camping/todos.md",
                                  method="GET").respond_with_data("", status=404)

        assert move_todos_to_lists(_client(httpserver)) == ([], None)
        assert not [r for r, _ in httpserver.log if r.method == "POST"]


class TestTheMigration:
    """`stacklets/memory/migrations/0001_move_todos_to_lists.py`, run by the
    framework's runner the way `stack up memory` runs it."""

    def _run(self, tmp_path, httpserver):
        from stack import Stack
        from stack.migrations import MigrationContext, Migrations
        from stack.output import CollectorOutput

        stck = Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())
        stck.secrets.set("memory", "MEMORY_BOT_TOKEN", "bottoken")
        migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory",
                                tmp_path / "data" / "memory")
        outcome = migrations.run(
            lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                       env={"CODE_URL": httpserver.url_for("")}),
            lambda msg: None)
        return outcome, migrations.record()

    def _old_list(self, httpserver):
        httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json(
            [{"type": "dir", "path": "family/itchy-scratchy-land"}])
        httpserver.expect_request(f"{CONTENTS}/family/itchy-scratchy-land/todos.md",
                                  method="GET").respond_with_json(_file(CAMPING_TODOS, "old-sha"))
        httpserver.expect_request(f"{CONTENTS}/family/lists/itchy-scratchy-land.md",
                                  method="GET").respond_with_data("", status=404)

    def test_its_commit_carries_the_trailer_and_is_its_record(self, tmp_path, httpserver):
        self._old_list(httpserver)
        httpserver.expect_request(CONTENTS, method="POST").respond_with_json(
            {"commit": {"sha": "c0ffee"}}, status=201)

        outcome, record = self._run(tmp_path, httpserver)

        assert outcome["applied"] == ["memory/0001"]
        assert _commit(httpserver)["message"].endswith("\n\nMigration: memory/0001")
        assert record.applied["0001"]["commit"] == "c0ffee"

    def test_a_file_changed_underneath_fails_it_and_writes_nothing(self, tmp_path, httpserver):
        """Forgejo refuses a commit whose sha no longer matches; the move
        is not recorded and runs again on the next start."""
        self._old_list(httpserver)
        httpserver.expect_request(CONTENTS, method="POST").respond_with_json(
            {"message": "sha does not match"}, status=409)

        outcome, record = self._run(tmp_path, httpserver)

        assert "409" in outcome["failed"]["memory/0001"]
        assert record.applied == {}
        assert [r.method for r, _ in httpserver.log].count("POST") == 1
