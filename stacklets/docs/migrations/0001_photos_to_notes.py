"""Move the photos filed as bookmarks to notes.

A photo posted outside the documents room used to be filed under
`bookmarks/` and is a note now. This moves the ones filed before, in one
commit by the archivist through the memory's curator, each file written
on the version it was read at, so an edit made meanwhile makes the
curator refuse the commit and nothing is written. The work runs in the
bot runner, which has the archivist's code (`migrate-photos-to-notes` in
`bot/cli_entrypoint.py`).
Undo: revert the commit in family/memory.

Before the framework ran migrations the archivist ran this itself and
left a marker file in its data dir; the marker is removed here. An
instance that moved its photos that way finds nothing left to move.
"""

from __future__ import annotations

import json

from stack.bot_runner import call

REPO = "family/memory"
ENTRYPOINT = "/stacklets/docs/bot/cli_entrypoint.py"


def run(ctx):
    answer = json.loads(call(ENTRYPOINT, "migrate-photos-to-notes",
                             "--trailer", ctx.trailer).strip().splitlines()[-1])
    (ctx.stack.data / "docs" / "bot" / "migration-photos-to-notes.done").unlink(missing_ok=True)
    if answer["moved"]:
        ctx.step(f"Docs: moved {len(answer['moved'])} photo(s) from bookmarks to notes")
    return answer["commit"]
