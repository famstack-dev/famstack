"""The command line of `stack memory diary` and its subcommands.

    _positional(["letters", "--limit", "20"], "memories")   # "letters"
    opt(["--limit", "20"], "--limit", "0")                 # "20"
    options(["--dry-run", "--limit", "20"])                # Options(dry_run=True, limit=20, ...)

A room is a positional, as in every other command that names one, so a
flag's value is never mistaken for it.
"""

from __future__ import annotations

from dataclasses import dataclass

from ...diary.grouping import DEFAULT_GROUP_WINDOW_S
from .progress import err

# Flags that consume the token after them, so the room can be picked out
# of the rest without mistaking a flag's value for it.
_TAKES_A_VALUE = ("--group-window", "--limit", "--preview", "--by", "--event", "--text")


def opt(argv: list[str], flag: str, fallback: str) -> str:
    for i, arg in enumerate(argv):
        if arg == flag and i + 1 < len(argv):
            return argv[i + 1]
    return fallback


def _positional(argv: list[str], fallback: str) -> str:
    """The room, named the way every other command names one.

    `stack messages read <room>`, `join <room>`, `send <room>`: a room
    is a positional everywhere in this CLI, so it is one here too.
    """
    skip = False
    for arg in argv:
        if skip:
            skip = False
            continue
        if arg in _TAKES_A_VALUE:
            skip = True
            continue
        if not arg.startswith("-"):
            return arg
    return fallback


# ── The options of a compile ─────────────────────────────────────────


@dataclass(frozen=True)
class Options:
    """What a compile was asked to do, read once from its command line."""

    room: str
    dry_run: bool
    as_json: bool
    force: bool
    rebuild: bool
    retranscribe: bool
    window: float
    limit: int
    preview: int


def options(argv: list[str]) -> "Options | None":
    """The options of `stack memory diary [<room>] ...`; None, and said
    why, for a flag whose value is not a number."""
    room_arg = _positional(argv, "memories")
    dry_run = "--dry-run" in argv
    # A machine-readable report of the cards on stdout, for the archivist,
    # which posts them. Progress stays on stderr.
    as_json = "--json" in argv
    # --force re-reads and re-summarises but keeps transcripts: whisper
    # costs minutes of GPU per recording and its output only changes
    # when its config or vocabulary does. --retranscribe is that case.
    force = "--force" in argv
    # --rebuild reads every entry whose card the compile owns under the
    # current prompts and writes the cards that come out different. A
    # reading already made under them costs nothing.
    rebuild = force or "--rebuild" in argv
    retranscribe = "--retranscribe" in argv
    try:
        window = float(opt(argv, "--group-window",
                            str(DEFAULT_GROUP_WINDOW_S)))
    except ValueError:
        err("--group-window wants a number of seconds")
        return None
    try:
        limit = int(opt(argv, "--limit", "0"))
    except ValueError:
        err("--limit wants a number of messages")
        return None
    # A limited run sees part of the room: an entry cut at the edge, a
    # reply without the entry it answers. A card written from that is
    # wrong, and so is every page built from it, so whatever else was
    # asked, a limited run is a dry run.
    if limit and not dry_run:
        err(f"--limit {limit} reads part of the room: a dry run, nothing is written")
    dry_run = dry_run or bool(limit)
    try:
        preview = int(opt(argv, "--preview", "0"))
    except ValueError:
        preview = -1
    if "--preview" in argv and preview < 1:
        err("--preview wants a number of entries")
        return None
    # A preview writes nothing either: its report is the output.
    dry_run = dry_run or bool(preview)
    return Options(room=room_arg, dry_run=dry_run, as_json=as_json, force=force,
                   rebuild=rebuild, retranscribe=retranscribe, window=window,
                   limit=limit, preview=preview)
