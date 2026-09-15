"""Prepare the paths the wiki container expects before it starts.

The media archive is mounted into the rendered tree at `<brain>/media`
so pages and the originals they embed are served from one site. Docker
cannot create that mountpoint on its own: its parent is a read-only
bind mount, `mkdir` fails there, and the container exits before Quartz
runs. An empty directory on disk is the whole fix.

It has to run on every start, not once at install. Brain is a
projection and is re-cloned whenever it is rebuilt from source, and a
fresh clone carries no empty directories because git does not track
them.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from lib import brain_path_for  # noqa: E402

# The rule keeps a stale writer honest. Nothing should put bytes in the
# mountpoint on the host -- the archive is elsewhere and the container
# sees it through the mount -- but if something ever does, the curator
# must not commit them into the projection.
EXCLUDE_RULE = "media/"
EXCLUDE_NOTE = (
    "# Mountpoint for the media archive, which lives outside this repo.\n"
    "# Empty here; the wiki container mounts the store over it.\n"
)


def run(ctx):
    brain = Path(brain_path_for(ctx.stack.data))
    mountpoint = brain / "media"
    try:
        mountpoint.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Worth saying out loud: without it the wiki will not start, and
        # the error Docker gives for that names a path inside its own
        # overlay and explains nothing.
        return {"ok": False, "message": f"could not create {mountpoint}: {e}"}

    _exclude(brain)
    return {"ok": True}


def _exclude(brain: Path) -> None:
    """Add the mountpoint to the repo's local excludes, once.

    `.git/info/exclude` rather than `.gitignore` because Quartz globs
    its content directory with `gitignore: true`: a tracked rule would
    hide the archive from the build and every link into it would answer
    404. Git honours both files; the site generator reads only the one.
    """
    info = brain / ".git" / "info"
    if not info.parent.is_dir():
        return
    exclude = info / "exclude"
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if EXCLUDE_RULE in (line.strip() for line in existing.splitlines()):
            return
        info.mkdir(parents=True, exist_ok=True)
        separator = "" if not existing or existing.endswith("\n") else "\n"
        exclude.write_text(
            f"{existing}{separator}{EXCLUDE_NOTE}{EXCLUDE_RULE}\n",
            encoding="utf-8",
        )
    except OSError:
        # Not worth failing a start over: the rule is a guard against a
        # bug that does not exist yet, and the mountpoint above is the
        # part the container needs.
        pass
