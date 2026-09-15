"""Media archive - keeping the original bytes an artifact arrived as.

A stacklet that files an artifact keeps a description of it: a page with
a summary, the metadata a reader needs, a link back to where it was
posted. The bytes themselves stay behind that link, addressed by an
identifier only the producing service understands, and nothing about
that arrangement promises they will still be there later. This module is
where a copy is kept instead.

The archive is a tree of originals addressed by the identifier of the
event that carried them:

    <root>/<yyyy>/<mm>/<artifact-id>.<ext>

`store` returns the path that addresses the file from the root of the
rendered site, which is the form a page links or embeds. That form is
the reason the archive lives inside the published tree: it resolves at
any page depth and in any deployment mode, where an absolute URL would
be right in one mode and wrong in the other, and pages outlive both.

Writes are idempotent and atomic. An identifier names one artifact
permanently, so a file already at its path is the file that belongs
there and is never rewritten.

The archive is deliberately excluded from version control; see
`ensure_ignored`. Stdlib only, like the rest of `lib/stack`.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

# The archive's directory name, and the first segment of every link into
# it. Both are derived from here so the name is stated once.
ARCHIVE_DIR = "media"

# What survives into a filename. Everything else collapses to a hyphen:
# identifiers come from event ids and user-supplied filenames, which
# carry sigils, separators and path punctuation a path must not inherit.
# Excluding the dot is what makes traversal impossible - `..` cannot
# survive the substitution, so no identifier can address a parent.
_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")

# Filenames are capped well below the ~255 byte limit filesystems impose,
# leaving room for the extension and the temporary write prefix.
_MAX_STEM = 96
_MAX_EXT = 16

# A conversion that has not finished by now is not going to. The caller
# degrades to what it had rather than blocking the run behind it.
_TRANSCODE_TIMEOUT_S = 300

_IGNORE_NOTE = (
    "# Original uploads, kept on disk only. Deleting the source event has\n"
    "# to be able to delete the copy, and history would keep it anyway.\n"
)


def archive_root(site_root) -> Path:
    """The archive directory inside a rendered site's content root."""
    return Path(site_root) / ARCHIVE_DIR


def safe_name(artifact_id: str) -> str:
    """An identifier reduced to something that can be a filename.

    Every run of unsupported characters becomes a single hyphen, and the
    result is trimmed and capped. An empty or wholly unsupported
    identifier still yields a name, because dropping the artifact is a
    worse outcome than filing it under a generic one.
    """
    cleaned = _UNSAFE.sub("-", str(artifact_id)).strip("-")
    return cleaned[:_MAX_STEM].strip("-") or "artifact"


def _safe_ext(ext: str) -> str:
    cleaned = _UNSAFE.sub("", str(ext).lstrip(".").lower())
    return cleaned[:_MAX_EXT] or "bin"


def store(root, artifact_id: str, data: bytes, *, ext: str, when) -> str:
    """Keep `data` in the archive and return the link that addresses it.

    `root` is the archive directory (see `archive_root`), `when` anything
    with `strftime` - the date the artifact belongs to, which decides the
    year and month folders. The returned link is rooted at the site, not
    at `root`, so it reads the same from a page at any depth.

    An artifact already in the archive is left exactly as it is: the
    identifier is the promise that the bytes are the same ones.

    Returns "" when the write fails. A caller that cannot archive an
    artifact still has whatever it had before and should carry on with
    it; losing the page over a failed copy helps nobody.
    """
    year, month = when.strftime("%Y"), when.strftime("%m")
    name = f"{safe_name(artifact_id)}.{_safe_ext(ext)}"
    target = Path(root) / year / month / name
    link = f"/{ARCHIVE_DIR}/{year}/{month}/{name}"

    if target.exists():
        return link
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(target, data)
    except OSError as e:
        log.warning("could not archive %s: %s", artifact_id, e)
        return ""
    return link


def _write_atomically(target: Path, data: bytes) -> None:
    """Write via a neighbouring temporary file and rename over the target.

    The rename is what makes a reader safe: the site serves this tree
    while it is being written, and a partially written file under the
    final name would be served as the artifact.
    """
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".tmp-")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, target)
    finally:
        Path(tmp).unlink(missing_ok=True)


def transcode_audio(src, dst) -> bool:
    """Convert an audio file to AAC in an MP4 container. True when done.

    The conversion exists for one reason: Safari does not decode Opus in
    an Ogg container, which is what a recording made in a chat client
    usually is, so an archived original alone would be silent for part of
    the audience. The original is kept regardless; this is the second
    copy that plays everywhere.

    False when ffmpeg is absent or the conversion fails, with nothing
    left behind at `dst`. Callers fall back to the link they had.
    """
    src, dst = Path(src), Path(dst)
    if dst.exists():
        return True

    tmp = dst.with_name(f".tmp-{dst.name}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
             "-i", str(src), "-vn", "-c:a", "aac", "-b:a", "96k", str(tmp)],
            capture_output=True, timeout=_TRANSCODE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("no audio conversion for %s: %s", src.name, e)
        tmp.unlink(missing_ok=True)
        return False

    if done.returncode != 0 or not tmp.exists():
        detail = done.stderr.decode(errors="replace").strip().splitlines()
        log.warning("ffmpeg could not convert %s: %s",
                    src.name, detail[-1] if detail else f"rc={done.returncode}")
        tmp.unlink(missing_ok=True)
        return False

    try:
        os.replace(tmp, dst)
    except OSError as e:
        log.warning("could not place %s: %s", dst.name, e)
        tmp.unlink(missing_ok=True)
        return False
    return True


def ensure_ignored(repo_root) -> None:
    """Make the repository at `repo_root` ignore the archive.

    Two reasons, and the first is the one that matters: deleting the
    source event has to delete the copy, and a blob that was committed
    once stays reachable in history no matter what happens to the working
    tree, which would make the deletion a false promise. The second is
    size - originals accumulate and every clone would carry all of them.

    Idempotent. A repository that already states the rule is left alone,
    so this is safe to call on every write.
    """
    gitignore = Path(repo_root) / ".gitignore"
    rule = f"{ARCHIVE_DIR}/"
    try:
        existing = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
        if rule in (line.strip() for line in existing.splitlines()):
            return
        separator = "" if not existing or existing.endswith("\n") else "\n"
        gitignore.write_text(
            f"{existing}{separator}{_IGNORE_NOTE}{rule}\n", encoding="utf-8",
        )
    except OSError as e:
        log.warning("could not record the archive's ignore rule: %s", e)
