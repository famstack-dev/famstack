"""Paired devices: who looks at the home address, and the gate that lets
only the family's devices in.

    app.middleware("http")(devices.gate)    # before the listener rule (server.py)
    devices.routes(app, phone_link=_phone_link)
    devices.who(request)                     # Who(kind="person", person="homer", ...)
    devices.pairing_link(_phone_link(), "homer")
    # "http://simpson.local:42000/go/app?pair=q0JbZ3..."

The home address (port 42000, or the family's domain) is reached by every
device on the Wi-Fi, and a page there cannot tell a phone of the family
from a guest's. So every page there opens only for a paired device: a
browser that holds a device key in a cookie, whose hash is in the Mac's
list of devices (`store.py`, written by `stack devices`). Any other
browser gets "This browser does not know you yet", whatever path it asked
for, and nothing else: no username, no card, no family list.

How a phone is paired, as a sequence:

    the Mac (127.0.0.1:42002)       tools server              Homer's phone
    "Add a phone" on Homer   ──▶ codes.make("homer")
    QR code of /go/app?pair=<code> ◀─┘
                                                     ◀── camera opens it
                                   codes.take(code): "homer"
                                   a new key; its hash ──▶ host API
                                     devices_set add (stack devices add)
                                   cookie stack-device=<key> ──▶ "This phone
                                                                 is now Homer's"
    "Homer's iPhone is added." ◀── codes.state(code)

A Mac without a screen pairs the first phone from its terminal, over ssh
too: `stack devices pair homer` asks the Mac's address for a code
(`POST /dashboard/api/devices/pairing`), prints the QR code as text and
asks for the code's state until the phone opened it. Ctrl-C ends the
code (`DELETE` on its state's address). The command is a client of this
server and holds no code itself (ADR-023 D21).

The code is 128 random bits, works once and for 10 minutes, and is made
only on the Mac's address or an admin's paired device, because whoever
makes it vouches for the device: a child cannot pair a phone as a parent
from the sofa. The key is 256 random bits; it stays in the browser and
the tools server's memory of one request, and the Mac keeps its hash
only. The cookie is
HttpOnly and SameSite=Lax, Secure over HTTPS, and is renewed on every
visit for 400 days, the longest a browser keeps one, so a phone used once
a year stays paired.

A browser sends a cookie only to the host that set it. So every link the
stack makes names the one host of `LINK_BASE_URL` (ADR-023 D16), and a
browser that came by another name of the Mac (its
IP address in place of `simpson.local`) is told which address knows it.

`who(request)` is what the routes ask: a person (their own device), the
household (a shared device), the Mac (its own address, with the admin
key, `admin_key.py`), or nobody (the stack's own
containers on the internal listener, which the gate does not check).

The Mac's address opens everything to a browser that holds the admin
key (`admin_key.door`), the internal listener as before, and core's health check on the home address
(`/dashboard/api/health`) answers without a key, as do the dashboard's
styles and fonts, which the unknown page loads.

Module map: `admin_key.py` is the door of the Mac's address, `who.py` says who a request is from, `store.py` reads the
list, `codes.py` keeps the pairing codes and join requests, `joining.py`
lets a paired device allow a browser by its code, `notice.py` tells the
Server room of a new device, `names.py` names a device, `pages.py` draws
the pages, `sign_in.py` says whose sign-in and password a page shows and
names who looks in the dashboard's top bar, `host.py` asks the stack. The design:
ADR-023 D9, D10, D12, D16, D20.
"""

from __future__ import annotations

import asyncio
import os
import secrets
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

import browser_checks
import family_view
from access import CARDS_PORT, DASHBOARD_PREFIX, PUBLIC_PORT, WIKI_PORT, mac_only
from stack import clock
from stack.devices import HOUSEHOLD, hash_of

import qr_code

from . import admin_key, host, joining, names, own_devices, pages, store
from .codes import CODE_SECS, CODES, JOINS, shown
from .notice import NOTICES
from .sign_in import NO_STORE, Looking, looking, own_password, passwords
from .store import Device
from .who import NOBODY, Who, who

__all__ = ["Who", "who", "gate", "routes", "pairing_link", "pairing_panel", "COOKIE", "CODES",
           "JOINS", "NOTICES", "ADMIN_KEY_TRIES", "Looking", "looking", "NO_STORE", "passwords", "own_password"]

# The wrong admin keys of the last minute (`admin_key`).
ADMIN_KEY_TRIES = admin_key.TRIES
# The cookie that holds a device's key.
COOKIE = "stack-device"
# How long a browser keeps the cookie: 400 days, the most a browser keeps
# any cookie; renewed on every visit.
KEY_KEPT_SECS = 400 * 24 * 3600
# What a request on the home address opens without a key: core's health
# check, and the styles and fonts the unknown page loads.
_OPEN = (f"{DASHBOARD_PREFIX}/api/health",)
_OPEN_UNDER = (f"{DASHBOARD_PREFIX}/static/", f"{DASHBOARD_PREFIX}/fonts/")


async def gate(request: Request, call_next):
    """The middleware of the home address: a request with a paired
    device's key goes on as that device; one with a pairing code
    (`?pair=<code>`) pairs the browser first, as does a browser whose
    join request a paired device allowed (`joining`); any other gets the
    unknown page with its join code (401), or "famstack is being set up" while the family is empty
    (503), as JSON for a path under `/api/`. The wiki's door
    (`wiki_door`) is behind the same gate; other listeners pass as before.
    Sets `request.state.who`, and `request.state.paired_now` for the
    request that paired.

        GET simpson.local:42000/dashboard               # no key: 401, "This browser does not know you yet"
        GET simpson.local:42000/go/app?pair=q0JbZ3...   # 200 as Homer, cookie stack-device=<key>
        GET simpson.local:42000/dashboard, that cookie  # 200, "Homer" in the top bar
    """
    listener = (request.scope.get("server") or (None, None))[1]
    if listener == CARDS_PORT:
        # SAFETY: the Mac's rights need the admin key as well as the Mac's
        # listener, which every container reaches too (`admin_key`).
        return await admin_key.door(request, call_next)
    if listener not in (PUBLIC_PORT, WIKI_PORT):
        request.state.who = NOBODY
        return await call_next(request)
    path = request.url.path
    if path in _OPEN or path.startswith(_OPEN_UNDER):
        return await call_next(request)

    key = request.cookies.get(COOKIE, "")
    device = store.current().find(key)
    code = request.query_params.get("pair", "")
    paired_now = False
    if code:
        refused, device_and_key = await _pair(request, code, device)
        if refused is not None:
            return refused
        if device_and_key is not None:
            device, key = device_and_key
            paired_now = True
    joined = None
    if device is None:
        joined = await _join(request)
        if joined is None:
            return await _refusal(request)
        device, key = joined

    request.state.who = Who("household" if device.shared else "person", device.person, device)
    request.state.paired_now = paired_now
    await _note_seen(device)
    response = await call_next(request)
    response.set_cookie(COOKIE, key, max_age=KEY_KEPT_SECS, path="/",
                        **browser_checks.cookie_flags(request))
    if joined is not None:
        response.delete_cookie(joining.JOIN_COOKIE, path="/")
    return response


def pairing_link(link: str, person: str) -> str:
    """`link` (an address on the home address) with a new pairing code
    for `person` (a user id, or "" for the household): what the Mac's QR
    code holds. "" when `link` is "" (core has no link base yet).

        pairing_link("http://simpson.local:42000/go/app", "homer")
        # "http://simpson.local:42000/go/app?pair=q0JbZ3..."
    """
    if not link:
        return ""
    return f"{link}{'&' if '?' in link else '?'}pair={CODES.make(person)}"


def pairing_panel(phone_link: str, person: str, *, again: str, done: str, language: str) -> str:
    """The QR code of a new pairing code for `person` on the phone page
    `phone_link`, with its states (`pages.pairing_panel`); "" while core
    has no link base. "Add a phone" and the setup's phone step show it.

        pairing_panel("http://simpson.local:42000/go/app", "homer",
                      again="/setup?step=phone", done="", language="en")
        # '<div class="pairing" data-pairing="/dashboard/api/devices/pairing/q0Jb...">...'
    """
    link = pairing_link(phone_link, person)
    if not link:
        return ""
    code = link.rsplit("pair=", 1)[-1]
    return pages.pairing_panel(link=link, state_url=f"{DASHBOARD_PREFIX}/api/devices/pairing/{code}",
                               again=again, done=done, language=language)


def routes(app: FastAPI, *, phone_link: Callable[[], str]) -> None:
    """Add the pairing routes to `app`. `phone_link` gives the phone page's address on the
    home address (`<LINK_BASE_URL>/app`), which the QR code holds.

        GET  /setup/add-a-phone?person=homer          "Add Homer's phone"
        GET  /dashboard/devices/add-a-phone?person=marge   the same, on an admin's device
        GET  /dashboard/api/devices/pairing/<code>    {"state": "added", "line": "..."}
        GET  /setup/devices                           the Devices list, and who waits
        POST /dashboard/api/devices/remove            {"id": "d-3f9a2c1b"}: the Mac any,
                                                      a person's device its person's own
        GET  /setup/this-browser?as=homer|shared      this browser, paired (ADR-023 D12)
        POST /dashboard/api/devices/pairing           {"person": "homer"}: a code for the terminal
        DELETE /dashboard/api/devices/pairing/<code>  the terminal stopped waiting: the code ends

    Then the join routes (`joining.routes`) and "Your devices"
    (`own_devices.routes`). Every route but "Add a phone" on an admin's
    device, its state, Remove and those answers on the Mac's address only.
    """
    only_here = [Depends(mac_only)]

    async def add_a_phone_page(request: Request, person: str, again: str) -> Response:
        family = await host.family()
        found = host.person_of(family, person)
        if found is None:
            return JSONResponse({"error": "No such person in the family."}, status_code=404)
        language = host.language_of(request)
        panel = pairing_panel(phone_link(), found["id"], again=f"{again}?person={found['id']}",
                              done=pages.DEVICES, language=language)
        if not panel:
            return JSONResponse({"error": "Core has no home address yet: run `./stack up core`."},
                                status_code=503)
        return host.html(pages.add_a_phone(person=found["name"], panel=panel,
                                           language=language, base=DASHBOARD_PREFIX))

    @app.get("/setup/add-a-phone", include_in_schema=False, dependencies=only_here)
    async def add_a_phone(request: Request, person: str = ""):
        return await add_a_phone_page(request, person, "/setup/add-a-phone")

    @app.get(f"{DASHBOARD_PREFIX}/devices/add-a-phone", include_in_schema=False)
    async def add_a_phone_from_a_device(request: Request, person: str = ""):
        # SAFETY: a pairing code makes a device of any person, so it is
        # made only on the Mac or an admin's paired device (ADR-023 D20).
        by = await joining.approver(request)
        if by is None or not by.admin:
            return host.html(pages.only_admin(host.language_of(request)), status=403)
        return await add_a_phone_page(request, person, f"{DASHBOARD_PREFIX}/devices/add-a-phone")

    @app.get(f"{DASHBOARD_PREFIX}/api/devices/pairing/{{code}}", include_in_schema=False)
    async def pairing_state(code: str, request: Request):
        by = await joining.approver(request)
        if by is None or not by.admin:
            return JSONResponse({"error": pages.words(host.language_of(request))["only_admin"]},
                                status_code=403)
        state = CODES.state(code)
        if state["state"] != "added":
            return state
        family = await host.family()
        person = host.person_of(family, CODES.person_of(code)) or {"name": ""}
        return {**state, "line": pages.added_line(person["name"], state["device"],
                                                  host.language_of(request))}

    @app.post(f"{DASHBOARD_PREFIX}/api/devices/pairing", include_in_schema=False,
              dependencies=only_here)
    async def new_pairing(request: Request):
        sent = await _sent(request)
        person_id = sent.get("person") if isinstance(sent, dict) else None
        if not isinstance(person_id, str) or not person_id:
            return JSONResponse({"error": 'Send {"person": "<person id>"}.'}, status_code=400)
        found = host.person_of(await host.family(), person_id)
        if found is None:
            return JSONResponse({"error": f"{person_id} is not in the family."}, status_code=404)
        link = pairing_link(phone_link(), found["id"])
        if not link:
            return JSONResponse({"error": "Core has no home address yet: run `./stack up core`."},
                                status_code=503)
        return _for_the_terminal(found, link)

    @app.delete(f"{DASHBOARD_PREFIX}/api/devices/pairing/{{code}}", include_in_schema=False,
                dependencies=only_here)
    async def end_pairing(code: str):
        CODES.end(code)
        return CODES.state(code)

    @app.get("/setup/devices", include_in_schema=False, dependencies=only_here)
    async def devices_page(request: Request):
        listed, family = await asyncio.gather(host.call({"cmd": "devices"}), host.family())
        language = host.language_of(request)
        return host.html(pages.devices_page(listed.get("devices", []),
                                            (family or {}).get("people", []),
                                            language=language, today=clock.today(),
                                            base=DASHBOARD_PREFIX,
                                            waiting=joining.waiting_list()))

    @app.post(f"{DASHBOARD_PREFIX}/api/devices/remove", include_in_schema=False)
    async def remove(request: Request):
        sent = await _sent(request)
        device_id = sent.get("id") if isinstance(sent, dict) else None
        if not isinstance(device_id, str) or not device_id:
            return JSONResponse({"error": 'Send {"id": "<device id>"}.'}, status_code=400)
        # SAFETY: the Mac removes any device; a person's device removes
        # only that person's own, and a shared device none.
        looking = who(request)
        if looking.kind != "mac" and not own_devices.owns(looking, device_id):
            return JSONResponse({"error": f"No device {device_id} of yours."}, status_code=404)
        result = await host.call({"cmd": "devices_set", "action": "remove", "id": device_id})
        return JSONResponse(result, status_code=404 if "error" in result else 200)

    @app.get("/setup/this-browser", include_in_schema=False, dependencies=only_here)
    async def this_browser(request: Request):
        chosen = request.query_params.get("as", "")
        family = await host.family()
        person = HOUSEHOLD if chosen == "shared" else (host.person_of(family, chosen) or {}).get("id")
        home = _home_of(phone_link())
        if person is None or not home:
            return JSONResponse({"error": "Choose a person of the family, or shared."},
                                status_code=404)
        return RedirectResponse(pairing_link(f"{home}{DASHBOARD_PREFIX}", person), status_code=303)

    joining.routes(app)
    own_devices.routes(app)


# ── A code for the terminal ──────────────────────────────────────────────

def _for_the_terminal(person: dict, link: str) -> dict:
    """What `stack devices pair` prints and waits on, for the new code in
    `link`: the person, the address, where to ask for its state, when it
    ends (ISO 8601, UTC) and the QR code as text.

        {"person": "homer", "name": "Homer",
         "link": "http://simpson.local:42000/go/app?pair=q0JbZ3...",
         "state_url": "/dashboard/api/devices/pairing/q0JbZ3...",
         "expires": "2026-11-11T13:10:00+00:00", "qr": "█████...\n"}
    """
    code = link.rsplit("pair=", 1)[-1]
    ends = datetime.fromtimestamp(CODES.ends_at(code), tz=timezone.utc)
    return {"person": person["id"], "name": person["name"], "link": link,
            "state_url": f"{DASHBOARD_PREFIX}/api/devices/pairing/{code}",
            "expires": ends.isoformat(timespec="seconds"), "qr": qr_code.text(link)}


# ── Pairing a browser ────────────────────────────────────────────────────

async def _pair(request: Request, code: str, device: Device | None):
    """Pair the browser of `request` by `code`: (None, (device, key)) when
    it is paired now; (None, None) when the code does not count and the
    browser already has a device, which goes on as it is; (page, None)
    when the browser is refused: the code expired, or the Mac did not add
    the device (the code then works again)."""
    language = host.language_of(request)
    person = CODES.take(code)
    if person is None:
        if device is not None:
            return None, None
        return host.html(pages.expired(language), status=410), None
    name = names.name_of(request.headers.get("user-agent", ""))
    added = await _add_device(person, name)
    if added is None:
        CODES.give_back(code)
        return host.html(pages.not_answered(language), status=503), None
    CODES.added(code, name)
    await NOTICES.added(added[0].id, "")
    return None, added


async def _join(request: Request) -> tuple[Device, str] | None:
    """The device and key of a browser whose join request a paired device
    allowed, added to the list now; None while it waits or has none. A
    request the Mac did not add goes back, so the next page tries again."""
    allowed = joining.collected(request)
    if allowed is None:
        return None
    secret, join = allowed
    added = await _add_device(join.person, join.name)
    if added is None:
        JOINS.give_back(secret, join)
    else:
        await NOTICES.added(added[0].id, join.by)
    return added


async def _add_device(person: str, name: str) -> tuple[Device, str] | None:
    """A new key for a device of `person` named `name`, whose hash the
    host API adds to the list (`stack devices add`): the device and the
    key, or None when the Mac did not add it. The key leaves this
    function only in the answer to the browser that is paired now."""
    key = secrets.token_urlsafe(32)
    added = await host.call({"cmd": "devices_set", "action": "add", "person": person, "name": name,
                             "key_hash": hash_of(key)})
    found = added.get("device")
    if not isinstance(found, dict):
        return None
    return Device(found["id"], found["person"], found["name"], found["paired"], found["seen"]), key


async def _refusal(request: Request) -> Response:
    """What a browser without a device gets: "famstack is being set up"
    while the family is empty, else "This browser does not know you
    yet"; as JSON with the same words for a page's script."""
    family = await host.family()
    language = host.language_of(request)
    w = pages.words(language)
    if family_view.is_empty(family):
        status, title, body = 503, w["set_up_heading"], pages.being_set_up(language)
    elif request.url.path == joining.JOIN_STATE:
        secret = request.cookies.get(joining.JOIN_COOKIE, "")
        return JSONResponse({"state": JOINS.state(secret) if secret else "expired"})
    elif "/api/" in request.url.path:
        return JSONResponse({"error": w["unknown_heading"]}, status_code=401)
    else:
        return await _unknown_page(request, family, language)
    if "/api/" in request.url.path:
        return JSONResponse({"error": title}, status_code=status)
    return host.html(body, status=status)


async def _unknown_page(request: Request, family: dict | None, language: str) -> Response:
    """"This browser does not know you yet", with this browser's join
    code and its QR code, and the cookie that keeps the request's secret
    in this browser alone. A browser that starts to wait here tells the
    Server room (`notice`, at most once in 10 minutes); the page then
    names the admins who heard of it in the chat, when the message named
    this browser's code."""
    secret, join = joining.join_of(request)
    home = _home_of(_link_base())
    link = f"{home}{joining.ALLOW_PAGE}?code={join.code}" if home else ""
    if link and secret != request.cookies.get(joining.JOIN_COOKIE, ""):
        await NOTICES.waits(request.headers.get("user-agent", ""), join)
    admins = [p["name"] for p in (family or {}).get("people", []) if p.get("role") == "admin"]
    body = pages.unknown(language, (family or {}).get("name", ""),
                         address=_other_address(request),
                         removed=request.query_params.get("removed") == "1",
                         join={"code": shown(join.code), "link": link,
                               "state_url": joining.JOIN_STATE,
                               "heard_by": admins if NOTICES.heard(join.code) else []} if link else None)
    response = host.html(body, status=401)
    # SAFETY: the secret is what turns an allowed request into a device
    # key, so it lives in this browser's cookie only, never in the page.
    response.set_cookie(joining.JOIN_COOKIE, secret, max_age=CODE_SECS, path="/",
                        **browser_checks.cookie_flags(request))
    return response


def _other_address(request: Request) -> str:
    """The home address's host, when the browser came by another name of
    the Mac; "" when it came by that host, or core has none."""
    home = urlsplit(_link_base()).netloc
    asked = request.headers.get("host", "")
    if not home or asked.split(":")[0].lower() == home.split(":")[0].lower():
        return ""
    return home


# Devices noted as seen today, so a page opened a minute does not ask the
# host API a minute: the list writes "seen" at most once a day anyway.
_SEEN: dict[str, str] = {}


async def _note_seen(device: Device) -> None:
    today = clock.today().isoformat()
    if device.seen == today or _SEEN.get(device.id) == today:
        return
    _SEEN[device.id] = today
    await host.call({"cmd": "devices_set", "action": "seen", "id": device.id})


# ── Reading a request ────────────────────────────────────────────────────

async def _sent(request: Request) -> object:
    """The request's JSON body; None when it is not JSON."""
    try:
        return await request.json()
    except ValueError:
        return None


def _link_base() -> str:
    return os.environ.get("LINK_BASE_URL", "")


def _home_of(link: str) -> str:
    parts = urlsplit(link)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
