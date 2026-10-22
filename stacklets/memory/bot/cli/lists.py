"""Archive what is over on every list, on the nightly sweep.

An item ticked today stays on its list for the rest of the day, so
"did we get eggs?" still has an answer in the store. The night after,
it moves to the list's archive, `<bucket>/lists/archive/<slug>/<YYYY-MM>.md`,
and the list holds only what is still open. Ticks made by hand without
a date get today's date first, and leave the night after. On a
calendar, an entry also leaves the night after its date.

Everything moves in one commit by the memory bot, written to Forgejo
against the hash each file was read at, so a tick made while this runs
fails the commit instead of being lost; the next night tries again.

    lists [--dry-run]

No model is involved; the command lives with the curator's other
nightly work because that is where the night is kept.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

sys.path.insert(0, "/app")  # stack.forgejo, stack.lists
# `memory.lib`, for the repository's name and the bot's identity.
sys.path.append("/stacklets")
sys.path.append(str(Path(__file__).resolve().parents[3]))

from stack.cards.commits import commit_subject  # noqa: E402
from stack.forgejo import FileChange, ForgejoClient, ForgejoError  # noqa: E402
from stack.lists import (  # noqa: E402
    LISTS_DIR,
    ListError,
    archive_month,
    archive_over,
    archive_path,
    over,
    read_list,
    stamp_ticks,
)

from memory.lib import BOT_EMAIL, BOT_USERNAME, REPO_NAME, REPO_OWNER, memory_forgejo  # noqa: E402


def _err(msg: str) -> None:
    print(msg, file=sys.stderr)


async def run(llm, argv: list[str]) -> int:
    client = _client()
    if client is None:
        _err("no Forgejo address or admin credentials here; nothing archived")
        return 1
    bucket = os.environ.get("SHARED_BUCKET") or "family"
    try:
        changes, moved = archive_lists(client, bucket=bucket, today=_today())
        if changes and "--dry-run" not in argv:
            client.change_files(REPO_OWNER, REPO_NAME, changes,
                                message=commit_subject("lists", f"archive {moved} item(s) that are over"),
                                author_name=BOT_USERNAME, author_email=BOT_EMAIL)
    except (ForgejoError, OSError) as e:
        _err(f"lists not archived: {e}")
        return 1
    print(f"archived {moved} item(s)" if moved else "nothing to archive")
    return 0


def archive_lists(client: ForgejoClient, *, bucket: str,
                  today: str) -> tuple[list[FileChange], int]:
    """The file changes that archive what is over on every list, and how many moved.

    A file that is not a list is reported and left alone.
    """
    changes: list[FileChange] = []
    moved = 0
    for entry in client.list_dir(REPO_OWNER, REPO_NAME, f"{bucket}/{LISTS_DIR}"):
        path = str(entry.get("path", ""))
        if entry.get("type") != "file" or not path.endswith(".md"):
            continue
        current = client.get_file(REPO_OWNER, REPO_NAME, path) or {}
        text = current.get("content") or ""
        try:
            stamped = stamp_ticks(text, path=path, today=today)
            months = {archive_month(i) for i in over(read_list(stamped, path=path), today)}
        except ListError as e:
            _err(f"  {e}; left alone")
            continue
        slug = Path(path).stem
        archives, shas = {}, {}
        for month in sorted(months):
            target = archive_path(slug, month, bucket=bucket)
            if found := client.get_file(REPO_OWNER, REPO_NAME, target):
                archives[target], shas[target] = found.get("content") or "", found.get("sha", "")
        after, written, items = archive_over(stamped, archives, path=path,
                                               today=today, bucket=bucket)
        if after != text:
            changes.append(FileChange("update", path, after, sha=current.get("sha", "")))
        for target, content in written.items():
            changes.append(FileChange("update" if target in shas else "create",
                                      target, content, sha=shas.get(target, "")))
        if items:
            print(f"  {slug}: {'; '.join(i.text for i in items)}")
        moved += len(items)
    return changes, moved


def _client() -> ForgejoClient | None:
    url = os.environ.get("CODE_URL", "").rstrip("/")
    user = os.environ.get("MATRIX_ADMIN_USER", "")
    password = os.environ.get("MATRIX_ADMIN_PASSWORD", "")
    if not (url and user and password):
        return None
    # The memory bot's commits, whose work the archive is.
    return memory_forgejo(url, admin_user=user, admin_password=password, timeout=60)


def _today() -> str:
    """Today in the household's clock, so midnight is the family's midnight."""
    try:
        zone = ZoneInfo(os.environ.get("TIMEZONE", "").strip() or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        zone = timezone.utc
    return datetime.now(zone).date().isoformat()
