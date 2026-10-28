"""Archive what is over on every list, on the nightly sweep.

An item ticked today stays on its list for the rest of the day, so
"did we get eggs?" still has an answer in the store. The night after,
it moves to the list's archive, `<bucket>/lists/archive/<slug>/<YYYY-MM>.md`,
and the list holds only what is still open. Ticks made by hand without
a date get today's date first, and leave the night after. On a
calendar, an entry also leaves the night after its date.

Everything moves in one commit by the memory bot, through the curator, on
the version each file was read at, so a tick made while this runs fails
the commit instead of being lost; the next night tries again.

    lists [--dry-run]

No model is involved; the command lives with the curator's other
nightly work because that is where the night is kept.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path, PurePosixPath

from stack import clock
from stack.lists import (
    LISTS_DIR,
    ListError,
    archive_month,
    archive_over,
    archive_path,
    over,
    read_list,
    stamp_ticks,
)

from ...memory_api import MEMORY_BOT, CuratorDown, File, MemoryAPI, Refused, put


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


async def run(llm, argv: list[str]) -> int:
    memory = MemoryAPI.from_container(os.environ)
    bucket = os.environ.get("SHARED_BUCKET") or "family"
    try:
        changes, moved = archive_lists(memory.files(f"{bucket}/{LISTS_DIR}"),
                                       bucket=bucket, today=clock.today().isoformat())
        if changes and "--dry-run" not in argv:
            memory.commit(changes, by=MEMORY_BOT, kind="lists",
                          title=f"archive {moved} item(s) that are over")
    except (Refused, CuratorDown) as e:
        _err(f"lists not archived: {e}")
        return 1
    print(f"archived {moved} item(s)" if moved else "nothing to archive")
    return 0


def archive_lists(files: list[File], *, bucket: str, today: str) -> tuple[list[dict], int]:
    """The changes that archive what is over on every list, and how many items moved.

    `files` is the bucket's lists directory as the curator read it: the
    lists, and their archives below it. A file that is not a list is
    reported and left alone.

        changes, moved = archive_lists(memory.files("family/lists"),
                                       bucket="family", today="2026-10-12")
    """
    by_path = {f.path: f for f in files}
    lists = [f for f in files if f.path.endswith(".md")
             and PurePosixPath(f.path).parent.as_posix() == f"{bucket}/{LISTS_DIR}"]
    changes: list[dict] = []
    moved = 0
    for found in lists:
        try:
            stamped = stamp_ticks(found.text, path=found.path, today=today)
            months = {archive_month(i) for i in over(read_list(stamped, path=found.path), today)}
        except ListError as e:
            _err(f"  {e}; left alone")
            continue
        slug = Path(found.path).stem
        targets = [archive_path(slug, month, bucket=bucket) for month in sorted(months)]
        archives = {target: by_path[target].text for target in targets if target in by_path}
        after, written, items = archive_over(stamped, archives, path=found.path,
                                               today=today, bucket=bucket)
        if after != found.text:
            changes.append(put(found.path, after, base=found.version))
        for target, content in written.items():
            changes.append(put(target, content,
                               base=by_path[target].version if target in by_path else ""))
        if items:
            print(f"  {slug}: {'; '.join(i.text for i in items)}")
        moved += len(items)
    return changes, moved


