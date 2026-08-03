"""stack memory capture — put something into the family's memory.

Reading the vault has always had a front door (`stack memory search`,
`stack memory person`, `stack memory topic`). Writing to it had exactly
one, and you had to be the archivist watching a Matrix room to use it.
So the agent could read everything the family knows and add nothing to
it: told "merk dir, dass Bart eine Erdnussallergie hat", it had nowhere
to put that.

This is the write door, and it is deliberately the *same* pipeline the
archivist runs, not a second way in. A note filed here is classified,
tagged, summarised, mirrored and attributed exactly as one pasted into a
room, because it is the same code doing it.

Examples:

    stack memory capture "Bart has a peanut allergy" --by homer
      Captured: Bart's peanut allergy
        vault: homer/notes/2026/08/barts-peanut-allergy-3a338e.md
        scope: homer

    stack memory capture "Zelt ist kaputt" --by marge --bucket family/camping

`--by` attributes the commit, so every entry says who filed it. Without
`--bucket` the note lands in that person's own bucket; a topic path like
`family/camping` files it under the shared topic instead, the same
routing a message in a topic room gets.

WHY THE COMMAND IS HERE AND THE CODE IS NOT
    Memory owns the vault, so filing into it is a memory command. The
    handler still lives at `stacklets/docs/bot/cli/capture.py` because
    the capture pipeline does, and the pipeline is under docs for
    historical reasons rather than good ones: it writes no Paperless
    document and touches no docs resource except the person-name roster.
    Moving it is its own piece of work. Until then this dispatcher points
    across, and the only thing that changes afterwards is the entrypoint
    path on the next line.
"""

HELP = "File a note, link, or image into the family memory vault"

from pathlib import Path

from stack.bot_runner import dispatch

# Points at the docs stacklet until the capture pipeline moves to memory.
# Nothing else in this file, and nothing in any caller, knows that.
_ENTRYPOINT = "/stacklets/docs/bot/cli_entrypoint.py"

_USAGE = ('usage: stack memory capture "<text|url>" --by <person> [--bucket <scope>]\n'
          "       stack memory capture --file <path> --by <person> [--bucket <scope>]")


def run(args, stacklet, config):
    if not args:
        return {"error": _USAGE}

    # The pipeline runs in a container that cannot see the host's disk, so
    # a `--file` path is resolved here and the bytes ride in on stdin. The
    # name travels separately because it is what the mime guess and the
    # vault entry's display link are built from.
    argv, payload = list(args), None
    if "--file" in argv:
        i = argv.index("--file")
        if i + 1 >= len(argv):
            return {"error": _USAGE}
        path = Path(argv[i + 1]).expanduser()
        try:
            payload = path.read_bytes()
        except OSError as e:
            return {"error": f"cannot read {path}: {e}"}
        argv[i:i + 2] = ["--stdin-file", path.name]

    return dispatch(_ENTRYPOINT, "capture", *argv, stdin_bytes=payload)
