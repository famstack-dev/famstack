"""The rollback drill's audit: what a snapshot step left on disk, and what a snapshot costs.

The drill (`tests/driver/rollback_drill.py`) runs the snapshot commands on
a real Mac and, after each step, asks the audit what is left that should
not be. These tests build the folders a step can leave and check that
the audit names each stale one, and nothing that a step is meant to keep.
The rules come from the snapshot store and the rollback record
(`lib/stack/snapshot/store.py`, `rollback_record.py`): a listed snapshot
has a manifest and no leading dot, a replaced state has its record, a
finished rollback names "finished".
"""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

from tests.unit.driver import driver_code

audit = driver_code.load("drill_audit")


def a_snapshot(data_root: Path, name: str) -> Path:
    """A whole snapshot: a folder with its manifest, as `store.take` leaves it."""
    folder = data_root / ".snapshots" / name
    (folder / "docs").mkdir(parents=True)
    (folder / "MANIFEST.json").write_text("{}")
    return folder


def a_replaced_state(data_root: Path, name: str, done: list[str]) -> Path:
    """A state a rollback replaced, with its record naming the steps in `done`."""
    folder = data_root / ".snapshots" / "replaced" / name
    (folder / "docs").mkdir(parents=True)
    (folder / "ROLLBACK.json").write_text(json.dumps({"done": done}))
    return folder


def test_a_whole_snapshot_and_a_finished_rollback_leave_nothing_stale(tmp_path):
    """Given one snapshot and one finished rollback. Then the audit names nothing.

    The drill calls a step clean on an empty list, so a false finding here
    would make every step of the run look faulty."""
    a_snapshot(tmp_path, "2026-11-25T1000Z")
    a_replaced_state(tmp_path, "2026-11-25T1100Z", ["moved docs", "restored docs", "starting", "finished"])
    (tmp_path / ".stack").mkdir()

    assert audit.stale(tmp_path, tmp_path / ".stack") == []


def test_a_folder_left_halfway_and_a_temporary_file_are_stale(tmp_path):
    """Given a `.taking-` folder and a stray file in `.snapshots/`. Then the audit names both.

    These are what a take or a remove that stopped halfway leaves, and
    what `stack snapshot list` lists as not whole."""
    a_snapshot(tmp_path, "2026-11-25T1000Z")
    (tmp_path / ".snapshots" / ".taking-2026-11-25T1200Z").mkdir()
    (tmp_path / ".snapshots" / "MANIFEST.json.tmp").write_text("")

    names = [finding.path.name for finding in audit.stale(tmp_path, tmp_path / ".stack")]

    assert sorted(names) == [".taking-2026-11-25T1200Z", "MANIFEST.json.tmp"]


def test_a_snapshot_without_its_manifest_is_stale(tmp_path):
    """Given a snapshot folder that holds no manifest. Then the audit names it.

    The store lists only folders with a manifest, so such a folder is on
    disk and in no list: space that nobody can remove by name."""
    (tmp_path / ".snapshots" / "2026-11-25T1000Z").mkdir(parents=True)

    assert [f.path.name for f in audit.stale(tmp_path, tmp_path / ".stack")] == ["2026-11-25T1000Z"]


def test_an_open_rollback_record_and_a_replaced_state_without_one_are_stale(tmp_path):
    """Given a record that does not name "finished", and a replaced folder with no record. Then both are named."""
    a_replaced_state(tmp_path, "2026-11-25T1100Z", ["moved docs", "restored docs"])
    (tmp_path / ".snapshots" / "replaced" / "2026-11-25T1300Z" / "docs").mkdir(parents=True)

    names = sorted(f.path.name for f in audit.stale(tmp_path, tmp_path / ".stack"))

    assert names == ["2026-11-25T1100Z", "2026-11-25T1300Z"]


def test_a_pending_take_is_stale_after_a_whole_snapshot(tmp_path):
    """Given a file in `.stack/snapshot-pending/`. Then the audit names it.

    The record of a failed take holds its stacklet: after a whole
    snapshot it must be gone."""
    pending = tmp_path / ".stack" / "snapshot-pending"
    pending.mkdir(parents=True)
    (pending / "docs.json").write_text("{}")

    assert [f.path.name for f in audit.stale(tmp_path, tmp_path / ".stack")] == ["docs.json"]


def test_a_held_run_lock_is_stale_and_a_free_one_is_not(tmp_path):
    """Given two run lock files, one held by an open file. Then only the held one is named.

    A lock file stays on disk by design; only a lock somebody holds
    after the step ended is a fault."""
    runs = tmp_path / ".stack" / "runs"
    runs.mkdir(parents=True)
    (runs / "docs.lock").write_text("")
    held = runs / "one-run.lock"
    held.write_text("")

    with held.open() as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)
        names = [f.path.name for f in audit.stale(tmp_path, tmp_path / ".stack")]

    assert names == ["one-run.lock"]


def test_the_size_counts_the_apparent_bytes_and_the_files(tmp_path):
    """Given a folder with two files of known size. Then the size names their bytes and their count.

    `du -A` is the apparent size on macOS: what a copy would take, while
    a clone takes no space until the data changes."""
    (tmp_path / "a").write_bytes(b"x" * 100_000)
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b").write_bytes(b"y" * 50_000)

    size = audit.size(tmp_path)

    assert size.files == 2
    assert 150_000 <= size.apparent_bytes < 160_000


def test_a_record_that_does_not_read_is_a_finding_not_an_error(tmp_path):
    """Given a ROLLBACK.json cut off halfway. Then the audit names it and goes on.

    The drill kills rollbacks while they write their record; an error
    here would end the step and skip the steps after it."""
    folder = a_replaced_state(tmp_path, "2026-11-25T1100Z", [])
    (folder / "ROLLBACK.json").write_text('{"done": ["moved do')

    findings = audit.stale(tmp_path, tmp_path / ".stack")

    assert [f.path.name for f in findings] == ["2026-11-25T1100Z"]
    assert "does not read" in findings[0].why


def test_an_entry_that_the_list_does_not_name_is_found(tmp_path):
    """Given two snapshots and a replaced state, and a list that names one snapshot and the replaced state.
    Then the other snapshot is named: it takes space that nobody sees."""
    a_snapshot(tmp_path, "2026-11-25T1000Z")
    a_snapshot(tmp_path, "2026-11-25T1100Z")
    a_replaced_state(tmp_path, "2026-11-25T1200Z", ["finished"])
    listed = "  2026-11-25T1000Z  2026-11-25 10:00  docs\n  replaced/2026-11-25T1200Z  docs  from 2026-11-25T1000Z\n"

    assert audit.not_listed(tmp_path / ".snapshots", listed) == ["2026-11-25T1100Z"]


def test_a_tag_of_the_stacks_image_that_nobody_runs_or_keeps_is_stale():
    """Given four tags: one a container runs, one a manifest keeps, one the Mac had, one of nobody.
    Then only the last is named. Ids and names compare in every form Docker and the manifest write them."""
    images = ["postgres:16-alpine aaaaaaaaaaaa", "valkey/valkey:8.1-alpine bbbbbbbbbbbb",
              "ghcr.io/paperless-ngx/paperless-ngx:3.0.4 cccccccccccc", "stack-core-tools:latest dddddddddddd",
              "alpine:3 eeeeeeeeeeee"]
    names = {"postgres:16-alpine", "docker.io/valkey/valkey:8.1-alpine", "ghcr.io/paperless-ngx/paperless-ngx:3.0.4"}

    found = audit.stale_tags(images, names, used={"sha256:" + "a" * 64}, kept={"sha256:" + "b" * 64},
                             before=["ghcr.io/paperless-ngx/paperless-ngx:3.0.4 cccccccccccc"])

    assert found == ["stack-core-tools:latest dddddddddddd: no container runs it and no snapshot keeps it"]


def test_an_open_rollback_is_no_fault_of_a_step_that_kills_one_and_every_other_finding_is():
    """Given an open rollback, a held lock and a doctor line about the rollback.
    Then a step that kills a rollback keeps only the lock as a fault; any other step keeps all three."""
    found = {"files": ["2026-11-25T1129Z: an open rollback: its record names ['starting']",
                       "docs.lock: a run lock still held after the step ended"],
             "doctor": ["A rollback of docs stopped halfway"]}

    assert audit.faults(found, open_rollback_expected=True) == {
        "files": ["docs.lock: a run lock still held after the step ended"], "doctor": []}
    assert audit.faults(found, open_rollback_expected=False) == found


def test_the_file_count_takes_symbolic_links_as_du_does(tmp_path):
    """Given a file and a symbolic link to it. Then the count is two, as `du -A` counts both."""
    (tmp_path / "a").write_bytes(b"x")
    (tmp_path / "link").symlink_to(tmp_path / "a")

    assert audit.size(tmp_path).files == 2
