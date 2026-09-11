"""Moving a vault to the current directory layout.

A vault written by 0.3.0-beta.3 or earlier holds one flat directory per
source, `data/photos-library`. Current releases nest them under the
stacklet, `data/photos/library`. Syncs read both, so the rename is
something the household does once, when it suits them.

The rename moves the directory, not its contents. That matters more than
it looks: every file on a vault carries the `uchg` flag that makes the
vault append-only, and a copy would both duplicate the data and leave
the original undeletable.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_ROOT / "stacklets" / "backup" / "cli"))

from migrate import declared_sources, migrate_vault  # noqa: E402

SOURCES = [("photos/library", "Photos"), ("messages/synapse", "Messages")]


def _flat(mount: Path, name: str, files: int = 1) -> Path:
    d = mount / "data" / name
    d.mkdir(parents=True)
    for i in range(files):
        (d / f"file-{i}.jpg").write_text("x")
    return d


# ── The rename ─────────────────────────────────────────────────────────────

class TestMigrateVault:

    def test_it_nests_a_flat_directory_under_its_stacklet(self, tmp_path):
        _flat(tmp_path, "photos-library", files=3)

        moves = migrate_vault(tmp_path, SOURCES, dry_run=False)

        assert [(m.display, m.status) for m in moves] == [("Photos", "moved")]
        assert not (tmp_path / "data" / "photos-library").exists()
        assert len(list((tmp_path / "data" / "photos" / "library")
                        .glob("*.jpg"))) == 3

    def test_a_vault_already_on_the_current_layout_is_left_alone(self, tmp_path):
        (tmp_path / "data" / "photos" / "library").mkdir(parents=True)

        assert migrate_vault(tmp_path, SOURCES, dry_run=False) == []

    def test_it_moves_every_source_it_finds(self, tmp_path):
        _flat(tmp_path, "photos-library")
        _flat(tmp_path, "messages-synapse")

        moves = migrate_vault(tmp_path, SOURCES, dry_run=False)

        assert {m.display for m in moves} == {"Photos", "Messages"}
        assert (tmp_path / "data" / "photos" / "library").is_dir()
        assert (tmp_path / "data" / "messages" / "synapse").is_dir()

    def test_a_dry_run_reports_without_touching_anything(self, tmp_path):
        _flat(tmp_path, "photos-library")

        moves = migrate_vault(tmp_path, SOURCES, dry_run=True)

        assert [m.status for m in moves] == ["moved"]
        assert (tmp_path / "data" / "photos-library").is_dir()
        assert not (tmp_path / "data" / "photos").exists()

    def test_it_refuses_when_both_layouts_hold_data(self, tmp_path):
        """No rename can reconcile this. The files are immutable, so the
        two trees cannot be merged without unlocking them, and picking
        one would quietly hide the other."""
        _flat(tmp_path, "photos-library")
        nested = tmp_path / "data" / "photos" / "library"
        nested.mkdir(parents=True)
        (nested / "newer.jpg").write_text("x")

        moves = migrate_vault(tmp_path, SOURCES, dry_run=False)

        assert [m.status for m in moves] == ["occupied"]
        assert (tmp_path / "data" / "photos-library" / "file-0.jpg").exists()
        assert (nested / "newer.jpg").exists()

    def test_one_stuck_directory_does_not_stop_the_others(self, tmp_path,
                                                          monkeypatch):
        _flat(tmp_path, "photos-library")
        _flat(tmp_path, "messages-synapse")

        real_rename = Path.rename

        def rename(self, target):
            if "photos" in str(self):
                raise OSError("Read-only file system")
            return real_rename(self, target)

        monkeypatch.setattr(Path, "rename", rename)
        moves = migrate_vault(tmp_path, SOURCES, dry_run=False)

        assert {m.display: m.status for m in moves} == {
            "Photos": "failed", "Messages": "moved",
        }


# ── The immutable flag ─────────────────────────────────────────────────────

class TestLockedFilesSurvive:
    """The reason this is a rename and not a copy."""

    def test_files_keep_their_immutable_flag_across_the_move(self, tmp_path):
        flat = _flat(tmp_path, "photos-library", files=2)
        subprocess.run(["find", str(flat), "-type", "f",
                        "-exec", "chflags", "uchg", "{}", "+"], check=True)
        try:
            moves = migrate_vault(tmp_path, SOURCES, dry_run=False)

            assert [m.status for m in moves] == ["moved"]
            moved = tmp_path / "data" / "photos" / "library" / "file-0.jpg"
            assert "uchg" in _flags(moved)
            with pytest.raises(PermissionError):
                moved.write_text("tampered")
        finally:
            subprocess.run(["chflags", "-R", "nouchg", str(tmp_path)],
                           check=False)


def _flags(path: Path) -> str:
    out = subprocess.run(["ls", "-lO", str(path)],
                         capture_output=True, text=True, check=True)
    return out.stdout


# ── What gets migrated ─────────────────────────────────────────────────────

class TestDeclaredSources:
    """Archives and snapshots both own a directory under `data/`, so a
    migration has to consider both."""

    def test_it_covers_archives_and_snapshots_of_enabled_stacklets(self,
                                                                   tmp_path):
        repo = tmp_path / "repo"
        (repo / "stacklets" / "messages").mkdir(parents=True)
        (repo / "stacklets" / "messages" / "stacklet.toml").write_text(
            'name = "Messages"\n'
            '[[backup.archive]]\n'
            'name = "media"\n'
            'path = "{data_dir}/messages/media"\n'
            '[[backup.snapshot]]\n'
            'name = "synapse"\n'
            'postgres = { container = "c", database = "d", user = "u" }\n'
        )
        instance = tmp_path / "instance"
        (instance / ".stack").mkdir(parents=True)
        (instance / ".stack" / "messages.setup-done").touch()

        found = declared_sources(repo, instance, tmp_path / "data")

        assert found == [("messages/media", "Messages"),
                         ("messages/synapse", "Messages")]

    def test_a_disabled_stacklet_contributes_nothing(self, tmp_path):
        """Its directory stays flat and stays readable, which is the
        point of keeping both layouts working."""
        repo = tmp_path / "repo"
        (repo / "stacklets" / "photos").mkdir(parents=True)
        (repo / "stacklets" / "photos" / "stacklet.toml").write_text(
            'name = "Photos"\n[[backup.archive]]\nname = "library"\n'
            'path = "{data_dir}/photos"\n'
        )
        instance = tmp_path / "instance"
        (instance / ".stack").mkdir(parents=True)

        assert declared_sources(repo, instance, tmp_path / "data") == []
