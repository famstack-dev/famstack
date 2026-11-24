"""wiki_input.py: the family wiki's input, the part of the memory the family shares.

    from stacklets.memory.bot import wiki_input

    wiki_input.publish(brain, media, wiki, shared_bucket="family")
    # wiki/index.md, wiki/family/..., wiki/media/<files a shared card shows>

    wiki = wiki_input.WikiInput(brain, media, wiki_dir, shared_bucket="family",
                                version=brain_version)
    wiki.refresh()     # publishes; None when the brain has not moved since

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

The curator owns the brain and asks `WikiInput.refresh` after each turn
of its loop. A publish walks the whole brain and reads every shared card,
under the brain's writer lock, so it runs only when the brain moved: a new
commit or another set of uncommitted changes. The folder is a derived
copy: it can be deleted, and the next publish after a change writes it
again (a restart of the curator publishes at once). Media files are hard
links into the archive where the file system allows, so a photo library
is not stored twice; `Published.copied_media` counts the files that had
to be copied instead, so the caller can say so.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from stack.cards.audience import readable
from stacklets.memory.graph import media_in

__all__ = ["WikiInput", "publish", "Published", "NotTheWikiInput", "shared_paths"]

# The media archive's place inside the wiki's input: cards embed its
# files as `/media/<path>` (ADR-017 §3).
MEDIA_DIR = "media"

# Git's own files are no part of the memory.
_GIT = ".git"

# The mark a publish leaves in the folder it owns. Publishing removes files,
# so it writes only into an empty folder it marks, or one that carries the
# mark: a folder of someone else's keeps its files.
MARK = ".wiki-input"

# No single person is the audience of what the family reads together.
_THE_FAMILY: frozenset[str] = frozenset()


class NotTheWikiInput(ValueError):
    """The target is no folder a publish may write and remove files in."""


@dataclass
class Published:
    """What one `publish` changed in the wiki's input, as paths inside it."""

    written: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    # Media files written as a copy because a hard link failed: each one
    # is stored twice on the disk.
    copied_media: int = 0

    def __bool__(self) -> bool:
        return bool(self.written or self.removed)


# ── Publishing ───────────────────────────────────────────────────────────────

class WikiInput:
    """The wiki's input, published again only when the brain moved.

    `version` says what the brain holds now (the curator's: its commit and
    its uncommitted changes); a publish runs when that differs from the last
    published one, or when the version is unknown (""). Kept in memory, so
    the first `refresh` after a start always publishes.

        wiki = WikiInput(brain, media, wiki_dir, shared_bucket="family", version=brain.version)
        wiki.refresh()   # Published(written=[...], ...)
        wiki.refresh()   # None: nothing moved
    """

    def __init__(self, brain: Path, media: Path, wiki: Path, *, shared_bucket: str,
                 version: Callable[[], str], keep_out: tuple[Path, ...] = ()):
        self.brain, self.media, self.wiki = Path(brain), Path(media), Path(wiki)
        self.shared_bucket, self.version, self.keep_out = shared_bucket, version, keep_out
        self._published = ""

    def refresh(self) -> Published | None:
        """Publish when the brain moved since the last publish; None when it did not.

        A publish that raises leaves the last version as it was, so the next
        `refresh` tries again.
        """
        now = self.version()
        if now and now == self._published:
            return None
        done = publish(self.brain, self.media, self.wiki, shared_bucket=self.shared_bucket,
                       keep_out=self.keep_out)
        self._published = now
        return done


def publish(brain: Path, media: Path, wiki: Path, *, shared_bucket: str,
            keep_out: tuple[Path, ...] = ()) -> Published:
    """Make `wiki` hold exactly the shared part of `brain`, and the files of
    `media` that a shared card shows, under `wiki/media/`.

    First removes every file the shared part no longer has (a removed card,
    a card moved into a person's bucket), then writes each file that is new
    or changed. Each file is written beside the input and moved into place,
    so Quartz never reads half a file. A file that cannot be written does
    not stop the others; the first such error is raised at the end. Reads
    `brain` without its writer lock: the caller holds it.

    Raises `NotTheWikiInput`, and touches nothing, for a target that is no
    folder of its own: see `_claim`. `keep_out` names more folders the
    target must stay clear of (the curator passes the memory's clone).
    """
    brain, media, wiki = Path(brain), Path(media), Path(wiki)
    _claim(wiki, (brain, media, *keep_out))
    wanted = _wanted(brain, media, shared_bucket)
    done = Published()
    # SAFETY: stale files go first, so a write that fails below never leaves
    # a card in the wiki that has moved into a person's bucket.
    for rel in sorted(_files_in(wiki, with_links=True) - set(wanted) - {MARK}):
        (wiki / rel).unlink()
        done.removed.append(rel)
    _remove_empty_folders(wiki)
    staging = wiki.parent / f".{wiki.name}-staging"
    staging.mkdir(parents=True, exist_ok=True)
    failed: list[OSError] = []
    for rel, source in sorted(wanted.items()):
        try:
            placed = _place(source, wiki / rel, staging, link=rel.startswith(f"{MEDIA_DIR}/"))
        except OSError as e:
            failed.append(e)
            continue
        if placed:
            done.written.append(rel)
        if placed == _COPIED_MEDIA:
            done.copied_media += 1
    if failed:
        raise failed[0]
    return done


def shared_paths(paths: list[str], *, shared_bucket: str) -> list[str]:
    """The brain paths of `paths` the family as a whole may read.

        >>> shared_paths(["index.md", "family/notes/a.md", "marge/notes/b.md", ".git/HEAD"],
        ...              shared_bucket="family")
        ['index.md', 'family/notes/a.md']
    """
    return [p for p in paths
            if p.split("/", 1)[0] != _GIT and readable(p, _THE_FAMILY, shared_bucket=shared_bucket)]


# ── Where it may write ───────────────────────────────────────────────────────

def _claim(wiki: Path, sources: tuple[Path, ...]) -> None:
    """Make sure `wiki` is a folder a publish may write and remove files in.

    SAFETY: publishing removes every file it did not mean to write, so the
    target must be the wiki's own folder. Refused, with nothing touched:
    an empty or relative path (an unset `WIKI_INPUT_DIR` is the working
    folder); a folder that is one of `sources` (the brain, the media
    archive, the memory's clone), lies inside one or holds one; a folder
    with files but no `MARK`. A missing or empty folder is made and marked.
    """
    if not str(wiki) or not wiki.is_absolute():
        raise NotTheWikiInput(f"the wiki's input needs an absolute folder, not {str(wiki)!r}")
    target = wiki.resolve()
    for source in sources:
        source = Path(source).resolve()
        if target == source or target.is_relative_to(source) or source.is_relative_to(target):
            raise NotTheWikiInput(f"{wiki} is or holds {source}, which a publish must not touch")
    if (wiki / MARK).is_file():
        return
    wiki.mkdir(parents=True, exist_ok=True)
    if any(wiki.iterdir()):
        raise NotTheWikiInput(f"{wiki} holds files no publish wrote ({MARK} is missing)")
    (wiki / MARK).write_text("The family wiki's input, written by the memory curator.\n",
                             encoding="utf-8")


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
                if _plain_file(media, name):
                    wanted[f"{MEDIA_DIR}/{name}"] = media / name
    return wanted


def _plain_file(root: Path, rel: str) -> bool:
    """Whether `root/rel` is a file, reached through no symbolic link below `root`.

    SAFETY: the archive is dated, not split by person, and a link in it
    could name any file; only what is really there is served.
    """
    path = root
    for part in rel.split("/"):
        path = path / part
        if path.is_symlink():
            return False
    return path.is_file()


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _files_in(root: Path, *, with_links: bool = False) -> set[str]:
    """Every file under `root`, by its path inside it; none for a missing root.

    SAFETY: a symbolic link is never followed, and is no file unless
    `with_links` asks for links too (to remove them). The audience rule
    judges a path, and a link in the shared bucket could carry a card of a
    person's own bucket under it.
    """
    if not root.is_dir():
        return set()
    found = set()
    for folder, _, names in os.walk(root):  # does not descend into a linked folder
        for name in names:
            path = Path(folder) / name
            if path.is_symlink() and not with_links:
                continue
            found.add(path.relative_to(root).as_posix())
        if with_links:
            found.update(Path(folder, d).relative_to(root).as_posix() for d in _
                         if Path(folder, d).is_symlink())
    return found


# ── Writing ──────────────────────────────────────────────────────────────────

# What `_place` did: nothing, wrote the file, or copied a media file that
# should have been a hard link.
_UNCHANGED, _WRITTEN, _COPIED_MEDIA = "", "written", "copied media"


def _place(source: Path, target: Path, staging: Path, *, link: bool) -> str:
    """Put `source` at `target` when it is not there yet, and say what it did.

    A copy keeps the source's modification time, so an unchanged file
    has the same size and time on the next publish and is not written
    again. A media file (`link`) is a hard link when the file system
    allows it, and a copy otherwise.
    """
    if _same(source, target):
        return _UNCHANGED
    temporary = staging / target.name
    temporary.unlink(missing_ok=True)
    done = _WRITTEN
    if not (link and _hard_link(source, temporary)):
        shutil.copy2(source, temporary, follow_symlinks=False)
        done = _COPIED_MEDIA if link else _WRITTEN
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temporary, target)
    return done


def _same(source: Path, target: Path) -> bool:
    try:
        have, want = target.stat(), source.stat()
    except OSError:
        return False
    return have.st_size == want.st_size and have.st_mtime_ns == want.st_mtime_ns


def _hard_link(source: Path, target: Path) -> bool:
    try:
        os.link(source, target, follow_symlinks=False)
    except OSError:
        return False
    return True


def _remove_empty_folders(root: Path) -> None:
    """Remove the folders under `root` that hold no file, deepest first."""
    for folder in sorted((p for p in root.rglob("*") if p.is_dir()), key=lambda p: len(p.parts),
                         reverse=True):
        if not any(folder.iterdir()):
            folder.rmdir()
