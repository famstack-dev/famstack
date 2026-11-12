"""The list of paired devices, and its one writer.

A paired device is a phone or a browser that the Mac let in: it opens the
home address's pages (the dashboard, the card pages, the `/go` links) as
one person of the family, or as the household for a shared screen. The
list says, per device, whom it belongs to, its name from its browser,
when it was paired and last seen, and the SHA-256 hash of its key:

    stack devices list --json
    stack devices add --person homer --name iPhone --key-hash <64 hex> --json
    stack devices remove d-3f9a2c1b --json
    stack devices seen d-3f9a2c1b --json
    stack devices notice --kind waits ...   # the Server room's notice (stack.devices.notice)

    command(instance_dir, "add", {"person": "homer", "name": "iPhone",
                                  "key_hash": "9f86d0...", }, now)
    # {"ok": True, "device": {"id": "d-3f9a2c1b", "person": "homer",
    #  "name": "iPhone", "paired": "2026-11-10", "seen": "2026-11-10"}}

Why one writer. The tools server in core's container checks every request
on the home address against this list, but it mounts nothing of the Mac
writable. So the list lives on the Mac, in `.stack/devices/devices.toml`,
and this module is the only code that writes it: the CLI on the Mac
directly, the tools server through the host API's `devices` and
`devices_set` (`action`: `add`, `remove`, `seen`), which run
`stack devices ... --json`. The tools server reads
the file through a read-only mount of its folder and reloads it when it
changes. The folder holds this file alone, so the mount shows the
container nothing else of `.stack/` (the secrets live next to it).

Why a hash. The device keeps a random key in a cookie; the Mac keeps only
the key's hash, never the key. Whoever reads the file (it is mode 0644,
and the container reads it) learns no key that opens anything.

"Last seen" is a date, written at most once a day per device (`seen`),
so a phone that opens a page a minute does not rewrite the file a minute.

A person who leaves the family loses their devices: `stack family set`
calls `forget_people` with the people who stay. A device of the household
(`person = ""`) stays until it is removed.

What it does not do: make keys, pairing codes or join requests. Those
belong to the tools server, which hands the device its key; a key never
reaches the Mac's disk. The design: ADR-023 D9.

The package holds the context "Devices" on the host. This `__init__` is
the list and its writer; `pairing` prints a pairing code in the terminal
(`stack devices pair`), `notice` writes the Server room's notice of a
new device (`stack devices notice`), and `first_pairing` says what the
restart after an update tells an install with no paired device. They are
reached as `stack.devices.pairing`, `stack.devices.notice` and
`stack.devices.first_pairing`; none writes the list.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import tomllib
from datetime import datetime
from pathlib import Path

from ..users import load_users, user_id

__all__ = ["DEVICES_FILE", "HOUSEHOLD", "command", "listed", "add", "remove", "seen",
           "forget_people", "hash_of", "file_of"]

# Where the list is, under the instance directory. Its folder is mounted
# into core's tools server read-only, so nothing else goes into it.
DEVICES_FILE = Path(".stack") / "devices" / "devices.toml"
# The person of a shared device: the household, nobody in particular.
HOUSEHOLD = ""

# A key's hash as `hash_of` writes it: SHA-256 in lowercase hex.
_KEY_HASH = re.compile(r"[0-9a-f]{64}")
# A device's name as its browser names it ("iPhone", "Chrome on a Mac"):
# shown on the Mac, so it is kept short and plain.
_NAME_MOST = 60


def command(instance_dir: Path, verb: str, args: dict, now: datetime) -> dict:
    """Run one `stack devices` verb and return its JSON answer: what the
    CLI prints with `--json` and what the host API relays.

    `verb` is `list`, `add` (`person`, `name`, `key_hash`), `remove`
    (`id`) or `seen` (`id`). A refusal is `{"error": ...}` and writes
    nothing.

        command(root, "list", {}, now)      # {"devices": [...]}
        command(root, "remove", {"id": "d-3f9a2c1b"}, now)
        # {"ok": True, "removed": {"id": "d-3f9a2c1b", ...}}
        command(root, "fly", {}, now)       # {"error": "Unknown verb 'fly'. Use: list, add, remove, seen"}
    """
    if verb == "list":
        return {"devices": listed(instance_dir)}
    if verb == "add":
        return add(instance_dir, person=args.get("person") or "", name=args.get("name") or "",
                   key_hash=args.get("key_hash") or "", now=now)
    if verb == "remove":
        gone = remove(instance_dir, args.get("id") or "")
        return {"ok": True, "removed": gone} if gone else {"error": "No device has that id."}
    if verb == "seen":
        if not any(d["id"] == args.get("id") for d in _read(instance_dir)):
            return {"error": "No device has that id."}
        return {"ok": True, "written": seen(instance_dir, args["id"], now)}
    return {"error": f"Unknown verb '{verb}'. Use: list, add, remove, seen"}


def listed(instance_dir: Path) -> list[dict]:
    """The paired devices, oldest first, without their key hashes.

        listed(root)
        # [{"id": "d-3f9a2c1b", "person": "homer", "name": "iPhone",
        #   "paired": "2026-11-10", "seen": "2026-11-11"}]
    """
    return [{k: v for k, v in d.items() if k != "key"} for d in _read(instance_dir)]


def add(instance_dir: Path, *, person: str, name: str, key_hash: str, now: datetime) -> dict:
    """Add a device of `person` (a user id of users.toml, or `HOUSEHOLD`)
    named `name`, whose key hashes to `key_hash`, paired and seen `now`.

    Refuses, writing nothing, a person not in the family, an empty or
    long name, and a hash that is not 64 lowercase hex digits (a key
    itself is never stored, and a hash of another form is a mistake).

        add(root, person="homer", name="iPhone", key_hash="9f86d0...", now=now)
        # {"ok": True, "device": {"id": "d-3f9a2c1b", "person": "homer", ...}}
        add(root, person="ned", name="iPhone", key_hash="9f86d0...", now=now)
        # {"error": "ned is not in the family."}
    """
    if person != HOUSEHOLD and person not in _people(instance_dir):
        return {"error": f"{person} is not in the family."}
    name = " ".join(name.split())
    if not name or len(name) > _NAME_MOST:
        return {"error": f"A device's name has 1 to {_NAME_MOST} characters."}
    if not _KEY_HASH.fullmatch(key_hash):
        return {"error": "The key's hash is SHA-256 in lowercase hex, 64 characters."}
    devices = _read(instance_dir)
    day = now.date().isoformat()
    device = {"id": _new_id(devices), "person": person, "name": name,
              "paired": day, "seen": day, "key": key_hash}
    _write(instance_dir, [*devices, device])
    return {"ok": True, "device": {k: v for k, v in device.items() if k != "key"}}


def remove(instance_dir: Path, device_id: str) -> dict | None:
    """Remove the device `device_id`; return it as `listed` shows it, or
    None when no device has that id (nothing is written then). Its key
    stops counting as soon as the tools server reloads the file, on its
    next request."""
    devices = _read(instance_dir)
    gone = next((d for d in devices if d["id"] == device_id), None)
    if gone is None:
        return None
    _write(instance_dir, [d for d in devices if d is not gone])
    return {k: v for k, v in gone.items() if k != "key"}


def seen(instance_dir: Path, device_id: str, now: datetime) -> bool:
    """Note that `device_id` was seen `now`. Writes only when the date
    changed since the last time, so at most once a day per device; True
    when it wrote."""
    devices = _read(instance_dir)
    day = now.date().isoformat()
    device = next((d for d in devices if d["id"] == device_id), None)
    if device is None or device.get("seen") == day:
        return False
    device["seen"] = day
    _write(instance_dir, devices)
    return True


def forget_people(instance_dir: Path, staying: set[str]) -> list[dict]:
    """Remove the devices of every person not in `staying` (user ids);
    the household's devices stay. Returns the removed devices. Writes
    nothing when there is nothing to remove, and no file at all when
    there was none."""
    devices = _read(instance_dir)
    gone = [d for d in devices if d["person"] != HOUSEHOLD and d["person"] not in staying]
    if gone:
        _write(instance_dir, [d for d in devices if d not in gone])
    return [{k: v for k, v in d.items() if k != "key"} for d in gone]


def hash_of(key: str) -> str:
    """The hash the list keeps of a device key: SHA-256, lowercase hex.

        hash_of("abc")   # "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    """
    return hashlib.sha256(key.encode()).hexdigest()


def file_of(instance_dir: Path) -> Path:
    """The list's file of the instance at `instance_dir`."""
    return Path(instance_dir) / DEVICES_FILE


# ── Reading and writing the file ─────────────────────────────────────────

def _people(instance_dir: Path) -> set[str]:
    return {user_id(u) for u in load_users(instance_dir)}


def _read(instance_dir: Path) -> list[dict]:
    """The devices in the file, in its order; [] when there is no file."""
    path = file_of(instance_dir)
    if not path.exists():
        return []
    with path.open("rb") as f:
        return [dict(d) for d in tomllib.load(f).get("device", [])]


def _write(instance_dir: Path, devices: list[dict]) -> None:
    """Write `devices` whole. Through a file next to it and a rename, so
    the tools server, which reads the file on its own schedule, never
    reads half of it. Mode 0644: it holds no key, and the container's
    user must read it."""
    path = file_of(instance_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# The devices paired with this stack. Written by `stack devices`;",
             "# `key` is the SHA-256 hash of the device's key, never the key.", ""]
    for d in devices:
        lines.append("[[device]]")
        lines += [f"{field} = {_quoted(d.get(field, ''))}"
                  for field in ("id", "person", "name", "paired", "seen", "key")]
        lines.append("")
    part = path.with_suffix(".toml.part")
    part.write_text("\n".join(lines), encoding="utf-8")
    os.chmod(part, 0o644)
    part.replace(path)


def _quoted(value: str) -> str:
    """`value` as a TOML basic string."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return '"' + "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in escaped) + '"'


def _new_id(devices: list[dict]) -> str:
    taken = {d["id"] for d in devices}
    while (found := f"d-{secrets.token_hex(4)}") in taken:
        pass
    return found
