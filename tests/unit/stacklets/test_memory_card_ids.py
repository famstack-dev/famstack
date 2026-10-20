"""Every card in the memory repo gets an id of its own: migration memory/0002
and the curator.

The migration gives one to every card that has none, the curator to every
file pushed without one, and both run one pass over a git working copy of
the repo (`give_card_ids`). The memory repo here is a real git repository
holding a small fabricated vault, and Forgejo a real HTTP server
(`pytest-httpserver`) that applies each commit to it the way Forgejo
would, refusing a file whose blob changed since it was read. So a second
run reads what the first one wrote, as it would on an instance.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from werkzeug.wrappers import Response

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "lib"))
sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory"))

from lib import BOT_USERNAME, REPO_NAME, REPO_OWNER, give_card_ids  # noqa: E402
from stack.forgejo import ForgejoClient, ForgejoError  # noqa: E402
from stack.frontmatter import parse  # noqa: E402

CONTENTS = f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents"
TRAILER = "Migration: memory/0002"

DOCUMENT = "family/documents/1989/12/1989-12-17-christmas-card-p12.md"
NOTE = "marge/notes/2026/10/art-supplies-for-next-time-5f9740.md"
DIARY = "family/diary/entries/2024/03/2024-03-09-b48902f594.md"
PERSON = "family/people/homer.md"
LIST = "family/lists/markt.md"
ARCHIVE = "family/lists/archive/markt/2026-10.md"
BY_HAND = "homer/donut-shops.md"
ODD = "family/recipes/pretzels.md"

VAULT = {
    "README.md": "# Memory\n\nThe family's memory.\n",
    "index.md": "---\ntitle: Family Memory\n---\n\n# Family Memory\n",
    "family/correspondents/README.md": "# Correspondents\n",
    DOCUMENT: ("---\ntype: document\ntitle: Christmas card from Patty and Selma\n"
               "paperless_id: 12\nsource: paperless\ntimestamp: \"2026-10-01T10:00:00Z\"\n---\n\n"
               "# Christmas card from Patty and Selma\n\nSeason's greetings.\n"),
    NOTE: ("---\ntype: note\ntitle: Art supplies for next time\n"
           "capture_id: $r-PlkCYBsNEjgZDfzCpcAoDaY66uCHNc7tJjDWrY8P4\n"
           "timestamp: \"2026-10-13T21:12:08Z\"\n---\n\n# Art supplies for next time\n\n"
           "Brushes and paper.  \nTrailing spaces stay.\n"),
    DIARY: ("---\ntype: diary\ntitle: Bart's first skateboard\ndate: \"2024-03-09\"\n"
            "entry_id: b48902f594\ntimestamp: \"2024-03-09T18:00:00.000Z\"\n---\n\n"
            "# Bart's first skateboard\n"),
    PERSON: "---\ntype: person\ntitle: Homer Simpson\naccount: homer\n---\n\n# Homer Simpson\n",
    LIST: "---\ntype: list\ntitle: Markt\n---\n\n- [ ] Äpfel\n",
    ARCHIVE: "---\ntype: list\ntitle: Markt, 2026-10\n---\n\n- [x] Milch ✅ 2026-10-02\n",
    BY_HAND: "Lard Lad, Springfield Mall.\n\n## Donut shops worth the drive\n\n- Lard Lad\n",
    ODD: "---\ntype: recipe\ntitle: Pretzels\n---\n\nKnot them.\n",
}

NO_CARDS = ("README.md", "index.md", "family/correspondents/README.md")


# ── The memory repo and Forgejo ──────────────────────────────────────────

def _git(repo: Path, *args: str, when: str = "2026-10-01T10:00:00") -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Marge", "GIT_AUTHOR_EMAIL": "marge@example.org",
           "GIT_COMMITTER_NAME": "Marge", "GIT_COMMITTER_EMAIL": "marge@example.org",
           "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _push(repo: Path, files: dict[str, str], message: str, *,
          when: str = "2026-10-01T10:00:00") -> str:
    """A commit as a person pushes it, from Obsidian or by hand."""
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", message, when=when)
    _git(repo, "update-server-info")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def memory(tmp_path) -> Path:
    """The family/memory repository on Forgejo, holding the fabricated vault."""
    repo = tmp_path / "forgejo" / "memory"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _push(repo, VAULT, "seed the vault")
    return repo


@pytest.fixture
def forgejo(httpserver, memory) -> ForgejoClient:
    """Forgejo's multi-file commit endpoint, applied to `memory`."""

    def commit(request):
        body = json.loads(request.data)
        for f in body["files"]:
            if _git(memory, "hash-object", "--", f["path"]) != f.get("sha"):
                return Response(json.dumps({"message": "sha does not match"}), status=409)
        for f in body["files"]:
            (memory / f["path"]).write_bytes(base64.b64decode(f["content"]))
        author = body["author"]
        _git(memory, "add", "-A")
        _git(memory, "-c", f"user.name={author['name']}", "-c", f"user.email={author['email']}",
             "commit", "-q", f"--author={author['name']} <{author['email']}>",
             "-m", body["message"], when="2026-10-19T12:00:00")
        _git(memory, "update-server-info")
        return Response(json.dumps({"commit": {"sha": _git(memory, "rev-parse", "HEAD")}}),
                        content_type="application/json")

    def clone(request):
        """Git's plain HTTP protocol: the repository's files, as they are."""
        file = memory / ".git" / request.path.removeprefix(f"/{REPO_OWNER}/{REPO_NAME}.git/")
        if not file.is_file():
            return Response("", status=404)
        return Response(file.read_bytes(), content_type="application/octet-stream")

    httpserver.expect_request(CONTENTS, method="POST").respond_with_handler(commit)
    httpserver.expect_request(re.compile(rf"^/{REPO_OWNER}/{REPO_NAME}\.git/.*"),
                              method="GET").respond_with_handler(clone)
    return ForgejoClient(url=httpserver.url_for(""), token="bottoken")


def _clone(memory: Path, tmp_path: Path, name: str) -> Path:
    """A fresh clone, as the migration reads the repo."""
    target = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(memory), str(target)], check=True)
    return target


def _card_id(memory: Path, path: str) -> str:
    return str(parse((memory / path).read_text(encoding="utf-8")).get("card_id") or "")


def _commits(httpserver) -> int:
    return sum(1 for r, _ in httpserver.log if r.method == "POST")


# ── Migration 0002 ───────────────────────────────────────────────────────

class TestTheMigration:
    def test_gives_every_card_an_id_in_one_commit_by_the_memory_bot(
            self, memory, forgejo, httpserver, tmp_path):
        found, sha = give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD") and _commits(httpserver) == 1
        assert _git(memory, "log", "-1", "--format=%an|%B").strip() == \
            f"{BOT_USERNAME}|cards: give 7 card(s) an id of their own\n\n{TRAILER}"

        for path, card_type in ((DOCUMENT, "document"), (NOTE, "note"), (DIARY, "diary")):
            assert re.fullmatch(rf"{card_type}-[0-9a-f]{{10}}", _card_id(memory, path)), path
        assert _card_id(memory, PERSON) == "person-homer"
        assert _card_id(memory, LIST) == "list-markt"
        assert _card_id(memory, ARCHIVE) == "list-markt-2026-10"
        assert found.left == [f"{ODD}: `type: recipe` is no card type"]

    def test_adds_only_the_id_line_and_leaves_every_other_byte(self, memory, forgejo, tmp_path):
        give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        for path in (DOCUMENT, NOTE, DIARY, PERSON, LIST, ARCHIVE):
            after = (memory / path).read_text(encoding="utf-8")
            assert after.replace(f"card_id: {_card_id(memory, path)}\n", "", 1) == VAULT[path]

    def test_leaves_navigation_pages_and_what_it_cannot_place_alone(
            self, memory, forgejo, tmp_path):
        give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        for path in (*NO_CARDS, ODD):
            assert (memory / path).read_text(encoding="utf-8") == VAULT[path]

    def test_makes_a_file_written_by_hand_a_note(self, memory, forgejo, tmp_path):
        give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        after = (memory / BY_HAND).read_text(encoding="utf-8")
        fields = parse(after)
        assert (fields["type"], fields["title"]) == ("note", "Donut shops worth the drive")
        assert re.fullmatch(r"note-[0-9a-f]{10}", fields["card_id"])
        assert after.endswith(f"---\n{VAULT[BY_HAND]}")

    def test_run_again_changes_nothing_and_makes_no_commit(
            self, memory, forgejo, httpserver, tmp_path):
        give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        head = _git(memory, "rev-parse", "HEAD")
        found, sha = give_card_ids(forgejo, _clone(memory, tmp_path, "run2"), trailer=TRAILER)
        assert (found.changes, sha) == ({}, None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head

    def test_writes_nothing_over_an_edit_pushed_after_it_read(self, memory, forgejo, tmp_path):
        """Forgejo refuses the commit; the next `stack up memory` reads again."""
        stale = _clone(memory, tmp_path, "stale")
        edited = VAULT[NOTE] + "Marge added a line meanwhile.\n"
        head = _push(memory, {NOTE: edited}, "edit the note")
        with pytest.raises(ForgejoError):
            give_card_ids(forgejo, stale, trailer=TRAILER)
        assert _git(memory, "rev-parse", "HEAD") == head
        assert (memory / NOTE).read_text(encoding="utf-8") == edited


class TestStackUpMemory:
    """`0002_give_every_card_an_id.py`, run by the framework's runner the way
    `stack up memory` runs it: a fresh clone over HTTP, one commit."""

    def _up(self, tmp_path, httpserver):
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

    def test_gives_the_ids_and_records_its_commit(self, memory, forgejo, tmp_path, httpserver):
        # 0001 runs first and finds no old todo lists to move.
        httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json([])
        outcome, record = self._up(tmp_path, httpserver)
        assert outcome["applied"][:2] == ["memory/0001", "memory/0002"]
        # Later migrations commit after it: its commit is the one with its trailer.
        assert record.applied["0002"]["commit"] == _git(memory, "log", "-1", "--format=%H",
                                                        f"--grep={TRAILER}")
        assert _card_id(memory, PERSON) == "person-homer"


class TestTwoFilesWithOneId:
    ORIGINAL = "marge/notes/zz-original.md"
    COPY = "marge/notes/aa-copy.md"
    TEXT = "---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Moe's opening hours\n---\n\nAlways.\n"

    def test_the_one_first_committed_later_gets_a_new_id(self, memory, forgejo, tmp_path):
        _push(memory, {self.ORIGINAL: self.TEXT}, "a note", when="2026-10-02T10:00:00")
        _push(memory, {self.COPY: self.TEXT}, "copy it", when="2026-10-03T10:00:00")
        give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        assert _card_id(memory, self.ORIGINAL) == "note-7f3a9c21e4"
        copied = _card_id(memory, self.COPY)
        assert re.fullmatch(r"note-[0-9a-f]{10}", copied) and copied != "note-7f3a9c21e4"
        assert (memory / self.COPY).read_text(encoding="utf-8") == \
            self.TEXT.replace("note-7f3a9c21e4", copied)

    def test_an_original_moved_to_a_new_name_keeps_its_age(self, memory, forgejo, tmp_path):
        """The archivist renames a document when its title changes; it is still the first."""
        moved = "marge/notes/zz-original-renamed.md"
        _push(memory, {self.ORIGINAL: self.TEXT}, "a note", when="2026-10-02T10:00:00")
        _push(memory, {self.COPY: self.TEXT}, "copy it", when="2026-10-03T10:00:00")
        _git(memory, "mv", self.ORIGINAL, moved)
        _git(memory, "commit", "-q", "-m", "rename", when="2026-10-04T10:00:00")
        give_card_ids(forgejo, _clone(memory, tmp_path, "run1"), trailer=TRAILER)
        assert _card_id(memory, moved) == "note-7f3a9c21e4"
        assert _card_id(memory, self.COPY) != "note-7f3a9c21e4"


# ── The curator, on a push ───────────────────────────────────────────────

class TestThePushedFiles:
    @pytest.fixture
    def migrated(self, memory, forgejo, tmp_path) -> str:
        give_card_ids(forgejo, _clone(memory, tmp_path, "migration"), trailer=TRAILER)
        return _git(memory, "rev-parse", "HEAD")

    def test_a_file_pushed_without_an_id_gets_one_and_nothing_else_does(
            self, memory, forgejo, httpserver, tmp_path, migrated):
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n\n- a reed\n",
                       ODD: VAULT[ODD] + "More salt.\n"}, "Marge's notes")
        found, sha = give_card_ids(forgejo, _clone(memory, tmp_path, "pull"), since=migrated)
        assert sorted(found.changes) == ["marge/lisa-birthday.md"]
        assert parse((memory / "marge/lisa-birthday.md").read_text())["type"] == "note"
        assert sha and _git(memory, "log", "-1", "--format=%an") == BOT_USERNAME
        assert "Migration:" not in _git(memory, "log", "-1", "--format=%B")

    def test_a_copied_note_gets_a_new_id_and_the_original_keeps_its(
            self, memory, forgejo, tmp_path, migrated):
        original = _card_id(memory, NOTE)
        _push(memory, {"marge/notes/copy.md": (memory / NOTE).read_text()}, "copy",
              when="2026-10-20T10:00:00")
        found, _ = give_card_ids(forgejo, _clone(memory, tmp_path, "pull"), since=migrated)
        assert sorted(found.changes) == ["marge/notes/copy.md"]
        assert _card_id(memory, NOTE) == original != _card_id(memory, "marge/notes/copy.md")

    def test_a_push_of_cards_that_have_their_ids_is_left_alone(
            self, memory, forgejo, httpserver, tmp_path, migrated):
        """The resting state: no commit, nothing to log."""
        edited = (memory / NOTE).read_text() + "One more brush.\n"
        head = _push(memory, {NOTE: edited}, "Marge edits her note")
        found, sha = give_card_ids(forgejo, _clone(memory, tmp_path, "pull"), since=migrated)
        assert (found.changes, found.left, sha) == ({}, [], None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head


class TestTheCuratorLoop:
    """`curator.give_ids_since`, what the curator runs after every pull."""

    @pytest.fixture
    def curator(self, monkeypatch, httpserver):
        sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot"))
        sys.path.insert(0, str(_REPO_ROOT / "stacklets" / "memory" / "bot" / "cli"))
        import curator

        monkeypatch.setenv("CODE_URL", httpserver.url_for("").rstrip("/"))
        monkeypatch.setenv("MATRIX_ADMIN_USER", "stackadmin")
        monkeypatch.setenv("MATRIX_ADMIN_PASSWORD", "secret")
        return curator

    def test_is_done_once_the_pushed_cards_have_their_ids(
            self, curator, memory, forgejo, tmp_path):
        since = _git(memory, "rev-parse", "HEAD")
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n"}, "Marge")
        assert curator.give_ids_since(_clone(memory, tmp_path, "pull"), since) is True
        assert re.fullmatch(r"note-[0-9a-f]{10}", _card_id(memory, "marge/lisa-birthday.md"))

    def test_tries_again_when_forgejo_refuses(self, curator, memory, forgejo, tmp_path):
        """An edit pushed after the pull: the same range is looked at next cycle."""
        since = _git(memory, "rev-parse", "HEAD")
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n"}, "Marge")
        stale = _clone(memory, tmp_path, "pull")
        _push(memory, {"marge/lisa-birthday.md": "# Ideas for Lisa's birthday\n\n- a reed\n"}, "again")
        assert curator.give_ids_since(stale, since) is False
