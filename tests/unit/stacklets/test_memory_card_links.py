"""Every record links the cards it names: migrations memory/0003 and memory/0004.

ADR-017 §3, §4 and §10: a record names its people by id and links each
person's card, links its correspondent's card, and a topic it is filed
into has a card of its own. The archivist files new records that way;
these two migrations bring the files that were there before to the same
shape, with the archivist's own functions (`stack.card_links`).

The memory repo here is a real git repository holding a small fabricated
vault, and Forgejo a real HTTP server (`pytest-httpserver`) that applies
each commit to it the way Forgejo would: it refuses a file whose blob
changed since it was read, and a new file that is there already. So a
second run reads what the first one wrote, as it would on an instance.
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

from lib import BOT_EMAIL, BOT_USERNAME, REPO_NAME, REPO_OWNER, declare_cards, name_people_by_id  # noqa: E402
from stack.forgejo import ForgejoClient  # noqa: E402
from stack.frontmatter import parse  # noqa: E402

CONTENTS = f"/api/v1/repos/{REPO_OWNER}/{REPO_NAME}/contents"

HOMER = "family/people/homer.md"
BART = "family/people/bart.md"
ABRAHAM = "family/people/abraham.md"
LETTER = "family/documents/2026/10/2026-10-09-springfield-elementary-school-notice-p72.md"
TAB = "family/documents/2026/10/2026-10-13-tab-p31.md"
NOTE = "family/itchy-scratchy-land/notes/2026/10/pick-up-grampa-0c37f8.md"
DIARY = "family/diary/entries/2024/03/2024-03-09-b48902f594.md"
LIST = "family/lists/education.md"
MOE = "family/correspondents/moe-s-tavern.md"
SCHOOL = "family/correspondents/springfield-elementary-school.md"
KRUSTY = "family/correspondents/krusty-burger.md"
TOPIC = "family/itchy-scratchy-land/topic.md"


def _person(title: str, pid: str, *aliases: str) -> str:
    names = "".join(f"  - {a}\n" for a in aliases)
    return (f"---\ntype: person\ncard_id: person-{pid}\ntitle: {title}\n"
            + (f"aliases:\n{names}" if names else "") + f"---\n\n# {title}\n")


VAULT = {
    HOMER: _person("Homer Simpson", "homer"),
    BART: _person("Bart Simpson", "bart"),
    ABRAHAM: _person("Abraham Simpson", "abraham", "Grampa"),
    # A school letter as the archivist filed it before cards: names in
    # `persons`, a `Person:` tag, links to the people's compiled pages.
    LETTER: ("---\ntype: document\ncard_id: document-671acc558d\ntitle: Springfield Elementary School Notice\n"
             "date: \"2026-10-09\"\ncorrespondent: Springfield Elementary School\npersons:\n  - Bart\n"
             "  - Mr. Burns\ntags:\n  - Education\n  - \"Person: Bart\"\npaperless_id: 72\nsource: paperless\n"
             "timestamp: \"2026-10-11T14:00:39Z\"\n---\n\n# Springfield Elementary School Notice\n\n"
             "> **From:** [Springfield Elementary School](../../../correspondents/springfield-elementary-school.md)"
             " · **About:** [Bart](../../../../bart/about.md), [Mr. Burns](../../../../mr/about.md)\n\n"
             "> [!summary]\n> A field trip. Bart's page is [here](../../../../bart/about.md).\n"),
    TAB: ("---\ntype: document\ncard_id: document-0b6e2d91aa\ntitle: Tab\ncorrespondent: Moe's Tavern\n"
          "paperless_id: 31\nsource: paperless\ntimestamp: \"2026-10-13T20:00:00Z\"\n---\n\n# Tab\n\n"
          "> **From:** [Moe's Tavern](../../../correspondents/moe-s-tavern.md)\n"),
    NOTE: ("---\ntype: note\ncard_id: note-7f3a9c21e4\ntitle: Pick up Grampa\npersons:\n  - Grampa\n"
           "tags:\n  - itchy-scratchy-land\n  - \"Person: Grampa\"\ntimestamp: \"2026-10-13T21:12:08Z\"\n---\n\n"
           "# Pick up Grampa\n\n> **About** [Grampa](../../../../../grampa/about.md)\n"
           "> **Captured** 2026-10-13 · **Kind** note\n\nBefore we leave.  \nTrailing spaces stay.\n"),
    # Signed with a digest: an edit from outside the compile would make it the family's.
    DIARY: ("---\ntype: diary\ncard_id: diary-3f9a0c1b2d\ntitle: Bart's first skateboard\ndate: \"2024-03-09\"\n"
            "persons:\n  - Bart\ntags:\n  - \"Person: Bart\"\nentry_id: b48902f594\n"
            "timestamp: \"2024-03-09T18:00:00.000Z\"\ndigest: 0123456789abcdef\n---\n\n# Bart's first skateboard\n"),
    # Items that link the letter under the title it had before a refile,
    # and one that names a document no file answers to.
    LIST: ("---\ntype: list\ncard_id: list-education\ntitle: Education\n---\n\n"
           "- [ ] Pay the trip fee [Permission Slip](../documents/2026/10/2026-10-09-permission-slip-p72.md)\n"
           "- [ ] Find the report card [Report Card](../documents/2026/06/2026-06-20-report-card-p99.md)\n"),
    # A correspondent the family wrote in the shape from before cards.
    KRUSTY: "---\nkind: correspondent\ncanonical: Krusty Burger\n---\n\nThe kids' favourite.\n",
    # One declared already: never rewritten.
    MOE: ("---\ntype: correspondent\ncard_id: correspondent-moe-s-tavern\ntitle: Moe's Tavern\n"
          "aliases:\n  - Moe's\n---\n\n# Moe's Tavern\n\nHomer's second home.\n"),
}


# ── The memory repo and Forgejo ──────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "Marge", "GIT_AUTHOR_EMAIL": "marge@example.org",
           "GIT_COMMITTER_NAME": "Marge", "GIT_COMMITTER_EMAIL": "marge@example.org"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


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


def _apply(memory: Path, files: list[dict], author: dict, message: str) -> Response:
    """One commit to `memory`, refused the way Forgejo refuses it."""
    for f in files:
        there = (memory / f["path"]).is_file()
        if f["operation"] == "create" and there:
            return Response(json.dumps({"message": "file exists"}), status=422)
        if f["operation"] == "update" and (
                not there or _git(memory, "hash-object", "--", f["path"]) != f.get("sha")):
            return Response(json.dumps({"message": "sha does not match"}), status=409)
    for f in files:
        (memory / f["path"]).parent.mkdir(parents=True, exist_ok=True)
        (memory / f["path"]).write_bytes(base64.b64decode(f["content"]))
    _git(memory, "add", "-A")
    _git(memory, "commit", "-q", f"--author={author['name']} <{author['email']}>",
         "-m", message)
    _git(memory, "update-server-info")
    return Response(json.dumps({"commit": {"sha": _git(memory, "rev-parse", "HEAD")}}),
                    content_type="application/json")


@pytest.fixture
def forgejo(httpserver, memory) -> ForgejoClient:
    """Forgejo's commit endpoints, applied to `memory`: several files in one
    commit, or one file created (POST) or updated (PUT) at its path."""
    memory = Path(memory)  # a test may hand it anything path-like

    def commit(request):
        body = json.loads(request.data)
        return _apply(memory, body["files"], body["author"], body["message"])

    def write_one(request):
        body = json.loads(request.data)
        path = request.path.removeprefix(f"{CONTENTS}/")
        operation = "update" if request.method == "PUT" else "create"
        return _apply(memory, [{"operation": operation, "path": path, **body}],
                      body["author"], body["message"])

    def clone(request):
        """Git's plain HTTP protocol: the repository's files, as they are."""
        file = memory / ".git" / request.path.removeprefix(f"/{REPO_OWNER}/{REPO_NAME}.git/")
        if not file.is_file():
            return Response("", status=404)
        return Response(file.read_bytes(), content_type="application/octet-stream")

    httpserver.expect_request(CONTENTS, method="POST").respond_with_handler(commit)
    for method in ("POST", "PUT"):
        httpserver.expect_request(re.compile(rf"^{re.escape(CONTENTS)}/.+"),
                                  method=method).respond_with_handler(write_one)
    httpserver.expect_request(re.compile(rf"^/{REPO_OWNER}/{REPO_NAME}\.git/.*"),
                              method="GET").respond_with_handler(clone)
    return ForgejoClient(url=httpserver.url_for(""), token="bottoken")


@pytest.fixture
def contents(httpserver, memory) -> None:
    """Forgejo's contents API for reading: a directory's entries, or a file
    with its blob sha, which an update has to name."""
    memory = Path(memory)

    def read(request):
        path = request.path.removeprefix(f"{CONTENTS}/")
        target = memory / path
        if target.is_dir():
            return Response(json.dumps([
                {"type": "dir" if p.is_dir() else "file", "path": f"{path}/{p.name}", "name": p.name}
                for p in sorted(target.iterdir()) if p.name != ".git"]),
                content_type="application/json")
        if target.is_file():
            return Response(json.dumps({
                "type": "file", "path": path, "sha": _git(memory, "hash-object", "--", path),
                "content": base64.b64encode(target.read_bytes()).decode()}),
                content_type="application/json")
        return Response(json.dumps({"message": "not found"}), status=404)

    httpserver.expect_request(re.compile(rf"^{re.escape(CONTENTS)}/.+"),
                              method="GET").respond_with_handler(read)


def _clone(memory: Path, tmp_path: Path, name: str) -> Path:
    """A fresh clone, as a migration reads the repo."""
    target = tmp_path / name
    subprocess.run(["git", "clone", "-q", str(memory), str(target)], check=True)
    return target


def _read(memory: Path, path: str) -> str:
    return (memory / path).read_text(encoding="utf-8")


def _commits(httpserver) -> int:
    """How many commits Forgejo was asked to make."""
    return sum(1 for r, _ in httpserver.log if r.method in ("POST", "PUT"))


# ── Migration 0003: people by id ─────────────────────────────────────────

class TestPeopleByIdOnce:
    """The first run: one commit by the memory bot, with the trailer."""

    TRAILER = "Migration: memory/0003"

    @pytest.fixture
    def found(self, memory, forgejo, tmp_path):
        found, sha = name_people_by_id(forgejo, _clone(memory, tmp_path, "run1"), trailer=self.TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory, httpserver):
        assert _commits(httpserver) == 1
        assert _git(memory, "log", "-1", "--format=%an <%ae>|%B").strip() == \
            f"{BOT_USERNAME} <{BOT_EMAIL}>|cards: name people by id in 3 card(s)\n\n{self.TRAILER}"

    def test_a_name_a_person_goes_by_becomes_their_id(self, found, memory):
        assert parse(_read(memory, LETTER))["persons"] == ["bart", "Mr. Burns"]
        assert parse(_read(memory, NOTE))["persons"] == ["abraham"]  # Grampa is Abraham

    def test_a_link_to_a_compiled_page_links_the_persons_card(self, found, memory):
        assert "> **About** [Grampa](../../../../people/abraham.md)\n" in _read(memory, NOTE)
        assert " · **About:** [Bart](../../../people/bart.md), " in _read(memory, LETTER)

    def test_person_tags_go(self, found, memory):
        assert parse(_read(memory, LETTER))["tags"] == ["Education"]
        assert parse(_read(memory, NOTE))["tags"] == ["itchy-scratchy-land"]

    def test_a_name_nobody_goes_by_stays_with_its_link_and_is_reported(self, found, memory):
        assert "[Mr. Burns](../../../../mr/about.md)" in _read(memory, LETTER)
        assert f"{LETTER}: no declared person goes by 'Mr. Burns'" in found.left

    def test_a_link_to_a_moved_document_follows_it_by_its_paperless_id(self, found, memory):
        assert ("[Permission Slip](../documents/2026/10/"
                "2026-10-09-springfield-elementary-school-notice-p72.md)") in _read(memory, LIST)

    def test_a_link_no_one_document_answers_is_reported_and_kept(self, found, memory):
        assert "(../documents/2026/06/2026-06-20-report-card-p99.md)" in _read(memory, LIST)
        assert any(line.startswith(f"{LIST}: ../documents/2026/06/") for line in found.left)

    def test_only_the_lines_it_is_about_change(self, found, memory):
        # Outside the meta block a link to a compiled page is the family's words.
        assert "Bart's page is [here](../../../../bart/about.md)." in _read(memory, LETTER)
        assert _read(memory, NOTE).endswith("\n\nBefore we leave.  \nTrailing spaces stay.\n")
        for path in (DIARY, TAB, MOE, KRUSTY, HOMER):
            assert _read(memory, path) == VAULT[path], path

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, forgejo,
                                                          httpserver, tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = name_people_by_id(forgejo, _clone(memory, tmp_path, "run2"), trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head


# ── Migration 0004: correspondents and topics ────────────────────────────

class TestDeclareCardsOnce:
    TRAILER = "Migration: memory/0004"

    @pytest.fixture
    def found(self, memory, forgejo, tmp_path):
        found, sha = declare_cards(forgejo, _clone(memory, tmp_path, "run1"), trailer=self.TRAILER)
        assert sha == _git(memory, "rev-parse", "HEAD")
        return found

    def test_one_commit_by_the_memory_bot(self, found, memory, httpserver):
        assert _commits(httpserver) == 1
        assert _git(memory, "log", "-1", "--format=%an <%ae>|%B").strip() == \
            f"{BOT_USERNAME} <{BOT_EMAIL}>|cards: declare 3 correspondent and topic card(s)\n\n{self.TRAILER}"

    def test_a_correspondent_a_document_names_gets_its_card(self, found, memory):
        assert parse(_read(memory, SCHOOL)) == {
            "type": "correspondent", "card_id": "correspondent-springfield-elementary-school",
            "title": "Springfield Elementary School"}

    def test_a_topic_folder_gets_its_card(self, found, memory):
        assert parse(_read(memory, TOPIC)) == {
            "type": "topic", "card_id": "topic-itchy-scratchy-land", "title": "Itchy Scratchy Land"}

    def test_a_declared_correspondent_is_never_rewritten(self, found, memory):
        assert _read(memory, MOE) == VAULT[MOE]

    def test_an_old_correspondent_file_gets_its_type_and_id_and_nothing_else(self, found, memory):
        assert _read(memory, KRUSTY) == ("---\ntype: correspondent\ncard_id: correspondent-krusty-burger\n"
                                         + VAULT[KRUSTY][len("---\n"):])

    def test_run_again_changes_nothing_and_makes_no_commit(self, found, memory, forgejo,
                                                          httpserver, tmp_path):
        head = _git(memory, "rev-parse", "HEAD")
        again, sha = declare_cards(forgejo, _clone(memory, tmp_path, "run2"), trailer=self.TRAILER)
        assert (again.changes, sha) == ({}, None)
        assert _commits(httpserver) == 1 and _git(memory, "rev-parse", "HEAD") == head


class TestStackUpMemory:
    """Both migrations, run by the framework's runner the way `stack up memory`
    runs it: each reads a fresh clone over HTTP and makes one commit."""

    def test_each_runs_once_and_records_its_commit(self, memory, forgejo, tmp_path, httpserver):
        from stack import Stack
        from stack.migrations import MigrationContext, Migrations
        from stack.output import CollectorOutput

        # 0001 finds no old todo lists to move; 0002 finds every card with an id.
        httpserver.expect_request(f"{CONTENTS}/family", method="GET").respond_with_json([])
        stck = Stack(root=tmp_path, data=tmp_path / "data", output=CollectorOutput())
        stck.secrets.set("memory", "MEMORY_BOT_TOKEN", "bottoken")
        migrations = Migrations("memory", _REPO_ROOT / "stacklets" / "memory",
                                tmp_path / "data" / "memory")
        outcome = migrations.run(
            lambda m: MigrationContext(m, stack=stck, stacklet_id="memory",
                                       env={"CODE_URL": httpserver.url_for("")}),
            lambda msg: None)

        assert outcome["applied"][:4] == ["memory/0001", "memory/0002", "memory/0003", "memory/0004"]
        log = _git(memory, "log", "--format=%H %(trailers:key=Migration,valueonly)").splitlines()
        commits = dict(reversed(line.split()) for line in log if len(line.split()) == 2)
        record = migrations.record().applied
        assert record["0003"]["commit"] == commits["memory/0003"]
        assert record["0004"]["commit"] == commits["memory/0004"]
        assert parse(_read(memory, NOTE))["persons"] == ["abraham"]
        assert (memory / TOPIC).is_file()


def test_a_card_that_appeared_meanwhile_makes_forgejo_refuse_the_commit(memory, forgejo, tmp_path):
    """Nothing is written over it; the next `stack up memory` reads again."""
    from stack.forgejo import ForgejoError

    stale = _clone(memory, tmp_path, "stale")
    (memory / TOPIC).write_text("---\ntype: topic\ntitle: Itchy & Scratchy Land\n---\n", encoding="utf-8")
    _git(memory, "add", "-A")
    _git(memory, "commit", "-q", "-m", "Marge declared the topic")
    with pytest.raises(ForgejoError):
        declare_cards(forgejo, stale)
    assert re.search(r"Itchy & Scratchy", _read(memory, TOPIC))
