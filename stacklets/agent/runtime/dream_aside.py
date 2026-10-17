"""Set aside, once, what nanobot's Dream wrote into the agent's workspace.

Dream condensed the history of every room every two hours into SOUL.md,
USER.md, memory/MEMORY.md and skills of its own, and nanobot puts all of
them into the prompt of every room. That carried stale list contents,
wrong notes about the tools, and what was said in one room into the
others. config.json switches it off; this moves what it already wrote
into `<home>/dream-archive/`, so nothing is deleted.

A profile file is moved only when it differs from the shipped seed, and
the entrypoint seeds it again. A skill is moved when the image does not
ship it and the agent's start hook does not write it. The archive
directory marks the job done, so a later edit, the family's own now,
stays where it is.

Run by entrypoint.sh: dream_aside.py <nanobot home> <seed dir> <agent name>
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ARCHIVE = "dream-archive"

# Written into the workspace by the agent's on_start hook from
# `[agent] allow` (lib/stack/agent_access.py SKILL_NAME), not by Dream.
GENERATED_SKILLS = {"stack-commands"}

PROFILE_FILES = ("SOUL.md", "USER.md")


def set_aside(home: Path, seed: Path, agent_name: str) -> None:
    archive = home / ARCHIVE
    if archive.is_dir():
        return
    workspace = home / "workspace"
    archive.mkdir(parents=True)
    for name in PROFILE_FILES:
        _move_if_changed(workspace / name, _seeded(seed / name, agent_name), archive)
    _move(workspace / "memory" / "MEMORY.md", archive)
    for skill in _skills_dream_made(workspace / "skills", seed / "skills"):
        _move(skill, archive / "skills")


def _seeded(path: Path, agent_name: str) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").replace("__AGENT_NAME__", agent_name)


def _move_if_changed(path: Path, seeded: str | None, archive: Path) -> None:
    if path.is_file() and path.read_text(encoding="utf-8") != seeded:
        _move(path, archive)


def _skills_dream_made(skills: Path, shipped: Path) -> list[Path]:
    if not skills.is_dir():
        return []
    return [d for d in sorted(skills.iterdir())
            if d.is_dir() and d.name not in GENERATED_SKILLS
            and not (shipped / d.name).is_dir()]


def _move(path: Path, into: Path) -> None:
    if not path.exists():
        return
    into.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(into / path.name))
    print(f"dream: set aside {path.name} -> {into}")


if __name__ == "__main__":
    set_aside(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
