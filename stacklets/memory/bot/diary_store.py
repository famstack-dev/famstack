"""What the diary compiler remembers between runs.

The compiler is a fold over the whole room, and it stays one. That is
what makes it correct: a reply to a memo from March arrives in
September, an edit lands on a year-old note, a remark turns out to be
about a photo posted last spring. Anything that walked forward from a
watermark and appended would never attach those, and would be wrong in
a way nobody notices until they read the page.

So the room is re-read every time and the pages are recomputed from
scratch. What is remembered is not *where we got to* but *what each
piece cost*: the model's reading of a message, and the paragraph
opening a month. Those are keyed by the thing they describe, so a
nightly run pays for what is genuinely new and nothing else, while a
recompile still produces the same diary it would have produced from an
empty cache.

Single-writer, like the archivist's tag cache: one batch at a time, no
locking. Every failure mode -- missing file, corrupt JSON, unwritable
directory -- degrades to an empty cache, because paying for a reading
twice is cheaper than a compile that refuses to run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from loguru import logger

DEFAULT_STATE_DIR = "/data/memory/diary"


def state_dir() -> Path:
    return Path(os.environ.get("DIARY_STATE_DIR", DEFAULT_STATE_DIR))


class JsonStore:
    """A dict on disk, written whole.

    One file rather than a file per key: the diary compiles as a single
    batch, so there is no concurrent writer to lose, and a room with ten
    thousand messages is a couple of megabytes rather than ten thousand
    inodes.
    """

    section = "items"

    def __init__(self, path: Path):
        self.path = Path(path)
        self._items: dict[str, dict] = {}

    def load(self) -> "JsonStore":
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._items = {}
            return self
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("[diary] unreadable cache at {} ({}), starting empty",
                           self.path, e)
            self._items = {}
            return self
        raw = data.get(self.section) if isinstance(data, dict) else None
        self._items = raw if isinstance(raw, dict) else {}
        return self

    def save(self) -> None:
        """Write the cache, or log and carry on.

        A cache that cannot be saved costs the next run some model
        calls. Failing the compile over it would cost the family their
        diary, which is the worse trade.
        """
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({self.section: self._items},
                                      ensure_ascii=False, indent=2),
                           encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError as e:
            logger.warning("[diary] could not save cache {}: {}", self.path, e)

    def __len__(self) -> int:
        return len(self._items)


class ReadingStore(JsonStore):
    """The model's reading of each message, keyed by event id.

    A message never changes: an edit is a new event pointing at the old
    one, so a reading is good forever and this cache never needs
    invalidating. The links between messages are deliberately not kept
    here -- they are a property of a slice of history rather than of one
    message, and a later message can create one.
    """

    section = "readings"

    def get(self, event_id: str) -> dict | None:
        found = self._items.get(event_id)
        return found if isinstance(found, dict) else None

    def put(self, event_id: str, reading: dict) -> None:
        self._items[event_id] = reading


class SummaryStore(JsonStore):
    """The paragraph opening each month, keyed by month and by content.

    Keyed by a digest of the month's entries as well as its name,
    because a month is not finished when it ends: a memo recorded in
    March can surface in September and belongs on March's page. When
    that happens the digest moves and the month is written again. When
    nothing moved, the paragraph is reused word for word, which is also
    what stops a nightly run quietly rewording the family's past.
    """

    section = "summaries"

    def get(self, month: str, digest: str) -> str:
        found = self._items.get(month)
        if not isinstance(found, dict) or found.get("digest") != digest:
            return ""
        text = found.get("text")
        return text if isinstance(text, str) else ""

    def put(self, month: str, digest: str, text: str) -> None:
        self._items[month] = {"digest": digest, "text": text}


def open_stores(directory: Path | None = None):
    """Both caches, loaded. Missing files are simply empty ones."""
    root = Path(directory) if directory else state_dir()
    return (ReadingStore(root / "readings.json").load(),
            SummaryStore(root / "summaries.json").load())
