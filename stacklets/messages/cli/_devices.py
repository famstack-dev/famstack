"""The sign-ins of a chat account, and which app each one is.

Synapse keeps one device per sign-in: the phone app, the computer app,
each browser. Its admin API lists them for an account
(`GET /_synapse/admin/v2/users/<user>/devices`), each with the user agent
of its last request (`last_seen_user_agent`). `stack messages users` names
each sign-in's app from that, so the setup page can tell a sign-in with
the phone app from one in the Mac's browser:

    device({"device_id": "QWERTY", "last_seen_ts": 1762700000000,
            "last_seen_user_agent": "Element X/25.10.1 (Google Pixel 8; "
                                    "Android 15; AP4A; Sdk 25.10.1)"})
    # {"id": "QWERTY", "app": "Element X Android", "kind": "phone",
    #  "last_seen": 1762700000000}

A sign-in is one of three kinds, or none:

    kind       the user agent                           app, for example
    phone      an app's own, naming Android or iOS      Element X Android
    computer   a browser's, with Electron in it         Element Desktop
    browser    a browser's (starts with Mozilla/)       Element Web
    ""         missing, or an agent none of the above   "" or its product

Why the user agent: it is the one field the app writes. The device's
display name is the person's to change ("Homer's phone"), so it decides
nothing. A browser on a phone is a browser, not the phone app: Element
Web says Android in its user agent too, behind "Mozilla/". A sign-in this
module cannot place is kind "", and whoever reads the list treats it as
unknown rather than guessing, because an unknown device can be the phone.

Pure: no I/O. The command (`users.py`) asks Synapse; this module reads
the answer.
"""

from __future__ import annotations

import re

__all__ = ["device"]


def device(raw: dict) -> dict:
    """One sign-in as `stack messages users` reports it: its `id`, its
    `app` ("" when the server does not say), its `kind` ("phone",
    "computer", "browser" or "") and `last_seen` (ms, or None), from one
    device of Synapse's admin API. Reads only; never fails on a missing
    field.

        device({"device_id": "MAC1", "last_seen_ts": 1762700000000,
                "last_seen_user_agent": "Mozilla/5.0 (Macintosh; Intel Mac "
                "OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Element/1.12.3 Chrome/138.0 Electron/37.2.0 Safari/537.36"})
        # {"id": "MAC1", "app": "Element Desktop", "kind": "computer", ...}

        device({"device_id": "OLD"})
        # {"id": "OLD", "app": "", "kind": "", "last_seen": None}
    """
    app, kind = _app_and_kind((raw.get("last_seen_user_agent") or "").strip())
    return {"id": raw.get("device_id", ""), "app": app, "kind": kind,
            "last_seen": raw.get("last_seen_ts")}


# ── Reading the user agent ────────────────────────────────────────────────

# The phone's system, as an app names it in its user agent: Element X and
# Element say "Android 15" or "iOS 18.1" inside the parentheses.
_ANDROID = re.compile(r"\bAndroid\b")
_IOS = re.compile(r"\b(iOS|iPadOS|iPhone|iPad)\b")
# Element X writes its name with or without the space ("ElementX/...").
_ELEMENT_X = re.compile(r"^Element ?X\b")


def _app_and_kind(agent: str) -> tuple[str, str]:
    """The app's name and the sign-in's kind, from a user agent."""
    if not agent:
        return "", ""
    if "Electron/" in agent:
        return "Element Desktop", "computer"
    if agent.startswith("Mozilla/"):
        return "Element Web", "browser"
    product = "Element X" if _ELEMENT_X.match(agent) else agent.split("/", 1)[0].strip()
    if _ANDROID.search(agent):
        return f"{product} Android", "phone"
    if _IOS.search(agent):
        return f"{product} iOS", "phone"
    return product, ""
