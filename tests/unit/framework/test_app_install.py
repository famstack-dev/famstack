"""`stack app install`: the menu bar app, built from the checkout and installed.

The app is built on the Mac that runs it (it is signed for that Mac only),
so installing means: run the checkout's build script, put the bundle in an
Applications folder, replace whatever version was there. Running it again
after `stack update` is how the app is updated.

These tests drive `install` against a checkout whose build script writes a
stand-in bundle, so they run without Swift.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))

from stack import app  # noqa: E402

BUILD = """#!/bin/sh
set -e
cd "$(dirname "$0")"
mkdir -p build/famstack.app/Contents
echo "{marker}" > build/famstack.app/Contents/marker
echo "$(pwd)/build/famstack.app"
"""


def _checkout(tmp_path: Path, marker: str = "new", script: str = BUILD) -> Path:
    root = tmp_path / "checkout"
    build = root / "apps" / "macos" / "build.sh"
    build.parent.mkdir(parents=True, exist_ok=True)
    build.write_text(script.format(marker=marker))
    build.chmod(build.stat().st_mode | stat.S_IEXEC)
    return root


def _marker(bundle: Path) -> str:
    return (bundle / "Contents" / "marker").read_text().strip()


class TestInstallingTheApp:

    def test_the_built_app_lands_in_applications(self, tmp_path):
        root = _checkout(tmp_path)
        apps = tmp_path / "Applications"
        apps.mkdir()

        result = app.install(root, folders=[apps], open_after=False)

        assert result["installed"] == str(apps / "famstack.app")
        assert _marker(apps / "famstack.app") == "new"

    def test_installing_again_replaces_the_old_app(self, tmp_path):
        # The update path: an older build is there and must not survive in parts.
        apps = tmp_path / "Applications"
        old = apps / "famstack.app" / "Contents"
        old.mkdir(parents=True)
        (old / "marker").write_text("old")
        (old / "left-over").write_text("from the old build")

        app.install(_checkout(tmp_path), folders=[apps], open_after=False)

        assert _marker(apps / "famstack.app") == "new"
        assert not (apps / "famstack.app" / "Contents" / "left-over").exists()

    def test_a_folder_that_cannot_be_written_falls_back_to_the_next(self, tmp_path):
        locked = tmp_path / "Applications"
        locked.mkdir()
        locked.chmod(0o555)
        home_apps = tmp_path / "home" / "Applications"
        try:
            result = app.install(_checkout(tmp_path), folders=[locked, home_apps], open_after=False)
        finally:
            locked.chmod(0o755)

        assert result["installed"] == str(home_apps / "famstack.app")

    def test_a_failed_build_installs_nothing_and_says_why(self, tmp_path):
        apps = tmp_path / "Applications"
        apps.mkdir()
        root = _checkout(tmp_path, script="#!/bin/sh\necho 'error: no such module SwiftUI' >&2\nexit 1\n")

        result = app.install(root, folders=[apps], open_after=False)

        assert "error" in result
        assert not (apps / "famstack.app").exists()

    def test_a_checkout_without_the_app_says_so(self, tmp_path):
        result = app.install(tmp_path, folders=[tmp_path], open_after=False)

        assert "apps/macos" in result["error"]


def test_installed_finds_the_app_in_either_folder(tmp_path):
    home_apps = tmp_path / "home" / "Applications"
    (home_apps / "famstack.app").mkdir(parents=True)

    assert app.installed(folders=[tmp_path / "Applications", home_apps]) == home_apps / "famstack.app"
    assert app.installed(folders=[tmp_path / "nowhere"]) is None


def test_the_default_folders_are_the_system_then_the_users_applications():
    assert app.FOLDERS == [Path("/Applications"), Path(os.path.expanduser("~/Applications"))]
