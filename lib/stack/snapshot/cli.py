"""The command line of `stack snapshot`: take a snapshot, list the snapshots.

    stack snapshot create docs [photos ...]    stop, clone the state, start again
    stack snapshot list                        the snapshots, newest first

    add_parser(sub)       # in lib/stack/cli.py, next to the other commands
    run(stck, args)       # what `stack snapshot ...` runs

`create` takes a snapshot of the named stacklets in four steps. It holds
their run locks (`stack.runs`), because no other run may start or stop
them meanwhile:

    record   what each stacklet runs, while it still runs (`stack.manifest`)
    stop     the stacklets that run (`CLI.down_many`)
    take     clone their data folders and the configuration (`stack.snapshot.store`)
    start    the stacklets that ran before, also when the take failed

The record comes first, because a running container names the image it
runs. A stacklet that was stopped stays stopped. A take that fails still
starts the stacklets again, because the family must not lose a page
because of a snapshot.

    $ stack snapshot create docs
      ✓  Snapshot 2026-11-24T2114Z: docs
    $ stack snapshot list
      2026-11-24T2114Z  2026-11-24 22:14  docs  0.4.0-rc.1

`--json` prints the answer as JSON. The exit code is 1 when the command
refused or a step failed.

The restart that applies a release (`stack restart` in `stack.cli`) takes
its snapshot through the same module, in three calls around its own stop
and start. They print what the restart says and exit 1 when no snapshot
was taken; the decisions are in `stack.snapshot.release`. A fourth call,
`waiting`, refuses the start of a stacklet whose last snapshot failed:

    before = record_or_exit(stck, ["docs"], no_snapshot=False)   # while docs runs
    cli.down_many(["docs"])
    taken = take_or_exit(stck, before)                           # nothing starts without it
    cli.up_many(["docs"])
    keep_and_say(stck, taken)                                    # the newest 3 stay

This module reads the command line and prints the answer. It calls the
store for the snapshot, and the CLI to start and stop. The snapshot's
folder and its rules are in `stack.snapshot.store`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import manifest as manifests
from .. import run_log, runs
from ..images import DockerImages
from ..manifest import Manifest, ManifestError
from ..prompt import DIM, GREEN, RED, RESET
from . import pending, release, store
from .release import Before, anything_up
from .store import SnapshotError, Taken, Volume

__all__ = ["add_parser", "keep_and_say", "record_or_exit", "run", "take_or_exit", "waiting"]


def add_parser(sub) -> None:
    """Add `stack snapshot` and its options to `sub`, the CLI's subparsers."""
    p = sub.add_parser("snapshot")
    p.add_argument("verb", nargs="?", default="list", choices=("create", "list"),
                   help="create a snapshot of stacklets, or list the snapshots")
    p.add_argument("stacklets", nargs="*", default=[],
                   help="(create) the stacklets to take a snapshot of")
    p.add_argument("--json", action="store_true", help="Output as JSON")


def run(stck, args, volume: Volume | None = None) -> None:
    """`stack snapshot <verb>` on the instance `stck`, with `args` from
    `add_parser`'s parser. Prints the answer. Exits 1 when the command
    refused or failed. `volume` is the file system of the data folder,
    `store.ThisVolume` by default. A test stands in another one.

        $ stack snapshot create docs --json
        {"ok": true, "snapshot": "2026-11-24T2114Z", "stacklets": ["docs"],
         "cloned": true, "started": ["docs"], ...}
    """
    if args.verb == "create":
        result = _create(stck, _ids(args.stacklets), volume)
        return _print(result, args.json, _created_lines)
    return _print(_listed(stck), args.json, _list_lines)


# ── The snapshot of a restart ────────────────────────────────────────────

def record_or_exit(stck, ids: list[str], no_snapshot: bool) -> Before:
    """`release.before_stop` for a restart of `ids`, before it stops them.
    `no_snapshot` is `stack restart --no-snapshot`. Exits 1 with the cause
    and the two ways on when the record fails; then nothing has stopped.
    Names each stacklet whose commit is unknown, which gets no snapshot.

        record_or_exit(stck, ["docs"], no_snapshot=False)   # Before(stacklets={"docs": …}, …)
    """
    try:
        before = release.before_stop(stck, ids, skip=no_snapshot)
    except pending.Unreadable as error:
        _exit(stck, [error.stacklet], str(error), release.unreadable_lines(error))
    except SnapshotError as error:
        _exit(stck, ids, str(error), release.refused_lines(str(error), [], ids))
    if before.unknown:
        print("\n".join(release.unknown_lines(before.unknown)), file=sys.stderr)
    return before


def take_or_exit(stck, before: Before) -> Taken | None:
    """`release.after_stop` between the stop and the start of the
    stacklets `before` recorded: the snapshot, or None when `before` is
    empty. Exits 1 with the cause and the two ways on when the take
    fails; then they stay stopped and wait for their snapshot
    (`waiting`).

        take_or_exit(stck, before).snapshot.name   # "2026-11-25T2114Z"
    """
    stacklets = list(before.stacklets)
    try:
        return release.after_stop(stck, before)
    except release.StillRunning as error:
        waits = [sid for sid in stacklets if sid not in error.ids]
        _exit(stck, stacklets, str(error), release.refused_lines(str(error), waits, error.ids))
    except SnapshotError as error:
        _exit(stck, stacklets, str(error), release.refused_lines(str(error), stacklets, []))


def waiting(stck, stacklet_id: str) -> dict | None:
    """The answer of a start refused because the stacklet's last snapshot
    failed (`stack.snapshot.pending`), with the two ways on. None when
    the stacklet waits for nothing. `stack up` and every restart ask it
    before they start a stacklet.

        waiting(stck, "docs")
        # {"error": "docs waits for its snapshot: … stack restart docs …"}
    """
    if not pending.waiting(stck.instance_dir, stacklet_id):
        return None
    return {"error": (f"{stacklet_id} waits for its snapshot, which failed at the last restart. "
                      f"Take it with: stack restart {stacklet_id}. Or start without one: "
                      f"stack restart {stacklet_id} --no-snapshot"),
            "stacklet": stacklet_id}


def keep_and_say(stck, taken: Taken | None) -> None:
    """After a restart that took the snapshot `taken`: keep the newest
    snapshots (`release.prune`) and print what was taken and removed.
    Says nothing without a snapshot.

        keep_and_say(stck, taken)
        #   ✓ Snapshot 2026-11-25T2114Z: docs
        #     Removed the snapshot 2026-11-01T0900Z; the newest 3 stay.
    """
    if taken is not None:
        print("\n".join(release.taken_lines(taken, release.prune(stck.data, taken.snapshot.name))))


def _exit(stck, ids: list[str], cause: str, lines: list[str]):
    """Record a restart without a snapshot as the result of each run of
    `ids`, print `lines` on stderr, and exit 1."""
    for sid in ids:
        run_log.record_result(stck.instance_dir, sid,
                              {"error": f"No snapshot: {cause}", "stacklet": sid})
    print("\n".join(lines), file=sys.stderr)
    sys.exit(1)


# ── Taking a snapshot ────────────────────────────────────────────────────

def _create(stck, ids: list[str], volume: Volume | None) -> dict:
    """Take a snapshot of the stacklets `ids`: record, stop, take, start
    again. The answer, with `error` when it refused or a step failed."""
    if not ids:
        return {"error": "Name the stacklets: stack snapshot create <id> [<id> ...]"}
    known = {s["id"]: Path(s["path"]) for s in stck.discover()}
    unknown = [sid for sid in ids if sid not in known]
    if unknown:
        return {"error": f"Unknown stacklet: {', '.join(unknown)}"}
    with runs.hold(stck.instance_dir, ids):
        try:
            record = _record(stck, {sid: known[sid] for sid in ids}, anything_up(stck))
        except ManifestError as error:
            return {"error": f"The snapshot cannot record what runs: {error}"}
        was_up = [sid for sid in ids if record.stacklets[sid].up]
        result: dict = {}
        try:
            result = _stop_and_take(stck, {sid: known[sid] for sid in ids}, record, was_up,
                                    volume)
        finally:
            # On every path out, also an exception: a snapshot must never
            # leave the family without a page that ran before it.
            started = _start_again(stck, was_up)
        return {**result, "started": started}


def _record(stck, stacklets: dict[str, Path], up) -> Manifest:
    """The manifest of a snapshot of `stacklets`, taken while they still
    run: the checkout's release and commit, and each stacklet's record.
    `up(id)` says whether a stacklet has anything up."""
    from ..cli import running_version_of

    commits = stck.started_commits()
    images = DockerImages()
    records = {sid: manifests.record_stacklet(path, stck.data / sid, up=up(sid),
                                              started_from=commits.get(sid), docker=images)
               for sid, path in stacklets.items()}
    return Manifest(kind="snapshot", name="", version=running_version_of(stck),
                    commit=stck._git_commit(), stacklets=records)


def _stop_and_take(stck, stacklets: dict[str, Path], record: Manifest, was_up: list[str],
                   volume: Volume | None) -> dict:
    """Stop the stacklets in `was_up`, then take the snapshot. The answer
    without `started`, which the caller adds."""
    from ..cli import CLI

    if was_up:
        stopped = CLI(stck).down_many(was_up)
        if not stopped.get("ok"):
            return {"error": "Some stacklets did not stop, so no snapshot was taken: "
                             + ", ".join(e["stacklet"] for e in stopped.get("errors", []))}
    try:
        # Asked again after the stop, with the same wide answer: a
        # container that did not stop keeps the take from cloning.
        up = anything_up(stck)
        taken = store.take(stck.data, stck.instance_dir, stacklets, record,
                           running=up, volume=volume)
    except SnapshotError as error:
        return {"error": f"No snapshot: {error}"}
    return {"ok": True, "snapshot": taken.snapshot.name, "path": str(taken.snapshot.path),
            "stacklets": list(stacklets), "cloned": taken.cloned,
            "version": record.version}


def _start_again(stck, was_up: list[str]) -> list[str]:
    """Start the stacklets in `was_up` again and return those that started.
    A start that fails is said on stderr; the snapshot stays as it was."""
    from ..cli import CLI

    if not was_up:
        return []
    started = CLI(stck).up_many(was_up)
    if not started.get("ok"):
        failed = ", ".join(e["stacklet"] for e in started.get("errors", []))
        print(f"\n  {RED}✗{RESET}  These stacklets did not start again: {failed}. "
              f"Start them with: stack up {failed.replace(', ', ' ')}\n", file=sys.stderr)
    return list(started.get("started", []))


# ── Listing ──────────────────────────────────────────────────────────────

def _listed(stck) -> dict:
    """The snapshots, newest first, as the answer of `stack snapshot list`."""
    return {"snapshots": [_listed_one(s) for s in store.snapshots(stck.data)]}


def _listed_one(snapshot: store.Snapshot) -> dict:
    """One snapshot of the list. One with a broken manifest says why."""
    listed = {"name": snapshot.name, "taken": snapshot.taken.isoformat(),
              "path": str(snapshot.path)}
    if snapshot.manifest is None:
        return {**listed, "unreadable": snapshot.unreadable}
    return {**listed, "stacklets": list(snapshot.manifest.stacklets),
            "version": snapshot.manifest.version, "commit": snapshot.manifest.commit}


# ── Printing ─────────────────────────────────────────────────────────────

def _ids(given: list[str]) -> list[str]:
    """The stacklet ids of the command line, also when they are comma separated.

        >>> _ids(["docs,photos", "memory"])
        ['docs', 'photos', 'memory']
    """
    return [sid for item in given for sid in item.split(",") if sid]


def _print(result: dict, as_json: bool, lines) -> None:
    """Print `result` as JSON, its error on stderr, or `lines(result)`.
    Exits 1 after an error, as every `stack` command does."""
    if as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif "error" in result:
        print(f"\n  {RED}✗{RESET}  {result['error']}\n", file=sys.stderr)
    else:
        print("\n".join(lines(result)))
    if "error" in result:
        sys.exit(1)


def _created_lines(result: dict) -> list[str]:
    """What a person reads after `create`."""
    lines = [f"\n  {GREEN}✓{RESET}  Snapshot {result['snapshot']}: {', '.join(result['stacklets'])}"]
    if not result["cloned"]:
        lines.append(f"     {DIM}The data folder is not on APFS, so the files were copied, "
                     f"not cloned.{RESET}")
    lines.append(f"     {DIM}{result['path']}{RESET}\n")
    return lines


def _list_lines(result: dict) -> list[str]:
    """What a person reads for `list`: one line for each snapshot, with its
    time in the Mac's time zone."""
    from datetime import datetime

    if not result["snapshots"]:
        return ["\n  No snapshots yet.\n"]
    lines = [""]
    for s in result["snapshots"]:
        local = datetime.fromisoformat(s["taken"]).astimezone().strftime("%Y-%m-%d %H:%M")
        if "unreadable" in s:
            lines.append(f"  {s['name']}  {local}  {RED}unreadable:{RESET} {s['unreadable']}")
        else:
            lines.append(f"  {s['name']}  {local}  {', '.join(s['stacklets'])}  {s['version']}")
    return [*lines, ""]
