"""The snapshot before a release: what a restart takes before it applies one.

`stack update` moves the checkout to a release and starts nothing. The
data changes at the restart that follows, when a stacklet starts on the
new code and its migrations run, some of them one way. So the restart
takes a snapshot of each stacklet it moves to a new release, while its
containers are stopped anyway, and starts it only after the snapshot is
whole. A restart that applies no release takes none.

    from stack.snapshot import release

    before = release.before_stop(stck, ["docs"])     # record while docs still runs
    cli.down_many(["docs"])
    taken = release.after_stop(stck, before)         # the snapshot, or None
    cli.up_many(["docs"])                            # docs starts on the release
    pruned = release.prune(stck.data, taken.snapshot.name)
    pruned.removed                                   # ["2026-11-01T0900Z"]

One restart, in time:

    stack restart
      record   docs runs rc.1: its commit, its images, its migrations
      stop     docs
      take     .snapshots/2026-11-25T2114Z/
      start    docs on rc.2, its migrations run
      prune    the newest 3 snapshots stay

    stack restart, when the take fails
      record   docs runs rc.1
      stop     docs
      take     refused: the disk is full
      keep     .stack/snapshot-pending/docs/   docs starts on no command
    stack restart docs                         after the admin freed space
      record   the kept record
      take     .snapshots/2026-11-25T2131Z/
      start    docs on rc.2

Why it works this way:

- **Only a stacklet that a release moves gets a snapshot.** Its
  containers, running or stopped, carry the commit they started from
  (`stack.commit`). The stacklet applies a release when the commits since
  then changed its files or the framework (`stack.updater.stale_stacklets`,
  the rule that `stack list` uses). A restart on the current code takes
  none, so the disk does not fill with copies of the same state.
- **The record comes first, while the stacklet still runs.** A running
  container names the image it runs. After the update the compose file
  is the new release's, which can name another tag or not parse at all
  (`stack.images`). The record names the commit the stacklet ran and the
  release the restart applies (`applies`).
- **No snapshot, no start.** When the snapshot cannot be recorded or
  taken, the restart starts nothing on the new release. A one-way
  migration with no way back is worse than a page that is down until the
  admin chooses: free space and restart again, or `--no-snapshot`.
- **A failed take keeps its record** (`stack.snapshot.pending`). The
  stacklet is stopped by then, so Docker no longer lists its commit, and
  a stacklet without containers has lost its `.up` stamp. While the
  record waits, `stack up` and every restart refuse to start the
  stacklet; the next restart of it takes the snapshot from the record,
  and `--no-snapshot` drops the record.
- **An unknown commit is said, not guessed.** A stacklet that runs with
  no commit label, or a commit git does not know (a shallow clone), may
  or may not apply a release. The restart takes no snapshot of it and
  says so.
- **The newest 3 stay.** After a new snapshot is whole, the older ones
  beyond the newest 3 are removed. The snapshot just taken always stays.
  A snapshot whose manifest cannot be read is never removed and never
  counts as one of the 3, because it is no way back and the admin may
  want to look at it.

This module decides. `stack.snapshot.restart_cli` says what the restart
did, and the restart in `stack.cli` stops and starts. The snapshot's
folder and its rules are in `stack.snapshot.store`.
Out of scope: a stacklet that was stopped before the update and starts
later with `stack up`, and the images that Watchtower changes at night.
"""

from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .. import manifest as manifests
from ..images import DockerImages
from ..manifest import Manifest, ManifestError
from . import pending, store
from .errors import SnapshotError
from .rollback_record import needs
from .running import anything_up
from .store import Taken
from .volume import Volume

__all__ = [
    "KEEP",
    "Before",
    "Pruned",
    "StillRunning",
    "after_stop",
    "applying",
    "before_stop",
    "prune",
    "unknown_commits",
]

KEEP = 3
"""How many readable snapshots stay after a restart took a new one."""


# ── The records ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Before:
    """What a restart recorded before it stopped its stacklets: the
    stacklets it moves to a release, with their folders, the manifest of
    their snapshot, and the stacklets that run a commit nobody knows.
    Empty when the restart applies no release, or the admin skipped the
    snapshot.

        Before(stacklets={"docs": Path("stacklets/docs")},
               record=Manifest(kind="snapshot", version="0.4.0-rc.1",
                               commit="f5834a67", applies="0.4.0-rc.2", …))
        Before(unknown=["photos"])     # nothing to take, and photos unknown
    """

    stacklets: dict[str, Path] = field(default_factory=dict)
    record: Manifest | None = None
    unknown: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Pruned:
    """The snapshots `prune` removed, by name, and those it could not
    remove, each with the reason.

        Pruned(removed=["2026-11-01T0900Z"], failed={})
    """

    removed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)


class StillRunning(SnapshotError):
    """The restart stopped the stacklets `ids` and they still run, so it
    took no snapshot.

        StillRunning(["docs"]).ids    # ["docs"]
    """

    def __init__(self, ids: list[str]):
        super().__init__(f"{', '.join(ids)} did not stop")
        self.ids = ids


# ── The steps of a restart ───────────────────────────────────────────────

def applying(stck, ids: list[str]) -> dict[str, str]:
    """The stacklets among `ids` that a restart moves to a new release,
    each with the commit it ran.

    A stacklet applies a release when the commit its running containers
    started from, or the commit of a stacklet without containers that is
    up, is one the checkout has moved past for its files. A stacklet with
    no such commit is left out (`unknown` names the ones that run). Empty
    when the checkout is not a git tree. Changes nothing.

        applying(stck, ["docs", "photos"])    # {"docs": "f5834a67"} after `stack update`
    """
    from ..updater import Checkout, stale_stacklets

    checkout = Checkout(stck.root)
    if not ids or not checkout.is_git():
        return {}
    commits = stck.started_commits()
    since: dict[str, list[str]] = {}

    def changes_since(commit: str) -> list[str]:
        if commit not in since:
            since[commit] = checkout.changed_paths(commit, "HEAD")
        return since[commit]

    return {sid: commits[sid] for sid in stale_stacklets(commits, set(ids), changes_since)}


def before_stop(stck, ids: list[str], *, skip: bool = False) -> Before:
    """Record what the stacklets among `ids` that apply a release run,
    while they still run, together with the kept record of each one whose
    last snapshot failed (`stack.snapshot.pending`). `skip` is `stack
    restart --no-snapshot`: it drops the kept records of `ids`.

    Keeps the new record in `.stack/` before anything stops, so that a
    stacklet whose take fails waits for its snapshot. Returns an empty
    `Before` when `skip` is set or nothing waits for a snapshot. Raises
    `SnapshotError` when a record cannot be made, kept or read (a broken
    migration record, a full disk), before anything stops; then no new
    record is kept.

        before_stop(stck, ["docs"]).record.stacklets["docs"].commit   # "f5834a67"
    """
    if skip:
        pending.clear(stck.instance_dir, ids)
        return Before()
    kept = pending.read(stck.instance_dir, ids)
    waits = set(kept.stacklets) if kept else set()
    moving = applying(stck, [sid for sid in ids if sid not in waits])
    known = {s["id"]: Path(s["path"]) for s in stck.discover()}
    stacklets = {sid: known[sid] for sid in [*waits, *moving] if sid in known}
    unknown = [sid for sid in unknown_commits(stck, ids) if sid not in waits]
    if not stacklets:
        return Before(unknown=unknown)
    new = {sid: stacklets[sid] for sid in moving if sid in stacklets}
    record = _record(stck, new, moving) if new else None
    if record is not None:
        try:
            pending.keep(stck.instance_dir, record)
        except OSError as error:
            pending.clear(stck.instance_dir, list(new))
            raise SnapshotError(f"the record cannot be kept in .stack/: {error}") from error
    return Before(stacklets, _joined(kept, record), unknown)


def after_stop(stck, before: Before, *, volume: Volume | None = None) -> Taken | None:
    """Take the snapshot that `before` recorded, now that its stacklets
    are stopped. None when `before` is empty.

    Raises `StillRunning` when a stacklet did not stop; its kept record
    goes, because it still runs what it ran. Raises `SnapshotError` when
    the take fails (no space, a file that cannot be read); then no
    snapshot is written, and the record that `before_stop` kept waits
    until a snapshot is whole (`stack.snapshot.pending`). The record goes
    once the snapshot is whole. `volume` is the file system of the data folder,
    `volume.ThisVolume` by default.

        after_stop(stck, before).snapshot.name    # "2026-11-25T2114Z"
    """
    if before.record is None:
        return None
    up = anything_up(stck)
    still = [sid for sid in before.stacklets if up(sid)]
    if still:
        # It did not stop, so it still runs what it ran: nothing to wait for.
        pending.clear(stck.instance_dir, still)
        raise StillRunning(still)
    taken = store.take(stck.data, stck.instance_dir, before.stacklets, before.record,
                       running=up, volume=volume)
    pending.clear(stck.instance_dir, list(before.stacklets))
    return taken


def prune(data_root: Path, taken: str) -> Pruned:
    """Remove the snapshots beyond the newest `KEEP` readable ones, after
    the snapshot `taken` is whole.

    `taken` always stays, also when the clock made it sort below an older
    one. A snapshot with an unreadable manifest is never removed and not
    counted. Nor is the snapshot of a rollback that has not ended
    (`rollback_record.needs`), because that rollback finishes from it.
    Removes nothing when `taken` is not among the snapshots. A removal
    that fails is named in `failed`, and the others go on.

        prune(Path("~/famstack-data"), "2026-11-25T2114Z")
        # Pruned(removed=["2026-11-01T0900Z"], failed={})
    """
    readable = [s for s in store.snapshots(data_root)
                if s.manifest is not None and needs(data_root, s.path) is None]
    if not any(s.name == taken for s in readable):
        return Pruned()
    pruned = Pruned()
    for snapshot in [s for s in readable if s.name != taken][KEEP - 1:]:
        try:
            store.remove(data_root, snapshot)
            pruned.removed.append(snapshot.name)
        except SnapshotError as error:
            pruned.failed[snapshot.name] = str(error)
    return pruned


def unknown_commits(stck, ids: list[str]) -> list[str]:
    """The stacklets among `ids` that run, but with no commit label, or
    with a commit git does not know: whether they apply a release cannot
    be told. Changes nothing.

        unknown_commits(stck, ["docs"])   # ["docs"] in a shallow clone
    """
    from ..updater import Checkout

    checkout, commits, up = Checkout(stck.root), stck.started_commits(), anything_up(stck)
    if not checkout.is_git():
        return []
    return [sid for sid in ids
            if up(sid) and not (sid in commits and checkout.describe_of(commits[sid]))]


# ── The record ───────────────────────────────────────────────────────────

def _record(stck, stacklets: dict[str, Path], commits: dict[str, str]) -> Manifest:
    """The manifest of the snapshot of `stacklets`, recorded while they
    run: each one's commit and images, the release they ran and the
    release the restart applies. Raises `SnapshotError` when a record
    cannot be made."""
    from ..updater import Checkout

    up, images = anything_up(stck), DockerImages()
    try:
        records = {sid: manifests.record_stacklet(path, stck.data / sid, up=up(sid),
                                                  started_from=commits[sid], docker=images)
                   for sid, path in stacklets.items()}
    except ManifestError as error:
        raise SnapshotError(f"the snapshot cannot record what runs: {error}") from error
    # Stacklets that started together ran one commit. Should they differ,
    # the manifest names the one most of them ran; each record keeps its own.
    commit = Counter(commits[sid] for sid in stacklets).most_common(1)[0][0]
    checkout = Checkout(stck.root)
    return Manifest(kind="snapshot", name="", version=_release(checkout.describe_of(commit)) or commit,
                    commit=commit, stacklets=records,
                    applies=_release(checkout.describe()) or None)


def _joined(kept: Manifest | None, new: Manifest | None) -> Manifest | None:
    """One manifest of the kept records and the new record: the new
    record's release and commit when there is one, every stacklet."""
    if kept is None or new is None:
        return new or kept
    return dataclasses.replace(new, stacklets={**kept.stacklets, **new.stacklets})


def _release(describe: str) -> str:
    """A release as the manifest names it, from git's description.

        >>> _release("v0.4.0-rc.1"), _release("v0.4.0-rc.1-3-gf5834a6"), _release("")
        ('0.4.0-rc.1', '0.4.0-rc.1-3-gf5834a6', '')
    """
    return describe.removeprefix("v")
