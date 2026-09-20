"""Moving a checkout from one release to the next.

A release is a git tag. Updating is therefore four questions: which tags
exist, which one are we on, what does the jump change, and what has to be
restarted because of it. This module answers them. The CLI does the
talking, the confirming and the restarting.

The awkward part is not git, it is the admin's own edits. A tag switch
refuses to run over them, so the update sets them aside and puts them
back, and when the release touched the same lines it has to say so
clearly rather than leave someone staring at a conflict.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


# ── Versions ─────────────────────────────────────────────────────────────

_VERSION_RE = re.compile(
    r"v?(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<label>[0-9A-Za-z]+)(?:\.(?P<num>\d+))?)?$"
)


def version_key(tag: str) -> tuple:
    """Sort key for a release tag, newest last.

    The tuple has a fixed shape so any two tags compare without raising:
    a tag that is not a version sorts below every one that is, rather
    than crashing a listing because someone tagged `nightly`.

    A prerelease ranks below the release it leads to, so `v0.3.0` wins
    over `v0.3.0-beta.3`, and prerelease numbers compare as numbers, so
    beta.10 is newer than beta.9.
    """
    match = _VERSION_RE.match(tag.strip())
    if not match:
        return (0, 0, 0, 0, 0, "", 0)
    label = match.group("label") or ""
    return (
        1,
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        0 if label else 1,
        label,
        int(match.group("num") or 0),
    )


def is_release(tag: str) -> bool:
    """Whether a tag names a version this module can order."""
    return version_key(tag)[0] == 1


def latest_tag(tags) -> str | None:
    """The newest release among these tags, or None if there is none."""
    releases = [t for t in tags if is_release(t)]
    return max(releases, key=version_key) if releases else None


# ── What a jump makes stale ──────────────────────────────────────────────

def _is_framework(path: str) -> bool:
    """A path every stacklet depends on, whatever it declares."""
    return path.startswith("lib/") or path == "stack"


def touches_framework(changed_paths) -> bool:
    """Whether a jump changes code every stacklet runs on."""
    return any(_is_framework(p) for p in changed_paths)


def touched_stacklets(changed_paths) -> set[str]:
    """The stacklets a jump changes files inside, running or not."""
    return {
        p.split("/")[1]
        for p in changed_paths
        if p.startswith("stacklets/") and p.count("/") >= 2
    }


def restart_targets(changed_paths, running) -> list[str]:
    """Which running stacklets a jump leaves stale.

    A stacklet is stale when the release changed a file inside it, and
    every stacklet is stale when the release changed the framework they
    all run on. Stacklets that are not running stay that way: they pick
    the release up whenever they are next started.
    """
    if touches_framework(changed_paths):
        return sorted(running)
    return sorted(touched_stacklets(changed_paths) & set(running))


# ── The checkout ─────────────────────────────────────────────────────────

class Checkout:
    """The git working tree the stack runs from.

    Every method reports rather than raises, because the caller is a CLI
    that has to turn a git failure into a sentence an admin can act on.
    """

    def __init__(self, root: Path):
        self.root = Path(root)

    def _run(self, *args: str) -> tuple[int, str, str]:
        result = subprocess.run(
            ["git", "-C", str(self.root), *args],
            capture_output=True, text=True, timeout=180,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()

    # ── Where we are ──

    def is_git(self) -> bool:
        code, out, _ = self._run("rev-parse", "--is-inside-work-tree")
        return code == 0 and out == "true"

    def tags(self) -> list[str]:
        code, out, _ = self._run("tag")
        return out.splitlines() if code == 0 else []

    def current_tag(self) -> str | None:
        """The tag HEAD sits exactly on, or None when it sits past one."""
        code, out, _ = self._run("describe", "--tags", "--exact-match")
        return out if code == 0 else None

    def describe(self) -> str:
        """Human position: a tag, a tag plus commits, or a bare SHA."""
        code, out, _ = self._run("describe", "--tags", "--always")
        return out if code == 0 else ""

    def contains(self, ref: str) -> bool:
        """Whether HEAD already includes that ref.

        True for anyone working past the newest release: on a branch, on
        main, or on a tag with commits after it. Moving them "up" to that
        release would be a move backwards.
        """
        code, _, _ = self._run("merge-base", "--is-ancestor", ref, "HEAD")
        return code == 0

    def is_dirty(self) -> bool:
        """Tracked files with uncommitted changes.

        Untracked files do not count. git carries them across a checkout
        untouched unless the target tag ships a file of the same name, in
        which case it refuses loudly and the caller relays that.
        """
        _, out, _ = self._run("status", "--porcelain", "--untracked-files=no")
        return bool(out)

    def dirty_paths(self) -> list[str]:
        """Paths of the tracked files that differ from HEAD.

        Porcelain pads each line with two status columns and a space.
        Splitting on whitespace rather than slicing a fixed offset keeps
        the first path intact, whose leading column is a space and is the
        easiest character in the output to lose.
        """
        _, out, _ = self._run("status", "--porcelain", "--untracked-files=no")
        return [line.split(maxsplit=1)[1] for line in out.splitlines() if line.strip()]

    # ── What changes ──

    def changed_paths(self, old: str, new: str) -> list[str]:
        code, out, _ = self._run("diff", "--name-only", old, new)
        return out.splitlines() if code == 0 else []

    def log_subjects(self, old: str, new: str) -> list[str]:
        code, out, _ = self._run("log", "--format=%s", f"{old}..{new}")
        return out.splitlines() if code == 0 else []

    # ── Moving ──

    def fetch_tags(self) -> tuple[bool, str]:
        """Bring in every remote's tags, not just the default remote's.

        A contributor works from a fork, where `origin` is their copy and
        the releases live on the remote they added. Fetching the default
        remote would offer them whatever their fork happens to be tagged
        with, usually nothing, and call it the newest release.
        """
        code, _, err = self._run("fetch", "--all", "--tags", "--quiet")
        return code == 0, err

    def remotes(self) -> list[str]:
        """Remote names, in the order git lists them."""
        code, out, _ = self._run("remote")
        return out.splitlines() if code == 0 else []

    def branch(self) -> str | None:
        """The branch HEAD is on, or None when detached.

        A tag checkout is detached, and a fresh clone is not. The update
        has something different to say to each: one is a developer mid
        work, the other is someone who has never left `main`.
        """
        code, out, _ = self._run("symbolic-ref", "--short", "-q", "HEAD")
        return out if code == 0 and out else None

    def position(self) -> str:
        """The ref to come back to if the update has to be wound back.

        A branch name when HEAD is on one, so restoring it moves the tree
        and not the branch pointer. A commit otherwise, which covers both
        a tag checkout and a detached HEAD past one.
        """
        return self.branch() or self._run("rev-parse", "HEAD")[1]

    def force_checkout(self, ref: str) -> tuple[bool, str]:
        """Restore `ref`, discarding whatever is in the way.

        Only ever used to undo this command's own half-applied move. The
        admin's edits are in the stash at that point, so what gets thrown
        away is a conflicted merge of two things we still have.
        """
        code, out, err = self._run("checkout", "--force", ref)
        return code == 0, (err or out)

    def checkout(self, ref: str) -> tuple[bool, str]:
        code, out, err = self._run("checkout", ref)
        return code == 0, (err or out)

    def stash(self) -> bool:
        """Set local edits aside. False when there was nothing to set."""
        code, out, _ = self._run("stash", "push", "-m", "stack update")
        return code == 0 and "No local changes" not in out

    def stash_pop(self) -> tuple[bool, str]:
        """Put them back. On conflict git keeps the entry, so the edit is
        recoverable and the message says how."""
        code, out, err = self._run("stash", "pop")
        return code == 0, "\n".join(part for part in (err, out) if part)

    def has_stash(self) -> bool:
        _, out, _ = self._run("stash", "list")
        return bool(out)
