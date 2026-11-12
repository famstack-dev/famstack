"""The door of the Mac's address: the Mac's rights for a browser that
holds the admin key, a stranger's welcome for anything else.

    # in the gate, for the Mac's listener (devices.gate):
    return await admin_key.door(request, call_next)

    GET  127.0.0.1:42002/setup?key=Zq3...   # 303 to /setup, cookie stack-admin=Zq3...
    GET  127.0.0.1:42002/setup, that cookie # 200, the Mac's rights: Who("mac")
    GET  127.0.0.1:42002/setup, no cookie   # 401, "Open famstack from the menu bar, ..."
    POST 127.0.0.1:42002/admin-key          # key=Zq3...&next=/setup: the pasted key
    GET  localhost:42002/setup              # 307 to http://127.0.0.1:42002/setup

Why a key. Core publishes the Mac's address on 127.0.0.1 only, yet every
container of the stack reaches it through `host.docker.internal` and
sends whatever Host header it likes. So the listener says "this Mac or a
container on it", never "the admin's browser". The admin key says that:
a random key of 256 bits that the Mac keeps in `.stack/admin-key`, out of
every container's reach (`stack.devices.admin_key`). This server holds
only its SHA-256 hash, next to the list of devices in the folder it
mounts read only, and compares hashes.

    request on the Mac's listener
      ├─ health check, styles, fonts, card door ─▶ on, as before
      ├─ named localhost or [::1] (GET) ─────────▶ 307 to 127.0.0.1
      ├─ ?key=<key> ─────────────────────────────▶ 303 without it, cookie
      ├─ POST /admin-key (the pasted key) ───────▶ 303 to `next`, cookie
      ├─ cookie holds the key ───────────────────▶ on as Who("mac")
      └─ anything else ──────────────────────────▶ 401, the key's page

The key leaves the address bar at once, so it is in no bookmark and no
history entry that outlives the first answer, and the access log writes
`key=…` in its place (`_HideTheKey`). Every answer of this server carries
`Referrer-Policy: no-referrer` (`browser_checks`), but the key's page,
whose form must send its own Origin, and which links nowhere.

The cookie is HttpOnly, SameSite=Strict, for every path and with no
Domain, renewed on every visit for 400 days, as a device key is. Without
a Domain it stays on `127.0.0.1`, the one host name famstack links the
Mac's address by; the Mac opens every other service at `localhost`, so
the cookie reaches none of them. Strict, because no other site's page
has a reason to open the Mac's pages with the admin's rights.

What opens without the key is what a container reaches anyway on the
internal listener: core's health check, the styles and fonts the key's
page loads, and the card door (`/cards/api`), which checks its own write
token. Nothing there shows a password or takes a change.

Wrong keys count against a limit (`TRIES`): 10 wrong in a minute, and the
next try is refused until the minute is over. A key of 256 bits is not
guessed; the limit keeps a script from trying anyway.

The design: ADR-023 D23.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlencode

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from access import DASHBOARD_PREFIX
from stack.devices import hash_of
from stack.devices.admin_key import COOKIE

from . import host, pages
from .store import DEVICES_FILE_IN_CONTAINER
from .who import MAC, NOBODY

__all__ = ["COOKIE", "FORM_PATH", "TRIES", "Tries", "door"]

# Where the key's page posts a pasted key. Outside `/dashboard`, whose
# writes take JSON only: an HTML form sends its fields urlencoded.
FORM_PATH = "/admin-key"
# How long a browser keeps the cookie: 400 days, the most a browser keeps
# any cookie; renewed on every visit.
KEY_KEPT_SECS = 400 * 24 * 3600
# The one host name of the Mac's address. A cookie holds for every port
# of the name that set it, and the Mac opens its services at `localhost`.
MAC_HOST = "127.0.0.1"
# The key's hash, next to the list of devices (`stack.devices.admin_key`).
_HASH_NAME = "admin-key.sha256"
# Open without the key: what the internal listener gives a container anyway.
_OPEN = (f"{DASHBOARD_PREFIX}/api/health",)
_OPEN_UNDER = (f"{DASHBOARD_PREFIX}/static/", f"{DASHBOARD_PREFIX}/fonts/", "/cards/api/")
# Where a pasted key lands when its page named no other page.
_LANDING = "/setup"


async def door(request: Request, call_next) -> Response:
    """Answer a request through the Mac's listener: on as `Who("mac")`
    with the admin key, else what the diagram in the module's header
    says. Sets `request.state.who`; renews the cookie on every answer to
    a browser that holds the key.

        GET 127.0.0.1:42002/setup?key=Zq3...   # 303, Location: /setup, Set-Cookie: stack-admin=Zq3...
        GET 127.0.0.1:42002/setup              # no cookie: 401, the key's page
    """
    key = request.cookies.get(COOKIE, "")
    holds = _is_the_key(key)
    path = request.url.path
    request.state.who = MAC if holds else NOBODY
    if not holds and (path in _OPEN or path.startswith(_OPEN_UNDER)):
        return await call_next(request)
    if request.method in ("GET", "HEAD") and _hostname(request) != MAC_HOST:
        return _to_the_macs_host(request)
    if path == FORM_PATH and request.method == "POST":
        return await _pasted(request)
    if "key" in request.query_params and request.method in ("GET", "HEAD"):
        return _offered(request, request.query_params["key"])
    if not holds:
        return _refusal(request)
    response = await call_next(request)
    _keep(response, key)
    return response


@dataclass
class Tries:
    """The wrong keys of the last minute, for every caller together: at
    `most` wrong, the next try is refused until the oldest is a minute
    old.

        TRIES.wrong(); TRIES.blocked()   # False after one, True after ten
    """

    most: int = 10
    window_secs: float = 60.0
    clock: Callable[[], float] = time.monotonic
    _wrong: list[float] = field(default_factory=list)

    def wrong(self) -> None:
        """Count one wrong key, now."""
        self._wrong.append(self.clock())

    def blocked(self) -> bool:
        """Whether the wrong keys of the last minute reached `most`."""
        now = self.clock()
        self._wrong = [t for t in self._wrong if now - t < self.window_secs]
        return len(self._wrong) >= self.most

    def forget(self) -> None:
        """Forget every wrong key (a test's fresh start)."""
        self._wrong.clear()


TRIES = Tries()


# ── The key ──────────────────────────────────────────────────────────────

def _is_the_key(offered: str) -> bool:
    """Whether `offered` is the admin key: its hash is the one the Mac
    wrote. False when the Mac wrote none yet."""
    if not offered:
        return False
    kept = _kept_hash()
    # SAFETY: compared in constant time, so the answer's timing tells a
    # caller nothing about how much of a guess was right.
    return bool(kept) and hmac.compare_digest(hash_of(offered), kept)


def _kept_hash() -> str:
    """The key's hash as the Mac wrote it, read on every call: the file
    is one line, and a new key counts at once. "" when there is none."""
    devices_file = Path(os.environ.get("DEVICES_FILE") or DEVICES_FILE_IN_CONTAINER)
    try:
        return (devices_file.parent / _HASH_NAME).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _keep(response: Response, key: str) -> None:
    """Set or renew the key's cookie on `response`."""
    response.set_cookie(COOKIE, key, max_age=KEY_KEPT_SECS, path="/", httponly=True,
                        samesite="strict")


# ── Taking the key ───────────────────────────────────────────────────────

def _offered(request: Request, key: str) -> Response:
    """The admin link: the right key sends the browser on to the same
    page without it, with the cookie; a wrong one shows the key's page."""
    if TRIES.blocked():
        return _page(request, status=429, said="key_too_many")
    if not _is_the_key(key):
        TRIES.wrong()
        return _page(request, status=401, said="key_wrong")
    rest = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
    response = RedirectResponse(request.url.path + (f"?{urlencode(rest)}" if rest else ""),
                                status_code=303)
    _keep(response, key)
    return response


async def _pasted(request: Request) -> Response:
    """The key's page posted a pasted key: on to `next` with the cookie,
    or the page again with what went wrong."""
    fields = parse_qs((await request.body()).decode("utf-8", "replace"))
    key = (fields.get("key") or [""])[0].strip()
    landing = _local((fields.get("next") or [""])[0])
    if TRIES.blocked():
        return _page(request, status=429, said="key_too_many", landing=landing)
    if not _is_the_key(key):
        TRIES.wrong()
        return _page(request, status=401, said="key_wrong", landing=landing)
    response = RedirectResponse(landing, status_code=303)
    _keep(response, key)
    return response


def _local(landing: str) -> str:
    """`landing` when it is a path of this server, else the setup: a
    pasted key never sends the browser to another site."""
    if landing.startswith("/") and not landing.startswith(("//", "/\\")):
        return landing
    return _LANDING


# ── Without the key ──────────────────────────────────────────────────────

def _refusal(request: Request) -> Response:
    """What a caller without the key gets: the key's page for a browser
    that opens a page, the same words as JSON for a script or a write."""
    if request.method in ("GET", "HEAD") and "/api/" not in request.url.path:
        return _page(request, status=401)
    w = pages.words(host.language_of(request))
    return JSONResponse({"error": f'{w["key_heading"]}. {w["key_how"]}'}, status_code=401)


def _page(request: Request, *, status: int, said: str = "", landing: str = "") -> Response:
    """The key's page, which sends a pasted key on to `landing` (the page
    asked for, without a key in it)."""
    if not landing:
        rest = [(k, v) for k, v in request.query_params.multi_items() if k != "key"]
        landing = request.url.path + (f"?{urlencode(rest)}" if rest else "")
    language = host.language_of(request)
    response = host.html(pages.admin_key_page(language, landing=_local(landing), form=FORM_PATH,
                                              said=said), status=status)
    # A form post sends `Origin: null` under `no-referrer`, and the Origin
    # check refuses that (`browser_checks`). The page links nowhere, so
    # `same-origin` gives away nothing and lets its own form post.
    response.headers["referrer-policy"] = "same-origin"
    return response


def _to_the_macs_host(request: Request) -> Response:
    """Send a browser that named the Mac otherwise (`localhost`, `[::1]`)
    on to `127.0.0.1` at the same port, path and query: the key's cookie
    lives on that name alone."""
    port = _port(request)
    target = f"http://{MAC_HOST}{':' + port if port else ''}{request.url.path}"
    if request.url.query:
        target += f"?{request.url.query}"
    return RedirectResponse(target, status_code=307)


def _port(request: Request) -> str:
    """The port of the Host header, "" for none: `[::1]:42002` gives
    `42002`."""
    after = request.headers.get("host", "").rsplit("]", 1)[-1]
    port = after.rsplit(":", 1)[1] if ":" in after else ""
    return port if port.isdigit() else ""


def _hostname(request: Request) -> str:
    """The host name of the Host header, lowercase, without its port."""
    sent = request.headers.get("host", "").lower()
    if sent.startswith("["):
        return sent[1:].split("]", 1)[0]
    return sent.rsplit(":", 1)[0]


# ── The access log ───────────────────────────────────────────────────────

class _HideTheKey(logging.Filter):
    """Write `key=…` for the key in every line of the access log: the
    first request of an admin link carries it in its query."""

    _KEY = re.compile(r"([?&]key=)[^&\s\"]*")

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(self._KEY.sub(r"\1…", a) if isinstance(a, str) else a
                                for a in record.args)
        return True


# SAFETY: installed when the server imports the gate, before the first request.
logging.getLogger("uvicorn.access").addFilter(_HideTheKey())
