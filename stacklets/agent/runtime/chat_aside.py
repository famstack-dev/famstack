"""Move, at every start, what nanobot kept for every chat out of the workspace.

    python3 chat_aside.py <nanobot home> <workspace seed>   # entrypoint.sh, before `nanobot gateway`

nanobot's consolidation appended a summary of every chat to one file,
`workspace/memory/history.jsonl`, with its cursor files and, from older
releases, `HISTORY.md`. The workspace is what every chat's file tools read,
so these files carried what was said in one chat into all the others.
`own_chat.py` stops new writes; this moves what is already there into
`<home>/chat-archive/`, outside the workspace, where no tool of the agent
reaches. Nothing is deleted.

It runs at every start, not once: a file that appears again (a nanobot
that wrote it before the gate attached) is moved again, under a name that
keeps the earlier copy.

SAFETY: a file that cannot be moved stops the start (exit 1, the file
named). The agent does not answer with a shared file in reach.
Everything else in the workspace, the family's own files and skills
included, stays where it is.

Files the model wrote into the workspace before writes were refused stay
too, and every chat reads them; a skill among them is in every chat's
prompt. Moving them would move the family's own files with them, so the
start names them instead (the names only, never the content):

    chat: in the workspace, read by every chat, not shipped: notes/party.md
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

__all__ = ["ARCHIVE", "SHARED_FILES", "set_aside", "not_shipped", "main"]

ARCHIVE = "chat-archive"

# Each file holds, or counts, every chat's archived summaries.
SHARED_FILES = ("history.jsonl", ".cursor", ".dream_cursor", "HISTORY.md")

# What nanobot and the agent's install keep in the workspace, as files or
# folders: their state, not what a chat wrote. nanobot writes its templates
# (`HEARTBEAT.md`, `prompts/`) and a git store of its profile files (`.git`,
# `.gitignore`) at each start; the install writes the `stack-commands` skill.
# `memory/` holds only the shared history, moved above.
AGENTS_OWN = ("memory", "cron", ".nanobot", ".git", ".gitignore", "HEARTBEAT.md", "prompts",
              "skills/stack-commands")


def set_aside(home: Path) -> list[Path]:
    """Move the shared files of `home`'s workspace into its archive; return
    where each went. Raises OSError when one cannot be moved."""
    memory = home / "workspace" / "memory"
    moved = []
    for name in SHARED_FILES:
        path = memory / name
        if path.is_file():
            moved.append(_move(path, home / ARCHIVE))
    return moved


def not_shipped(workspace: Path, seed: Path) -> list[str]:
    """The workspace's files that neither the image's seed nor nanobot put
    there, as paths relative to the workspace, sorted. Reads no content.

        not_shipped(ws, seed)   # ["notes/party.md", "skills/geheim/SKILL.md"]
    """
    found = []
    for path in sorted(workspace.rglob("*")):
        relative = path.relative_to(workspace)
        if not path.is_file() or _agents_own(relative.as_posix()):
            continue
        if (seed / relative).exists():
            continue
        found.append(relative.as_posix())
    return found


def main(home: Path, seed: Path | None = None) -> int:
    """The entrypoint's step: 0 when nothing shared is left in the workspace,
    1 with the file named on stderr when one could not be moved. With the
    seed, it also names each file in the workspace the seed did not ship."""
    if seed is not None:
        for name in not_shipped(home / "workspace", seed):
            # A model can name a file with a newline in it; repr keeps it one line.
            print(f"chat: in the workspace, read by every chat, not shipped: {name!r}")
    try:
        set_aside(home)
    except OSError as error:
        print(f"chat: could not move {error.filename or error} out of the workspace "
              f"({error.strerror or error}); the agent does not start with it in reach",
              file=sys.stderr)
        return 1
    return 0


def _agents_own(relative: str) -> bool:
    return any(relative == own or relative.startswith(f"{own}/") for own in AGENTS_OWN)


def _move(path: Path, archive: Path) -> Path:
    archive.mkdir(parents=True, exist_ok=True)
    target = archive / path.name
    if target.exists():
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        target = archive / f"{path.name}.{stamp}"
    shutil.move(str(path), str(target))
    print(f"chat: set aside {path.name} -> {target}")
    return target


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None))
