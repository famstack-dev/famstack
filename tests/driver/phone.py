"""phone: Element X on an Android phone over adb, tapped and typed as a person does.

    driver phone sign-in --server http://<mac-ip>:42031 --user homer --password - < password.txt

The install test's phone step: the phone on this Mac's USB signs in to the
family chat in Element X, and the driver times each screen, because a slow
first sign-in is what a new admin meets there. Only adb is used: screenshots
are not needed to find a field, the screen's accessibility tree names each
one by its id.

Each field is found by Element X's own id (`change_server-server`,
`login-continue`), tapped, and checked to hold the focus before anything is
typed, and its text is read back after. Positions on the screen are never
reused, because the keyboard moves the fields once it opens: typing at an
old position put a password into the user name field. Every check waits on
the screen (`_wait_for`) rather than a fixed sleep, because a phone under
load takes longer than any sleep that is short enough on a fast one.

A sign-in counts only when the app shows a signed-in screen: the home
screen, or a screen outside the sign-in flow without a dialog (the new
device's identity check, which has no id of its own). An error answers in a
dialog, and a dialog is never taken for a signed-in screen.

The password comes from stdin and goes only into the phone's field; it is
never printed. What it does not do: confirm the new device's identity, which
needs a person at another device.
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass
from html import unescape

APP = "io.element.android.x"
# Element X's ids for the manual sign-in, as its accessibility tree shows them.
SIGN_IN_MANUALLY = "onboarding-sign_in"
SERVER_FIELD = "change_server-server"
CONTINUE = "login-continue"
USER_FIELD = "login-email_username"
PASSWORD_FIELD = "login-password"
CHANGE_SERVER = "login-change_server"
# Every id of the sign-in flow: while one shows, the app is not signed in.
SIGN_IN_FLOW = {SIGN_IN_MANUALLY, SERVER_FIELD, CONTINUE, USER_FIELD, PASSWORD_FIELD, CHANGE_SERVER}
# The home screen's settings button: signed in for certain.
HOME = "home_screen-settings"
# Element X's dialog buttons; a dialog after Continue is the server's refusal.
DIALOG = {"dialog-positive", "dialog-negative", "dialog-neutral"}
# How long a screen may take before the sign-in counts as stuck. A first
# sign-in has been seen to wait 94 s for the app's own HTTPS attempts.
SCREEN_SECONDS = 600
# How long a field may take to take the focus or show what was typed, and
# the keyboard to close; seen well under a second on an idle phone.
FIELD_SECONDS = 10
# How often a wait reads the screen again; one read takes about as long.
POLL_SECONDS = 1


@dataclass
class Node:
    """One element of the phone's screen: its id, text, class, focus and centre."""

    id: str
    text: str
    cls: str
    focused: bool
    x: int
    y: int


# ── Signing in ───────────────────────────────────────────────────────────

def sign_in(server: str, user: str, password: str, log) -> dict:
    """Sign in to Element X from its first screen; the seconds each screen took.

    Starts the app fresh, so a running try does not carry over. Returns
    `{"signed_in": bool, "server_seconds": ..., "sign_in_seconds": ..., "after": "<texts>"}`;
    `signed_in` is False when the app answered Continue with a dialog
    (`after_sign_in`). Exits when a screen does not come, with what the
    phone shows.
    """
    _adb("shell", "am", "force-stop", APP)
    _adb("shell", "monkey", "-p", APP, "-c", "android.intent.category.LAUNCHER", "1")
    _tap(_wait_for(lambda s: _by_id(s, SIGN_IN_MANUALLY), 30, "the first screen"))
    _type_into(SERVER_FIELD, server)
    log.detail(f"the server field reads {_by_id(screen(), SERVER_FIELD).text!r}")
    started = time.monotonic()
    _tap(_by_id(screen(), CONTINUE))
    _wait_for(lambda s: _by_id(s, USER_FIELD), SCREEN_SECONDS, "the user name screen")
    server_seconds = round(time.monotonic() - started)
    log.detail(f"the user name screen came after {server_seconds}s")
    _type_into(USER_FIELD, user)
    # With the keyboard open the password field is off the screen and out of
    # the tree; the keyboard's "next" (Tab) moves the focus and the field in.
    _adb("shell", "input", "keyevent", "KEYCODE_TAB")
    _type_into(PASSWORD_FIELD, password, secret=True)
    started = time.monotonic()
    _tap(_continue_button())
    outcome = _wait_for(after_sign_in, SCREEN_SECONDS, "the screen after the sign-in")
    sign_in_seconds = round(time.monotonic() - started)
    texts = " | ".join(n.text for n in screen() if n.text)[:200]
    log.detail(f"{outcome} after {sign_in_seconds}s: {texts}")
    return {"signed_in": outcome == "signed in", "server_seconds": server_seconds,
            "sign_in_seconds": sign_in_seconds, "after": texts}


def after_sign_in(nodes: list[Node]) -> str | None:
    """What the screen after Continue says: "signed in", "refused", or None while it loads.

        after_sign_in([Node("home_screen-settings", ...)])    # "signed in"
        after_sign_in([Node("dialog-positive", "OK", ...)])   # "refused"
        after_sign_in([])                                     # None: the loading overlay

    The loading overlay has no text and the sign-in screen stays under it,
    so both read as None. Element X answers a failed sign-in with a dialog.
    """
    ids = {n.id for n in nodes}
    if HOME in ids:
        return "signed in"
    if ids & DIALOG:
        return "refused"
    if ids & SIGN_IN_FLOW or not any(n.text for n in nodes):
        return None
    return "signed in"


def _continue_button() -> Node:
    """Continue, with the keyboard closed first when it covers the button.

    Back closes an open keyboard; it is pressed only when Continue is not
    on the screen, because with the keyboard closed Back leaves the screen.
    """
    button = _by_id(screen(), CONTINUE)
    if button:
        return button
    _adb("shell", "input", "keyevent", "KEYCODE_BACK")
    return _wait_for(lambda s: _by_id(s, CONTINUE), FIELD_SECONDS, "Continue, with the keyboard closed")


# ── Typing into a field ──────────────────────────────────────────────────

def _type_into(field_id: str, text: str, secret: bool = False) -> None:
    """Tap the field, check it has the focus, replace its text, check the result.

    Refuses to type when the focus is elsewhere, because typing then lands
    in whatever field the keyboard serves.
    """
    field = _by_id(screen(), field_id)
    if not field:
        raise SystemExit(f"driver: no field {field_id!r} on the phone's screen")
    _tap(field)
    field = _wait_for(lambda s: _focused(s, field_id), FIELD_SECONDS,
                      f"the focus in the field {field_id!r} (nothing typed)")
    # The tap puts the cursor where it lands, so the old text goes in both
    # directions; the field must then be empty before anything is typed.
    count = len(field.text) + 2
    _adb("shell", "input", "keyevent", *["KEYCODE_DEL"] * count, *["KEYCODE_FORWARD_DEL"] * count)
    # An empty field shows its hint (`example.com`) as its text, so "cleared"
    # means: what it shows is no longer a piece of the old text.
    if field.text:
        _wait_for(lambda s: (n := _by_id(s, field_id)) and not (n.text and n.text in field.text),
                  FIELD_SECONDS, f"the field {field_id!r} cleared")
    _adb("shell", "input", "text", text.replace(" ", "%s"))
    # A password field may show its text masked, so only its length is checked.
    holds = (lambda shown: len(shown) == len(text)) if secret else (lambda shown: shown == text)
    _wait_for(lambda s: (n := _by_id(s, field_id)) and holds(n.text), FIELD_SECONDS,
              f"what was typed in the field {field_id!r}")


def _focused(nodes: list[Node], field_id: str) -> Node | None:
    """The field when it holds the focus, else None."""
    field = _by_id(nodes, field_id)
    return field if field and field.focused else None



# ── Reading the screen ───────────────────────────────────────────────────

NODE = re.compile(r"<node [^>]*>")


def screen() -> list[Node]:
    """The phone's screen as its accessibility tree lists it, in order."""
    _adb("shell", "uiautomator", "dump", "/sdcard/driver-ui.xml")
    xml = _adb("shell", "cat", "/sdcard/driver-ui.xml")
    nodes = []
    for match in NODE.finditer(xml):
        raw = match.group(0)
        attrs = dict(re.findall(r'([a-z-]+)="([^"]*)"', raw))
        x1, y1, x2, y2 = map(int, re.findall(r"\d+", attrs.get("bounds", "[0,0][0,0]")))
        node_id = attrs.get("resource-id", "")
        if node_id.startswith(f"{APP}:id/"):
            node_id = node_id.split("/", 1)[1]
        nodes.append(Node(id=node_id, text=unescape(attrs.get("text") or attrs.get("content-desc", "")),
                          cls=attrs.get("class", ""), focused=attrs.get("focused") == "true",
                          x=(x1 + x2) // 2, y=(y1 + y2) // 2))
    return nodes


def _by_id(nodes: list[Node], node_id: str) -> Node | None:
    return next((n for n in nodes if n.id == node_id), None)



def _wait_for(find, seconds: int, what: str):
    """Read the screen until `find` returns something, or exit naming `what`."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = find(screen())
        if found:
            return found
        time.sleep(POLL_SECONDS)
    shown = " | ".join(n.text for n in screen() if n.text)[:200]
    raise SystemExit(f"driver: {what} did not come within {seconds}s; the phone shows: {shown}")


def _tap(node: Node) -> None:
    _adb("shell", "input", "tap", str(node.x), str(node.y))


def _adb(*args: str) -> str:
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=True).stdout
