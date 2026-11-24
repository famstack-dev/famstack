"""wiki_input.py: the family wiki's input, the part of the memory the family shares.

    from stacklets.memory.bot import wiki_input

    wiki_input.publish(brain, media, wiki, shared_bucket="family")
    # wiki/index.md, wiki/family/..., wiki/media/<files a shared card shows>

The family wiki is one static site for every paired device. It cannot ask
who reads a page, so it holds only what the family as a whole may read:
the cards `stack.cards.audience.readable` gives an audience of no single
person, which are the top level and the shared bucket. Each person's own
bucket (their notes, their own topics, their member page, their lists) is
read on the memory pages, which check who asks.

The cut is made before Quartz, in the folder the wiki container mounts as
its content (`WIKI_INPUT_DIR`, at `/vault`). Quartz builds its search
index, graph, explorer, backlinks, folder and tag pages, RSS and sitemap
from everything in that folder, and its plugins read files beside a page
(a calendar's archive, a card's sidecar). A filter at the wiki's door
would leave a person's card in all of those, so the card is not in the
folder at all.

The rule decides by the first folder of a path, never by a list of
people, so a person added after the install stays out with no change.
The media archive is dated, not split by bucket, so a file of it is in
the folder only when a shared card embeds or links it. A photo only a
person's own note shows is not served.

The curator owns the brain and calls `publish` after each turn of its
loop. The folder is a derived copy: it can be deleted, and the next
publish writes it again. Media files are hard links into the archive
where the file system allows, so a photo library is not stored twice.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from stack.cards.audience import readable
from stacklets.memory.graph import media_in

__all__ = ["publish", "Published", "shared_paths"]

# The media archive's place inside the wiki's input: cards embed its
# files as `/media/<path>` (ADR-017 §3).
MEDIA_DIR = "media"

# Git's own files are no part of the memory.
_GIT = ".git"

# No single person is the audience of what the family reads together.
_THE_FAMILY: frozenset[str] = frozenset()


@dataclass
class Published:
    """What one `publish` changed in the wiki's input, as paths inside it."""

    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.written or self.removed)


# ── Publishing ───────────────────────────────────────────────────────────────

def publish(brain: Path, media: Path, wiki: Path, *, shared_bucket: str) -> Published:
    """Make `wiki` hold exactly the shared part of `brain`, and the files of
    `media` that a shared card shows, under `wiki/media/`.

    Writes a file only when it is new or changed, and removes every file
    the shared part no longer has (a removed card, a card moved into a
    person's bucket), then the folders left empty. Each file is written
    beside the input and moved into place, so Quartz never reads half a
    file. Reads `brain` without its writer lock: the caller holds it.
    """
    brain, media, wiki = Path(brain), Path(media), Path(wiki)
    wanted = _wanted(brain, media, shared_bucket)
    staging = wiki.parent / f".{wiki.name}-staging"
    staging.mkdir(parents=True, exist_ok=True)
    wiki.mkdir(parents=True, exist_ok=True)
    done = Published()
    for rel, source in sorted(wanted.items()):
        if _place(source, wiki / rel, staging, link=rel.startswith(f"{MEDIA_DIR}/")):
            done.written.append(rel)
    for rel in sorted(_files_in(wiki) - set(wanted)):
        (wiki / rel).unlink()
        done.removed.append(rel)
    _remove_empty_folders(wiki)
    return done


def shared_paths(paths: list[str], *, shared_bucket: str) -> list[str]:
    """The brain paths of `paths` the family as a whole may read.

        >>> shared_paths(["index.md", "family/notes/a.md", "marge/notes/b.md", ".git/HEAD"],
        ...              shared_bucket="family")
        ['index.md', 'family/notes/a.md']
    """
    return [p for p in paths
            if p.split("/", 1)[0] != _GIT and readable(p, _THE_FAMILY, shared_bucket=shared_bucket)]


# ── What goes in ─────────────────────────────────────────────────────────────

def _wanted(brain: Path, media: Path, shared_bucket: str) -> dict[str, Path]:
    """Every file of the wiki's input, by its path inside it, with its source."""
    shared = shared_paths(sorted(_files_in(brain)), shared_bucket=shared_bucket)
    # A brain path under `media/` is the old mountpoint of the archive, never
    # a card: the archive below is the one source of media.
    wanted = {rel: brain / rel for rel in shared if rel.split("/", 1)[0] != MEDIA_DIR}
    for rel in shared:
        if rel.endswith(".md"):
            for name in media_in(_text(brain / rel), rel):
                if (media / name).is_file():
                    wanted[f"{MEDIA_DIR}/{name}"] = media / name
    return wanted


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _files_in(root: Path) -> set[str]:
    """Every file under `root`, by its path inside it; none for a missing root."""
    if not root.is_dir():
        return set()
    return {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}


# ── Writing ──────────────────────────────────────────────────────────────────

def _place(source: Path, target: Path, staging: Path, *, link: bool) -> bool:
    """Put `source` at `target` when it is not there yet; True when it wrote.

    A copy keeps the source's modification time, so an unchanged file
    has the same size and time on the next publish and is not written
    again. A media file is a hard link when the file system allows it.
    """
    if _same(source, target):
        return False
    temporary = staging / target.name
    temporary.unlink(missing_ok=True)
    if not (link and _hard_link(source, temporary)):
        shutil.copy2(source, temporary)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, target)
    return True


def _same(source: Path, target: Path) -> bool:
    try:
        have, want = target.stat(), source.stat()
    except OSError:
        return False
    return have.st_size == want.st_size and have.st_mtime_ns == want.st_mtime_ns


def _hard_link(source: Path, target: Path) -> bool:
    try:
        os.link(source, target)
    except OSError:
        return False
    return True


def _remove_empty_folders(root: Path) -> None:
    """Remove the folders under `root` that hold no file, deepest first."""
    for folder in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts),
                         reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
