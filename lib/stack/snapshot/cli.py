"""The command line of `stack snapshot` and `stack rollback`.

    stack snapshot create docs [photos ...]    stop, clone the state, start again
    stack snapshot list                        the snapshots, newest first
    stack rollback [<snapshot>] [--yes]        back to the snapshot before the last update

    add_parser(sub)                # in lib/stack/cli.py, next to the other commands
    run(stck, args)                # what `stack snapshot ...` runs
    add_rollback_parser(sub)
    run_rollback(stck, args)       # what `stack rollback ...` runs

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

`stack rollback` shows its plan (`stack.snapshot.rollback`): the
snapshot, the release it goes back to, the stacklets it replaces, and the
files that came in after the snapshot. It waits for a confirmation, or
`--yes` where there is no terminal. Then the rollback runs, and the old
release starts its stacklets with a `stack up` process of its own:

    $ stack rollback
      Roll back to 0.4.0-rc.1, the snapshot 2026-11-25T2114Z (2026-11-25 22:14)
      Replaces docs, photos. Their changes since then move to
      .snapshots/replaced/, and the stacklets lose them.
      ? Roll back? [y/N]

This module reads the command line and prints the answer. It calls the
store for the snapshot, and the CLI to start and stop. The snapshot's
folder and its rules are in `stack.snapshot.store`.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

from .. import manifest as manifests
from .. import run_log, runs
from ..images import DockerImages
from ..manifest import Manifest, ManifestError
from ..prompt import DIM, GREEN, ORANGE, RED, RESET, confirm
from . import pending, release, rollback, store
from .release import Before, anything_up
from .store import SnapshotError, Taken, Volume

__all__ = [
    "add_parser",
    "add_rollback_parser",
    "keep_and_say",
    "record_or_exit",
    "run",
    "run_rollback",
    "take_or_exit",
    "waiting",
]


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


def add_rollback_parser(sub) -> None:
    """Add `stack rollback` and its options to `sub`, the CLI's subparsers."""
    p = sub.add_parser("rollback")
    p.add_argument("snapshot", nargs="?", default=None,
                   help="the snapshot to go back to (default: the newest)")
    p.add_argument("--yes", action="store_true", help="Skip the confirmation")
    p.add_argument("--json", action="store_true", help="Output as JSON")


def run_rollback(stck, args, volume: Volume | None = None) -> None:
    """`stack rollback [<snapshot>]` on the instance `stck`: show the
    plan, wait for the confirmation, run it. Prints the answer. Exits 1
    when it refused or a step failed. A declined confirmation changes
    nothing and exits 0; with `--json` it prints the plan as JSON, and the
    question goes to stderr. Without a terminal it needs `--yes`. `volume`
    is the file system of the data folder, `store.ThisVolume` by default.

        $ stack rollback --yes --json
        {"ok": true, "snapshot": "2026-11-25T2114Z", "version": "0.4.0-rc.1",
         "replaced": ".../.snapshots/replaced/2026-11-25T2203Z", "started": ["docs"],
         "newer_files": {"docs": {"paperless/media": 3}}, "tags": [], ...}
    """
    try:
        planned = rollback.plan(stck, args.snapshot)
    except rollback.RollbackError as error:
        return _print({"error": str(error), "problems": error.problems}, args.json, None)
    if not args.json:
        print("\n".join(_plan_lines(planned)))
    if not args.yes:
        if not sys.stdin.isatty():
            return _print({"error": "Pass --yes to confirm (no terminal to ask in)"},
                          args.json, None)
        if not _confirmed(planned, quiet=args.json):
            return _print({"ok": False, "declined": True, **_planned(planned)}, args.json,
                          lambda _: [f"  {DIM}Nothing changed.{RESET}", ""])
    try:
        done = rollback.run(stck, planned, stop=_stopping(stck),
                            start=_starting(stck, quiet=args.json), volume=volume)
    except SnapshotError as error:
        return _print({"error": str(error), "problems": getattr(error, "problems", [])},
                      args.json, None)
    _print({**_planned(planned), "ok": True, "replaced": str(done.replaced),
            "started": done.started, "cloned": done.cloned, "moved_tags": done.tags},
           args.json, _rolled_back_lines)


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
    failed (`stack.snapshot.pending`), with the two ways on, or because a
    rollback of it stopped halfway (`stack.snapshot.rollback`). None when
    the stacklet waits for nothing. `stack up` and every restart ask it
    before they start a stacklet.

        waiting(stck, "docs")
        # {"error": "docs waits for its snapshot: … stack restart docs …"}
    """
    held = rollback.holding(stck.data, stacklet_id)
    if held is not None:
        try:
            rollback.read_record(held)
        except rollback.RollbackError as unreadable:
            return {"error": str(unreadable), "stacklet": stacklet_id}
        return {"error": (f"A rollback of {stacklet_id} stopped halfway, so its state may be "
                          f"half restored ({held}). Finish it with: stack rollback"),
                "stacklet": stacklet_id}
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


# ── Rolling back ─────────────────────────────────────────────────────────

def _stopping(stck):
    """The rollback's stop: `CLI.down_many`, dependents first."""
    from ..cli import CLI

    return lambda ids: CLI(stck).down_many(ids)


def _starting(stck, quiet: bool):
    """The rollback's start: `stack up <ids>` as a process of its own, from
    the checkout, which is on the snapshot's commit by then. It runs that
    release's code, framework included, and not the code this process
    loaded. Its output goes to the terminal, or to stderr with `--json`
    (`quiet`). Says whether it exited 0."""
    def start(ids: list[str]) -> bool:
        if not ids:
            return True
        # The checkout's lib/ first: the wrapper `./stack` sets the same.
        paths = [str(Path(stck.root) / "lib"), os.environ.get("PYTHONPATH", "")]
        env = {**os.environ, "PYTHONPATH": os.pathsep.join(p for p in paths if p)}
        done = subprocess.run([sys.executable, "-m", "stack", "up", *ids], cwd=stck.root,
                              env=env, stdout=sys.stderr if quiet else None)
        return done.returncode == 0
    return start


def _confirmed(planned: rollback.Plan, quiet: bool) -> bool:
    """Ask the confirmation; on stderr when stdout carries JSON (`quiet`)."""
    question = "Finish the rollback?" if planned.replaced else "Roll back?"
    if not quiet:
        return confirm(question, default=False)
    with contextlib.redirect_stdout(sys.stderr):
        return confirm(question, default=False)


def _planned(planned: rollback.Plan) -> dict:
    """The plan, as the answer carries it."""
    return {"snapshot": planned.snapshot.name, "version": planned.version,
            "stacklets": list(planned.stacklets), "starts": planned.starts,
            "newer_files": planned.newer, "others": planned.others,
            "tags": [{"name": t.name, "image": t.image_id, "now": t.now,
                      "also_used_by": list(t.also_used_by)} for t in planned.tags],
            "finishes": planned.replaced.name if planned.replaced else None,
            "left": planned.left}


def _plan_lines(planned: rollback.Plan) -> list[str]:
    """What a person reads before the confirmation."""
    if planned.replaced is not None:
        lines = ["", f"  {ORANGE}⚠{RESET} The rollback of {planned.replaced.name} to "
                     f"{planned.version} (snapshot {planned.snapshot.name}) stopped halfway.",
                 f"  This finishes it. Left: {'; '.join(planned.left)}.",
                 f"  {DIM}The state it replaced: {planned.replaced}{RESET}"]
    else:
        local = planned.snapshot.taken.astimezone().strftime("%Y-%m-%d %H:%M")
        lines = ["", f"  Roll back to {planned.version}, the snapshot {planned.snapshot.name} "
                     f"({local})",
                 f"  Replaces {', '.join(planned.stacklets)}. Their changes since then move to "
                 f"{store.SNAPSHOTS_FOLDER}/{rollback.REPLACED_FOLDER}/,",
                 "  and the stacklets lose them."]
    lines += _newer_lines(planned.newer) + _tag_lines(planned.tags)
    if planned.others:
        lines.append(f"  {DIM}Not in the snapshot, still running: {', '.join(planned.others)}. "
                     f"They keep the newer code until they restart.{RESET}")
    return [*lines, ""]


def _newer_lines(newer: dict[str, dict[str, int]]) -> list[str]:
    """One line for each archive source with files newer than the snapshot."""
    return [f"  {ORANGE}⚠{RESET} {count} file{'s' if count != 1 else ''} in {sid}/{source} "
            f"came after the snapshot. {'They stay' if count != 1 else 'It stays'}, "
            f"and the restored {sid} does not know {'them' if count != 1 else 'it'}."
            for sid, sources in newer.items() for source, count in sources.items()]


def _tag_lines(tags: list) -> list[str]:
    """One line for each tag the rollback moves back, and one for the
    stacklets outside the rollback that start it too."""
    lines = []
    for tag in tags:
        lines.append(f"  The tag {tag.name} names the image {tag.image_id[:19]} again.")
        if tag.also_used_by:
            lines.append(f"    {ORANGE}⚠{RESET} {', '.join(tag.also_used_by)} start "
                         f"this tag too, and get that image at the next start.")
    return lines


def _rolled_back_lines(result: dict) -> list[str]:
    """What a person reads after the rollback."""
    lines = [f"\n  {GREEN}✓{RESET}  Rolled back to {result['version']} "
             f"(snapshot {result['snapshot']}): {', '.join(result['stacklets'])}",
             f"     {DIM}The state it replaced: {result['replaced']}{RESET}"]
    lines += [f"     {DIM}The tag {name} names the snapshot's image again.{RESET}"
              for name in result["moved_tags"]]
    lines += _newer_lines(result["newer_files"])
    return [*lines, ""]


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
