# ruff: noqa: F811  (the imported fixture `updated` is the tests' parameter)
"""`stack snapshot remove <name>`, as the admin runs it to free space.

A snapshot is a clone of a stacklet's state. It takes space as soon as
the live data changes, and only the admin knows when it is no longer
needed. The same holds for the state a rollback moved aside into
`.snapshots/replaced/<time>/`. `stack snapshot remove` deletes one of
them: it shows what it deletes, waits for a confirmation, and deletes
nothing outside `.snapshots/`. `stack snapshot list` names the replaced
states and the folders that a stop halfway left.

The instance is the one of `test_snapshot_rollback.py`: the admin
updated to rc.2 and restarted, which took one snapshot of docs and
photos, and the family changed both since. Every `./stack` command is a
process of its own, as the admin runs it. A question is answered in a
pseudo terminal (`in_terminal`). A test runs two to six commands, about
2 to 4 seconds.
"""

from __future__ import annotations

import contextlib
import json
import os
import pty
import shutil
import subprocess
import sys
import time

import pytest

from stack import runs
from stack.manifest import MANIFEST_FILE
from stack.snapshot import release, removal
from stack.snapshot.store import SnapshotError
from tests.unit.framework.test_snapshot_at_restart import REPO, STACK_SECONDS
from tests.unit.framework.test_snapshot_rollback import (  # noqa: F401 (the fixture)
    Checkout,
    Ran,
    _stopped_at_the_clone_of_docs,
    everything_in,
    in_terminal,
    replaced_folders,
    rollback,
    updated,
)


def remove(checkout: Checkout, *args: str) -> Ran:
    """`stack snapshot remove --yes --json <args>`, as a script runs it."""
    return checkout.stack("snapshot", "remove", "--yes", "--json", *args)


def listed(checkout: Checkout) -> dict:
    ran = checkout.stack("snapshot", "list", "--json")
    assert ran.code == 0, ran.err
    return answer_of(ran)


def answer_of(ran: Ran) -> dict:
    """The JSON answer of a `--json` command."""
    return json.loads(ran.out)


def the_snapshot(checkout: Checkout) -> str:
    """The name of the one snapshot, besides `replaced/` and half folders."""
    [name] = [n for n in checkout.snapshot_folders() if n != "replaced" and not n.startswith(".")]
    return name


def size_of(folder) -> int:
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


# ── A snapshot ───────────────────────────────────────────────────────────

def test_the_remove_shows_the_snapshot_and_deletes_it_after_the_confirmation(updated):
    """Given a snapshot, when the admin runs `stack snapshot remove <name>`
    in a terminal and confirms, then the stack has shown its name, its
    release, its stacklets and its size, and the snapshot is gone. The
    data folders are as they were."""
    name = the_snapshot(updated)
    folder = updated.data / ".snapshots" / name
    version = json.loads((folder / MANIFEST_FILE).read_text())["version"]
    size = size_of(folder)
    docs_before = everything_in(updated.data / "docs")

    ran = in_terminal(updated, "snapshot", "remove", name, "--json", answer="y")

    assert ran.code == 0, ran.err
    answer = answer_of(ran)
    assert answer["removed"] == name
    assert answer["kind"] == "snapshot"
    assert answer["version"] == version
    assert answer["stacklets"] == ["docs", "photos"]
    assert answer["bytes"] == size
    assert not folder.exists()
    assert listed(updated)["snapshots"] == []
    assert everything_in(updated.data / "docs") == docs_before


def test_the_plan_a_person_reads_names_the_release_the_stacklets_and_the_size(updated):
    """Given a snapshot, when the admin runs `stack snapshot remove <name>`
    in a terminal and declines, then the screen named the snapshot, its
    release and its stacklets before the question."""
    name = the_snapshot(updated)
    version = json.loads((updated.data / ".snapshots" / name / MANIFEST_FILE).read_text())["version"]

    ran = in_terminal(updated, "snapshot", "remove", name, answer="n")

    assert ran.code == 0, ran.err
    for fact in (name, version, "docs", "photos"):
        assert fact in ran.out


def test_a_declined_remove_changes_nothing(updated):
    """Given a snapshot, when the admin runs `stack snapshot remove <name>`
    and answers no, then nothing in the data folder changes."""
    name = the_snapshot(updated)
    before = everything_in(updated.data)

    ran = in_terminal(updated, "snapshot", "remove", name, "--json", answer="n")

    assert ran.code == 0, ran.err
    assert answer_of(ran)["declined"] is True
    assert everything_in(updated.data) == before


def test_without_a_terminal_and_without_yes_it_refuses(updated):
    """Given no terminal to ask in, when a script runs `stack snapshot
    remove <name>` without `--yes`, then it refuses and deletes nothing,
    because a typo must not delete a snapshot unasked."""
    name = the_snapshot(updated)
    before = everything_in(updated.data)

    ran = updated.stack("snapshot", "remove", name, "--json")

    assert ran.code == 1
    assert "--yes" in answer_of(ran)["error"]
    assert everything_in(updated.data) == before


def test_a_snapshot_with_an_unreadable_manifest_is_removed_after_the_confirmation(updated):
    """Given a snapshot whose manifest cannot be read, when the admin runs
    `stack snapshot remove <name>` and confirms, then the stack removes
    it, because the admin named it, and says that its manifest was
    unreadable."""
    name = the_snapshot(updated)
    (updated.data / ".snapshots" / name / MANIFEST_FILE).write_text("{not json")

    ran = in_terminal(updated, "snapshot", "remove", name, "--json", answer="y")

    assert ran.code == 0, ran.err
    assert answer_of(ran)["unreadable"]
    assert updated.snapshot_folders() == []


# ── The state a rollback replaced ────────────────────────────────────────

def test_the_replaced_state_of_a_finished_rollback_is_listed_and_removed(updated):
    """Given a rollback that ended, when the admin runs `stack snapshot
    list`, then it names the replaced state with its snapshot and its
    stacklets. When the admin runs `stack snapshot remove replaced/<time>`
    and confirms, then that state is gone, and the snapshot and the data
    folders stay as they were."""
    assert rollback(updated).code == 0
    [time] = replaced_folders(updated)
    snapshot = the_snapshot(updated)
    [entry] = listed(updated)["replaced"]
    assert entry["name"] == f"replaced/{time}"
    assert entry["snapshot"] == snapshot
    assert entry["stacklets"] == ["docs", "photos"]
    assert entry["finished"] is True
    docs_before = everything_in(updated.data / "docs")

    ran = in_terminal(updated, "snapshot", "remove", f"replaced/{time}", "--json", answer="y")

    assert ran.code == 0, ran.err
    assert answer_of(ran)["kind"] == "replaced"
    assert replaced_folders(updated) == []
    assert updated.snapshot_folders() == [snapshot, "replaced"]
    assert everything_in(updated.data / "docs") == docs_before


def test_the_confirmation_of_a_replaced_state_says_it_is_the_only_copy(updated):
    """Given a rollback that ended, when the admin runs `stack snapshot
    remove replaced/<time>` in a terminal, then the stack warns, before it
    asks, that this is the only copy of the state from before the
    rollback, and the warning names the snapshot of that rollback."""
    assert rollback(updated).code == 0
    [time] = replaced_folders(updated)

    state = in_terminal(updated, "snapshot", "remove", f"replaced/{time}", answer="n")

    # The entry's own lines name `replaced/<time>`; the warning is the
    # line that names the rollback's snapshot apart from it.
    assert state.code == 0, state.err
    assert [line for line in state.out.splitlines()
            if the_snapshot(updated) in line and "replaced/" not in line]


def test_a_declined_remove_of_a_replaced_state_changes_nothing(updated):
    """Given a rollback that ended, when the admin runs `stack snapshot
    remove replaced/<time>` and answers no, then nothing changes."""
    assert rollback(updated).code == 0
    [time] = replaced_folders(updated)
    before = everything_in(updated.data)

    ran = in_terminal(updated, "snapshot", "remove", f"replaced/{time}", answer="n")

    assert ran.code == 0, ran.err
    assert everything_in(updated.data) == before


# ── A rollback that has not ended ────────────────────────────────────────

def test_the_snapshot_and_the_replaced_state_of_an_open_rollback_are_refused(updated):
    """Given a rollback that stopped halfway, when the admin removes its
    snapshot or its replaced state, then the stack refuses, names `stack
    rollback`, and deletes nothing: the rollback needs both to finish."""
    _stopped_at_the_clone_of_docs(updated)
    [time] = replaced_folders(updated)
    before = everything_in(updated.data)

    for name in (the_snapshot(updated), f"replaced/{time}"):
        ran = remove(updated, name)
        assert ran.code == 1, name
        assert "stack rollback" in answer_of(ran)["error"]
    assert everything_in(updated.data) == before


def test_an_unreadable_record_keeps_every_snapshot(updated):
    """Given the record of a rollback that cannot be read, when the admin
    removes a snapshot, then the stack refuses: it cannot tell which
    snapshot that rollback goes back to, and does not guess."""
    folder = updated.data / ".snapshots" / "replaced" / "2026-01-01T0000Z"
    (folder / "docs").mkdir(parents=True)
    (folder / "ROLLBACK.json").write_text("{not json")

    ran = remove(updated, the_snapshot(updated))

    assert ran.code == 1
    assert str(folder / "ROLLBACK.json") in answer_of(ran)["error"]
    assert len(updated.snapshot_folders()) == 2


def test_the_restart_keeps_the_snapshot_of_an_open_rollback(updated):
    """Given a rollback that stopped halfway on the oldest of four
    snapshots, when a restart keeps the newest three, then the oldest
    stays too, because the rollback needs it to finish."""
    _stopped_at_the_clone_of_docs(updated)
    oldest = the_snapshot(updated)
    for name in ("2027-01-01T0900Z", "2027-01-02T0900Z", "2027-01-03T0900Z"):
        updated.older_snapshot(name)

    pruned = release.prune(updated.data, "2027-01-03T0900Z")

    assert oldest not in pruned.removed
    assert (updated.data / ".snapshots" / oldest).is_dir()


# ── A rollback that is about to restore a snapshot ───────────────────────

@contextlib.contextmanager
def at_its_question(checkout: Checkout, *args: str):
    """`./stack <args>` in a terminal, waiting at its question once it
    holds the run lock of docs. Yields a function that types the answer
    and returns how the command ended."""
    typing, terminal = pty.openpty()
    process = subprocess.Popen([sys.executable, "-m", "stack", *args],
                               cwd=checkout.root, stdin=terminal, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True,
                               env={**os.environ, "PYTHONPATH": str(REPO / "lib")})
    try:
        deadline = time.monotonic() + STACK_SECONDS
        while not runs.is_held(checkout.instance, "docs"):
            assert process.poll() is None, process.communicate()
            assert time.monotonic() < deadline, f"{args} never asked"
            time.sleep(0.05)

        def answer(text: str) -> Ran:
            os.write(typing, f"{text}\n".encode())
            out, err = process.communicate(timeout=STACK_SECONDS)
            return Ran(process.returncode, out, err)
        yield answer
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
        os.close(typing)
        os.close(terminal)


def a_rollback_at_its_question(checkout: Checkout):
    """`stack rollback` in a terminal, waiting at "Roll back?"."""
    return at_its_question(checkout, "rollback", "--json")


def test_a_snapshot_is_refused_while_a_rollback_asks_to_restore_it(updated):
    """Given a `stack rollback` that shows its plan and waits at its
    question, when the admin removes the snapshot it restores in another
    terminal, then the remove refuses and names the rollback, and the
    rollback still finds its snapshot."""
    name = the_snapshot(updated)

    with a_rollback_at_its_question(updated) as answer:
        ran = remove(updated, name)
        assert ran.code == 1
        assert "rollback" in answer_of(ran)["error"]
        assert (updated.data / ".snapshots" / name / MANIFEST_FILE).is_file()
        assert answer("n").code == 0


def test_a_rollback_whose_snapshot_went_after_the_plan_stops_nothing(updated):
    """Given a `stack rollback` that waits at its question, and its
    snapshot deleted meanwhile (by a remove that came before the rollback
    held its locks), when the admin confirms, then the rollback refuses
    before it stops anything: no replaced state, the data folders as they
    were, and no stacklet stopped."""
    name = the_snapshot(updated)
    data_before = {sid: everything_in(updated.data / sid) for sid in ("docs", "photos")}

    with a_rollback_at_its_question(updated) as answer:
        shutil.rmtree(updated.data / ".snapshots" / name)
        ran = answer("y")

    assert ran.code == 1
    assert name in answer_of(ran)["error"]
    assert replaced_folders(updated) == []
    assert {sid: everything_in(updated.data / sid) for sid in ("docs", "photos")} == data_before
    assert updated.docker.compose_steps() == []


def test_a_rollback_that_starts_during_the_removes_question_is_refused(updated):
    """Given `stack snapshot remove <snapshot>` waiting at its question,
    when the admin starts `stack rollback --yes` to that snapshot in
    another terminal, then the rollback refuses before it stops or moves
    anything, and the remove, confirmed after, deletes the snapshot. The
    data folders are as they were."""
    name = the_snapshot(updated)
    data_before = {sid: everything_in(updated.data / sid) for sid in ("docs", "photos")}

    with at_its_question(updated, "snapshot", "remove", name, "--json") as answer:
        rolled = rollback(updated, name)
        removed = answer("y")

    assert rolled.code == 75, rolled.err
    assert answer_of(rolled)["running"]["action"] == "snapshot remove"
    assert replaced_folders(updated) == []
    assert updated.docker.compose_steps() == []
    assert {sid: everything_in(updated.data / sid) for sid in ("docs", "photos")} == data_before
    assert removed.code == 0, removed.err
    assert not (updated.data / ".snapshots" / name).exists()


# ── Names that are not a snapshot ────────────────────────────────────────

NOT_A_SNAPSHOT = ["..", "../docs", "docs", "replaced", "replaced/../../docs",
                  ".snapshots", "", "2026-11-25T0100Z/../../docs", "/"]


@pytest.mark.parametrize("name", NOT_A_SNAPSHOT)
def test_a_name_that_is_not_a_snapshot_is_refused(updated, name):
    """Given a name that is not a snapshot or a replaced state (`..`, a
    path, a stacklet's folder), when the admin runs `stack snapshot
    remove <name>`, then it refuses and deletes nothing."""
    before = everything_in(updated.data)

    ran = remove(updated, name)

    assert ran.code == 1
    assert "error" in answer_of(ran)
    assert everything_in(updated.data) == before


def test_an_absolute_path_is_refused(updated):
    """Given the absolute path of a stacklet's data folder, or of the
    snapshot itself, when the admin runs `stack snapshot remove <path>`,
    then it refuses and deletes nothing: the remove takes only names."""
    before = everything_in(updated.data)

    for path in (updated.data / "docs", updated.data / ".snapshots" / the_snapshot(updated)):
        ran = remove(updated, str(path))
        assert ran.code == 1, path
    assert everything_in(updated.data) == before


def a_copy_of_the_snapshot(checkout: Checkout, folder) -> list[str]:
    """A whole copy of the snapshot at `folder`, outside `.snapshots/`, as
    a person keeps one: it holds a manifest. Returns what is in it."""
    shutil.copytree(checkout.data / ".snapshots" / the_snapshot(checkout), folder)
    return everything_in(folder)


def test_a_link_with_a_snapshots_name_is_refused(updated):
    """Given a link in `.snapshots/` with a snapshot's name that points at
    a whole copy of a snapshot outside, when the admin removes it, then
    the stack refuses, and the link and the copy stay whole."""
    keepsake = updated.root.parent / "keepsake"
    copy = a_copy_of_the_snapshot(updated, keepsake)
    link = updated.data / ".snapshots" / "2027-01-01T0900Z"
    link.symlink_to(keepsake)

    ran = remove(updated, link.name)

    assert ran.code == 1
    assert everything_in(keepsake) == copy
    assert link.is_symlink()


def test_the_plan_refuses_a_link_with_a_snapshots_name(updated):
    """Given the same link to a whole copy, when the remove plans it, then
    the plan refuses it on its own, before the delete's check."""
    keepsake = updated.root.parent / "keepsake"
    a_copy_of_the_snapshot(updated, keepsake)
    (updated.data / ".snapshots" / "2027-01-01T0900Z").symlink_to(keepsake)

    with pytest.raises(removal.RemovalError):
        removal.plan(updated.data, updated.instance, "2027-01-01T0900Z")


@pytest.mark.parametrize("name", ["../keepsake", "replaced/../../keepsake", "keepsake"])
def test_the_plan_refuses_a_name_without_a_snapshots_form(updated, name):
    """Given whole copies of a snapshot in the data folder and in
    `.snapshots/` under the name `keepsake`, when the remove plans a name
    that leads to one, then the plan refuses the name on its own, before
    it looks into the folder: only a snapshot's name passes."""
    a_copy_of_the_snapshot(updated, updated.data / "keepsake")
    a_copy_of_the_snapshot(updated, updated.data / ".snapshots" / "keepsake")
    (updated.data / ".snapshots" / "replaced").mkdir(exist_ok=True)

    with pytest.raises(removal.RemovalError):
        removal.plan(updated.data, updated.instance, name)


def test_the_remove_refuses_an_entry_made_by_hand_outside(updated):
    """Given an entry that names the docs data folder, made by hand and
    not by the plan, when the remove is asked to delete it, then the
    delete refuses, and the docs data folder stays whole."""
    docs_before = everything_in(updated.data / "docs")
    entry = removal.Entry("2026-11-24T2114Z", removal.SNAPSHOT, updated.data / "docs")

    with pytest.raises(SnapshotError):
        removal.remove(updated.data, entry)

    assert everything_in(updated.data / "docs") == docs_before


def test_a_link_inside_a_snapshot_goes_and_its_target_stays(updated):
    """Given a snapshot that holds a link to a folder and a link to a file
    outside `.snapshots/`, when the admin removes the snapshot, then the
    snapshot is gone and the folder and the file are whole."""
    name = the_snapshot(updated)
    outside = updated.root.parent / "outside"
    (outside / "letters").mkdir(parents=True)
    (outside / "letters" / "school.txt").write_text("the letter from the school")
    (outside / "notes.txt").write_text("marge's notes")
    before = everything_in(outside)
    snapshot = updated.data / ".snapshots" / name
    (snapshot / "docs" / "letters").symlink_to(outside / "letters")
    (snapshot / "docs" / "notes.txt").symlink_to(outside / "notes.txt")

    ran = remove(updated, name)

    assert ran.code == 0, ran.err
    assert not snapshot.exists()
    assert everything_in(outside) == before


# ── A snapshot that stopped halfway ──────────────────────────────────────

def a_half_snapshot(checkout: Checkout) -> str:
    """A `.taking-` folder, as a power loss during a take leaves it."""
    name = ".taking-2026-11-25T0100Z"
    half = checkout.data / ".snapshots" / name / "docs" / "postgres"
    half.mkdir(parents=True)
    (half / "base.dat").write_text("homer's inv")
    return name


def test_a_half_snapshot_is_listed_as_not_whole_and_never_as_a_snapshot(updated):
    """Given a `.taking-` folder that a stop halfway left, when the admin
    runs `stack snapshot list`, then the list names it as not whole, and
    not among the snapshots, so no rollback goes to half a snapshot."""
    name = a_half_snapshot(updated)

    answer = listed(updated)
    pretty = updated.stack("snapshot", "list")

    assert [entry["name"] for entry in answer["incomplete"]] == [name]
    assert all(s["name"] != name for s in answer["snapshots"])
    assert name in pretty.out


def test_a_half_snapshot_is_removed_after_the_confirmation(updated):
    """Given a `.taking-` folder that a stop halfway left, when the admin
    runs `stack snapshot remove <name>` and confirms, then it is gone and
    the whole snapshot stays."""
    name = a_half_snapshot(updated)

    ran = in_terminal(updated, "snapshot", "remove", name, "--json", answer="y")

    assert ran.code == 0, ran.err
    assert answer_of(ran)["kind"] == "incomplete"
    assert not (updated.data / ".snapshots" / name).exists()
    assert len(listed(updated)["snapshots"]) == 1


def test_a_half_snapshot_is_refused_while_a_run_may_still_write_it(updated):
    """Given a `.taking-` folder and a run of the stack that goes on (a
    snapshot may be taking it right now), when the admin removes it, then
    the stack refuses and names the run."""
    name = a_half_snapshot(updated)

    with runs.hold(updated.instance, ["docs"]):
        ran = remove(updated, name)

    assert ran.code == 1
    assert (updated.data / ".snapshots" / name).is_dir()


# ── The resting state ────────────────────────────────────────────────────

def test_the_list_changes_nothing(updated):
    """Given snapshots, a replaced state and a half snapshot, when the
    admin runs `stack snapshot list` twice, then nothing in the data
    folder changes."""
    assert rollback(updated).code == 0
    a_half_snapshot(updated)
    before = everything_in(updated.data)

    listed(updated)
    assert updated.stack("snapshot", "list").code == 0

    assert everything_in(updated.data) == before
