"""`migrate-photos-to-notes --trailer "<line>"`: the bot-runner half of migration docs/0001.

`stacklets/docs/migrations/0001_photos_to_notes.py` runs this through
`stack.bot_runner.call`, because the move needs the archivist's own
code and its dependencies, which only this container has. It answers
with one JSON line on stdout, `{"moved": [[old, new], ...], "commit":
<sha or null>}`, which the host side records; logs go to stderr. It
needs no Paperless, so it is dispatched before the entrypoint asks for
the Paperless token, which core's env only carries after the first
`stack up docs`.

An instance with no local copy of the family memory has nothing to
move. An old entry that names only its upload is placed by downloading
the upload with the archivist's own Matrix session, the file MicroBot
keeps in the bot's data dir; without one, such an entry fails the run,
so it is retried once the archivist has logged in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import aiohttp

from git_mirror import BOT_EMAIL, BOT_USERNAME
from git_mirror import REPO_NAME as MEMORY_REPO
from migration_photos_to_notes import is_picture_bytes, migrate_photos_to_notes

from cli._mirror import _DATA_DIR, build_mirror_like_bot
from cli._shared import err

# MicroBot writes `<bot id>.session.json` into the session dir the bot
# runner gives the archivist, which is this command's `_DATA_DIR`.
_SESSION_FILE = _DATA_DIR / "archivist-bot.session.json"
_HOMESERVER = os.environ.get("MATRIX_HOMESERVER", "http://stack-messages-synapse:8008")


async def run(argv: list[str]) -> int:
    trailer = argv[argv.index("--trailer") + 1] if "--trailer" in argv[:-1] else ""
    vault = os.environ.get("MEMORY_VAULT_DIR", "")
    mirror = build_mirror_like_bot()
    if not vault or not Path(vault).is_dir() or mirror is None:
        _answer([], None)
        return 0

    client = await mirror.vault_client()
    if client is None:
        err("Forgejo is not reachable, or the family memory repository is not set up yet")
        return 1

    async with aiohttp.ClientSession() as http:
        async def is_picture(mxc: str) -> bool:
            data = await _download(http, mxc)
            return bool(data) and is_picture_bytes(data)

        moved, commit = await migrate_photos_to_notes(
            client, mirror.repo_owner, MEMORY_REPO, root=Path(vault),
            is_picture=is_picture, author_name=BOT_USERNAME, author_email=BOT_EMAIL,
            trailer=trailer,
        )
    _answer(moved, commit)
    return 0


def _answer(moved: list[tuple[str, str]], commit: str | None) -> None:
    print(json.dumps({"moved": [list(m) for m in moved], "commit": commit}))


async def _download(http: aiohttp.ClientSession, mxc: str) -> bytes | None:
    """An upload's bytes through the authenticated media API, as the
    archivist downloads it (`MicroBot._download_media`); None on any
    answer but 200."""
    try:
        token = json.loads(_SESSION_FILE.read_text())["access_token"]
    except (OSError, ValueError, KeyError) as e:
        raise RuntimeError(f"the archivist has no Matrix session yet ({_SESSION_FILE})") from e
    server_name, _, media_id = mxc.removeprefix("mxc://").partition("/")
    url = f"{_HOMESERVER}/_matrix/client/v1/media/download/{server_name}/{media_id}"
    async with http.get(url, headers={"Authorization": f"Bearer {token}"}) as resp:
        return await resp.read() if resp.status == 200 else None
