"""drill_inventory: what is on the Mac before a drill, and what the drill changed.

    before = take(run_folder)              # {"containers": [...], "images": [...], ...}
    after = take(run_folder)
    differences(before, after)             # ["networks: added stack"]

A drill on a shared test Mac must leave it as it found it. The rollback
drill (`rollback_drill.py`) takes an inventory before its first command
and again after its cleanup, writes both into its report, and names each
difference. The report then explains each one, or the cleanup is wrong.

The inventory lists what a famstack instance can leave behind: the
Docker containers, images, networks, volumes and build cache of the
user that runs it, the entries of the drill's run folder (`~/famstack-dev`), the user's
LaunchAgents, and the free space of the disk. It reads only. It covers
the Docker of the user who runs it, which is the only Docker the drill
uses.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# The free space of a Mac moves on its own (swap, caches, Spotlight), so
# only a change past this is named. A 2 GB test file left behind, or a
# snapshot outside the run folder, is above it.
FREE_SPACE_NOISE_KB = 1024 * 1024


def take(run_folder: Path) -> dict:
    """The inventory of this Mac now: Docker, `run_folder`'s entries, LaunchAgents, free space."""
    return {
        "containers": _docker("ps", "-a", "--format", "{{.Names}} {{.Image}} {{.Status}}"),
        "images": _docker("images", "--format", "{{.Repository}}:{{.Tag}} {{.ID}}"),
        "networks": _docker("network", "ls", "--format", "{{.Name}}"),
        "volumes": _docker("volume", "ls", "--format", "{{.Name}}"),
        # The drill builds images; their layers stay in the build cache, which no image list shows.
        "build_cache": _docker("buildx", "du", "--format", "{{.ID}}"),
        "run_folder": sorted(p.name for p in run_folder.iterdir()) if run_folder.is_dir() else [],
        "launch_agents": sorted(p.name for p in (Path.home() / "Library/LaunchAgents").glob("*")),
        "free_kb": _free_kb(Path.home()),
    }


def differences(before: dict, after: dict) -> list[str]:
    """Each entry added or removed between two inventories, by kind; [] when they agree.

    The container lines carry their status, so a container that only
    changed its status ("Up 2 hours" to "Up 3 hours") is compared by its
    name alone.
    """
    found = []
    for kind in ("containers", "images", "networks", "volumes", "run_folder", "launch_agents"):
        old, new = _keys(kind, before[kind]), _keys(kind, after[kind])
        found += [f"{kind}: removed {old[key]}" for key in sorted(old.keys() - new.keys())]
        found += [f"{kind}: added {new[key]}" for key in sorted(new.keys() - old.keys())]
    added = set(after.get("build_cache", [])) - set(before.get("build_cache", []))
    if added:
        found.append(f"build cache: {len(added)} entries added")
    lost_kb = before["free_kb"] - after["free_kb"]
    if abs(lost_kb) > FREE_SPACE_NOISE_KB:
        word = "less" if lost_kb > 0 else "more"
        found.append(f"free space: {abs(lost_kb) / 1024 / 1024:.1f} GB {word}")
    return found


def _keys(kind: str, lines: list[str]) -> dict[str, str]:
    """Each line by the part that names it: a container by its name, anything else by the whole line."""
    return {(line.split()[0] if kind == "containers" else line): line for line in lines}


def _docker(*args: str) -> list[str]:
    """The lines of one `docker` listing, sorted."""
    out = subprocess.run(["docker", *args], capture_output=True, text=True, check=True).stdout
    return sorted(line for line in out.splitlines() if line.strip())


def _free_kb(folder: Path) -> int:
    """The free space of the volume that holds `folder`, in KiB."""
    out = subprocess.run(["df", "-k", str(folder)], capture_output=True, text=True, check=True).stdout
    return int(out.splitlines()[-1].split()[3])
