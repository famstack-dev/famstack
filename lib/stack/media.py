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

Beside each file is `<artifact-id>.json`, written by `keep`: who sent
the artifact, from where, under what name, and when. The archive is
meant to be readable by someone who finds the tree with none of this
software left, so everything needed to place a file back in its
context is next to it rather than in a database.

`derive` adds the second copies a page can actually show: audio a
browser will play, an image bounded to a width a page can carry. The
sidecar lists them, so a later conversion replaces its own output
instead of leaving an orphan behind.

Writes are idempotent and atomic. An identifier names one artifact
permanently, so a file already at its path is the file that belongs
there and is never rewritten.

The archive is deliberately excluded from version control; see
`ensure_ignored`. Stdlib only, like the rest of `lib/stack`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import mimetypes
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
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

# How wide an image derivative may be. A page embedding a dozen
# originals downloads a dozen originals: the width a wikilink alias
# carries is a display attribute and changes nothing about what
# crosses the wire. 1600px still fills a retina-width column, which is
# where shrinking further starts costing the reader something.
MAX_IMAGE_WIDTH = 1600

# The forms a derivative takes, by the kind of artifact it came from.
# AAC in MP4 because Safari does not decode Opus in Ogg. JPEG because
# mjpeg is built into ffmpeg itself, where WebP needs libwebp linked in
# at build time: a build without it produces no derivative at all, so
# the page falls back to embedding the full-size original, which is the
# one outcome the derivative exists to prevent. WebP would be roughly a
# quarter smaller, which does not buy that risk.
_DERIVED_EXT = {"audio": "m4a", "image": "jpg"}

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


def extension_for(filename: str, mime: str = "") -> str:
    """The extension to file an artifact under.

    The name it was posted as decides, because that is what the sender
    saw and what a reader of the archive will recognise. A name without
    one (a paste, a client that sends none) falls back to what the
    declared type implies, and then to a neutral extension.
    """
    from_name = Path(str(filename or "")).suffix
    if from_name.lstrip("."):
        return _safe_ext(from_name)
    declared = str(mime or "").split(";")[0].strip()
    guessed = mimetypes.guess_extension(declared) if declared else ""
    return _safe_ext(guessed or "")


def kind_for(mime: str) -> str:
    """The kind of artifact a media type describes.

    One of image, video, audio, file. The kind is what decides whether
    a page can show the thing inline and what a derivative of it would
    be, so it is recorded rather than re-guessed at every reading.
    """
    top = str(mime or "").split("/")[0].strip().lower()
    return top if top in ("image", "video", "audio") else "file"


def _located(root, artifact_id: str, ext: str, when) -> "tuple[Path, str]":
    """Where an artifact lives, and the link that addresses it."""
    year, month = when.strftime("%Y"), when.strftime("%m")
    name = f"{safe_name(artifact_id)}.{_safe_ext(ext)}"
    return (Path(root) / year / month / name,
            f"/{ARCHIVE_DIR}/{year}/{month}/{name}")


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
    target, link = _located(root, artifact_id, ext, when)
    if target.exists():
        return link
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(target, data)
    except OSError as e:
        log.warning("could not archive %s: %s", artifact_id, e)
        return ""
    return link


def kept(root, artifact_id: str, *, ext: str, when) -> str:
    """The link to an artifact already in the archive, or "".

    Lets a caller skip fetching bytes it would only throw away: the
    archive is rebuilt from the same source on every run, and the
    identifier already says whether this artifact is in it.
    """
    target, link = _located(root, artifact_id, ext, when)
    return link if target.exists() else ""


def keep(root, artifact_id: str, data: bytes, *, ext: str, when,
         kind: str, source: str, mime: str = "", filename: str = "",
         room_id: str = "", sender: str = "") -> str:
    """Store an artifact together with the record of where it came from.

    `store` with a sidecar: the same bytes, plus `<artifact-id>.json`
    naming who sent it, from which room, under what name, and when.
    Nothing else in the tree says any of that - the filename is a
    scrubbed identifier and the directories are a date - so without the
    sidecar the archive is a pile of anonymous files the moment it
    outlives the software that wrote it.

    `source` names the pipeline that filed the artifact. `kind` is the
    caller's own classification (see `kind_for`), recorded rather than
    inferred here because the caller knows what it handled.

    Returns the link, or "" when the bytes could not be written.
    """
    link = store(root, artifact_id, data, ext=ext, when=when)
    if not link:
        return ""
    sidecar, _ = _located(root, artifact_id, _SIDECAR_EXT, when)
    if not sidecar.exists():
        # Field order is the order a reader wants them in: identity,
        # then the file, then the two timestamps, then our own notes.
        _write_sidecar(sidecar, {
            "event_id": artifact_id,
            "room_id": room_id,
            "sender": sender,
            "filename": filename,
            "mime": mime,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "captured_at": when.isoformat(),
            "stored_at": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "source": source,
            "derived": [],
        })
    return link


# ── The record beside the file ───────────────────────────────────────────

_SIDECAR_EXT = "json"


def _read_sidecar(path: Path) -> dict:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _write_sidecar(path: Path, record: dict) -> None:
    """Write a sidecar the same way the payload is written.

    Atomically, and never fatally: an archived file with no record
    beside it is a loss worth a warning, and losing the file as well
    because the record failed would be a worse one.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_atomically(
            path, json.dumps(record, indent=2, ensure_ascii=False).encode("utf-8"),
        )
    except OSError as e:
        log.warning("could not record %s: %s", path.name, e)


def _record_derived(sidecar: Path, link: str, kind: str) -> None:
    """Add a derivative to the sidecar's list, once.

    The list is what lets a later conversion replace its own output:
    without it a second format, a better encoder or a changed width
    leaves the previous file in the tree with nothing pointing at it
    and nothing saying where it came from.
    """
    record = _read_sidecar(sidecar)
    if not record:
        return
    derived = record.get("derived")
    if not isinstance(derived, list):
        derived = []
    if any(isinstance(d, dict) and d.get("path") == link for d in derived):
        return
    record["derived"] = [*derived, {"path": link, "kind": kind}]
    _write_sidecar(sidecar, record)


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
    return _convert(src, dst, ["-vn", "-c:a", "aac", "-b:a", "96k"])


def transcode_image(src, dst, *, max_width: int = MAX_IMAGE_WIDTH) -> bool:
    """Convert an image to a JPEG no wider than `max_width`. True when done.

    A page that embeds originals makes a reader download originals, and
    twenty photographs off a phone are a hundred megabytes of them. The
    display width a wikilink carries does not change that; only a
    smaller file does.

    Aspect ratio is preserved and an image already narrower than the
    bound keeps its dimensions - `min()` in the scale expression is
    what stops an old small photo being blown up to fill the width.

    False when ffmpeg is absent or the conversion fails, with nothing
    left behind at `dst`. Callers fall back to the original.
    """
    return _convert(src, dst, [
        "-vf", f"scale='min({int(max_width)},iw)':-1:flags=lanczos",
        # One frame: the source may be an animation, and the point of
        # the derivative is a bounded still.
        "-frames:v", "1",
        # ffmpeg's JPEG default is soft enough to show on a photograph.
        # 3 is near the top of the quality scale and still a fraction of
        # the original's size.
        "-q:v", "3",
    ])


def _convert(src, dst, options: "list[str]") -> bool:
    """Run ffmpeg from `src` to `dst` with `options`, atomically.

    Shared by every derivative. The output is built under a temporary
    name and renamed into place, because the site serves this tree
    while a compile is running and a half-written file under the final
    name would be both served and taken for finished work on the next
    run.

    Never raises. A missing converter, a codec the build lacks and a
    file ffmpeg cannot read are all the same to the caller: no
    derivative, and whatever it had before.
    """
    src, dst = Path(src), Path(dst)
    if dst.exists():
        return True

    tmp = dst.with_name(f".tmp-{dst.name}")
    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
             "-i", str(src), *options, str(tmp)],
            capture_output=True, timeout=_TRANSCODE_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("no conversion for %s: %s", src.name, e)
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


def derive(root, artifact_id: str, *, ext: str, when, kind: str) -> str:
    """The copy of an archived artifact a page can show, or "".

    Audio becomes playable, an image becomes bounded; everything else
    has no derivative and says so by returning "". The result is
    recorded in the artifact's sidecar, so the tree explains its own
    extra files.

    Idempotent and cheap to call again: an artifact whose derivative is
    already there costs one stat. An artifact that is already in the
    derived form is its own derivative and gets none, which is also
    what stops a conversion writing over its own input.

    Returns "" whenever the conversion cannot be made - ffmpeg absent,
    a format it will not read, the original gone. The caller shows the
    original instead.
    """
    target_ext = _DERIVED_EXT.get(kind, "")
    if not target_ext or _safe_ext(ext) == target_ext:
        return ""
    src, _ = _located(root, artifact_id, ext, when)
    if not src.exists():
        return ""
    dst, link = _located(root, artifact_id, target_ext, when)

    convert = transcode_audio if kind == "audio" else transcode_image
    if not convert(src, dst):
        return ""
    sidecar, _ = _located(root, artifact_id, _SIDECAR_EXT, when)
    _record_derived(sidecar, link, kind)
    return link


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
