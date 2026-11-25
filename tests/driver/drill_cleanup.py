"""drill_cleanup: take a drill's instance away and give the Mac back as the inventory before found it.

    mac = Mac(run=report.run, note=report.line, home=HOME, checkout=CHECKOUT,
              drill_folder=DRILL_FOLDER, data=DATA, run_folder=RUN_FOLDER)
    guarded(body=lambda: (keep(mac, before), run_the_drill()),
            clean=lambda: clean(mac, before), note=report.line)

The rollback drill (`rollback_drill.py`) installs an instance on a shared
test Mac, builds and pulls images, and moves image tags. Its cleanup must
leave the Mac as the inventory before the first change found it
(`drill_inventory`), also when the drill fails halfway, when its ssh
connection drops, or when a person stops it.

How it works, and why:

- **Every step runs, whatever failed before it.** `clean` runs its steps
  through `run_all`, each in its own `try`, and returns the failures for
  the report. A data folder that cannot be removed must not keep the
  image names of another checkout from coming back.
- **The cleanup runs first and cannot be stopped.** `guarded` runs it in
  the `finally`, before any table of the report, with SIGTERM, SIGINT and
  SIGHUP ignored. A second Ctrl-C would otherwise end it halfway.
- **A keep name goes only when another name holds its image**
  (`keep_tags_to_remove`), because Docker deletes an image with its last
  name. The cleanup asks Docker after the restore, not its own plan.
- **No image of the Mac is lost to a build.** A stacklet that builds its
  image tags the build with a fixed name. When the Mac holds an image
  under that name already (another checkout's build), the drill's build
  moves the name, and Docker drops the old image once no name is left on
  it. `keep` gives each image of the Mac a second name first
  (`KEEP_PREFIX`). The cleanup puts each name back on the image it named
  before (`tags_to_restore`), removes the keep names, and only then
  removes what the drill added (`images_to_remove`).
- **It deletes only what the drill made**: its clone and tags, what is in
  the data folder that it found absent or empty, the images the inventory
  before did not list, and the network `stack` when it was absent before.

The decisions are pure functions over the inventory's image lines
(`"<repository>:<tag> <id>"`), tested without Docker. The steps that run
commands take the drill's `run` and `note`, so this module has no report
of its own.
"""

from __future__ import annotations

import contextlib
import shutil
import signal
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

import drill_inventory as inventory
import drill_release as releases

# ── The decisions ────────────────────────────────────────────────────────

KEEP_PREFIX = "rollback-drill-keep/"
NO_NAME = "<none>"


def foreign_instances(container_names: list[str]) -> list[str]:
    """The containers of an instance on this Mac: every name that starts with `stack-`.

    The stack names its containers `stack-<id>` on every instance, so any
    such container, running or stopped, belongs to an instance that the
    drill must not touch. The drill refuses to start when this is not [].

        foreign_instances(["orb-other", "stack-docs-db"])   # ["stack-docs-db"]
    """
    return [name for name in container_names if name.startswith("stack-")]


def keep_tag(name: str) -> str:
    """The second name that keeps the image of `name` alive: `valkey/valkey:8.1-alpine` -> `rollback-drill-keep/valkey_valkey:8.1-alpine`."""
    return KEEP_PREFIX + name.replace("/", "_")


def images_to_keep(images: list[str]) -> list[tuple[str, str]]:
    """(image id, keep name) for each named image of the inventory; images without a name have nothing to lose."""
    return [(image, keep_tag(name)) for name, image in _parsed(images)
            if NO_NAME not in name and not name.startswith(KEEP_PREFIX)]


def tags_to_restore(before: list[str], now: list[str]) -> list[tuple[str, str]]:
    """(image id, name) for each name of `before` that names another image now, or none.

    `docker image tag <id> <name>` puts the name back. A name that still
    names its image is left alone.

        tags_to_restore(["stack-core-tools:latest aa"], ["stack-core-tools:latest bb"])
        # [("aa", "stack-core-tools:latest")]
    """
    named_now = {name: image for name, image in _parsed(now)}
    return [(image, name) for name, image in _parsed(before)
            if NO_NAME not in name and not name.startswith(KEEP_PREFIX) and named_now.get(name) != image]


def keep_tags_to_remove(before: list[str], now: list[str]) -> tuple[list[str], list[str]]:
    """(keep names to remove, keep names to keep): a keep name goes only when a name of `before` holds its image.

    Docker deletes an image with its last name. A keep name is the last
    one when a restore failed, or when an earlier run died after its
    build moved the image's name. Only a name that `before` listed
    counts as another name, because the next step removes the names the
    drill added. Every other keep name stays, and the cleanup names it,
    so the image stays and criterion 4 shows it.

        keep_tags_to_remove(["valkey:8.1 bb"],
                            ["valkey:8.1 bb", "rollback-drill-keep/valkey:8.1 bb", "rollback-drill-keep/x:1 aa"])
        # (["rollback-drill-keep/valkey:8.1"], ["rollback-drill-keep/x:1"])
    """
    lines = _parsed(now)
    names_before = {name for name, _ in _parsed(before)}
    named = {image for name, image in lines if name in names_before
             and NO_NAME not in name and not name.startswith(KEEP_PREFIX)}
    keeps = [(name, image) for name, image in lines if name.startswith(KEEP_PREFIX)]
    return ([name for name, image in keeps if image in named],
            [name for name, image in keeps if image not in named])


def images_to_remove(before: list[str], now: list[str]) -> list[str]:
    """What `docker image rm` takes, in order: the drill's names first, then its images without a name.

    Expects `now` after the restore, so each name of `before` names its
    old image again. A line of `now` that `before` did not list is the
    drill's, with two exceptions:

    - A name that `before` had is never removed: its restore failed, and
      the cleanup names the difference instead of deleting another
      checkout's name.
    - An image without a name goes by its id, because `<none>:<none>` is
      no name Docker can remove, unless the Mac had that image before.

    A name goes by its name, never by its id: Docker refuses an id that
    two names share, and removing the last name removes the image.

        images_to_remove(["postgres:16 aa"], ["postgres:16 aa", "valkey:8.0 bb", "<none>:<none> cc"])
        # ["valkey:8.0", "cc"]
    """
    names_before = {name for name, _ in _parsed(before)}
    images_before = {image for _, image in _parsed(before)}
    added = [(name, image) for name, image in _parsed(now) if f"{name} {image}" not in set(before)]
    names = [name for name, _ in added
             if NO_NAME not in name and not name.startswith(KEEP_PREFIX) and name not in names_before]
    unnamed = sorted({image for name, image in added if NO_NAME in name and image not in images_before})
    return [*names, *unnamed]


def _parsed(images: list[str]) -> list[tuple[str, str]]:
    """(name, id) of each inventory line `"<repository>:<tag> <id>"`."""
    return [tuple(line.rsplit(" ", 1)) for line in images if line.strip()]  # type: ignore[misc]


# ── Running every step ───────────────────────────────────────────────────


def _ignore(signum, frame) -> None:
    """A stop signal during the cleanup: nothing happens."""


@contextlib.contextmanager
def signals_ignored() -> Iterator[None]:
    """Ignore SIGTERM, SIGINT and SIGHUP inside the block, then put the handlers back.

    A handler that does nothing, not `SIG_IGN`: a child inherits an
    ignored signal, and a `stack down` started by the cleanup could then
    not be stopped. A handler is reset to the default in the child.
    """
    kept = {number: signal.signal(number, _ignore)
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)}
    try:
        yield
    finally:
        for number, handler in kept.items():
            signal.signal(number, handler)


def run_all(steps: list[tuple[str, Callable[[], object]]], note: Callable[[str], None]) -> list[str]:
    """Run each step, also after one failed; the failures as `"<step>: <error>"`, [] when all ran."""
    failures = []
    for name, step in steps:
        try:
            step()
        except BaseException as error:  # noqa: BLE001  the next step runs whatever this one raised
            failures.append(f"{name}: {error!r}")
            _quietly(note, f"Cleanup step failed: {failures[-1]}")
    return failures


def guarded(body: Callable[[], object], clean: Callable[[], object], note: Callable[[str], None],
            trace: Callable[[], str] = lambda: "") -> None:
    """Run `body`; whatever it raises, run `clean` after it, with the stop signals ignored.

    The error of `body` goes to `note` with `trace()`, and a `note` that
    raises in turn (stdout closed with the ssh connection) is ignored, so
    nothing between `body` and `clean` can skip the cleanup. Raises only
    what `clean` raises.
    """
    try:
        body()
    except BaseException:  # noqa: BLE001  the cleanup runs on every failure, Ctrl-C included
        _quietly(note, "```\n" + trace() + "```")
    finally:
        with signals_ignored():
            clean()


def _quietly(note: Callable[[str], None], text: str) -> None:
    """`note(text)`, ignoring its failure: the cleanup is worth more than one line of the report."""
    try:
        note(text)
    except BaseException:  # noqa: BLE001  a closed stdout, or a stop signal before the cleanup's starts
        pass


# ── The cleanup's steps ──────────────────────────────────────────────────


@dataclass(frozen=True)
class Mac:
    """The drill's Mac: how to run a command, how to note a line, and the drill's folders.

    `run(argv, cwd=..., stdin=..., timeout=..., tail=...)` runs one command
    and records it, as the drill's report does; it never raises.
    """

    run: Callable[..., object]
    note: Callable[[str], None]
    home: Path
    checkout: Path
    drill_folder: Path
    data: Path
    run_folder: Path


def keep(mac: Mac, before: dict) -> None:
    """Give each named image of the inventory before a keep name, so no build of the drill drops one."""
    for image, name in images_to_keep(before["images"]):
        mac.run(["docker", "image", "tag", image, name], cwd=mac.home, tail=1)


def clean(mac: Mac, before: dict) -> list[str]:
    """Take the drill's instance away, in this order, each step whatever the one before did; the failures.

    1. Stop the drill's processes and the instance, and uninstall it.
    2. Put each image name of `before` back, remove each keep name another name covers.
    3. Remove the images the drill added.
    4. Remove the network `stack` when `before` did not have it.
    5. Empty the build cache when `before` had none.
    6. Empty the data folder, and leave it as `before` found it.
    7. Delete the drill's tags and its clone.
    """
    return run_all([
        ("stop the instance", lambda: _stop_instance(mac)),
        ("restore the image names", lambda: _restore_names(mac, before)),
        ("remove the drill's images", lambda: _remove_images(mac, before)),
        ("remove the network", lambda: _remove_network(mac, before)),
        ("empty the build cache", lambda: _empty_build_cache(mac, before)),
        ("empty the data folder", lambda: _empty_data(mac, before)),
        ("delete the clone", lambda: _delete_clone(mac)),
    ], mac.note)


def _stop_instance(mac: Mac) -> None:
    """Stop every stacklet of the drill's instance and uninstall it; nothing when the clone is gone.

    The commands' exit codes are in the report; a failure here shows in
    criterion 4. The only process the drill starts outside `run` (the
    rollback that its kills poll) is killed by its poll, on every exit.
    """
    if mac.checkout.exists():
        mac.run(["./stack", "down", "all"], cwd=mac.checkout, timeout=900)
        mac.run(["./stack", "uninstall", "--yes"], cwd=mac.checkout, stdin="delete\n", timeout=900)


def _restore_names(mac: Mac, before: dict) -> None:
    """Put each name of `before` back on its image, then remove the keep names that another name now covers.

    Docker is asked again after the restore, so a restore that failed
    leaves its keep name in place. Raises naming each keep name it kept:
    the step then counts as failed, and its image stays on the Mac.
    """
    for image, name in tags_to_restore(before["images"], inventory.take(mac.run_folder)["images"]):
        mac.run(["docker", "image", "tag", image, name], cwd=mac.home, tail=1)
    removable, kept = keep_tags_to_remove(before["images"], inventory.take(mac.run_folder)["images"])
    for name in removable:
        mac.run(["docker", "image", "rm", name], cwd=mac.home, tail=1)
    if kept:
        raise RuntimeError(f"kept, the only name of their image: {kept}")


def _remove_images(mac: Mac, before: dict) -> None:
    """Remove what `images_to_remove` names, from Docker's list after the restore; exit codes go to the report."""
    for reference in images_to_remove(before["images"], inventory.take(mac.run_folder)["images"]):
        mac.run(["docker", "image", "rm", reference], cwd=mac.home, tail=2)


def prune_build_cache(before: dict) -> bool:
    """Whether the cleanup empties Docker's build cache: only when `before` listed none of it.

    Docker prunes the build cache as a whole, not by entry, so a Mac with
    a cache of its own keeps it, and the drill's entries show as a
    difference of the two inventories.

        prune_build_cache({"build_cache": []})       # True
        prune_build_cache({"build_cache": ["x1"]})   # False
    """
    return not before.get("build_cache")


def _empty_build_cache(mac: Mac, before: dict) -> None:
    """Prune the build cache when `prune_build_cache` says so; the exit code goes to the report."""
    if prune_build_cache(before):
        mac.run(["docker", "buildx", "prune", "--all", "--force"], cwd=mac.home, tail=1)


def _remove_network(mac: Mac, before: dict) -> None:
    """Remove the network `stack` when `before` did not list it; a Mac that had it keeps it."""
    if "stack" not in before["networks"]:
        mac.run(["docker", "network", "rm", "stack"], cwd=mac.home)


def _empty_data(mac: Mac, before: dict) -> None:
    """Everything in the data folder is the drill's: the preflight refused a folder that held anything."""
    if mac.data.exists():
        for entry in mac.data.iterdir():
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
        if not before.get("data_existed"):
            mac.data.rmdir()
    elif before.get("data_existed"):
        mac.data.mkdir()   # `stack uninstall` deletes the folder; the Mac had it, empty


def _delete_clone(mac: Mac) -> None:
    """Delete the drill's tags first, then its folder; a folder that does not go leaves no drill tag in it."""
    mac.note(f"Tags deleted: {releases.remove(mac.checkout)}")
    shutil.rmtree(mac.drill_folder, ignore_errors=True)
