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
old position put a password into the user name field.

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
# How long a screen may take before the sign-in counts as stuck. A first
# sign-in has been seen to wait 94 s for the app's own HTTPS attempts.
SCREEN_SECONDS = 600


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
    `{"server_seconds": ..., "sign_in_seconds": ..., "after": "<texts>"}`.
    Exits when a screen does not come, with what the phone shows.
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
    # A loading overlay hides the screen and has no text, so the next screen
    # is one with words on it and no password field.
    after = _wait_for(lambda s: None if _by_id(s, PASSWORD_FIELD) else next((n for n in s if n.text), None),
                      SCREEN_SECONDS, "the screen after the sign-in")
    sign_in_seconds = round(time.monotonic() - started)
    texts = " | ".join(n.text for n in screen() if n.text)[:200]
    log.detail(f"signed in after {sign_in_seconds}s: {texts}")
    return {"server_seconds": server_seconds, "sign_in_seconds": sign_in_seconds, "after": texts,
            "first": after.text}


def _continue_button() -> Node:
    """Continue, with the keyboard closed first when it covers the button.

    Back closes an open keyboard; it is pressed only when Continue is not
    on the screen, because with the keyboard closed Back leaves the screen.
    """
    button = _by_id(screen(), CONTINUE)
    if button:
        return button
    _adb("shell", "input", "keyevent", "KEYCODE_BACK")
    time.sleep(0.8)
    button = _by_id(screen(), CONTINUE)
    if not button:
        raise SystemExit("driver: no Continue on the phone's screen")
    return button


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
    time.sleep(0.8)
    field = _by_id(screen(), field_id)
    if not field or not field.focused:
        raise SystemExit(f"driver: the phone's field {field_id!r} did not take the focus; nothing typed")
    # The tap puts the cursor where it lands, so the old text goes in both
    # directions; the field must then be empty before anything is typed.
    count = len(field.text) + 2
    _adb("shell", "input", "keyevent", *["KEYCODE_DEL"] * count, *["KEYCODE_FORWARD_DEL"] * count)
    # An empty field shows its hint (`example.com`) as its text, so "cleared"
    # means: what it shows is no longer a piece of the old text.
    left = _by_id(screen(), field_id)
    if left and left.text and field.text and left.text in field.text:
        raise SystemExit(f"driver: the phone's field {field_id!r} kept text after clearing")
    _adb("shell", "input", "text", text.replace(" ", "%s"))
    time.sleep(0.5)
    now = _by_id(screen(), field_id)
    shown = now.text if now else ""
    # A password field may show its text masked, so only its length is checked.
    if (secret and len(shown) == len(text)) or (not secret and shown == text):
        return
    raise SystemExit(f"driver: the phone's field {field_id!r} does not hold what was typed"
                     + ("" if secret else f": {shown!r}"))



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
        time.sleep(1)
    shown = " | ".join(n.text for n in screen() if n.text)[:200]
    raise SystemExit(f"driver: {what} did not come within {seconds}s; the phone shows: {shown}")


def _tap(node: Node) -> None:
    _adb("shell", "input", "tap", str(node.x), str(node.y))


def _adb(*args: str) -> str:
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=True).stdout
