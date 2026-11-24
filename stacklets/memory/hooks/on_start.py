"""Prepare the folder the wiki container renders before it starts.

The wiki container mounts the wiki's input (`WIKI_INPUT_DIR`) as Quartz's
content. The curator fills it with the family's shared part of the brain
(`bot/wiki_input.py`), but the wiki container can start first, and a bind
mount of a missing folder leaves it to Docker to make one on the host. An
empty folder made here, as the admin, is the whole fix.

It runs on every start, because the folder is disposable: deleting it is
how an admin makes the curator write it again.
"""

from __future__ import annotations

from pathlib import Path


def run(ctx):
    wiki_input = Path(ctx.env["WIKI_INPUT_DIR"])
    try:
        wiki_input.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        # Worth saying out loud: without it the wiki will not start, and
        # the error Docker gives for that names a path inside its own
        # overlay and explains nothing.
        return {"ok": False, "message": f"could not create {wiki_input}: {e}"}
    return {"ok": True}
