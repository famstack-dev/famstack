"""The snapshot store: a stopped stacklet's state, cloned into `.snapshots/`.

A snapshot before a change is the way back when the change breaks a
stacklet. These tests drive `stack.snapshot.store` through its public
functions, as `stack snapshot create` and the restart call it.

The data folder, the instance folder and the files are real, in the
test's temporary folder. On this Mac that folder is on APFS, so the real
volume (`ThisVolume`) clones. Two cases need a volume this Mac does not
have: one that cannot clone and has little space (`CopyOnly`), and one
whose clone fails halfway (`BreaksAfter`). Both are stand-ins for the file
system at the port `Volume`. Whether a stacklet runs is the caller's
answer, so the tests give it as a function.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from stack.manifest import MANIFEST_FILE, Manifest, StackletRecord
from stack.snapshot.store import (
    CONFIG_FOLDER,
    SNAPSHOTS_FOLDER,
    SnapshotError,
    ThisVolume,
    Snapshot,
    archive_paths,
    clone_back,
    remove,
    snapshots,
    take,
)

AT_2114 = datetime(2026, 11, 24, 21, 14, 5, tzinfo=timezone.utc)
AT_2140 = datetime(2026, 11, 24, 21, 40, tzinfo=timezone.utc)

DOCS_TOML = """
id = "docs"
name = "Documents"

[[backup.archive]]
name = "media"
path = "{data_dir}/docs/paperless/media"
"""


def nothing_runs(_stacklet: str) -> bool:
    return False


def a_manifest(*stacklets: str) -> Manifest:
    """The manifest the caller hands over: the release before the change."""
    return Manifest(kind="snapshot", name="", version="0.4.0-rc.1", commit="f5834a67",
                    stacklets={sid: StackletRecord(up=True, commit="f5834a67",
                                                   migrations=["0001_move_tags"], images={})
                               for sid in stacklets})


class Mac:
    """A Simpsons instance with the docs stacklet set up: its folder in
    the checkout, its data folder with a database and two documents, and
    the configuration."""

    def __init__(self, root: Path):
        self.root = root
        self.data = root / "data"
        self.instance = root / "instance"
        self.docs = root / "stacklets" / "docs"
        self.docs.mkdir(parents=True)
        (self.docs / "stacklet.toml").write_text(DOCS_TOML)
        live = self.data / "docs"
        (live / "postgres").mkdir(parents=True)
        (live / "postgres" / "PG_VERSION").write_text("16\n")
        (live / "postgres" / "base.dat").write_bytes(b"homer's invoices" * 64)
        (live / "paperless" / "data").mkdir(parents=True)
        (live / "paperless" / "data" / "index.db").write_text("index")
        (live / "paperless" / "media" / "originals").mkdir(parents=True)
        (live / "paperless" / "media" / "originals" / "dentist-bill.pdf").write_bytes(b"%PDF" * 1000)
        (live / ".migrations.json").write_text('{"applied": {}, "failed": {}}\n')
        (live / "current").symlink_to("postgres")
        (self.instance / ".stack").mkdir(parents=True)
        (self.instance / "stack.toml").write_text('[core]\nname = "stack"\n')
        (self.instance / "users.toml").write_text('[[users]]\nid = "homer"\n')
        (self.instance / ".stack" / "secrets.toml").write_text('docs__DB_PASSWORD = "donuts"\n')

    def take(self, **kwargs):
        options = {"running": nothing_runs, "now": AT_2114, **kwargs}
        return take(self.data, self.instance, {"docs": self.docs}, a_manifest("docs"), **options)

    def files(self, folder: Path) -> dict[str, bytes | str]:
        """Each file below `folder` with its content, a link with its target."""
        found: dict[str, bytes | str] = {}
        for path in sorted(folder.rglob("*")):
            name = str(path.relative_to(folder))
            if path.is_symlink():
                found[name] = "-> " + os.readlink(path)
            elif path.is_file():
                found[name] = path.read_bytes()
        return found


@pytest.fixture
def mac(tmp_path, monkeypatch) -> Mac:
    # `tmutil` of this Mac would mark the test's folder for Time Machine;
    # the stand-in records the call instead.
    tmutil_on_path(monkeypatch, tmp_path / "bin")
    return Mac(tmp_path)


def tmutil_on_path(monkeypatch, folder: Path) -> Path:
    """A `tmutil` first on `PATH` that writes its arguments to a log."""
    folder.mkdir()
    log = folder / "tmutil.log"
    script = folder / "tmutil"
    script.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\n')
    script.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder), prepend=os.pathsep)
    return log


class CopyOnly(ThisVolume):
    """A volume that is not APFS: it cannot clone, and has `free` bytes."""

    def __init__(self, free: int):
        self.free = free

    def can_clone(self, folder: Path) -> bool:
        return False

    def free_bytes(self, folder: Path) -> int:
        return self.free


class BreaksAfter(ThisVolume):
    """A volume that clones `count` files, then fails as a full disk does."""

    def __init__(self, count: int):
        self.count = count

    def can_clone(self, folder: Path) -> bool:
        return True

    def clone(self, source: Path, target: Path) -> None:
        if self.count == 0:
            raise OSError(28, "No space left on device", str(target))
        self.count -= 1
        shutil.copy2(source, target)


# ── What a snapshot holds ────────────────────────────────────────────────

def test_a_snapshot_holds_the_data_folder_the_configuration_and_the_manifest(mac):
    """Given the stopped docs stacklet, when the store takes its snapshot,
    then `.snapshots/<time>/` holds docs' data folder, `stack/` with the
    configuration, and the manifest. A rollback puts each file back where
    its path says, so the paths are those of the data folder."""
    taken = mac.take()

    folder = mac.data / SNAPSHOTS_FOLDER / "2026-11-24T2114Z"
    assert taken.snapshot.path == folder
    held = mac.files(folder)
    assert held["docs/postgres/base.dat"] == b"homer's invoices" * 64
    assert held["docs/paperless/data/index.db"] == b"index"
    assert held["docs/.migrations.json"] == b'{"applied": {}, "failed": {}}\n'
    assert held["docs/current"] == "-> postgres"
    assert held[f"{CONFIG_FOLDER}/stack.toml"] == b'[core]\nname = "stack"\n'
    assert held[f"{CONFIG_FOLDER}/users.toml"] == b'[[users]]\nid = "homer"\n'
    assert held[f"{CONFIG_FOLDER}/.stack/secrets.toml"] == b'docs__DB_PASSWORD = "donuts"\n'
    written = json.loads(held[MANIFEST_FILE])
    assert (written["kind"], written["name"], written["version"]) == \
        ("snapshot", "2026-11-24T2114Z", "0.4.0-rc.1")
    assert list(written["stacklets"]) == ["docs"]


def test_a_snapshot_leaves_out_the_archive_sources(mac):
    """Given docs, which declares `paperless/media` as `[[backup.archive]]`,
    when the store takes its snapshot, then no file of it is in the
    snapshot: the originals are the large files, and a copy of them could
    fill the disk."""
    held = mac.files(mac.take().snapshot.path)

    assert not [name for name in held if name.startswith("docs/paperless/media")]
    assert archive_paths(mac.docs, mac.data) == [Path("paperless/media")]


def test_an_archive_source_outside_the_data_folder_is_not_left_out_of_it(tmp_path):
    """Given a source outside the stacklet's own data folder, then it is
    no path of the snapshot: the snapshot never holds it anyway."""
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "stacklet.toml").write_text(
        'id = "photos"\n[[backup.archive]]\npath = "/Volumes/Family/library"\n'
        '[[backup.archive]]\npath = "{data_dir}/photos/library/library"\n')

    assert archive_paths(photos, tmp_path / "data") == [Path("library/library")]


# ── Clones and copies ────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "darwin", reason="clonefile(2) exists only on macOS")
def test_on_apfs_each_file_is_a_clone_and_a_later_change_does_not_reach_it(mac):
    """Given a data folder on APFS (the test's folder on this Mac), when
    the store takes a snapshot, then it clones, and a later change of the
    live file leaves the snapshot as it was."""
    taken = mac.take()
    (mac.data / "docs" / "postgres" / "base.dat").write_bytes(b"marge's receipts")

    assert taken.cloned is True
    assert (taken.snapshot.path / "docs" / "postgres" / "base.dat").read_bytes() == \
        b"homer's invoices" * 64


def test_a_volume_that_cannot_clone_gets_a_copy_and_says_so(mac):
    """Given a data folder that is not on APFS, when the store takes a
    snapshot, then it copies the files and says that it copied."""
    taken = mac.take(volume=CopyOnly(free=10**9))

    assert taken.cloned is False
    assert mac.files(taken.snapshot.path)["docs/postgres/base.dat"] == b"homer's invoices" * 64


def test_a_copy_that_does_not_fit_is_refused_and_names_the_size(mac):
    """Given a volume that cannot clone and has 100 bytes free, when the
    store takes a snapshot, then it refuses, names the size the copy
    needs, and writes no snapshot."""
    with pytest.raises(SnapshotError) as refused:
        mac.take(volume=CopyOnly(free=100))

    assert "100 bytes" in str(refused.value)
    assert "bytes" in str(refused.value).split("needs", 1)[1]
    assert snapshots(mac.data) == []
    assert [p.name for p in (mac.data / SNAPSHOTS_FOLDER).iterdir()] == []


# ── What a take never does ───────────────────────────────────────────────

def test_a_running_stacklet_is_refused_and_nothing_is_cloned(mac):
    """Given docs whose containers still run, when the store is asked for
    its snapshot, then it refuses: a database cloned while its server
    writes is torn, and a rollback to it breaks the stacklet."""
    with pytest.raises(SnapshotError, match="docs"):
        mac.take(running=lambda sid: sid == "docs")

    assert not (mac.data / SNAPSHOTS_FOLDER).exists()


def test_a_take_does_not_change_the_data_folder(mac):
    """Given the data folder, when the store takes a snapshot, then every
    file and link of the data folder is as it was: the take only reads."""
    before = mac.files(mac.data / "docs")

    mac.take()

    assert mac.files(mac.data / "docs") == before


def test_a_take_that_stops_halfway_leaves_no_snapshot(mac):
    """Given a volume that fails after two files (a full disk), when the
    store takes a snapshot, then the take fails, the half snapshot is
    removed, and the list does not show it."""
    with pytest.raises(SnapshotError, match="halfway"):
        mac.take(volume=BreaksAfter(2))

    assert snapshots(mac.data) == []
    assert list((mac.data / SNAPSHOTS_FOLDER).iterdir()) == []


@pytest.mark.parametrize("volume", [None, CopyOnly(free=10**9)], ids=["clones", "copies"])
def test_a_clone_back_never_writes_over_a_file_that_is_there(mac, volume):
    """Given a snapshot of docs, and a data folder that still holds the
    family's database file, when the state is cloned back, on APFS or on
    a volume that copies, then the call refuses and names the file, and
    the file keeps its content: a restore never replaces a file, the
    rollback moves it aside first."""
    taken = mac.take()
    target = mac.root / "restored"
    live = target / "postgres" / "base.dat"
    live.parent.mkdir(parents=True)
    live.write_bytes(b"homer's invoices and the parking ticket")

    with pytest.raises(SnapshotError, match="base.dat"):
        clone_back(taken.snapshot, "docs", target, volume)

    assert live.read_bytes() == b"homer's invoices and the parking ticket"


def test_a_folder_without_a_manifest_is_not_listed(mac):
    """Given a snapshot whose take was stopped before its manifest (the
    Mac lost power), when the snapshots are listed, then it is not among
    them, because a rollback to it would go to a broken state."""
    mac.take()
    cut = mac.data / SNAPSHOTS_FOLDER / "2026-11-24T2140Z"
    (cut / "docs").mkdir(parents=True)
    (mac.data / SNAPSHOTS_FOLDER / ".taking-2026-11-24T2150Z" / "docs").mkdir(parents=True)

    assert [s.name for s in snapshots(mac.data)] == ["2026-11-24T2114Z"]


# ── The list ─────────────────────────────────────────────────────────────

def test_the_list_is_newest_first_with_the_time_and_the_release(mac):
    """Given two snapshots, when the store lists them, then the newest
    comes first, each with its time, its stacklets and its release."""
    mac.take(now=AT_2114)
    mac.take(now=AT_2140)

    listed = snapshots(mac.data)

    assert [s.name for s in listed] == ["2026-11-24T2140Z", "2026-11-24T2114Z"]
    assert listed[0].taken == AT_2140
    assert list(listed[0].manifest.stacklets) == ["docs"]
    assert listed[0].manifest.version == "0.4.0-rc.1"


def test_two_snapshots_in_one_minute_are_both_kept(mac):
    """Given a snapshot taken at 21:14, when a second one is taken in the
    same minute, then both are kept, the second one listed first."""
    mac.take(now=AT_2114)
    mac.take(now=AT_2114)

    assert [s.name for s in snapshots(mac.data)] == ["2026-11-24T2114Z-2", "2026-11-24T2114Z"]


def test_no_snapshots_is_an_empty_list(tmp_path):
    """Given a data folder without `.snapshots/`, then the list is empty."""
    assert snapshots(tmp_path) == []


# ── Time Machine ─────────────────────────────────────────────────────────

def test_time_machine_skips_the_snapshots_folder_when_it_is_made(tmp_path, monkeypatch):
    """Given no `.snapshots/` yet, when the store makes it, then it asks
    `tmutil addexclusion` to skip it, once: the snapshot is a clone of data
    that Time Machine already holds."""
    log = tmutil_on_path(monkeypatch, tmp_path / "bin")
    mac = Mac(tmp_path)

    mac.take(now=AT_2114)
    mac.take(now=AT_2140)

    assert log.read_text().splitlines() == [f"addexclusion {mac.data / SNAPSHOTS_FOLDER}"]


class RaisesOnClone(ThisVolume):
    """A volume whose clone fails with an error the store does not expect."""

    def can_clone(self, folder: Path) -> bool:
        return True

    def clone(self, source: Path, target: Path) -> None:
        raise RuntimeError("the volume went away")


def test_an_unexpected_error_leaves_no_half_snapshot(mac):
    """Given a clone that raises an error the store does not expect (or
    Ctrl-C), when the store takes a snapshot, then the error reaches the
    caller and the `.taking-` folder is removed: in a copy it can hold
    gigabytes."""
    with pytest.raises(RuntimeError):
        mac.take(volume=RaisesOnClone())

    assert list((mac.data / SNAPSHOTS_FOLDER).iterdir()) == []


def test_a_failed_manifest_write_leaves_no_snapshot_folder(mac):
    """Given a manifest that cannot be written, when the store takes a
    snapshot, then no folder with the snapshot's name is left: the
    manifest is written before the rename, so a named folder always holds
    one."""
    broken = Manifest(kind="snapshot", name="", version="0.4.0-rc.1", commit="f5834a67",
                      stacklets={"docs": StackletRecord(up=True, commit=None,
                                                        migrations=[object()], images={})})

    # `json` refuses a value it cannot encode with a TypeError, and
    # `manifest.write` lets it through.
    with pytest.raises(TypeError, match="not JSON serializable"):
        take(mac.data, mac.instance, {"docs": mac.docs}, broken,
             running=nothing_runs, now=AT_2114)

    assert list((mac.data / SNAPSHOTS_FOLDER).iterdir()) == []


def test_an_unreadable_manifest_is_listed_as_unreadable(mac):
    """Given a snapshot whose manifest is cut off, when the store lists the
    snapshots, then it lists that one as unreadable, naming the file, and
    still lists the others."""
    mac.take(now=AT_2114)
    mac.take(now=AT_2140)
    (mac.data / SNAPSHOTS_FOLDER / "2026-11-24T2114Z" / MANIFEST_FILE).write_text("{")

    listed = snapshots(mac.data)

    assert [(s.name, s.manifest is None) for s in listed] == \
        [("2026-11-24T2140Z", False), ("2026-11-24T2114Z", True)]
    assert MANIFEST_FILE in listed[1].unreadable


# ── Removing ─────────────────────────────────────────────────────────────

def test_a_removed_snapshot_is_gone_and_the_others_stay(mac):
    """Given two snapshots, when the store removes the older one, then it
    is gone from the list and from the disk, the newer one stays, and the
    data folder is as it was."""
    mac.take(now=AT_2114)
    mac.take(now=AT_2140)
    data_before = mac.files(mac.data / "docs")

    remove(snapshots(mac.data)[-1])

    assert [s.name for s in snapshots(mac.data)] == ["2026-11-24T2140Z"]
    assert sorted(p.name for p in (mac.data / SNAPSHOTS_FOLDER).iterdir()) == ["2026-11-24T2140Z"]
    assert mac.files(mac.data / "docs") == data_before


def test_a_folder_outside_the_snapshots_is_never_removed(mac):
    """Given a path that is not a snapshot in `.snapshots/` (the data
    folder of docs), when the store is asked to remove it, then it refuses
    and the folder stays."""
    with pytest.raises(SnapshotError):
        remove(Snapshot("docs", mac.data / "docs", None))

    assert (mac.data / "docs").is_dir()
