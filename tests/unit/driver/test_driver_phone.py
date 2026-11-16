"""The phone driver: it waits on the screen, and a sign-in counts only when it signed in.

Element X on the phone is read through `adb` (its accessibility tree) and
typed into with `adb shell input`. Here a stand-in `adb` first on `PATH`
serves a list of screens, one more each time the driver reads the screen,
so a test says what the phone shows and in which order.

The browser's ssh proxy to another Mac is in this file too: like adb, it is
a command the driver starts and nobody at the walk can answer.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.unit.driver import driver_code

phone = driver_code.load("phone")
browser = driver_code.load("browser")
instance = driver_code.load("instance")

USER = phone.USER_FIELD


def node(node_id: str, text: str = "", focused: bool = False) -> str:
    """One element of a screen, as `uiautomator dump` writes it."""
    return (f'<node resource-id="{phone.APP}:id/{node_id}" text="{text}" class="android.widget.EditText" '
            f'focused="{"true" if focused else "false"}" bounds="[0,100][600,200]" />')


@pytest.fixture
def screens(tmp_path, monkeypatch):
    """Serve `screens(*xml)` to the driver: each screen read gets the next one, the last stays."""
    monkeypatch.setattr(phone, "POLL_SECONDS", 0)
    monkeypatch.setattr(phone, "FIELD_SECONDS", 0.5)

    def serve(*shown: str) -> Path:
        for number, xml in enumerate(shown, 1):
            (tmp_path / f"screen-{number}.xml").write_text(f"<hierarchy>{xml}</hierarchy>")
        count = tmp_path / "reads"
        # `uiautomator dump` turns to the next screen; `cat` shows the current one.
        then = (f'case "$*" in *"uiautomator dump"*) n=$(( $(cat {count} 2>/dev/null || echo 0) + 1 )); '
                f'[ -f {tmp_path}/screen-$n.xml ] && echo $n > {count}; :;; '
                f'*"cat /sdcard"*) cat {tmp_path}/screen-$(cat {count}).xml;; esac')
        calls = driver_code.command(tmp_path, "adb", then)
        monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
        return calls

    return serve


# ── Waiting on the screen ─────────────────────────────────────────────────

def test_typing_waits_until_the_field_takes_the_focus(screens):
    """Given a field that takes the focus only on the second read after the
    tap, when the driver types into it, then it waits for the focus and
    types once. A fixed pause after the tap was too short on a busy phone,
    and the driver gave up with nothing typed."""
    calls = screens(node(USER), node(USER), node(USER, focused=True), node(USER, "homer", focused=True))

    phone._type_into(USER, "homer")

    typed = [line for line in calls.read_text().splitlines() if line.startswith("shell input text")]
    assert typed == ["shell input text homer"]


def test_typing_into_a_field_that_never_takes_the_focus_types_nothing(screens):
    """Given a field that never takes the focus, when the driver types into
    it, then it stops and types nothing, because the keyboard would put the
    text into another field."""
    calls = screens(node(USER))

    with pytest.raises(SystemExit):
        phone._type_into(USER, "homer")

    assert not [line for line in calls.read_text().splitlines() if line.startswith("shell input text")]


# ── The screen after Continue ─────────────────────────────────────────────

def nodes(*xml: str) -> list:
    """`Node`s as the driver reads them, from `node()` strings."""
    return [phone.Node(id=i, text=t, cls="", focused=False, x=0, y=0)
            for i, t in (_parts(x) for x in xml)]


def _parts(xml: str) -> tuple[str, str]:
    node_id = xml.split(f"{phone.APP}:id/")[1].split('"')[0]
    return node_id, xml.split('text="')[1].split('"')[0]


@pytest.mark.parametrize("shown, outcome", [
    ((node("dialog-positive", "OK"), node("", "Invalid username or password")), "refused"),
    ((node("home_screen-settings", "Settings"), node("", "All chats")), "signed in"),
    ((node(phone.PASSWORD_FIELD, "********"), node(phone.CONTINUE, "Continue")), None),
    ((), None),
    ((node("", "Confirm your identity"), node("", "Use another device")), "signed in"),
], ids=["error dialog", "home screen", "still signing in", "loading overlay", "identity check"])
def test_the_screen_after_continue_is_read_by_what_it_is(shown, outcome):
    """Given the screen after Continue, then an error dialog is a refusal,
    the home screen or a screen outside the sign-in flow is signed in, and
    the sign-in screen or the empty loading overlay is still waiting. An
    error dialog has text and no password field, which was once taken for
    a signed-in screen."""
    assert phone.after_sign_in(nodes(*shown)) == outcome


# ── The browser's ssh proxy ───────────────────────────────────────────────

def test_the_ssh_proxy_never_waits_for_a_password(tmp_path, monkeypatch):
    """Given another Mac whose ssh would ask for a password, when the walk
    opens its proxy, then ssh runs in batch mode and ends instead of
    prompting, so the walk fails at once rather than hanging."""
    calls = driver_code.command(tmp_path, "ssh", "exit 255")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")

    with pytest.raises(SystemExit), browser.instance_proxy(instance.Instance(host="other-mac", root=None)):
        pass

    options = calls.read_text().split()
    assert "BatchMode=yes" in options
