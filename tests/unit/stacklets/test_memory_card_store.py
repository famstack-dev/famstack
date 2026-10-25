"""The card store: the one writer of the family memory, as its callers see it.

The memory is a git repository. Forgejo holds it, the curator holds a clone
of it on the data disk, and every write goes through the store, which turns
it into one commit in that clone and pushes it to Forgejo before it answers
(ADR-019 §3, §4). These tests are the store's contract: what a caller gives
it, what it answers, and what the memory looks like afterwards.

Real git, no stand-ins. Forgejo is a bare repository in a temporary
directory, which is all Forgejo is to git; the curator's clone is a clone of
it; Marge editing a card in Forgejo's web editor is a second clone that
commits and pushes. What a test asserts is what git shows afterwards in
Forgejo and in the clone: who made a commit, what it says, what the files
hold. Nothing inside the store is patched or asserted.

The data is the Simpsons'.
"""

from __future__ import annotations

import contextlib
import re
import socket
import threading
import time
from pathlib import Path

import pytest

from stacklets.memory.cards import CardChanged, CardStore, CloneMissing, Put, Refused, Remove, StoreError

from memory_git import Forgejo, clone_of, commit_as, git, version_of
from stack.http_json import TIMEOUT_S

LIST = "family/lists/camping.md"
SIDECAR = "family/notes/2026/10/tent.json"
NOTE = "family/notes/2026/10/tent.md"

CAMPING = """---
type: list
card_id: list-camping
title: Camping
---

# Camping

- [ ] Tent
- [x] Marshmallows
"""

TENT_FOR_FOUR = CAMPING.replace("- [ ] Tent", "- [ ] Tent for four")


@pytest.fixture
def forgejo(tmp_path) -> Forgejo:
    """family/memory, holding Homer's camping list."""
    forgejo = Forgejo(tmp_path)
    forgejo.seed("homer", "chore(lists): homer added Camping", {LIST: CAMPING})
    return forgejo


@pytest.fixture
def clone(forgejo, tmp_path) -> Path:
    """The curator's clone on the data disk."""
    (tmp_path / "curator").mkdir()
    return clone_of(forgejo, tmp_path / "curator" / "vault")


@pytest.fixture
def store(clone) -> CardStore:
    return CardStore(clone, remote="origin", server_name="simpson")


@pytest.fixture
def marge(forgejo, tmp_path) -> Path:
    """Marge's own clone, standing in for her editing in Forgejo's web editor."""
    return clone_of(forgejo, tmp_path / "marge")


# ── Reading ──────────────────────────────────────────────────────────────

def test_a_file_reads_as_the_last_commit_left_it_with_its_version(store):
    """Given Homer's camping list, when the store reads it, then it has
    the list's text and its version is the id Forgejo gives the file.

    A writer names the version it read, so the store can tell whether the
    file changed in the meantime; Forgejo's id and the store's agree, so
    a version read from either is good for a write.
    """
    camping = store.read(LIST)

    assert camping is not None
    assert (camping.path, camping.text) == (LIST, CAMPING)
    assert camping.version == version_of(CAMPING)


def test_a_file_that_is_not_there_reads_as_nothing(store):
    """Given no such file, when the store reads it, then it answers None."""
    assert store.read("family/lists/groceries.md") is None


def test_a_directory_reads_as_every_file_under_it_with_its_version(store, forgejo, marge):
    """Given the camping list, its archive and a list of another bucket, when
    the store reads the bucket's lists, then it has every file under that
    directory, at any depth, each with its text and version, by path, and
    nothing from outside it.

    The nightly archive and the diary read a whole directory to decide what
    to write; each file's version is what that write is then based on.
    """
    archive = "family/lists/archive/camping/2026-09.md"
    commit_as(marge, "marge", "chore(lists): marge archived", {
        archive: "- [x] Matches\n", "marge/lists/books.md": "- [ ] Dune\n"})
    store.sync()

    found = store.files("family/lists")

    assert [(f.path, f.text, f.version) for f in found] == [
        (archive, "- [x] Matches\n", version_of("- [x] Matches\n")),
        (LIST, CAMPING, version_of(CAMPING))]
    assert [f.path for f in store.files()] == [archive, LIST, "marge/lists/books.md"]


def test_the_paths_under_a_directory_or_in_the_whole_memory_are_listed(store, marge):
    """Given the camping list and a list of Marge's, when the store lists a
    directory, then it has the path of every file under it, by path; with
    no directory, every file of the memory.

    The archivist finds the card it filed for a document, or the capture a
    message was filed as before, by its file name: a listing of the paths
    is all it needs, without every file's text.
    """
    commit_as(marge, "marge", "chore(lists): marge added Books", {"marge/lists/books.md": "- [ ] Dune\n"})
    store.sync()

    assert store.paths("family") == [LIST]
    assert store.paths() == [LIST, "marge/lists/books.md"]


def test_a_directory_that_is_not_there_reads_as_no_files(store):
    """Given no lists in Bart's bucket, when the store reads them, then it
    answers no files: an empty bucket is no error."""
    assert store.files("bart/lists") == []
    assert store.paths("bart") == []


@pytest.mark.parametrize("under", ["/family", "family/../bart", "family/.git"])
def test_a_directory_outside_the_memory_is_refused(store, under):
    with pytest.raises(Refused, match="not a directory of the memory"):
        store.files(under)
    with pytest.raises(Refused, match="not a directory of the memory"):
        store.paths(under)


def test_a_card_is_found_by_its_id_or_one_it_had_before(store):
    """Given Homer's camping list, whose id was `list-campen` before it was
    renamed, when it is looked up by either id, then it is the list, with
    its path, text, version and the id it carries now.

    Links and chat answers written before the rename still name the old
    id; they must reach the card.
    """
    renamed = CAMPING.replace("card_id: list-camping\n", "card_id: list-camping\nformer_card_ids:\n  - list-campen\n")
    store.commit([Put(LIST, renamed, base=version_of(CAMPING))], by="marge", kind="chore(lists)",
                 title="Camping")

    by_now, by_before = store.card("list-camping"), store.card("list-campen")

    assert by_now == by_before
    assert (by_now.path, by_now.text, by_now.card_id) == (LIST, renamed, "list-camping")
    assert by_now.version == version_of(renamed)
    assert store.card("list-groceries") is None


def test_a_later_commit_includes_an_earlier_one_and_not_the_other_way(store):
    """Given two writes, then the second includes the first, the first does
    not include the second, and a commit the clone never had is in neither.

    The curator answers whether a write reached the trees people read by
    asking whether what it mirrored includes the write.
    """
    first = store.commit([Put(LIST, TENT_FOR_FOUR, base=version_of(CAMPING))], by="marge",
                         kind="chore(lists)", title="Camping").sha
    second = store.commit([Put(NOTE, "# Tent\n", base="")], by="homer", via="archivist-bot",
                          kind="capture", title="Tent").sha

    assert store.includes(first, later=second)
    assert not store.includes(second, later=first)
    assert not store.includes("0" * 40, later=second)
    assert not store.includes(first, later="")


def test_reading_from_a_missing_clone_says_how_to_make_it(tmp_path):
    """Given no clone yet, when a file is read, then the store says what is
    missing, instead of a file that is not there: the card exists, the
    curator just cannot see it yet."""
    store = CardStore(tmp_path / "nowhere", remote="origin", server_name="simpson")

    with pytest.raises(CloneMissing, match="stack up memory"):
        store.read(LIST)
    with pytest.raises(CloneMissing, match="stack up memory"):
        store.card("list-camping")
    with pytest.raises(CloneMissing, match="stack up memory"):
        store.files("family/lists")
    with pytest.raises(CloneMissing, match="stack up memory"):
        store.paths()


def test_an_empty_memory_reads_as_empty_and_takes_its_first_commit(tmp_path):
    """Given a memory repository Forgejo has just created, with no commit
    yet, when the curator reads it and then writes the seed, then it reads
    as no files, and the write is its first commit, in Forgejo too.

    A new instance's memory starts out empty: the curator clones it and
    gives it its seed as one commit, as it makes every other.
    """
    forgejo = Forgejo(tmp_path)
    (tmp_path / "curator").mkdir()
    store = CardStore(clone_of(forgejo, tmp_path / "curator" / "vault"), remote="origin",
                      server_name="simpson")

    assert (store.read(LIST), store.files("family"), store.paths(), store.card("list-camping")) == \
        (None, [], [], None)
    assert (store.sync().state, store.pending()) == ("ok", ())
    written = store.commit([Put(LIST, CAMPING, base=""), Put("README.md", "# Memory\n", base="")],
                           by="memory-bot", kind="seed", title="initial memory from famstack")

    assert written.changed and not written.pending
    assert forgejo.file(LIST) == CAMPING
    assert git(forgejo.repo, "log", "--format=%s", "main") == "seed: initial memory from famstack"


def test_a_seed_written_while_forgejo_is_down_waits_and_is_pushed_once_it_is_back(tmp_path):
    forgejo = Forgejo(tmp_path)
    (tmp_path / "curator").mkdir()
    store = CardStore(clone_of(forgejo, tmp_path / "curator" / "vault"), remote="origin",
                      server_name="simpson")
    forgejo.goes_down()

    written = store.commit([Put("README.md", "# Memory\n", base="")],
                           by="memory-bot", kind="seed", title="initial memory from famstack")

    assert written.pending
    assert [p.subject for p in store.pending()] == ["seed: initial memory from famstack"]
    forgejo.comes_back()
    assert store.sync().state == "ok" and store.pending() == ()
    assert forgejo.file("README.md") == "# Memory\n"


# ── Writing ──────────────────────────────────────────────────────────────

def test_a_write_is_one_commit_by_the_person_through_the_bot(store, forgejo, clone):
    """Given Homer's camping list, when Marge rewords an item through the
    memory bot, then Forgejo has exactly one new commit, authored by Marge
    and committed by the memory bot, whose subject says what changed, and
    both Forgejo and the curator's clone hold her text.

    The history is read by the family and by the agent: a commit that
    names its author and its change is what makes it answerable (ADR-015).
    """
    camping = store.read(LIST)

    written = store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                           by="marge", kind="chore(lists)", title="marge changed Camping")

    assert forgejo.new_commits() == [
        "marge <marge@simpson> | memory-bot <memory-bot@local> | chore(lists): marge changed Camping"]
    assert forgejo.file(LIST) == TENT_FOR_FOUR
    assert (clone / LIST).read_text(encoding="utf-8") == TENT_FOR_FOUR
    assert written.sha == git(forgejo.repo, "rev-parse", "main")
    assert written.changed and not written.pending


def test_the_message_is_the_subject_then_the_body_then_the_trailers(store, forgejo):
    """Given a write with a body and trailers, when it is committed, then
    the message reads subject, blank line, body, blank line, trailers.

    The release of a filing is read in this shape: the subject places the
    commit in the history, the trailers say where it came from.
    """
    camping = store.read(LIST)

    store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                 by="marge", kind="chore(lists)", title="marge changed Camping",
                 body="Tent -> Tent for four", trailers={"Source": "chat", "Model": "small"})

    assert forgejo.message() == ("chore(lists): marge changed Camping\n\nTent -> Tent for four\n\n"
                                 "Source: chat\nModel: small")


def test_the_committer_is_the_bot_that_wrote_the_change(store, forgejo):
    """Given a note the archivist files for Homer, when it is committed,
    then Homer is the author and the archivist the committer.

    Readers tell a person's change from the machine's by both names
    (`is_machine`): the archivist's filing for Homer is Homer's, while the
    memory bot writing `update:` for him is a rebuild.
    """
    store.commit([Put(NOTE, "# Tent\n", base="")],
                 by="homer", via="archivist-bot", kind="capture", title="Tent")

    assert forgejo.new_commits() == [
        "homer <homer@simpson> | archivist-bot <archivist-bot@local> | capture: Tent"]


def test_a_change_of_a_bots_own_is_by_its_account(store, forgejo):
    """Given a topic card the archivist declares on its own, when it is
    committed, then the archivist is author and committer, under its own
    account's address: no bot is a person of the household."""
    store.commit([Put("family/camping/topic.md", "# Camping\n", base="")],
                 by="archivist-bot", via="archivist-bot", kind="declare", title="Camping")

    assert forgejo.new_commits() == [
        "archivist-bot <archivist-bot@local> | archivist-bot <archivist-bot@local> | declare: Camping"]


def test_a_card_and_its_sidecar_change_in_one_commit(store, forgejo):
    """Given a new note, when its card and its sidecar are written
    together, then they arrive in one commit.

    A note is two files (ADR-018): neither may change without the other.
    """
    store.commit([Put(NOTE, "# Tent\n", base=""), Put(SIDECAR, '{"sources": []}\n', base="")],
                 by="homer", via="archivist-bot", kind="capture", title="Tent")

    assert len(forgejo.new_commits()) == 1
    assert forgejo.file(NOTE) == "# Tent\n"
    assert forgejo.file(SIDECAR) == '{"sources": []}\n'


def test_a_move_is_one_commit_that_removes_the_old_path(store, forgejo):
    """Given Homer's camping list, when it is moved to the archive, then
    one commit removes it at the old path and writes it at the new one.

    One commit is what lets a card's history follow it across the move.
    """
    camping = store.read(LIST)
    archived = "family/lists/archive/camping.md"

    store.commit([Remove(LIST, base=camping.version), Put(archived, CAMPING, base="")],
                 by="memory-bot", kind="rename", title="Camping")

    assert len(forgejo.new_commits()) == 1
    assert forgejo.file(LIST) is None
    assert forgejo.file(archived) == CAMPING


def test_writing_what_is_there_already_commits_nothing(store, forgejo):
    """Given Homer's camping list, when the same text is written again,
    then nothing is committed and the store says nothing changed.

    The agent retries a write it is not sure of; a retry must not fill the
    history with empty commits.
    """
    camping = store.read(LIST)

    written = store.commit([Put(LIST, CAMPING, base=camping.version)],
                           by="marge", kind="chore(lists)", title="marge changed Camping")

    assert forgejo.new_commits() == []
    assert not written.changed and written.sha == ""


def test_a_write_on_an_old_version_changes_nothing_and_names_the_file(store, forgejo, marge):
    """Given Homer's camping list read by the agent, when Marge changes it
    in Forgejo and the agent then writes its version, then nothing of the
    agent's is written, Marge's change stands, and the agent is told which
    file changed under it.

    A write must never undo a change it did not see: Marge's edit would be
    lost without a trace.
    """
    read_by_agent = store.read(LIST)
    commit_as(marge, "marge", "Tent is torn", {LIST: CAMPING.replace("Tent", "Torn tent")})

    with pytest.raises(CardChanged) as changed:
        store.commit([Put(LIST, TENT_FOR_FOUR, base=read_by_agent.version)],
                     by="homer", kind="chore(lists)", title="homer changed Camping")

    assert changed.value.paths == (LIST,)
    assert LIST in str(changed.value) and "read it again" in str(changed.value)
    assert [c.split(" | ")[2] for c in forgejo.new_commits()] == ["Tent is torn"]
    assert forgejo.file(LIST) == CAMPING.replace("Tent", "Torn tent")


def test_a_file_with_windows_line_endings_is_written_again_on_the_version_it_was_read_at(
        store, forgejo):
    """Given an email card whose quoted lines end in CR LF, as plain-text
    mail and a Windows editor write them, when it is read and written again
    on that version, then the write goes in and Forgejo holds the bytes as
    written. A version is the blob id of the file's bytes: a store that read
    the file with its line endings changed would refuse every later write
    to it as changed."""
    thread = "family/emails/kwik-e-mart.md"
    first = "# Kwik-E-Mart\n\n> Your order is ready.\r\n> Thank you, come again.\r\n"
    store.commit([Put(thread, first, base="")],
                 by="archivist-bot", via="archivist-bot", kind="capture", title="Kwik-E-Mart")

    found = store.read(thread)
    store.commit([Put(thread, found.text + "> Picked up.\r\n", base=found.version)],
                 by="archivist-bot", via="archivist-bot", kind="update", title="Kwik-E-Mart")

    assert found.text == first
    assert forgejo.file(thread) == first + "> Picked up.\r\n"


def test_a_new_file_where_one_already_is_counts_as_changed(store):
    """Given Homer's camping list, when a write creates a file at its path,
    then it is refused as changed: the writer did not know it was there."""
    with pytest.raises(CardChanged):
        store.commit([Put(LIST, TENT_FOR_FOUR, base="")],
                     by="marge", kind="chore(lists)", title="marge added Camping")


def test_a_change_made_in_forgejo_meanwhile_is_kept_beside_the_write(store, forgejo, marge, clone):
    """Given Marge adding a groceries list in Forgejo since the curator last
    pulled, when Homer's change to the camping list is written, then
    Forgejo has both, and so has the curator's clone.

    The family edits in Forgejo too; a write is made on top of what is
    there, not on top of what the curator saw last.
    """
    commit_as(marge, "marge", "add groceries", {"family/lists/groceries.md": "# Groceries\n"})
    camping = store.read(LIST)

    store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                 by="homer", kind="chore(lists)", title="homer changed Camping")

    assert [c.split(" | ")[2] for c in forgejo.new_commits()] == [
        "add groceries", "chore(lists): homer changed Camping"]
    assert (clone / "family/lists/groceries.md").read_text(encoding="utf-8") == "# Groceries\n"


# ── Refusals, before anything is written ─────────────────────────────────

def test_a_kind_outside_the_commit_grammar_is_refused(store, forgejo):
    """Given a write of a kind the memory's history cannot place, when it is
    committed, then it is refused and nothing is written.

    A subject outside the grammar reads as a person's hand edit (ADR-018 §6).
    """
    with pytest.raises(Refused, match="tidy"):
        store.commit([Put(LIST, TENT_FOR_FOUR, base=version_of(CAMPING))],
                     by="marge", kind="tidy", title="Camping")

    assert forgejo.new_commits() == []


@pytest.mark.parametrize("path", ["../outside.md", "/etc/passwd", ".git/config", "", "family/../../x.md"])
def test_a_path_outside_the_memory_is_refused(store, forgejo, path):
    """Given a path that is not a file of the memory, when a write names
    it, then it is refused and nothing is written.

    The write API takes paths from the network; none of them may reach
    outside the repository or into git's own files.
    """
    with pytest.raises(Refused):
        store.commit([Put(path, "x\n", base="")], by="marge", kind="chore(memory)", title="x")

    assert forgejo.new_commits() == []


def test_a_person_needs_the_server_name_to_be_named(clone, forgejo):
    """Given a store with no Matrix server name, when Marge's change is
    written, then it is refused with the setting that is missing.

    An address made up instead would leave her commits under two
    addresses for good: the history is never rewritten.
    """
    store = CardStore(clone, remote="origin", server_name="")

    with pytest.raises(Refused, match="server_name"):
        store.commit([Put(LIST, TENT_FOR_FOUR, base=version_of(CAMPING))],
                     by="marge", kind="chore(lists)", title="marge changed Camping")

    assert forgejo.new_commits() == []


def test_a_change_that_is_neither_a_put_nor_a_remove_is_refused(store, forgejo):
    """Given something in a write's changes that is no `Put` and no
    `Remove`, when it is committed, then it is refused and nothing is
    written: an unknown change must never be taken for a removal."""
    class Rename:
        path, base = LIST, version_of(CAMPING)

    with pytest.raises(Refused):
        store.commit([Rename()], by="marge", kind="rename", title="Camping")

    assert forgejo.new_commits() == []
    assert forgejo.file(LIST) == CAMPING


def test_only_a_bot_account_commits(store, forgejo):
    """Given a write whose committer is a person, when it is committed, then
    it is refused: the committer is the account that wrote the change, and
    people do not write through the store themselves."""
    with pytest.raises(Refused, match="bot"):
        store.commit([Put(LIST, TENT_FOR_FOUR, base=version_of(CAMPING))],
                     by="marge", via="marge", kind="chore(lists)", title="Camping")

    assert forgejo.new_commits() == []


def test_a_file_changed_in_the_clone_but_never_committed_is_not_overwritten(store, clone, forgejo):
    """Given someone's edit of the camping list made in the curator's clone
    on disk and never committed, when a write of that list arrives, then it
    is refused naming the file, and the edit is still there.

    The clone is the curator's, and edits belong in Forgejo; but an edit
    made in it by hand is somebody's work, and a write must not destroy it
    without a trace.
    """
    (clone / LIST).write_text(CAMPING + "- [ ] a hand edit\n", encoding="utf-8")

    with pytest.raises(Refused, match="never committed") as refused:
        store.commit([Put(LIST, TENT_FOR_FOUR, base=version_of(CAMPING))],
                     by="marge", kind="chore(lists)", title="marge changed Camping")

    assert LIST in str(refused.value)
    assert (clone / LIST).read_text(encoding="utf-8") == CAMPING + "- [ ] a hand edit\n"
    assert forgejo.new_commits() == []


def test_a_missing_clone_says_how_to_make_it(tmp_path):
    """Given no clone on the data disk yet, when a write arrives, then the
    store says what is missing and which command makes it."""
    store = CardStore(tmp_path / "nowhere", remote="origin", server_name="simpson")

    with pytest.raises(CloneMissing, match="stack up memory"):
        store.commit([Put(LIST, CAMPING, base="")], by="marge", kind="chore(lists)", title="x")


def test_a_failed_commit_leaves_the_clone_as_it_was(store, clone, forgejo):
    """Given another git process holding the clone's index, when a write
    arrives, then it fails with a sentence a person can act on, and the
    clone's files are as they were; once the index is free, the same write
    goes through.

    A write that stops halfway must not leave a half-written card behind
    for the next reader, or for the next write to commit by accident.
    """
    lock = clone / ".git" / "index.lock"
    lock.write_text("")
    camping = store.read(LIST)

    with pytest.raises(StoreError, match="another git process") as failed:
        store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version), Put(NOTE, "# Tent\n", base="")],
                     by="marge", kind="chore(lists)", title="marge changed Camping")

    assert not isinstance(failed.value, CardChanged)
    assert (clone / LIST).read_text(encoding="utf-8") == CAMPING
    assert not (clone / NOTE).exists()
    assert git(clone, "status", "--porcelain") == ""
    lock.unlink()
    store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                 by="marge", kind="chore(lists)", title="marge changed Camping")
    assert forgejo.file(LIST) == TENT_FOR_FOUR


# ── Reaching Forgejo ─────────────────────────────────────────────────────
#
# A write is pushed before the store answers. When Forgejo is down or
# refuses, the commit waits in the clone on the data disk and the curator's
# cycle (`sync`) pushes it. Nothing is ever forced, and the clone is never
# reset: it may hold the only copy of a write.

def refuse_pushes(forgejo: Forgejo, reason: str = "Forgejo is read-only for a moment") -> Path:
    """A pre-receive hook that refuses every push, the way Forgejo refuses
    one while it is busy or read-only."""
    hook = forgejo.repo / "hooks" / "pre-receive"
    hook.write_text(f"#!/bin/sh\necho '{reason}' >&2\nexit 1\n")
    hook.chmod(0o755)
    return hook


def overtake_next_push(forgejo: Forgejo, marge: Path, files: dict[str, str], subject: str) -> None:
    """Marge's change, pushed to Forgejo while the store's own push is on
    its way: the next push Forgejo receives finds her commit there first."""
    git(marge, "pull", "-q", "--ff-only")
    for path, text in files.items():
        (marge / path).parent.mkdir(parents=True, exist_ok=True)
        (marge / path).write_text(text, encoding="utf-8")
    git(marge, "add", "-A")
    git(marge, "-c", "user.name=marge", "-c", "user.email=marge@simpson", "commit", "-q", "-m", subject)
    hook = forgejo.repo / "hooks" / "pre-receive"
    done = forgejo.repo / "overtaken"
    hook.write_text(f"""#!/bin/sh
[ -e '{done}' ] && exit 0
touch '{done}'
env -i PATH="$PATH" HOME="$HOME" git -C '{marge}' push -q origin HEAD:main >&2
exit 0
""")
    hook.chmod(0o755)


def subjects(forgejo: Forgejo) -> list[str]:
    return [c.split(" | ")[2] for c in forgejo.new_commits()]


def test_when_forgejo_is_down_a_write_is_kept_and_waits(store, forgejo, clone):
    """Given Forgejo down, when Marge's change is written, then it is in the
    curator's clone, the store says it is pending, and it is listed as
    waiting.

    The family's chat does not stop when the code stacklet restarts: the
    change is on the data disk, and the wiki and the agent read it from
    there already.
    """
    camping = store.read(LIST)
    forgejo.goes_down()

    written = store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                           by="marge", kind="chore(lists)", title="marge changed Camping")

    assert written.changed and written.pending
    assert (clone / LIST).read_text(encoding="utf-8") == TENT_FOR_FOUR
    assert [p.subject for p in store.pending()] == ["chore(lists): marge changed Camping"]


def test_with_forgejo_not_answering_a_write_is_answered_before_its_caller_gives_up(store, clone):
    """Given Forgejo taking connections and answering none, as while it
    restarts, when a change is written, then it is committed and answered
    pending before a caller stops waiting (`stack.http_json.TIMEOUT_S`). A
    caller that hears nothing reports a failure, and a filing sent again
    is a duplicate. Half the wait: the curator's own sync may hold the
    clone as long first."""
    with _forgejo_not_answering() as url:
        git(clone, "remote", "set-url", "origin", url)
        started = time.monotonic()
        written = store.commit([Put(LIST, TENT_FOR_FOUR, base=version_of(CAMPING))],
                               by="marge", kind="chore(lists)", title="marge changed Camping")
        took = time.monotonic() - started

    assert written.sha and written.pending
    assert took < TIMEOUT_S / 2


@contextlib.contextmanager
def _forgejo_not_answering():
    """An address that takes every connection and never answers on it."""
    listener = socket.create_server(("127.0.0.1", 0))
    held: list[socket.socket] = []
    stop = threading.Event()

    def take():
        listener.settimeout(0.1)
        while not stop.is_set():
            try:
                held.append(listener.accept()[0])
            except TimeoutError:
                continue

    taker = threading.Thread(target=take, daemon=True)
    taker.start()
    try:
        yield f"http://127.0.0.1:{listener.getsockname()[1]}/family/memory.git"
    finally:
        stop.set()
        taker.join()
        for connection in held:
            connection.close()
        listener.close()


def test_the_next_sync_pushes_what_waited(store, forgejo):
    """Given a write that waited while Forgejo was down, when Forgejo is
    back and the curator's cycle runs, then Forgejo has it and nothing
    waits any more."""
    camping = store.read(LIST)
    forgejo.goes_down()
    store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                 by="marge", kind="chore(lists)", title="marge changed Camping")
    forgejo.comes_back()

    synced = store.sync()

    assert synced.state == "ok" and synced.pending == ()
    assert subjects(forgejo) == ["chore(lists): marge changed Camping"]
    assert store.pending() == ()


def test_a_push_forgejo_refuses_waits_until_it_is_taken(store, forgejo):
    """Given Forgejo refusing pushes, when a change is written, then it
    waits, and the cycle says Forgejo refused and why; once Forgejo takes
    pushes again, the next cycle delivers it."""
    camping = store.read(LIST)
    hook = refuse_pushes(forgejo)

    written = store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                           by="marge", kind="chore(lists)", title="marge changed Camping")
    refused = store.sync()
    hook.unlink()
    delivered = store.sync()

    assert written.pending
    assert refused.state == "refused" and "read-only for a moment" in refused.detail
    assert len(refused.pending) == 1
    assert delivered.state == "ok"
    assert subjects(forgejo) == ["chore(lists): marge changed Camping"]


def test_a_push_overtaken_by_a_change_elsewhere_is_joined_and_pushed(store, forgejo, marge):
    """Given Marge pushing a groceries list in Forgejo while the store
    pushes Homer's camping change, when the write finishes, then Forgejo
    has both, Marge's first, and nothing waits.

    Forgejo moving on between the store's pull and its push is not the
    writer's problem: the store joins the two and pushes again. (About a
    second: Forgejo's hook pushes Marge's commit inside the store's push.)
    """
    camping = store.read(LIST)
    overtake_next_push(forgejo, marge, {"family/lists/groceries.md": "# Groceries\n"}, "add groceries")

    written = store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                           by="homer", kind="chore(lists)", title="homer changed Camping")

    assert not written.pending
    assert subjects(forgejo) == ["add groceries", "chore(lists): homer changed Camping"]
    assert written.sha == git(forgejo.repo, "rev-parse", "main")


def test_a_push_overtaken_by_a_change_to_the_same_card_is_taken_back(store, forgejo, marge, clone):
    """Given Marge changing the camping list in Forgejo while the store
    pushes Homer's change to it, when the write finishes, then Homer's
    change is taken back, he is told the card changed, and Forgejo and the
    clone hold Marge's version.

    Neither change may silently undo the other; the one that came second
    is asked to look again. (About a second, for the same push in a push.)
    """
    camping = store.read(LIST)
    torn = CAMPING.replace("- [ ] Tent", "- [ ] Torn tent")
    overtake_next_push(forgejo, marge, {LIST: torn}, "Tent is torn")

    with pytest.raises(CardChanged) as changed:
        store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                     by="homer", kind="chore(lists)", title="homer changed Camping")

    assert changed.value.paths == (LIST,)
    assert subjects(forgejo) == ["Tent is torn"]
    store.sync()
    assert (clone / LIST).read_text(encoding="utf-8") == torn
    assert store.pending() == ()


def test_a_waiting_write_is_joined_to_forgejos_changes_keeping_its_names(store, forgejo, marge):
    """Given the archivist's filing for Homer waiting while Forgejo was
    down, and Marge adding a groceries list once it is back, when the cycle
    runs, then Forgejo has both, and the filing is still Homer's, committed
    by the archivist.

    Joining the histories must not rename a commit: readers tell a
    person's change from the machine's by its author and committer.
    """
    forgejo.goes_down()
    store.commit([Put(NOTE, "# Tent\n", base="")],
                 by="homer", via="archivist-bot", kind="capture", title="Tent")
    forgejo.comes_back()
    commit_as(marge, "marge", "add groceries", {"family/lists/groceries.md": "# Groceries\n"})

    synced = store.sync()

    assert synced.state == "ok"
    assert forgejo.new_commits() == [
        "marge <marge@simpson> | marge <marge@simpson> | add groceries",
        "homer <homer@simpson> | archivist-bot <archivist-bot@local> | capture: Tent"]


def test_a_waiting_write_that_conflicts_stops_the_pushing_and_is_kept(store, forgejo, marge, clone):
    """Given Homer's change to the camping list waiting while Forgejo was
    down, and Marge changing the same list in Forgejo once it is back, when
    the cycle runs, then nothing is pushed or thrown away: the clone keeps
    Homer's change, Forgejo keeps Marge's, and the cycle says the two
    diverged.

    Only a person can choose between two edits of one card. Until then the
    clone is the only copy of Homer's change, so it is never reset.
    """
    camping = store.read(LIST)
    forgejo.goes_down()
    store.commit([Put(LIST, TENT_FOR_FOUR, base=camping.version)],
                 by="homer", kind="chore(lists)", title="homer changed Camping")
    forgejo.comes_back()
    commit_as(marge, "marge", "Tent is torn", {LIST: CAMPING.replace("- [ ] Tent", "- [ ] Torn tent")})

    synced = store.sync()

    assert synced.state == "diverged"
    assert [p.subject for p in synced.pending] == ["chore(lists): homer changed Camping"]
    assert (clone / LIST).read_text(encoding="utf-8") == TENT_FOR_FOUR
    assert subjects(forgejo) == ["Tent is torn"]
    assert git(clone, "status", "--porcelain") == ""


def test_a_forgejo_repository_made_again_stops_the_pushing_and_keeps_the_clone(store, forgejo, clone, tmp_path):
    """Given Forgejo's memory repository created again from scratch, so
    that its history shares nothing with the clone's, when the cycle runs,
    then the clone keeps the family's memory, nothing is pushed, and the
    cycle says the two diverged.

    The clone then holds the family's only full copy. Resetting it to an
    empty repository would empty the wiki and the agent's memory.
    """
    forgejo.goes_down()
    fresh = Forgejo(tmp_path / "again")
    stranger = clone_of(fresh, tmp_path / "again" / "seed")
    (stranger / "README.md").write_text("# Memory\n", encoding="utf-8")
    git(stranger, "add", "-A")
    git(stranger, "-c", "user.name=memory-bot", "-c", "user.email=memory-bot@local", "commit", "-q", "-m", "seed: a new memory")
    git(stranger, "push", "-q", "origin", "HEAD:main")
    git(clone, "remote", "set-url", "origin", str(fresh.repo))

    synced = store.sync()

    assert synced.state == "diverged"
    assert (clone / LIST).read_text(encoding="utf-8") == CAMPING
    assert git(fresh.repo, "log", "--format=%s", "main") == "seed: a new memory"


def test_credentials_forgejo_refuses_are_said_to_be_refused(store, clone, httpserver):
    """Given Forgejo answering the clone's credentials with 401 and a line of
    its own, when the curator syncs, then the sync says what git concluded,
    that authentication failed, not the server's line. The curator renews
    its credentials on that answer; on "unreachable" it only waits, and
    what waits would never go out."""
    httpserver.expect_request(re.compile(".*")).respond_with_data(
        "Unauthorized\n", status=401, content_type="text/plain",
        headers={"WWW-Authenticate": 'Basic realm="Forgejo"'})
    stale = httpserver.url_for("/family/memory.git").replace("http://", "http://curator:stale@")
    git(clone, "remote", "set-url", "origin", stale)

    synced = store.sync()

    assert synced.state == "unreachable"
    assert synced.detail.startswith("Authentication failed")


def test_nothing_waits_when_forgejo_has_everything(store):
    """Given a clone at Forgejo's head, then nothing is pending."""
    assert store.sync().state == "ok"
    assert store.pending() == ()


# ── A card's history ─────────────────────────────────────────────────────
#
# Every version of a card, oldest first, followed across moves by its
# `card_id`: what a refile reads a person's edits from (ADR-018 §7).

TENT_NOTE = """---
type: note
card_id: note-3f2a1b9c0d
title: Tent
---

# Tent

The tent is torn.
"""


def test_a_cards_history_is_every_version_oldest_first_across_a_move(store):
    """Given Homer's note filed, then corrected by Marge, then moved to
    another month's folder, when its history is read at its new path, then
    it lists the three versions oldest first, each with who made it and
    the card as that commit left it.

    A refile reapplies a person's edits from the history; one that lost
    the versions before a move would undo Marge's correction.
    """
    moved_to = "family/notes/2026/11/tent.md"
    corrected = TENT_NOTE.replace("is torn", "is torn at the seam")
    store.commit([Put(NOTE, TENT_NOTE, base="")], by="homer", via="archivist-bot",
                 kind="capture", title="Tent")
    store.commit([Put(NOTE, corrected, base=version_of(TENT_NOTE))], by="marge",
                 kind="edit", title="Tent")
    store.commit([Remove(NOTE, base=version_of(corrected)), Put(moved_to, corrected, base="")],
                 by="memory-bot", kind="rename", title="Tent")

    versions = store.history(moved_to, card_id="note-3f2a1b9c0d")

    assert [(v.subject, v.author, v.committer) for v in versions] == [
        ("capture: Tent", "homer", "archivist-bot"),
        ("edit: Tent", "marge", "memory-bot"),
        ("rename: Tent", "memory-bot", "memory-bot")]
    assert [v.card for v in versions] == [TENT_NOTE, corrected, corrected]


def test_a_card_that_was_never_written_has_no_history(store):
    """Given no such card, when its history is read, then there is none."""
    assert store.history("family/notes/2026/10/nothing.md", card_id="note-0000000000") == []


def test_reading_a_history_from_a_missing_clone_says_how_to_make_it(tmp_path):
    """Given no clone yet, when a history is read, then the store says what
    is missing and which command makes it, instead of an empty history
    that would read as a card nobody ever edited."""
    store = CardStore(tmp_path / "nowhere", remote="origin", server_name="simpson")

    with pytest.raises(CloneMissing):
        store.history(NOTE, card_id="note-3f2a1b9c0d")
