"""The list of paired devices, as the tools server reads it: read only.

    store = current()                 # the list at $DEVICES_FILE
    store.find("Zx8...the cookie's key")
    # Device(id="d-3f9a2c1b", person="homer", name="iPhone",
    #        paired="2026-11-10", seen="2026-11-11")
    store.all()                       # every device, oldest first

The file is `.stack/devices/devices.toml` on the Mac, written by its one
writer, `stack devices` (`lib/stack/devices/`), and mounted into core's
container read only (`/devices/devices.toml`). The tools server changes
it only through the host API. A request on the home address asks this
store, in process, so a removed device is refused on its next request:
the store looks at the file's size and modification time on every
lookup and reads it again when either changed, which costs one `stat`.

The file holds each key's SHA-256 hash, never a key, so the store hashes
the key a browser sends and looks the hash up.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from stack.devices import HOUSEHOLD, hash_of

__all__ = ["Device", "Store", "current", "DEVICES_FILE_IN_CONTAINER"]

# Where core's compose file mounts the list's folder. `DEVICES_FILE`
# names another path for a test.
DEVICES_FILE_IN_CONTAINER = "/devices/devices.toml"


@dataclass(frozen=True)
class Device:
    """One paired device as the list holds it, without its key's hash.
    `person` is a user id, or "" for the household (a shared device).

        Device("d-3f9a2c1b", "homer", "iPhone", "2026-11-10", "2026-11-11").shared   # False
    """

    id: str
    person: str
    name: str
    paired: str
    seen: str

    @property
    def shared(self) -> bool:
        """Whether the device is the household's: a shared screen."""
        return self.person == HOUSEHOLD


class Store:
    """The devices in one file, read again whenever the file changed.
    A missing or unreadable file is an empty list: before the first
    pairing there is none, and a file being replaced is read on the next
    request.

        Store(Path("/devices/devices.toml")).find(key)   # a Device, or None
    """

    def __init__(self, path: Path):
        self.path = path
        self._stamp: tuple[int, int] | None = None
        self._by_hash: dict[str, Device] = {}
        self._order: list[Device] = []

    def find(self, key: str) -> Device | None:
        """The device whose key is `key`, or None (also for "")."""
        if not key:
            return None
        self._refresh()
        return self._by_hash.get(hash_of(key))

    def all(self) -> list[Device]:
        """Every device, in the file's order (oldest first)."""
        self._refresh()
        return list(self._order)

    def _refresh(self) -> None:
        try:
            stat = self.path.stat()
        except OSError:
            self._stamp, self._by_hash, self._order = None, {}, []
            return
        stamp = (stat.st_mtime_ns, stat.st_size)
        if stamp == self._stamp:
            return
        try:
            rows = tomllib.loads(self.path.read_text(encoding="utf-8")).get("device", [])
        except (OSError, tomllib.TOMLDecodeError):
            return
        self._order = [Device(r["id"], r["person"], r["name"], r["paired"], r["seen"]) for r in rows]
        self._by_hash = {r["key"]: d for r, d in zip(rows, self._order)}
        self._stamp = stamp


_STORES: dict[str, Store] = {}


def current() -> Store:
    """The store of the list at `DEVICES_FILE` (read on each call, like the
    rest of core's settings), the container's mount when unset."""
    path = os.environ.get("DEVICES_FILE") or DEVICES_FILE_IN_CONTAINER
    if path not in _STORES:
        _STORES[path] = Store(Path(path))
    return _STORES[path]
