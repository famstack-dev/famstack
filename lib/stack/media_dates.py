"""media_dates: the day a kept photo was taken, from its EXIF.

    days = capture_days(media.open_archive(), kept)   # {artifact id: date | None}
    capture_day(Path("IMG_0031.jpg"))                  # date(2026, 10, 31)

Matrix records when a message reached the server, never when a photo was
taken. The file can still say it: EXIF `DateTimeOriginal`, when the
client did not strip it (Element X on Android keeps it; on the iPhone
only with "Optimise media" off). A caller such as the diary compile uses
the day to date what the photo shows.

It reads the archive `stack.media` keeps and writes nothing. The archive
lays out an artifact as `<root>/<yyyy>/<mm>/<id>.<ext>` and links it as
`/media/<yyyy>/<mm>/<id>.<ext>`; a derived copy (a bounded JPEG, an AAC
recording) sits beside the original under the same id. The day is read
from the original, because a derived copy is re-encoded by ffmpeg and
carries no EXIF.

Pillow is imported inside `capture_day`: only the bot runner calls it,
and the bot runner has Pillow; the host CLI, which is stdlib only, never
does. A file Pillow cannot open (HEIC without a plugin, a video, audio)
has no day.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from stack import media

# EXIF's Exif sub-IFD, and in it DateTimeOriginal: when the shutter
# fired, on the camera's clock, as "YYYY:MM:DD HH:MM:SS".
_EXIF_IFD = 0x8769
_DATE_TIME_ORIGINAL = 0x9003


def capture_days(root, kept: "dict[str, str]") -> "dict[str, date | None]":
    """The day each kept artifact was taken, by artifact id.

    `kept` maps an artifact id to the link of the copy a page shows
    (`media.kept`, `media.derive`); `root` is the archive directory, or
    None when there is none, which gives no days.
    """
    if root is None:
        return {}
    return {artifact_id: capture_day(_original(Path(root), link))
            for artifact_id, link in kept.items()}


def capture_day(path) -> "date | None":
    """The day a photo was taken, from its EXIF `DateTimeOriginal`, or None.

    The day as the camera's clock had it: EXIF carries no timezone, and
    the household's day is the one the phone showed. None for a file
    that keeps no such date, for a date of zeros, for a file that is no
    image or is not there, and for a host without Pillow. It never
    raises, because a photo it cannot read only loses its capture day,
    never its entry.
    """
    try:
        from PIL import Image  # the bot runner's dependency, see the header

        with Image.open(path) as image:
            value = image.getexif().get_ifd(_EXIF_IFD).get(_DATE_TIME_ORIGINAL)
        return datetime.strptime(str(value).strip()[:10], "%Y:%m:%d").date()
    except Exception:  # noqa: BLE001  (no image, no tag, no Pillow: no day)
        return None


def _original(root: Path, link: str) -> Path:
    """The original behind a link into the archive.

    The linked file itself, unless a derived copy is what is linked: then
    the other file under the same id in that folder, which is the
    original. The sidecar (`<id>.json`) is never an original.
    """
    shown = root.joinpath(*link.removeprefix(f"/{media.ARCHIVE_DIR}/").split("/"))
    others = [p for p in shown.parent.glob(f"{shown.stem}.*")
              if p != shown and p.suffix != ".json"]
    return others[0] if others else shown
