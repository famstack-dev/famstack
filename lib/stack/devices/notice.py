"""The Server room's notice of a new device: its text, its link and whom
it mentions, written on the host from fields only.

    stack devices notice --kind waits --browser Chrome --system Mac --code K7MP4XQD --since 21:14
    stack devices notice --kind added d-3f9a2c1b --by d-77aa01c2

    waits(root, {"browser": "Chrome", "system": "Mac", "code": "K7MP4XQD", "since": "21:14"},
          home="http://simpson.local:42000")
    # {"text": "A browser waits to join famstack: Chrome on a Mac, since 21:14,
    #           code K7MP-4XQD. If it belongs to the family: Allow it
    #           (http://simpson.local:42000/dashboard/devices/allow?code=K7MP4XQD&from=chat)",
    #  "html": "... <a href=\"http://...\">Allow it</a>", "mentions": ["homer"]}

Every admin hears of every new device: a browser that waits to join, and
a device that a person's own paired device, an admin or the Mac added. A
Mac in a cupboard has no screen to show who waits, so the Server room is
where the admins see it, and the notice is a chat message that mentions
each admin, because a mention makes their phones ring and a silent
notice does not. Only admins are in the Server room.

    tools server (container)        host API          this module, on the Mac
    a browser waits ──▶ join_notice {browser, system, code, since}
                                    ──▶ stack devices notice ──▶ waits(): text, link
                                                             ──▶ stack messages send famstack
                                                                 --mention <each admin>

Why fields, and why here. A browser chooses its User-Agent, so no text
of a browser may reach the chat. The tools server sends names from the
fixed lists below and a code; this module refuses anything else, and a
device that was added is named from the Mac's own list of devices. The
link goes to the allow page on the home address (`LINK_BASE_URL`'s host,
so a paired phone's key counts there) and names the request by its code
only, never by the waiting browser's secret. `from=chat` makes that page
answer only an admin's device or the Mac, so a forwarded message lets
nobody in. The tap on the link allows nothing: the page does.

What it does not do: send the message (the command does, with `stack
messages send`), or count notices: at most one "waits" in 10 minutes is
counted where the browsers arrive, in the tools server's
`devices/notice.py`. The messages are in English, as the Server room's
other messages. The design: ADR-023 D3, D10 and D20.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from .. import devices
from ..users import load_users, user_id

__all__ = ["BROWSERS", "SYSTEMS", "ALLOW_PATH", "waits", "added", "admins"]

# The browsers a notice may name, as the tools server reads them from a
# User-Agent. "" is a browser it does not know.
BROWSERS = ("Chrome", "Edge", "Firefox", "Opera", "Safari")
# The systems a notice may name, and how a sentence says them.
SYSTEMS = {"Mac": "on a Mac", "Windows": "on Windows", "Linux": "on Linux",
           "Chromebook": "on a Chromebook", "iPhone": "on an iPhone", "iPad": "on an iPad",
           "Android phone": "on an Android phone", "Android tablet": "on an Android tablet"}
# The page that allows a waiting browser, on the home address.
ALLOW_PATH = "/dashboard/devices/allow"
# A join code as the tools server makes it: 8 letters and digits without
# look-alikes (`devices/codes.py`).
_CODE = re.compile(r"[23456789ABCDEFGHJKMNPQRSTUVWXYZ]{8}")
# The time a browser started to wait, as the Mac's clock shows it.
_SINCE = re.compile(r"([01][0-9]|2[0-3]):[0-5][0-9]")


def waits(instance_dir: Path, fields: dict, *, home: str) -> dict:
    """The notice that a browser waits to join: `text` (the body, the
    link written out), `html` (the link as "Allow it") and `mentions`
    (each admin's user id). `fields` are `browser` and `system` (from
    `BROWSERS` and `SYSTEMS`, or ""), `code` and `since`; `home` is the
    home address (`http://simpson.local:42000`). Refuses, as
    `{"error": ...}`, any field outside those, and a missing home address.

        waits(root, {"browser": "Firefox", "system": "Linux", "code": "K7MP4XQD",
                     "since": "09:05"}, home="http://simpson.local:42000")["text"]
        # "A browser waits to join famstack: Firefox on Linux, since 09:05, code K7MP-4XQD. ..."
        waits(root, {"browser": "<b>Hi</b>", ...}, home=...)
        # {"error": "The browser is one of: Chrome, Edge, Firefox, Opera, Safari."}
    """
    refused = _refusal(fields)
    if refused:
        return {"error": refused}
    if not home:
        return {"error": "Core has no home address yet: run `./stack up core`."}
    code = fields["code"]
    link = f"{home.rstrip('/')}{ALLOW_PATH}?code={code}&from=chat"
    said = (f"A browser waits to join famstack: {_device(fields['browser'], fields['system'])}, "
            f"since {fields['since']}, code {code[:4]}-{code[4:]}. If it belongs to the family:")
    return {"text": f"{said} Allow it ({link})",
            "html": f'{html.escape(said)} <a href="{html.escape(link)}">Allow it</a>',
            "mentions": admins(instance_dir)}


def added(instance_dir: Path, device_id: str, by: str) -> dict:
    """The notice that a device was added: `text`, `html` and `mentions`.
    `device_id` is the new device in the Mac's list; `by` is the device
    that allowed it (an id of that list), "mac" for the Mac, or "" for a
    pairing code. Everything the text says comes from the list and the
    family: a shared device's name, which an admin typed, is left out.

        added(root, "d-3f9a2c1b", "d-77aa01c2")["text"]
        # "Homer added a device: Chrome on a Mac, allowed from Homer's iPhone."
        added(root, "d-3f9a2c1b", "mac")["text"]
        # "A device was added for Bart on the Mac: Chrome on a Mac."
        added(root, "d-nothere", "")   # {"error": "No device has that id."}
    """
    listed = {d["id"]: d for d in devices.listed(instance_dir)}
    device = listed.get(device_id)
    approver = listed.get(by)
    if device is None or (by not in ("", "mac") and approver is None):
        return {"error": "No device has that id."}
    names = {user_id(u): u.get("name") or user_id(u) for u in load_users(instance_dir)}
    text = _added_text(device, approver, names, on_the_mac=by == "mac")
    return {"text": text, "html": html.escape(text), "mentions": admins(instance_dir)}


def admins(instance_dir: Path) -> list[str]:
    """The user ids of the family's admins, in the order of users.toml:
    the people a notice mentions.

        admins(root)   # ["homer"]
    """
    return [user_id(u) for u in load_users(instance_dir) if u.get("role") == "admin"]


# ── Checking the fields ──────────────────────────────────────────────────

def _refusal(fields: dict) -> str:
    """Why `fields` cannot make a notice, "" when they can. Each field
    is checked against its fixed list or form, so nothing a browser wrote
    reaches the chat."""
    if fields.get("browser", "") not in ("", *BROWSERS):
        return f"The browser is one of: {', '.join(BROWSERS)}."
    if fields.get("system", "") not in ("", *SYSTEMS):
        return f"The system is one of: {', '.join(SYSTEMS)}."
    if not _CODE.fullmatch(str(fields.get("code", ""))):
        return "The code is 8 letters and digits, as the waiting browser shows it."
    if not _SINCE.fullmatch(str(fields.get("since", ""))):
        return "The time is hours and minutes, 21:14."
    return ""


# ── Writing the text ─────────────────────────────────────────────────────

def _device(browser: str, system: str) -> str:
    """The waiting device in words: "Chrome on a Mac", "Safari on an
    iPhone", "Firefox", "A browser on Linux" or "A browser"."""
    where = SYSTEMS.get(system, "")
    return " ".join(part for part in (browser or "A browser", where) if part)


def _added_text(device: dict, approver: dict | None, names: dict, *, on_the_mac: bool) -> str:
    """The sentence of an added device. A person's own device that
    allowed it names that person; otherwise the sentence is passive."""
    shared = device["person"] == devices.HOUSEHOLD
    owner = names.get(device["person"], device["person"])
    if approver is not None:
        by = names.get(approver["person"], approver["person"])
        source = f"allowed from {by}'s {approver['name']}"
        if shared:
            return f"{by} added a shared device, {source}."
        if device["person"] == approver["person"]:
            return f"{by} added a device: {device['name']}, {source}."
        return f"{by} added a device for {owner}: {device['name']}, {source}."
    where = " on the Mac" if on_the_mac else ""
    if shared:
        return f"A shared device was added{where}."
    return f"A device was added for {owner}{where}: {device['name']}."
