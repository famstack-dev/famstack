"""The pages of pairing: what a browser sees before it is paired, and what
the Mac shows to pair one and to list the paired devices.

    unknown("en", "The Simpsons")         # "This browser does not know you yet"
    being_set_up("en")                     # "famstack is being set up"
    expired("en")                          # "This code has expired. Make a new one on the Mac."
    paired("Homer", "en")                  # ("This phone is now Homer's", "famstack knows ...")
    add_a_phone(person="Homer", panel=pairing_panel(...), language="en", base="/dashboard")
    devices_page(rows, people, language="en", today=date(2026, 11, 11), base="/dashboard")
    allow_page(asked, choices=None, person="Homer", language="en", base="/dashboard")
    add_a_device("en", base="/dashboard")
    your_devices(rows, this="d-3f9a2c1b", others=[], language="en", today=..., base="/dashboard")

The labels are the copy deck's, in English and
German. Each page is plain HTML on the frame of the phone page
(`dashboard/phone.html`), which reads complete without a script, because
any browser a phone's camera opens must show it. The pages that act
load a script (`dashboard/devices.js`): it follows a code until a phone
opened it or a browser was allowed, asks before it removes a device, and
sends Allow, "Not me" and Remove as JSON, the only body the dashboard's
writes take.

A page for a browser that is not paired names no person, no username and
no card: only the family's name, so the browser knows it is at the right
server. Pure: no request, no file but the frame, no host API.
"""

from __future__ import annotations

from datetime import date, datetime
from html import escape
from pathlib import Path
from string import Template

import qr_code
from access import DASHBOARD_PREFIX
from stack import clock
from stack.users import family_display_name

from . import names
from .codes import shown

__all__ = ["WORDS", "words", "unknown", "being_set_up", "expired", "not_answered", "paired",
           "add_a_phone", "pairing_panel", "added_line", "devices_page", "allow_page",
           "allowed_line", "not_waiting", "only_admin", "wait_a_minute", "add_a_device", "your_devices",
           "since", "DEVICES", "admin_key_page"]

FRAME = Path(__file__).resolve().parent.parent / "dashboard" / "phone.html"
MAC_ADDRESS = "127.0.0.1:42002"
# The command that prints the admin link and the key (`stack.devices.admin_key`).
ADMIN_LINK = "stack devices admin-link"

WORDS = {
    "en": {
        "unknown_title": "Add this browser",
        "key_title": "Open famstack on this Mac",
        "key_heading": "This browser has no admin key",
        "key_how": "Open famstack from the menu bar, or run stack devices admin-link.",
        "key_where": "stack devices admin-link prints the key under the link. Paste it here:",
        "key_field": "Admin key",
        "key_open": "Open",
        "key_wrong": "This is not the admin key.",
        "key_too_many": "Too many wrong keys. Try again in a minute.",
        "unknown_heading": "This browser does not know you yet",
        "unknown_mac": f"On the Mac that runs famstack? Open {MAC_ADDRESS}.",
        "unknown_address": "famstack knows a paired browser only at its own address: {address}.",
        "set_up_heading": "famstack is being set up",
        "set_up_line": ("The setup happens on the Mac that runs famstack. Once your family is in "
                        "it, the Mac adds this phone with a code."),
        "expired_phone": "This code has expired. Ask for a new one.",
        "not_answered": "famstack did not answer. Open the code again in a moment.",
        "paired_heading": "This phone is now {name}'s",
        "paired_line": ("famstack knows this phone from now on. Links in the chat open here, "
                        "as {name}."),
        "add_title": "Add a phone",
        "add_heading": "Add {name}'s phone",
        "add_line": "Open the camera on {name}'s phone and point it at this code.",
        "once": "The code works once, for 10 minutes.",
        "waiting": "Waiting for the phone…",
        "added": "{name}'s {device} is added.",
        "done": "Done",
        "expired_mac": "This code has expired.",
        "new_code": "Show a new code",
        "devices_heading": "Devices",
        "row": "{person} · {device}",
        "row_when": "added {paired} · seen {seen}",
        "shared": "Shared",
        "remove": "Remove",
        "cancel": "Cancel",
        "remove_ask": ("Remove {name}'s {device}? It opens nothing of famstack any more, until "
                       "it is added again."),
        "remove_ask_shared": ("Remove {device}? It opens nothing of famstack any more, until it "
                              "is added again."),
        "empty": "No phone or laptop is added yet. Add one in Your family.",
        "this_browser_heading": "This browser",
        "this_browser": "Use this browser for the family's pages",
        "as": "as",
        "family": "Your family",
        "join_line": "Use your paired phone to allow this browser. Point its camera at this code.",
        "join_type": "Or type the code on your phone: famstack, Add a device.",
        "join_minutes": "The code works for 10 minutes.",
        "heard": "No paired phone yet? {names} heard in the chat that this browser waits. "
                 "Tell them the code.",
        "heard_one": "No paired phone yet? {names} heard in the chat that this browser waits. "
                     "Tell them the code.",
        "not_heard": "Show this code to someone who looks after famstack, or open Devices on the Mac.",
        "stays_waiting": "Nothing changed. The browser keeps waiting for 10 minutes.",
        "and": "and",
        "removed": "This device is removed.",
        "allow_heading": "Add this browser as {name}'s?",
        "allow_heading_any": "Add this browser?",
        "waiting_since": "{device}, waiting since {time}.",
        "allow": "Allow",
        "not_me": "Not me",
        "allow_as": "Allow as",
        "name_device": "Name this device",
        "name_example": "Living room",
        "now_persons": "{device} is now {name}'s.",
        "now_shared": "{device} is now a shared device.",
        "not_waiting": "This code is not waiting any more. Show a new one on the new device.",
        "wait_minute": "Wait a minute, then try again.",
        "only_admin": "Only someone who looks after famstack can allow a browser for another person.",
        "add_device_heading": "Add a device",
        "add_device_line": "Type the code that the new device shows.",
        "code": "Code",
        "your_devices": "Your devices",
        "this_device": "This device",
        "add_phone": "Add a phone",
        "waiting_row": "Waiting: {device} · {code} · since {time}",
        "not_now": "Not now",
        "dashboard": "Back to the dashboard",
        "today": "today", "yesterday": "yesterday",
        "months": ("January", "February", "March", "April", "May", "June", "July", "August",
                   "September", "October", "November", "December"),
    },
    "de": {
        "unknown_title": "Diesen Browser hinzufügen",
        "key_title": "famstack auf diesem Mac öffnen",
        "key_heading": "Dieser Browser hat keinen Admin-Schlüssel",
        "key_how": "Öffne famstack über die Menüleiste oder führe stack devices admin-link aus.",
        "key_where": "stack devices admin-link zeigt den Schlüssel unter dem Link. Füge ihn hier ein:",
        "key_field": "Admin-Schlüssel",
        "key_open": "Öffnen",
        "key_wrong": "Das ist nicht der Admin-Schlüssel.",
        "key_too_many": "Zu viele falsche Schlüssel. Versuch es in einer Minute noch einmal.",
        "unknown_heading": "Dieser Browser kennt dich noch nicht",
        "unknown_mac": f"Am Mac, auf dem famstack läuft? Öffne {MAC_ADDRESS}.",
        "unknown_address": ("famstack erkennt einen hinzugefügten Browser nur unter seiner "
                            "eigenen Adresse: {address}."),
        "set_up_heading": "famstack wird eingerichtet",
        "set_up_line": ("Die Einrichtung passiert am Mac, auf dem famstack läuft. Sobald eure "
                        "Familie darin ist, fügt der Mac dieses Handy mit einem Code hinzu."),
        "expired_phone": "Dieser Code ist abgelaufen. Lass dir einen neuen zeigen.",
        "not_answered": "famstack hat nicht geantwortet. Öffne den Code gleich noch einmal.",
        "paired_heading": "Dieses Handy gehört jetzt {name}",
        "paired_line": ("famstack kennt dieses Handy ab jetzt. Links im Chat öffnen sich hier, "
                        "als {name}."),
        "add_title": "Handy hinzufügen",
        "add_heading": "{genitive} Handy hinzufügen",
        "add_line": "Öffne die Kamera auf {genitive} Handy und richte sie auf diesen Code.",
        "once": "Der Code gilt einmal, für 10 Minuten.",
        "waiting": "Warte auf das Handy…",
        "added": "{genitive} {device} ist hinzugefügt.",
        "done": "Fertig",
        "expired_mac": "Dieser Code ist abgelaufen.",
        "new_code": "Neuen Code anzeigen",
        "devices_heading": "Geräte",
        "row": "{person} · {device}",
        "row_when": "hinzugefügt {paired} · zuletzt gesehen {seen}",
        "shared": "Gemeinsam",
        "remove": "Entfernen",
        "cancel": "Abbrechen",
        "remove_ask": ("{genitive} {device} entfernen? Es öffnet dann nichts mehr von famstack, "
                       "bis es wieder hinzugefügt wird."),
        "remove_ask_shared": ("{device} entfernen? Es öffnet dann nichts mehr von famstack, bis "
                              "es wieder hinzugefügt wird."),
        "empty": "Noch kein Handy und kein Laptop hinzugefügt. Füge eins unter Eure Familie hinzu.",
        "this_browser_heading": "Dieser Browser",
        "this_browser": "Diesen Browser für die Seiten der Familie nutzen",
        "as": "als",
        "family": "Eure Familie",
        "join_line": ("Erlaube diesen Browser mit deinem hinzugefügten Handy. Richte seine Kamera "
                      "auf diesen Code."),
        "join_type": "Oder tippe den Code auf deinem Handy ein: famstack, Gerät hinzufügen.",
        "join_minutes": "Der Code gilt 10 Minuten.",
        "heard": ("Noch kein Handy hinzugefügt? {names} wissen aus dem Chat, dass dieser Browser "
                  "wartet. Nenn ihnen den Code."),
        "heard_one": ("Noch kein Handy hinzugefügt? {names} weiß aus dem Chat, dass dieser "
                      "Browser wartet. Nenn {names} den Code."),
        "not_heard": ("Zeig diesen Code jemandem, der famstack betreut, oder öffne Geräte auf dem "
                      "Mac."),
        "stays_waiting": "Nichts geändert. Der Browser wartet noch 10 Minuten.",
        "and": "und",
        "removed": "Dieses Gerät ist entfernt.",
        "allow_heading": "Diesen Browser für {name} hinzufügen?",
        "allow_heading_any": "Diesen Browser hinzufügen?",
        "waiting_since": "{device}, wartet seit {time}.",
        "allow": "Erlauben",
        "not_me": "Das bin ich nicht",
        "allow_as": "Erlauben als",
        "name_device": "Gib diesem Gerät einen Namen",
        "name_example": "Wohnzimmer",
        "now_persons": "{device} gehört jetzt {name}.",
        "now_shared": "{device} ist jetzt ein gemeinsames Gerät.",
        "not_waiting": ("Dieser Code wartet nicht mehr. Lass dir auf dem neuen Gerät einen neuen "
                        "zeigen."),
        "wait_minute": "Warte eine Minute und versuch es dann noch einmal.",
        "only_admin": "Nur wer famstack betreut, kann einen Browser für jemand anderen erlauben.",
        "add_device_heading": "Gerät hinzufügen",
        "add_device_line": "Tippe den Code ein, den das neue Gerät zeigt.",
        "code": "Code",
        "your_devices": "Deine Geräte",
        "this_device": "Dieses Gerät",
        "add_phone": "Handy hinzufügen",
        "waiting_row": "Wartet: {device} · {code} · seit {time}",
        "not_now": "Nicht jetzt",
        "dashboard": "Zurück zur Übersicht",
        "today": "heute", "yesterday": "gestern",
        "months": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                   "September", "Oktober", "November", "Dezember"),
    },
}


def words(language: str) -> dict:
    """The labels in `language`: German for "de", English for anything else."""
    return WORDS["de" if language == "de" else "en"]


# ── The home address, for a browser that is not paired ──────────────────

def unknown(language: str, family_name: str, *, address: str = "", join: dict | None = None,
            removed: bool = False) -> str:
    """The page every path of the home address answers to a browser
    without a valid device key. `family_name` is the family's name as
    `stack family` has it ("Simpson"), "" when unknown. `address` is the
    home address (`simpson.local:42000`) when the browser came by another
    name of the Mac: its key, if it has one, belongs to that name.
    `join` is this browser's join request: `code` ("K7MP-4XQD"), `link`
    (the allow page on the home address, which the QR code holds) and
    `state_url` (what the page's script asks until it is allowed), and
    `heard_by`, the admins' names when the Server room told them that
    this browser waits ([] says to show the code to an admin or open
    Devices on the Mac instead); None for no request. `removed` adds the note that this device was
    just removed.

        unknown("en", "Simpson", join={"code": "K7MP-4XQD", "link": "http://simpson.local:42000/
                dashboard/devices/allow?code=K7MP4XQD", "state_url": "/dashboard/api/devices/join"})
        # "... The Simpsons ... This browser does not know you yet ... K7MP-4XQD ..."
    """
    w = words(language)
    shown = _family_name(family_name, language)
    main = (f'<p class="setup-text devices-family">{_e(shown)}</p>' if shown else "") \
        + f'<h1 class="setup-heading">{_e(w["unknown_heading"])}</h1>'
    if removed:
        main += _p(w["removed"], "setup-done")
    if address:
        main += _p(w["unknown_address"].format(address=address), "setup-text")
    if join:
        main += _join_panel(join, w)
    main += _p(w["unknown_mac"], "setup-note")
    return _frame(language, w["unknown_title"], main,
                  script=_script(DASHBOARD_PREFIX) if join else "")


# ── The Mac's address, for a browser without the admin key ─────────────

def admin_key_page(language: str, *, landing: str, form: str, said: str = "") -> str:
    """The page the Mac's address answers to a browser without the admin
    key (`admin_key`): how to get in, and a field for the key that
    `stack devices admin-link` prints. The field posts to `form` and the
    browser then lands on `landing`, the page it asked for. `said` names
    what went wrong first: "key_wrong" or "key_too_many", "" for nothing.

        admin_key_page("en", landing="/setup", form="/admin-key")
        # "... This browser has no admin key ... <input name="key" ...> ..."
    """
    w = words(language)
    main = f'<h1 class="setup-heading">{_e(w["key_heading"])}</h1>'
    if said:
        main += _p(w[said], "setup-note")
    main += "".join(f'<p class="setup-text">{_with_command(w[k])}</p>' for k in ("key_how", "key_where"))
    main += (f'<form class="setup-box admin-key" method="post" action="{_e(form)}">'
             f'<input type="hidden" name="next" value="{_e(landing)}">'
             f'<label class="fs-field"><span class="fs-field-label">{_e(w["key_field"])}</span>'
             f'<input class="fs-field-input" name="key" type="password" autocomplete="off" '
             f'spellcheck="false" required></label>'
             f'<div class="setup-act"><button class="fs-button" data-kind="go" type="submit">'
             f'{_e(w["key_open"])}</button></div></form>')
    field = f'<link rel="stylesheet" href="{_e(DASHBOARD_PREFIX)}/static/components/field.css">'
    return _frame(language, w["key_title"], main, script=field)


def being_set_up(language: str) -> str:
    """The page of the home address while the family has nobody in it."""
    w = words(language)
    return _frame(language, w["set_up_heading"],
                  f'<h1 class="setup-heading">{_e(w["set_up_heading"])}</h1>'
                  + _p(w["set_up_line"], "setup-text"))


def expired(language: str) -> str:
    """The page for a pairing code that is unknown, used or too old."""
    w = words(language)
    return _frame(language, w["unknown_title"], _p(w["expired_phone"], "setup-waiting"))


def not_answered(language: str) -> str:
    """The page for a code whose device the Mac did not add: the code
    works again (`codes.Codes.give_back`)."""
    w = words(language)
    return _frame(language, w["unknown_title"], _p(w["not_answered"], "setup-waiting"))


def paired(name: str, language: str) -> tuple[str, str]:
    """The heading and the line the phone page shows right after the
    phone was paired as the person called `name`.

        paired("Homer", "en")[0]   # "This phone is now Homer's"
    """
    w = words(language)
    return w["paired_heading"].format(name=name), w["paired_line"].format(name=name)


# ── The Mac's address ────────────────────────────────────────────────────

def add_a_phone(*, person: str, panel: str, language: str, base: str) -> str:
    """The Mac's page "Add Homer's phone": its heading and line over
    `panel`, the QR code of a pairing code for `person` with its states
    (`pairing_panel`, Done going to the Devices list, `DEVICES`).

        add_a_phone(person="Homer",
                    panel=pairing_panel(link="http://simpson.local:42000/go/app?pair=q0Jb...",
                                        state_url="/dashboard/api/devices/pairing/q0Jb...",
                                        again="/setup/add-a-phone?person=homer",
                                        done=DEVICES, language="en"),
                    language="en", base="/dashboard")
    """
    w = words(language)
    said = {"name": person, "genitive": _genitive(person)}
    main = (f'<h1 class="setup-heading">{_e(w["add_heading"].format(**said))}</h1>'
            + _p(w["add_line"].format(**said), "setup-text") + panel)
    return _frame(language, w["add_title"], main, script=_script(base))


def pairing_panel(*, link: str, state_url: str, again: str, done: str, language: str) -> str:
    """The QR code of `link` and its three states, waiting, added and
    expired; `devices.js` shows the one `state_url` answers. The setup's
    phone step shows the same panel for the admin's own phone. `done`
    is where Done goes, "" for no Done."""
    w = words(language)
    qr = qr_code.svg(link, label=f"QR code: {link}")
    done_button = (f'<div class="setup-act"><a class="fs-button" href="{_e(done)}">'
                   f'{_e(w["done"])}</a></div>') if done else ""
    return (f'<div class="pairing" data-pairing="{_e(state_url)}">'
            f'<div data-when="waiting"><figure class="setup-qr">{qr}'
            f'<figcaption>{_e(w["once"])}</figcaption></figure>'
            f'{_p(w["waiting"], "setup-waiting")}</div>'
            f'<div data-when="added" hidden><p class="setup-done" data-added></p>{done_button}</div>'
            f'<div data-when="expired" hidden>{_p(w["expired_mac"], "setup-waiting")}'
            f'<div class="setup-act"><a class="fs-button" href="{_e(again)}">'
            f'{_e(w["new_code"])}</a></div></div></div>')


def added_line(person: str, device: str, language: str) -> str:
    """The Mac's line once the phone opened the code: "Homer's iPhone is added."."""
    return words(language)["added"].format(name=person, genitive=_genitive(person),
                                           device=names.said(device, language))


def devices_page(rows: list[dict], people: list[dict], *, language: str, today: date,
                 base: str, waiting: list[dict] = ()) -> str:
    """The Mac's page "Devices": the browsers that wait to join, each with
    "Allow as" and "Not now", each paired device with Remove, which asks
    first, and "This browser": "Use this browser for the family's pages"
    as one of `people` or Shared. `rows` are the devices as `stack devices
    list` shows them; `people` the family as `stack family` shows it;
    `waiting` the join requests, each `code` ("K7MP-4XQD"), `device` and
    `since` ("21:14").

        devices_page([{"id": "d-3f9a2c1b", "person": "homer", "name": "iPhone",
                       "paired": "2026-11-10", "seen": "2026-11-11"}], family["people"],
                     language="en", today=date(2026, 11, 11), base="/dashboard")
        # "... Homer · iPhone ... added 10 November · seen today ... Remove ..."
    """
    w = words(language)
    called = {p["id"]: p["name"] for p in people}
    items = "".join(_device_row(d, called, w, language, today) for d in rows)
    listed = (f'<ul class="setup-people devices-list">{items}</ul>' if items
              else _p(w["empty"], "setup-note"))
    pills = "".join(f'<a class="fs-pill" href="/setup/this-browser?as={_e(p["id"])}">'
                    f'<span class="fs-pill-name">{_e(p["name"])}</span></a>' for p in people)
    pills += (f'<a class="fs-pill" href="/setup/this-browser?as=shared">'
              f'<span class="fs-pill-name">{_e(w["shared"])}</span></a>')
    asking = "".join(_waiting_row(j, people, w, language) for j in waiting)
    main = (f'<h1 class="setup-heading">{_e(w["devices_heading"])}</h1>'
            + (f'<ul class="setup-people devices-waiting">{asking}</ul>' if asking else "")
            + f'{listed}<h2 class="setup-subheading">{_e(w["this_browser_heading"])}</h2>'
            f'<p class="setup-text">{_e(w["this_browser"])}</p>'
            f'<nav class="fs-pills devices-as"><span>{_e(w["as"])}</span>{pills}</nav>'
            f'<div class="setup-act"><a class="fs-link" href="/setup?step=family">'
            f'{_e(w["family"])}</a></div>' + _ask_dialog(w))
    return _frame(language, w["devices_heading"], main, script=_script(base))


# ── Allowing a browser, on a paired device or the Mac ────────────────────

def allow_page(join: dict, *, person: str, choices: list[dict] | None, language: str,
               base: str) -> str:
    """The page a paired device opens for a waiting browser, by its QR
    code or its typed code. `join` is the request: `code` ("K7MP4XQD"),
    `device` ("Chrome on a Mac") and `since` ("21:14"); the code shows as
    the new screen shows it ("K7MP-4XQD"), to compare. For a person's
    own device (`choices` None) it asks "Add this browser as Homer's?"
    with Allow and "Not me", `person` being the device's person's name.
    For an admin's device or the Mac, `choices` is the family (`id`,
    `name`), offered after "Allow as" with Shared, which asks for a name.

        allow_page({"code": "K7MP4XQD", "device": "Chrome on a Mac", "since": "21:14"},
                   person="Homer", choices=None, language="en", base="/dashboard")
        # "... Add this browser as Homer's? ... Chrome on a Mac, waiting since 21:14.
        #  K7MP-4XQD ... Allow ..."
    """
    w = words(language)
    device = names.said(join["device"], language)
    heading = (w["allow_heading"].format(name=person) if choices is None
               else w["allow_heading_any"])
    act = (f'<button class="fs-button" type="button" data-allow="">{_e(w["allow"])}</button>'
           if choices is None else _choices(choices, w))
    main = (f'<h1 class="setup-heading">{_e(heading)}</h1>'
            + _p(w["waiting_since"].format(device=device, time=join["since"]), "setup-text")
            + f'<p class="join-code">{_e(shown(join["code"]))}</p>'
            + f'<div class="join-allow" data-join="{_e(join["code"])}">'
            f'<div class="setup-act">{act}<button class="setup-mini" type="button" data-decline>'
            f'{_e(w["not_me"])}</button></div>'
            f'<p class="setup-done" data-allowed hidden></p>'
            f'<p class="setup-waiting" data-refused hidden></p></div>')
    return _frame(language, w["unknown_title"], main, script=_script(base))


def allowed_line(device: str, person: str, language: str) -> str:
    """What the approver reads once a browser is allowed: "Chrome on a Mac
    is now Homer's.", or for a shared device (`person` "") "Living room
    is now a shared device.".

        allowed_line("Chrome on a Mac", "Homer", "de")   # "Chrome auf einem Mac gehört jetzt Homer."
    """
    w = words(language)
    said = names.said(device, language)
    if not person:
        return w["now_shared"].format(device=said)
    return w["now_persons"].format(device=said, name=person)


def not_waiting(language: str) -> str:
    """The page for a code that does not wait: ended, used or wrong."""
    w = words(language)
    return _frame(language, w["unknown_title"], _p(w["not_waiting"], "setup-waiting"))


def only_admin(language: str) -> str:
    """The page for a device that may not allow this: a shared device, or
    a person's device asked for another person."""
    w = words(language)
    return _frame(language, w["unknown_title"], _p(w["only_admin"], "setup-waiting"))


def wait_a_minute(language: str) -> str:
    """The page for an approver who sent ten wrong codes in a minute."""
    w = words(language)
    return _frame(language, w["unknown_title"], _p(w["wait_minute"], "setup-waiting"))


def add_a_device(language: str, *, base: str, choose: bool = False) -> str:
    """"Add a device": the field for the code that the new device shows.
    The form opens the allow page for the code. With the page's script, a
    person's own device allows at once instead; on an admin's device
    (`choose`) the form still opens the allow page, which asks for whom.

        add_a_device("en", base="/dashboard")
        # "... Add a device ... Type the code that the new device shows. ... Allow ..."
    """
    w = words(language)
    main = (f'<h1 class="setup-heading">{_e(w["add_device_heading"])}</h1>'
            + _p(w["add_device_line"], "setup-text")
            + f'<form class="join-type"{"" if choose else " data-add-device"} method="get" '
            f'action="{_e(base)}/devices/allow"><label class="setup-text">{_e(w["code"])} '
            f'<input class="setup-value" name="code" placeholder="K7MP-4XQD" autocomplete="off" '
            f'autocapitalize="characters" spellcheck="false" required></label>'
            f'<div class="setup-act"><button class="fs-button">{_e(w["allow"])}</button></div>'
            f'<p class="setup-done" data-allowed hidden></p>'
            f'<p class="setup-waiting" data-refused hidden></p></form>')
    return _frame(language, w["add_device_heading"], main, script=_script(base))


def your_devices(rows: list[dict], *, this: str, others: list[dict], language: str,
                 today: date, base: str) -> str:
    """"Your devices" on a person's paired device: the person's own
    devices (`rows`, as `stack devices list` shows them), the one with the
    id `this` marked "This device", Remove on each, and "Add a device".
    `others` are the family's other people (`id`, `name`) an admin's
    device offers "Add a phone" for; [] on anyone else's device.

        your_devices([{"id": "d-3f9a2c1b", "person": "homer", "name": "iPhone",
                       "paired": "2026-11-10", "seen": "2026-11-11"}], this="d-3f9a2c1b",
                     others=[], language="en", today=date(2026, 11, 11), base="/dashboard")
        # "... Your devices ... iPhone ... This device ... Remove ... Add a device ..."
    """
    w = words(language)
    items = "".join(_own_row(d, this, w, language, today) for d in rows)
    family = "".join(f'<li><b>{_e(p["name"])}</b> <a class="fs-link" '
                     f'href="{_e(base)}/devices/add-a-phone?person={_e(p["id"])}">'
                     f'{_e(w["add_phone"])}</a></li>' for p in others)
    main = (f'<h1 class="setup-heading">{_e(w["your_devices"])}</h1>'
            f'<ul class="setup-people devices-list">{items}</ul>'
            f'<div class="setup-act"><a class="fs-button" href="{_e(base)}/devices/add">'
            f'{_e(w["add_device_heading"])}</a></div>'
            + (f'<h2 class="setup-subheading">{_e(w["family"])}</h2>'
               f'<ul class="setup-people">{family}</ul>' if family else "")
            + f'<div class="setup-act"><a class="fs-link" href="{_e(base)}">'
            f'{_e(w["dashboard"])}</a></div>' + _ask_dialog(w))
    return _frame(language, w["your_devices"], main, script=_script(base))


def since(asked: float) -> str:
    """The time a request was made in the family's zone (`TIMEZONE`, see
    `stack.clock`), as the pages and the Server room's notice say it. The
    container's own clock runs on UTC, so it would be an hour or more off.

        since(1762892040.0)   # "21:14" with TIMEZONE=Europe/Berlin
    """
    return datetime.fromtimestamp(asked, clock.zone()).strftime("%H:%M")


# ── Parts ────────────────────────────────────────────────────────────────

# The Mac's Devices list, where Done on "Add a phone" goes.
DEVICES = "/setup/devices"


def _device_row(d: dict, called: dict, w: dict, language: str, today: date) -> str:
    """One row of the Devices list: "Homer · iPhone", the dates, Remove."""
    device = names.said(d["name"], language)
    person = called.get(d["person"], d["person"]) if d["person"] else w["shared"]
    ask = (w["remove_ask"].format(name=person, genitive=_genitive(person), device=device)
           if d["person"] else w["remove_ask_shared"].format(device=device))
    when = w["row_when"].format(paired=_day(d["paired"], w, today, language),
                                seen=_day(d["seen"], w, today, language))
    return (f'<li data-device="{_e(d["id"])}"><b>{_e(w["row"].format(person=person, device=device))}'
            f'</b> <span>{_e(when)}</span> <button class="setup-mini" type="button" '
            f'data-remove="{_e(d["id"])}" data-ask="{_e(ask)}">{_e(w["remove"])}</button></li>')


def _own_row(d: dict, this: str, w: dict, language: str, today: date) -> str:
    """One row of "Your devices": the device, its dates, "This device"
    on the one that looks, Remove."""
    device = names.said(d["name"], language)
    mark = d["id"] == this
    when = w["row_when"].format(paired=_day(d["paired"], w, today, language),
                                seen=_day(d["seen"], w, today, language))
    ask = w["remove_ask_shared"].format(device=device)
    return (f'<li data-device="{_e(d["id"])}"{" data-this" if mark else ""}><b>{_e(device)}</b> '
            + (f'<span class="devices-this">{_e(w["this_device"])}</span> ' if mark else "")
            + f'<span>{_e(when)}</span> <button class="setup-mini" type="button" '
            f'data-remove="{_e(d["id"])}" data-ask="{_e(ask)}"{" data-this" if mark else ""}>'
            f'{_e(w["remove"])}</button></li>')


def _join_panel(join: dict, w: dict) -> str:
    """The unknown page's code: its QR code, the code in letters, and the
    three states the page's script shows (waiting, expired; allowed
    reloads the page, which then opens as the device)."""
    qr = qr_code.svg(join["link"], label=f"QR code: {join['link']}")
    return (f'<div class="pairing" data-join-state="{_e(join["state_url"])}">'
            f'<div data-when="waiting">{_p(w["join_line"], "setup-text")}'
            f'<figure class="setup-qr">{qr}<figcaption class="join-code">{_e(join["code"])}'
            f'</figcaption></figure>{_p(w["join_type"], "setup-text")}'
            f'{_heard(join.get("heard_by") or [], w)}'
            f'{_p(w["join_minutes"], "setup-note")}</div>'
            f'<div data-when="expired" hidden>{_p(w["expired_mac"], "setup-waiting")}'
            f'<div class="setup-act"><a class="fs-button" href="">{_e(w["new_code"])}</a></div>'
            f'</div></div>')


def _heard(admins: list[str], w: dict) -> str:
    """The line that names the admins who heard in the chat that this
    browser waits ("Marge and Homer"); for nobody, the line that says how
    else to get in, because no admin knows of this browser's code."""
    if not admins:
        return _p(w["not_heard"], "setup-text")
    names = admins[0] if len(admins) == 1 else \
        f'{", ".join(admins[:-1])} {w["and"]} {admins[-1]}'
    return _p(w["heard_one" if len(admins) == 1 else "heard"].format(names=names), "setup-text")


def _choices(people: list[dict], w: dict) -> str:
    """"Allow as": the family as pills, and Shared, which asks for the
    device's name first."""
    pills = "".join(f'<button class="fs-pill" type="button" data-allow-as="{_e(p["id"])}">'
                    f'<span class="fs-pill-name">{_e(p["name"])}</span></button>' for p in people)
    return (f'<nav class="fs-pills devices-as"><span>{_e(w["allow_as"])}</span>{pills}'
            f'<details class="join-shared"><summary class="fs-pill">'
            f'<span class="fs-pill-name">{_e(w["shared"])}</span></summary>'
            f'<label class="setup-text">{_e(w["name_device"])} <input class="setup-value" '
            f'name="name" placeholder="{_e(w["name_example"])}" maxlength="60"></label>'
            f'<button class="fs-button" type="button" data-allow-as="shared">{_e(w["allow"])}'
            f'</button></details></nav>')


def _waiting_row(join: dict, people: list[dict], w: dict, language: str) -> str:
    """One browser that waits, on the Mac's Devices page."""
    line = w["waiting_row"].format(device=names.said(join["device"], language),
                                   code=join["code"], time=join["since"])
    return (f'<li class="join-allow" data-join="{_e(join["code"].replace("-", ""))}">'
            f'<span class="devices-wait">{_e(line)}</span>{_choices(people, w)}'
            f'<button class="setup-mini" type="button" data-decline>{_e(w["not_now"])}</button>'
            f'<p class="setup-done" data-allowed hidden></p>'
            f'<p class="setup-waiting" data-refused hidden></p></li>')


def _ask_dialog(w: dict) -> str:
    """The question before a device is removed, filled by the page script."""
    return (f'<dialog class="devices-ask"><p data-ask></p><div class="setup-act">'
            f'<button class="fs-button" type="button" data-yes>{_e(w["remove"])}</button>'
            f'<button class="setup-mini" type="button" data-no>{_e(w["cancel"])}</button>'
            f'</div></dialog>')


def _day(iso: str, w: dict, today: date, language: str) -> str:
    """A date of the list as the page says it: "today", "yesterday",
    "10 November" ("am 10. November" in German)."""
    try:
        day = date.fromisoformat(iso)
    except ValueError:
        return iso
    if day == today:
        return w["today"]
    if (today - day).days == 1:
        return w["yesterday"]
    month = w["months"][day.month - 1]
    return f"am {day.day}. {month}" if language == "de" else f"{day.day} {month}"


def _genitive(name: str) -> str:
    """A German name's genitive: "Homers", "Hans'"."""
    return name + ("'" if name[-1:].lower() in ("s", "ß", "x", "z") else "s")


def _family_name(name: str, language: str) -> str:
    if not name:
        return ""
    return f"Familie {name}" if language == "de" else family_display_name(name)


def _script(base: str) -> str:
    """What the Mac's two pages load on top of the frame: their script,
    and the styles of links and pills, which the setup page has too."""
    styles = "".join(f'<link rel="stylesheet" href="{_e(base)}/static/{name}">'
                     for name in ("widgets/fs-widget.css", "components/pills.css"))
    return styles + f'<script type="module" src="{_e(base)}/static/devices.js"></script>'


def _frame(language: str, title: str, main: str, *, script: str = "") -> str:
    return Template(FRAME.read_text(encoding="utf-8")).substitute(
        lang="de" if language == "de" else "en", base=DASHBOARD_PREFIX, title=_e(title), main=main,
        script=script)


def _with_command(text: str) -> str:
    """`text`, escaped, with the command that prints the admin link set as code."""
    return _e(text).replace(ADMIN_LINK, f"<code>{ADMIN_LINK}</code>")


def _p(text: str, cls: str) -> str:
    return f'<p class="{cls}">{_e(text)}</p>'


def _e(text: str) -> str:
    return escape(text, quote=True)
