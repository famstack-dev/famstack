"""host_api: the commit the running host API started from.

The host API (`stacklets/core/famstack-api.py`) is core's process on the
Mac, run by launchd. Containers carry the commit they were started from as
their `stack.commit` label; a launchd process has no label, so the host
API writes its commit into a stamp file when it starts:

    from stack import host_api

    host_api.record_start(instance_dir, "9a5bdc9")   # famstack-api.py, at start
    host_api.started_commit(instance_dir)            # "9a5bdc9"

    <instance>/.stack/host-api.commit                9a5bdc9

Why the process writes it, not core's start hook: launchd starts the
process again on its own (`KeepAlive`, a login after a reboot), each time
from the code on disk at that moment. Only the process knows which code
that was. A stamp the hook wrote would name the code of the last
`stack up core`, and a reboot after an update would then report old code
that no longer runs.

`Stack.host_api_stale()` compares the stamp with the checkout by the same
rule a release uses for a stale container, and both core's start hook and
`stack list` read that answer. A host API with no stamp at all predates
it: core's start restarts it (`has_stamp`), list and doctor say nothing.
This module only reads and writes the stamp; it knows nothing of git,
ports or launchd.

The file is not named `*.up`, because `.stack/<id>.up` is the stamp of a
running stacklet without containers, and a `host-api.up` would read as a
stacklet called `host-api`.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["has_stamp", "record_start", "started_commit", "stamp_path"]

STAMP_NAME = "host-api.commit"


def record_start(instance_dir: Path, commit: str) -> None:
    """Note that the host API started from `commit`, for this instance.

    Writes `<instance_dir>/.stack/host-api.commit`, creating `.stack/` if
    needed, and replaces an earlier stamp. `commit` is what
    `git rev-parse --short HEAD` says, or "unknown" outside a git tree.

        record_start(Path("~/famstack"), "9a5bdc9")
    """
    stamp = stamp_path(instance_dir)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(commit + "\n")


def has_stamp(instance_dir: Path) -> bool:
    """Whether the running host API wrote a stamp at all.

    False for a host API from before the stamp existed. Core's start
    restarts such a one, because nothing says which code it runs; list
    and doctor only say nothing about it. True for a stamp of "unknown"
    too: that host API started outside a git tree, and restarting it
    would not make its code known.

        has_stamp(Path("~/famstack"))   # True once the host API started
    """
    return stamp_path(instance_dir).is_file()


def started_commit(instance_dir: Path) -> str:
    """The commit the host API last started from, or "" when not known.

    "" for a host API that predates the stamp, or one that started outside
    a git tree: an unknown answer is not a finding, as for a container
    without the label.

        started_commit(Path("~/famstack"))   # "9a5bdc9"
    """
    try:
        commit = stamp_path(instance_dir).read_text().strip()
    except OSError:
        return ""
    return "" if commit == "unknown" else commit


def stamp_path(instance_dir: Path) -> Path:
    """Where the stamp lives: `<instance_dir>/.stack/host-api.commit`."""
    return Path(instance_dir) / ".stack" / STAMP_NAME
