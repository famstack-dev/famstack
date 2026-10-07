"""The menu bar app: built from the checkout, installed into Applications.

The app is signed ad hoc by `apps/macos/build.sh`, which makes it run on the
Mac that built it and nowhere else, so it ships as source and every install
is a build. Installing again after `stack update` is how it is updated.

    result = install(repo_root)          # {"installed": "/Applications/famstack.app"}
    installed()                          # the bundle's path, or None
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

BUNDLE = "famstack.app"
# The system's Applications folder first; the user's own when that one is
# not writable, which is the case for an account that is not an admin.
FOLDERS = [Path("/Applications"), Path(os.path.expanduser("~/Applications"))]
# A release build with SwiftPM takes about a minute on an M1.
BUILD_TIMEOUT_SECS = 900


def installed(folders: list[Path] = FOLDERS) -> Path | None:
    """Where the app is installed, or None."""
    for folder in folders:
        if (folder / BUNDLE).exists():
            return folder / BUNDLE
    return None


def install(repo_root: Path, *, folders: list[Path] = FOLDERS, open_after: bool = True) -> dict:
    """Build the app from the checkout and put it in the first writable folder.

    An app already there is replaced as a whole, so nothing of an older
    build survives. A failed build installs nothing.
    """
    script = Path(repo_root) / "apps" / "macos" / "build.sh"
    if not script.exists():
        return {"error": "this checkout has no menu bar app (apps/macos); it arrived in 0.4.0"}

    built, error = _build(script)
    if built is None:
        return {"error": error}

    target = _writable(folders)
    if target is None:
        return {"error": "no Applications folder to write to: " + ", ".join(str(f) for f in folders)}
    destination = target / BUNDLE
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(built, destination, symlinks=True)

    if open_after:
        subprocess.run(["open", str(destination)], check=False)
    return {"installed": str(destination)}


def _build(script: Path) -> tuple[Path | None, str]:
    """Run the build script; the bundle it prints, or why it failed."""
    try:
        done = subprocess.run([str(script)], capture_output=True, text=True,
                              timeout=BUILD_TIMEOUT_SECS)
    except FileNotFoundError:
        return None, f"cannot run {script}"
    except subprocess.TimeoutExpired:
        return None, f"the build took longer than {BUILD_TIMEOUT_SECS // 60} minutes"
    if done.returncode != 0:
        tail = (done.stderr or done.stdout).strip().splitlines()[-5:]
        hint = ("" if shutil.which("swift") else
                " Swift is missing: install the Command Line Tools with `xcode-select --install`.")
        return None, "the build failed: " + " / ".join(tail) + hint
    lines = done.stdout.strip().splitlines()
    built = Path(lines[-1]) if lines else None
    if built is None or not built.is_dir():
        return None, "the build printed no app bundle"
    return built, ""


def _writable(folders: list[Path]) -> Path | None:
    for folder in folders:
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            continue
        if os.access(folder, os.W_OK):
            return folder
    return None
