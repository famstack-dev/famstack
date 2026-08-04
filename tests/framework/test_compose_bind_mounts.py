"""A script mounted over a command has to be executable in the repo.

Bind mounting a file into a container replaces the image's copy *and* its
permissions. A Dockerfile that carefully does `chmod +x` on the file it
copied buys nothing once compose mounts the host's version over the top:
what the container gets is whatever mode the file has in the working tree.

That is not hypothetical. Mounting `stacklets/agent/client/stack` over
`/usr/local/bin/stack` so the shim could be edited without a rebuild took
the agent's only route to the CLI offline, because the repo file was 644.
Every vault tool failed at once with `executable file not found in $PATH` --
from a two-line compose change that looked like pure convenience.

The mode is tracked by git, so this is an audit of the repo rather than of
one machine: a fresh clone gets the same bit, and so does CI.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# `- ./source:/container/target[:ro]` in a compose volumes list. Sources
# built from ${VARIABLES} are host data directories, not repo files, and
# are skipped -- there is nothing in the tree to check their mode against.
_MOUNT_RE = re.compile(
    r"^\s*-\s+(?P<source>\.[^\s:]+):(?P<target>/[^\s:]+)(?::[a-z,]+)?\s*$",
    re.MULTILINE,
)

# Where something has to be executable to be reachable at all: these are the
# directories on a container's PATH.
_BIN_DIRS = ("/usr/local/bin/", "/usr/bin/", "/bin/", "/usr/local/sbin/")


def _compose_files() -> list[Path]:
    return sorted(REPO_ROOT.glob("stacklets/*/docker-compose*.yml"))


def _mounts_onto_commands() -> list[tuple[Path, Path, str]]:
    """(compose file, host source, container target) for every bind mount
    that lands on a PATH directory."""
    found = []
    for compose in _compose_files():
        for match in _MOUNT_RE.finditer(compose.read_text(encoding="utf-8")):
            target = match.group("target")
            if not target.startswith(_BIN_DIRS):
                continue
            source = (compose.parent / match.group("source")).resolve()
            found.append((compose, source, target))
    return found


def test_a_mounted_command_is_executable_in_the_tree():
    """Otherwise the container has the file and cannot run it."""
    not_executable = [
        (compose.relative_to(REPO_ROOT), source.relative_to(REPO_ROOT), target)
        for compose, source, target in _mounts_onto_commands()
        if source.is_file() and not os.access(source, os.X_OK)
    ]

    assert not not_executable, "\n".join(
        f"{compose} mounts {source} over {target}, but {source} is not "
        f"executable — the container will report 'executable file not found'. "
        f"Fix with: chmod +x {source}"
        for compose, source, target in not_executable
    )


def test_a_mounted_command_actually_exists():
    """A typo in the source path mounts an empty directory over the command,
    which fails the same way and reads like a missing binary."""
    missing = [
        (compose.relative_to(REPO_ROOT), match_source, target)
        for compose, match_source, target in _mounts_onto_commands()
        if not match_source.exists()
    ]

    assert not missing, f"compose mounts a source that is not in the tree: {missing}"


def test_the_audit_is_looking_at_something():
    """Guards the guard: if the mount pattern stops matching, both tests
    above pass by finding nothing, which is how an audit quietly dies."""
    found = _mounts_onto_commands()

    assert found, (
        "no compose bind mount onto a PATH directory was found — either the "
        "agent's `stack` shim mount was removed, or _MOUNT_RE no longer "
        "matches the compose volume syntax"
    )
