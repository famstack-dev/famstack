"""The admin key: what opens the Mac's address to the admin's browser.

    stack devices admin-link                 # the link with the key, the key under it
    stack devices admin-link --page setup --json
    # {"link": "http://127.0.0.1:42002/setup?key=Zq3...", "key": "Zq3...",
    #  "address": "http://127.0.0.1:42002", "tunnel": "ssh -L 42002:127.0.0.1:42002 <this Mac>"}

    ensure(instance_dir)                     # the key, made once
    link(instance_dir, config, "/dashboard") # the same answer as --json

The Mac's address (`127.0.0.1:42002`, `[core] cards_port`) shows every
person's password and takes every change to the stack. Core publishes it
on 127.0.0.1 only, but every container of the stack reaches that address
too, through `host.docker.internal`, and the Host header it sends is its
own choice. So the listener cannot tell the admin's browser from a
container. The admin key can: a random key of 256 bits that the Mac
keeps in `.stack/admin-key`, mode 0600, which no container mounts. Its
SHA-256 hash goes into `.stack/devices/admin-key.sha256`, the folder core's
tools server mounts read only, so the server can check a key without
holding one.

How a browser gets the key: the menu bar app's "Setup…", the installer's
last line and `stack devices admin-link` open the Mac's address once with
`?key=<key>`. The tools server checks it, sets it as a cookie on
`127.0.0.1` and sends the browser on to the same page without the key.
The command prints the key under the link too, for the field the Mac's
address shows a browser without the key. Over ssh the key works the
same, through `ssh -L 42002:127.0.0.1:42002`: it proves the admin's
browser, not the Mac, hence its name.

The key is made once and then kept: by core's start (an install from
beta.1 gets it on its first start after the update) or by the first
command that needs it, whichever runs first. A browser that holds it
keeps its rights across restarts.

Every link to the Mac's address names `127.0.0.1`, never `localhost`: a
browser sends a cookie to every port of the host name that set it, and
the Mac opens photos, the documents, the chat and code at
`localhost:<port>`. On `127.0.0.1` the key's cookie reaches no other
service.

What it does not do: check a key (the tools server does, `devices/
admin_key.py` in core), or change the key. A new key is `rm
.stack/admin-key` and a `stack devices admin-link`; every browser then
needs the new link.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Mapping
from pathlib import Path

from . import hash_of

__all__ = ["KEY_FILE", "HASH_FILE", "COOKIE", "MAC_PORT", "ensure", "link", "cookie_header", "mac_port"]

# SAFETY: the key itself, readable by the Mac's user alone. Never mounted
# into a container: core's tools server gets the hash below, the bot
# runner mounts `.stack` but holds every secret already.
KEY_FILE = Path(".stack") / "admin-key"
# The key's hash, in the folder core's tools server mounts read only.
HASH_FILE = Path(".stack") / "devices" / "admin-key.sha256"
# The cookie a browser keeps the key in; the tools server names it alike.
COOKIE = "stack-admin"
# The Mac's address unless `[core] cards_port` names another port.
MAC_PORT = 42002
# 32 random bytes: 256 bits, 43 characters of URL-safe base64.
_KEY_BYTES = 32


def ensure(instance_dir: Path) -> str:
    """The admin key of the instance at `instance_dir`, made on the first
    call: written to `KEY_FILE` (mode 0600), its hash to `HASH_FILE`
    (mode 0644). A later call returns the same key and writes the hash
    again only when it is missing or wrong.

        ensure(root)    # "Zq3VJm0...", the same on every call
    """
    root = Path(instance_dir)
    key_file = root / KEY_FILE
    if not key_file.exists():
        _write_key(key_file, secrets.token_urlsafe(_KEY_BYTES))
    key = key_file.read_text(encoding="utf-8").strip()
    _write_hash(root / HASH_FILE, hash_of(key))
    return key


def link(instance_dir: Path, config: Mapping, path: str = "/setup") -> dict:
    """The admin link to `path` on the Mac's address of the instance at
    `instance_dir`, whose stack.toml is `config`: the link with the key,
    the key alone, the address, and the ssh tunnel that reaches it from
    another computer.

        link(root, {"core": {}}, "/dashboard")
        # {"link": "http://127.0.0.1:42002/dashboard?key=Zq3...", "key": "Zq3...",
        #  "address": "http://127.0.0.1:42002",
        #  "tunnel": "ssh -L 42002:127.0.0.1:42002 <this Mac>"}
    """
    key = ensure(instance_dir)
    port = mac_port(config)
    address = f"http://127.0.0.1:{port}"
    return {"link": f"{address}{path}?key={key}", "key": key, "address": address,
            "tunnel": f"ssh -L {port}:127.0.0.1:{port} <this Mac>"}


def cookie_header(instance_dir: Path) -> dict[str, str]:
    """The header a command on this Mac sends to the Mac's address, as the
    admin's browser sends its cookie.

        cookie_header(root)    # {"Cookie": "stack-admin=Zq3..."}
    """
    return {"Cookie": f"{COOKIE}={ensure(instance_dir)}"}


def mac_port(config: Mapping) -> int:
    """The port of the Mac's address in `config` (stack.toml).

        mac_port({"core": {}})                      # 42002
        mac_port({"core": {"cards_port": 42902}})   # 42902
    """
    return int(((config or {}).get("core") or {}).get("cards_port") or MAC_PORT)


# ── Writing the files ────────────────────────────────────────────────────

def _write_key(path: Path, key: str) -> None:
    """Write `key` to `path`, created with mode 0600, so the key is never
    readable by another user, not even for a moment."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(key + "\n")
    os.chmod(path, 0o600)


def _write_hash(path: Path, digest: str) -> None:
    """Write `digest` to `path` unless it holds it already. Through a file
    next to it and a rename, as the list of devices is written, so the
    tools server never reads half of it. Mode 0644: no key, and the
    container's user reads it."""
    if path.exists() and path.read_text(encoding="utf-8").strip() == digest:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(".part")
    part.write_text(digest + "\n", encoding="utf-8")
    os.chmod(part, 0o644)
    part.replace(path)
