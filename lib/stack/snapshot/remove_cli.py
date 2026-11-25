"""The command line of `stack snapshot remove <name>`.

    stack snapshot remove 2026-11-24T2114Z [--yes]
    stack snapshot remove replaced/2026-11-25T2203Z

    remove_cli.run(stck, args)      # what `stack snapshot remove` runs
    remove_cli.entry_answer(entry)  # an entry as `stack snapshot list` prints it

It shows what it deletes (`stack.snapshot.removal`): the name, the
release, the stacklets and the size. It waits for a confirmation, or
`--yes` where there is no terminal:

    $ stack snapshot remove 2026-11-24T2114Z
      Remove the snapshot 2026-11-24T2114Z: 0.4.0-rc.1, docs, 1.2 MB
      ? Remove it? [y/N]

The decisions and the refusals are in `stack.snapshot.removal`; this
module reads the command line and prints. It holds the run locks of the
entry's stacklets from its plan to its delete, so a rollback cannot
start on the snapshot while the admin reads the question.

Debt: a question left open keeps the entry's
stacklets locked: `stack up` and `stack restart` of them refuse until
the terminal answers or closes. Trigger: a report of a held lock, or a
question on a page or in a bot.
"""

from __future__ import annotations

import sys

from .. import runs
from ..prompt import DIM, GREEN, ORANGE, RED, RESET
from . import removal, store
from .answer import ask, print_answer
from .store import SnapshotError

RUN_ACTION = "snapshot remove"
"""The action a remove writes into the run locks it holds (`stack.runs`)."""

__all__ = ["entry_answer", "run"]


def run(stck, args) -> None:
    """`stack snapshot remove <name>`: show the entry, wait for the
    confirmation, delete it. A declined confirmation changes nothing and
    exits 0. Without a terminal it needs `--yes`. Exits 1 when it refused.

    Holds the run locks of the entry's stacklets from the plan to the
    delete, as the action `snapshot remove`, and plans again under them.
    Refused when a run of one of them goes on.

        $ stack snapshot remove 2026-11-24T2114Z --yes --json
        {"ok": true, "removed": "2026-11-24T2114Z", "kind": "snapshot",
         "version": "0.4.0-rc.1", "stacklets": ["docs"], "bytes": 1240000, ...}
    """
    if len(args.stacklets) != 1:
        return print_answer({"error": "Name one thing to remove: stack snapshot remove <name> "
                                "(stack snapshot list shows the names)"}, args.json, None)
    name = args.stacklets[0]
    try:
        first = removal.plan(stck.data, stck.instance_dir, name)
        # The locks of the entry's stacklets, from the plan to the delete:
        # a rollback that starts meanwhile is refused, and one that came
        # before the locks is seen by the second plan.
        with runs.reserve(stck.instance_dir, first.stacklets, action=RUN_ACTION):
            return _remove(stck, args, removal.plan(stck.data, stck.instance_dir, name))
    except runs.AlreadyRunning as busy:
        return print_answer(busy.as_result(), args.json, None)
    except SnapshotError as error:
        return print_answer({"error": str(error)}, args.json, None)


def entry_answer(entry: removal.Entry) -> dict:
    """An entry of `.snapshots/`, as an answer carries it. The size only
    when `removal.plan` measured it.

        entry_answer(removal.replaced(data_root)[0])
        # {"name": "replaced/2026-11-25T2203Z", "kind": "replaced", "path": "...",
        #  "version": "0.4.0-rc.1", "stacklets": ["docs"],
        #  "snapshot": "2026-11-25T2114Z", "finished": True}
    """
    found = {"name": entry.name, "kind": entry.kind, "path": str(entry.path)}
    if entry.kind == removal.INCOMPLETE:
        return {**found, **_sized(entry)}
    if entry.unreadable is not None:
        return {**found, "unreadable": entry.unreadable, **_sized(entry)}
    found.update(version=entry.version, stacklets=entry.stacklets)
    if entry.kind == removal.REPLACED:
        found.update(snapshot=entry.snapshot, finished=entry.finished)
    return {**found, **_sized(entry)}


def _remove(stck, args, entry: removal.Entry) -> None:
    """Show `entry`, wait for the confirmation, delete it, print the
    answer. Runs under the locks of `run`."""
    if not args.json:
        print("\n".join(_removal_lines(entry)))
    if not args.yes:
        if not sys.stdin.isatty():
            return print_answer({"error": "Pass --yes to confirm (no terminal to ask in)"},
                                args.json, None)
        if not ask("Remove it?", quiet=args.json):
            return print_answer({"ok": False, "declined": True, **entry_answer(entry)}, args.json,
                                lambda _: [f"  {DIM}Nothing changed.{RESET}", ""])
    removal.remove(stck.data, entry)
    print_answer({"ok": True, "removed": entry.name, **entry_answer(entry)}, args.json,
                 lambda _: [f"  {GREEN}✓{RESET}  Removed {entry.name}.", ""])


def _sized(entry: removal.Entry) -> dict:
    """The size of an entry, when `removal.plan` measured it."""
    return {} if entry.size_bytes is None else {"bytes": entry.size_bytes}


def _removal_lines(entry: removal.Entry) -> list[str]:
    """What a person reads before the confirmation of a remove. For a
    replaced state it says that the state is the only copy of the data
    from before the rollback, because no snapshot holds it."""
    what = {removal.SNAPSHOT: "the snapshot", removal.REPLACED: "the replaced state",
            removal.INCOMPLETE: "the incomplete folder"}[entry.kind]
    if entry.unreadable is not None:
        facts = f"{RED}unreadable:{RESET} {entry.unreadable}"
    elif entry.kind == removal.INCOMPLETE:
        facts = "a stop halfway left it"
    else:
        facts = f"{entry.version}, {', '.join(entry.stacklets)}"
    size = store.size_text(entry.size_bytes or 0)
    lines = ["", f"  Remove {what} {entry.name}: {facts}, {size}"]
    if entry.kind == removal.REPLACED:
        lines.append(f"  {ORANGE}⚠{RESET} It is the only copy of the state from before the "
                     f"rollback{f' to {entry.snapshot}' if entry.snapshot else ''}.")
    return [*lines, f"  {DIM}{entry.path}{RESET}", ""]
