"""drill_audit: what a snapshot step left on disk, and what a snapshot costs.

    stale(data_root, stack_dir)     # [Stale(path=…/.taking-2026-11-25T1200Z, why="not whole: …")]
    not_listed(snapshots, listed)   # ["2026-11-25T1200Z"]: on disk, not in `stack snapshot list`
    stale_tags(images, stack_names, used, kept, before)   # ["postgres:15 aa: …"]
    faults({"files": [...], "doctor": [...]}, open_rollback_expected=True)
    size(folder)                    # Size(apparent_bytes=812_331_008, files=4211)
    free_kb(folder)                 # 181_223_456, from `df -k`
    excluded_from_time_machine(p)   # True, from `tmutil isexcluded`

The rollback drill (`rollback_drill.py`) runs the snapshot commands on a
real Mac. After each step it calls `stale`, and the report lists each
finding with the step that left it. A step is clean when the list is
empty. The rules are the snapshot feature's own promises, read from the
store and the rollback record (`lib/stack/snapshot/store.py`,
`rollback_record.py`):

- `.snapshots/` holds only what `stack snapshot list` lists: folders
  without a leading dot that hold `MANIFEST.json`, and `replaced/`.
- Each folder in `replaced/` holds its `ROLLBACK.json`, and that record
  names "finished" once the rollback ended.
- `.stack/snapshot-pending/` is empty after a whole snapshot.
- No run lock in `.stack/runs/` is held after a command ended. The lock
  files stay on disk by design (an advisory `flock`), so only a held one
  counts.
- Each entry of `.snapshots/` is in the output of `stack snapshot list`
  (`not_listed`).
- No tag of an image the stack uses names an image that no container
  runs and no snapshot's manifest keeps (`stale_tags`).

A step that kills a rollback leaves its record open on purpose, until a
later step finishes it. `faults` drops those findings for such a step and
keeps every other one, so the report can say for each step whether what
it left is a fault.

The audit reads only. It never removes what it finds, because a finding
is evidence for the report, and the drill's cleanup removes the whole
instance at the end anyway.

The sizes answer two questions. `du -A` is the apparent size, what a
plain copy of the snapshot would take. The free space before and after
a take, from `df -k`, is what the disk really lost: on APFS a clone
shares its blocks with the live data until that data changes.
"""

from __future__ import annotations

import fcntl
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ── The findings ─────────────────────────────────────────────────────────

MANIFEST = "MANIFEST.json"
RECORD = "ROLLBACK.json"
FINISHED = "finished"


@dataclass(frozen=True)
class Stale:
    """One file or folder a step left that the snapshot feature promises not to leave."""

    path: Path
    why: str


def stale(data_root: Path, stack_dir: Path) -> list[Stale]:
    """Every stale entry under `<data_root>/.snapshots` and in `stack_dir`; [] when clean.

    `data_root` is the instance's data folder (`~/famstack-data`),
    `stack_dir` its `.stack/` folder. A missing folder is clean.
    """
    return [*_stale_snapshots(data_root / ".snapshots"),
            *_stale_pending(stack_dir / "snapshot-pending"),
            *_held_locks(stack_dir / "runs")]


def _stale_snapshots(snapshots: Path) -> list[Stale]:
    """Entries of `.snapshots/` that `stack snapshot list` does not list as a snapshot or replaced state."""
    if not snapshots.is_dir():
        return []
    found = []
    for entry in sorted(snapshots.iterdir()):
        if entry.name == "replaced" and entry.is_dir():
            found += _stale_replaced(entry)
        elif entry.name.startswith(".") or not entry.is_dir():
            found.append(Stale(entry, "not whole: a take or a remove stopped halfway, or a stray file"))
        elif not (entry / MANIFEST).is_file():
            found.append(Stale(entry, "a snapshot folder without its manifest: listed nowhere"))
    return found


def _stale_replaced(replaced: Path) -> list[Stale]:
    """Replaced states without their record, and records of a rollback that did not finish."""
    found = []
    for entry in sorted(replaced.iterdir()):
        record = entry / RECORD
        if not record.is_file():
            found.append(Stale(entry, "a replaced state without its ROLLBACK.json"))
            continue
        try:
            done = json.loads(record.read_text()).get("done", [])
        except (OSError, ValueError, AttributeError) as error:
            # A rollback killed while it wrote its record can leave it
            # unreadable: a finding of its own, not the end of the step.
            found.append(Stale(entry, f"a ROLLBACK.json that does not read: {error}"))
            continue
        if FINISHED not in done:
            found.append(Stale(entry, f"an open rollback: its record names {done}"))
    return found


def _stale_pending(pending: Path) -> list[Stale]:
    """Records of a failed take; after a whole snapshot there are none."""
    if not pending.is_dir():
        return []
    return [Stale(entry, "the record of a failed take") for entry in sorted(pending.iterdir())]


def _held_locks(runs: Path) -> list[Stale]:
    """Run locks that somebody holds now.

    The test takes each lock without waiting and gives it back at once.
    That is safe only between steps, when no command of the stack runs,
    which is when the drill calls it.
    """
    if not runs.is_dir():
        return []
    found = []
    for lock in sorted(runs.glob("*.lock")):
        with lock.open() as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                found.append(Stale(lock, "a run lock still held after the step ended"))
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
    return found


def not_listed(snapshots: Path, listed: str) -> list[str]:
    """The snapshots and replaced states on disk that the output of `stack snapshot list` does not name.

    A folder with a leading dot is left out, because `stale` names it
    already. A replaced state is listed as `replaced/<name>`.
    """
    if not snapshots.is_dir():
        return []
    names = [entry.name for entry in snapshots.iterdir()
             if entry.is_dir() and entry.name != "replaced" and not entry.name.startswith(".")]
    replaced = snapshots / "replaced"
    if replaced.is_dir():
        names += [f"replaced/{entry.name}" for entry in replaced.iterdir() if entry.is_dir()]
    return sorted(name for name in names if name not in listed)


def stale_tags(images: list[str], stack_names: set[str], used: set[str], kept: set[str],
               before: list[str]) -> list[str]:
    """Tags of the stack's images that name an image no container runs and no snapshot keeps.

    `images` are the inventory's lines (`"<repository>:<tag> <id>"`),
    `stack_names` the image names the stack's containers and manifests
    use, `used` and `kept` image ids (any form: `sha256:` and long ids are
    cut to Docker's short id), `before` the image lines the Mac had before
    the drill, which are never a finding. An image built by the stack
    (`stack-*`) counts as the stack's.

        stale_tags(["postgres:16-alpine aa"], {"postgres:16-alpine"}, set(), set(), [])
        # ["postgres:16-alpine aa: no container runs it and no snapshot keeps it"]
    """
    wanted = {_short_name(name) for name in stack_names}
    ids = {_short_id(image) for image in used | kept}
    found = []
    for line in images:
        name, image = line.rsplit(" ", 1)
        if line in before or "<none>" in name:
            continue
        if (_short_name(name) in wanted or name.startswith("stack-")) and _short_id(image) not in ids:
            found.append(f"{line}: no container runs it and no snapshot keeps it")
    return found


def faults(findings: dict[str, list[str]], open_rollback_expected: bool) -> dict[str, list[str]]:
    """The findings of one step that are faults, by kind.

    With `open_rollback_expected` (a step that kills a rollback on
    purpose), a finding about an open rollback is not a fault: a later
    step finishes it, and that step's audit judges it. Every other
    finding stays a fault.
    """
    if not open_rollback_expected:
        return {kind: list(found) for kind, found in findings.items()}
    return {kind: [text for text in found if "rollback" not in text.lower()] for kind, found in findings.items()}


def _short_id(image: str) -> str:
    """Docker's short id (12 hex digits) of any form of an image id."""
    return image.removeprefix("sha256:")[:12]


def _short_name(name: str) -> str:
    """An image name as `docker images` prints it: no `docker.io/`, no `library/`."""
    return name.removeprefix("docker.io/").removeprefix("library/")


# ── The sizes ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Size:
    """The apparent size of a folder and its number of files."""

    apparent_bytes: int
    files: int


def size(folder: Path) -> Size:
    """The apparent size (`du -A`, macOS) of `folder` and the number of files in it.

    `du -A -k` counts each file at its length, not at the blocks the file
    system gave it, so a clone counts as much as its original. The count
    takes every entry that `du` counts and that is not a folder, symbolic
    links included.
    """
    out = subprocess.run(["du", "-A", "-k", "-s", str(folder)],
                         capture_output=True, text=True, check=True).stdout
    files = sum(1 for path in folder.rglob("*") if path.is_symlink() or not path.is_dir())
    return Size(apparent_bytes=int(out.split()[0]) * 1024, files=files)


def free_kb(folder: Path) -> int:
    """The free space of the volume that holds `folder`, in KiB, from `df -k`."""
    out = subprocess.run(["df", "-k", str(folder)], capture_output=True, text=True, check=True).stdout
    return int(out.splitlines()[-1].split()[3])


def excluded_from_time_machine(folder: Path) -> str:
    """The answer of `tmutil isexcluded <folder>`, as macOS prints it: "[Excluded] …" or "[Included] …"."""
    done = subprocess.run(["tmutil", "isexcluded", str(folder)], capture_output=True, text=True)
    return (done.stdout or done.stderr).strip()
