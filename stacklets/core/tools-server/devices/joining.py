"""Joining by a code: a paired device, an admin's device or the Mac
allows a browser that the home address does not know yet.

    joining.routes(app)                     # from devices.routes
    joining.join_of(request)                # the unknown page: (secret, request) of this browser
    joining.collected(request)              # the gate: this browser's allowed request, once
    await joining.approver(request)         # who may allow, and for whom

A laptop, a tablet or the living room's screen has no camera to read the
Mac's QR code, and a Mac in a cupboard has no screen to show one. So the
page for an unknown browser shows a short code and the same code as a QR
code, and a device that famstack already knows allows it: the device
flow of RFC 8628, as a TV signs in, and WhatsApp's linked devices.

    laptop (unknown)                tools server                Homer's phone
    "This browser does not   ──▶ JOINS.ask("Chrome on a Mac")
     know you yet", K7MP-4XQD    cookie stack-join=<secret>
     and its QR code                                    ◀── camera, or "Add a device"
                                 GET  /dashboard/devices/allow?code=K7MP4XQD
                                 "Add this browser as Homer's?"     ◀── Allow
                                 POST /dashboard/api/devices/allow
                                 JOINS.allow(code, "homer", ...)  ──▶ "Chrome on a Mac
                                                                      is now Homer's."
    its script asks the state ──▶ the gate: JOINS.collect(<secret>)
    the dashboard, as Homer   ◀── a new key, `stack devices add` (devices.gate)

Who may allow, and for whom:

    a person's paired device   for that person only
    an admin's paired device   for anyone of the family, or as Shared with a name
    the Mac's address          as an admin's device (Devices lists every waiting browser)
    a shared device, nobody    nothing

Why this way. The code only names the request; the device key goes only
to the browser that holds the request's secret (`codes.Joins`), so a
person who reads the code over a shoulder gets nothing. The approver
types or scans the code the new screen shows, never a code pushed to
them, so nobody can be talked into allowing a device that is not in
front of them. An approver who sends ten codes that do not wait in a
minute waits a minute. The allowing device makes no key: the waiting
browser collects the allowance on its next request, and the gate adds it
to the list through the host API, the list's one writer (ADR-023 D9).

Allow and "Not me" are writes on the home address, the only ones besides
Remove there, and each answers only a paired person's device or the Mac;
a shared device and an unknown browser are refused. They take JSON, as
every write of the dashboard does. The design: ADR-023 D2, D4
and D20.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from access import DASHBOARD_PREFIX
from stack.devices import HOUSEHOLD

from . import host, names, pages
from .codes import JOINS, Join, shown
from .who import Who, who

__all__ = ["JOIN_COOKIE", "JOIN_STATE", "ALLOW_PAGE", "Approver", "approver", "join_of",
           "collected", "waiting_list", "routes"]

# The cookie that holds a waiting browser's secret: the one value that
# turns its allowed request into a device key.
JOIN_COOKIE = "stack-join"
# What the unknown page's script asks until the browser is allowed.
JOIN_STATE = f"{DASHBOARD_PREFIX}/api/devices/join"
# The page an approver opens for a code: the QR code holds it.
ALLOW_PAGE = f"{DASHBOARD_PREFIX}/devices/allow"
# The longest name a shared device gets ("Living room"), as `stack devices` takes it.
_NAME_MOST = 60


@dataclass(frozen=True)
class Approver:
    """A device that may allow a browser: `who` it is, and whether it
    allows for anyone (`admin`: an admin's paired device, or the Mac).

        Approver(Who("person", "marge", device), admin=False).key   # "d-3f9a2c1b"
    """

    who: Who
    admin: bool

    @property
    def key(self) -> str:
        """What wrong codes are counted by: the device's id, "mac" for the Mac."""
        return self.who.device.id if self.who.device else self.who.kind


async def approver(request: Request) -> Approver | None:
    """The approver `request` comes from: the Mac (as an admin), or a
    person's paired device, an admin when the family gives that person
    the admin role; None for a shared device or anyone else.

        await approver(request)   # Homer's phone: Approver(Who("person", "homer", ...), admin=True)
    """
    looking = who(request)
    if looking.kind == "mac":
        return Approver(looking, admin=True)
    if looking.kind != "person":
        return None
    person = host.person_of(await host.family(), looking.person) or {}
    return Approver(looking, admin=person.get("role") == "admin")


def join_of(request: Request) -> tuple[str, Join]:
    """This browser's join request: the one its cookie names while it
    still waits, else a new one, named after the browser. Returns the
    secret, for the cookie the answer sets, and the request.

        join_of(request)   # Chrome on a Mac, no cookie: ("q0JbZ3...", Join("K7MP4XQD", ...))
        join_of(request)   # again, with the cookie stack-join=q0JbZ3...: the same request
    """
    secret = request.cookies.get(JOIN_COOKIE, "")
    mine = JOINS.mine(secret)
    if mine is not None:
        return secret, mine
    return JOINS.ask(names.name_of(request.headers.get("user-agent", "")))


def collected(request: Request) -> tuple[str, Join] | None:
    """The secret and the request of this browser once a device allowed
    it, once: the gate then adds the device. None while it waits, or
    when it has none.

        collected(request)   # after Homer allowed it: ("q0JbZ3...", Join(..., person="homer"))
        collected(request)   # the next request: None, the request is gone
    """
    secret = request.cookies.get(JOIN_COOKIE, "")
    found = JOINS.collect(secret) if secret else None
    return (secret, found) if found is not None else None


def waiting_list() -> list[dict]:
    """The browsers that wait, as the Mac's Devices page lists them.

        waiting_list()   # [{"code": "K7MP-4XQD", "device": "Chrome on a Mac", "since": "21:14"}]
    """
    return [{"code": shown(j.code), "device": j.device, "since": pages.since(j.asked)}
            for j in JOINS.all_waiting()]


def routes(app: FastAPI) -> None:
    """Add the routes that allow a waiting browser, and the waiting
    browser's own question, to `app`. Each answers on the home address
    and the Mac's address alike; who may use it is the approver's rule.

        GET  /dashboard/devices/allow?code=K7MP4XQD   "Add this browser as Homer's?"
        GET  /dashboard/devices/allow?code=K7MP4XQD&from=chat   the Server room's link:
                                                      an admin's device or the Mac only
        GET  /dashboard/devices/add                   "Add a device", the field for the code
        POST /dashboard/api/devices/allow             {"code": "K7MP4XQD", "as": "bart"}
                                                      {"person": "bart", "device": "Chrome on a Mac",
                                                       "line": "Chrome on a Mac is now Bart's."}
        POST /dashboard/api/devices/decline           {"code": "K7MP4XQD"}: "Not me", "Not now"
                                                      {"declined": true} from an admin or the Mac;
                                                      {"declined": false, "line": "Nothing changed. ..."}
                                                      from anyone else, and the request waits on
        GET  /dashboard/api/devices/join              {"state": "allowed"} once this browser is in
    """

    @app.get(ALLOW_PAGE, include_in_schema=False)
    async def allow_page(request: Request, code: str = ""):
        language = host.language_of(request)
        by = await approver(request)
        # SAFETY: the Server room's link (`from=chat`) answers only an
        # admin's device or the Mac, so a forwarded message lets nobody
        # in; a person's own device allows by the code its screen shows.
        if by is None or (request.query_params.get("from") == "chat" and not by.admin):
            return host.html(pages.only_admin(language), status=403)
        join, refused = _waiting(by, code)
        if refused:
            return host.html((pages.wait_a_minute if refused == 429 else pages.not_waiting)(language),
                             status=refused)
        family = await host.family()
        own = host.person_of(family, by.who.person) or {"name": ""}
        choices = [{"id": p["id"], "name": p["name"]} for p in (family or {}).get("people", [])]
        return host.html(pages.allow_page(
            {"code": join.code, "device": join.device, "since": pages.since(join.asked)},
            person=own["name"], choices=choices if by.admin else None, language=language,
            base=DASHBOARD_PREFIX))

    @app.get(f"{DASHBOARD_PREFIX}/devices/add", include_in_schema=False)
    async def add_a_device(request: Request):
        language = host.language_of(request)
        by = await approver(request)
        if by is None:
            return host.html(pages.only_admin(language), status=403)
        return host.html(pages.add_a_device(language, base=DASHBOARD_PREFIX, choose=by.admin))

    @app.post(f"{DASHBOARD_PREFIX}/api/devices/allow", include_in_schema=False)
    async def allow(request: Request):
        language = host.language_of(request)
        w = pages.words(language)
        by = await approver(request)
        if by is None:
            return _refuse(w["only_admin"], 403)
        sent = await _sent(request)
        join, refused = _waiting(by, str(sent.get("code") or ""))
        if refused:
            return _refuse(w["wait_minute" if refused == 429 else "not_waiting"], refused)
        family = await host.family()
        chosen, refusal = _chosen(by, family, sent, join, w)
        if refusal is not None:
            return refusal
        person, name = chosen
        JOINS.allow(join.code, person, name, by=by.key)
        called = (host.person_of(family, person) or {"name": person})["name"] if person else ""
        return {"person": person, "device": name,
                "line": pages.allowed_line(name, called, language)}

    @app.post(f"{DASHBOARD_PREFIX}/api/devices/decline", include_in_schema=False)
    async def decline(request: Request):
        w = pages.words(host.language_of(request))
        by = await approver(request)
        if by is None:
            return _refuse(w["only_admin"], 403)
        code = str((await _sent(request)).get("code") or "")
        # SAFETY: only an admin's device or the Mac ends a request, because
        # a request names no person yet and the admin may be about to allow
        # it for someone else (a shared screen). A person's "Not me" leaves
        # it waiting and counts no wrong code: saying "not mine" honestly
        # must not use up the limit.
        if not by.admin:
            if JOINS.waiting(code) is None:
                return _refuse(w["not_waiting"], 410)
            return {"declined": False, "line": w["stays_waiting"]}
        if not JOINS.decline(code):
            return _refuse(w["not_waiting"], 410)
        return {"declined": True}

    @app.get(JOIN_STATE, include_in_schema=False)
    async def join_state(request: Request):
        # The gate answers a browser that still waits; a request that
        # gets here comes from a device, which is in.
        if who(request).kind == "mac":
            return RedirectResponse("/setup/devices", status_code=303)
        return {"state": "allowed"}


# ── Choosing the request and the person ──────────────────────────────────

def _waiting(by: Approver, code: str) -> tuple[Join | None, int]:
    """The waiting request `code` names, or the status that refuses it:
    429 after ten wrong codes in a minute, 410 for a code that does not
    wait (which counts as one more wrong code)."""
    if JOINS.too_many_wrong(by.key):
        return None, 429
    join = JOINS.waiting(code)
    if join is None:
        JOINS.wrong(by.key)
        return None, 410
    return join, 0


def _chosen(by: Approver, family: dict | None, sent: dict, join: Join,
            w: dict) -> tuple[tuple[str, str], Response | None]:
    """(person, device name) the approver allows the request for, or the
    refusal. No `as` means the approver's own person. A person who is
    not an admin allows only for themselves; an admin for anyone of the
    family, or as Shared with a name."""
    chosen = str(sent.get("as") or by.who.person)
    if not chosen:
        return ("", ""), _refuse(w["allow_as"], 400)
    # SAFETY: a person's device allows only for that person; a device for
    # another person, or a shared one, takes an admin's device or the Mac.
    if not by.admin and chosen != by.who.person:
        return ("", ""), _refuse(w["only_admin"], 403)
    if chosen == "shared":
        name = " ".join(str(sent.get("name") or "").split())
        if not name or len(name) > _NAME_MOST:
            return ("", ""), _refuse(w["name_device"], 400)
        return (HOUSEHOLD, name), None
    if host.person_of(family, chosen) is None:
        return ("", ""), _refuse(w["only_admin"], 404)
    return (chosen, join.device), None


async def _sent(request: Request) -> dict:
    try:
        sent = await request.json()
    except ValueError:
        return {}
    return sent if isinstance(sent, dict) else {}


def _refuse(error: str, status: int) -> JSONResponse:
    return JSONResponse({"error": error}, status_code=status)
