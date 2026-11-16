"""The setup walk's gates: a step that fails is a failed step in the protocol.

The walk is the release gate's install test, and `<out>/protocol.md` is what
the gate reads. So a step that raises still leaves the protocol, with that
step marked stuck and `./stack doctor`'s answer; a phone the setup page never
saw fails the phone step; and Element X refusing the sign-in fails it too.

The walk runs here against a stub instance (its `./stack` answers) and a
setup page that shows one step and never moves on by itself. The browser
is the boundary: `instance_context` hands the walk that page instead of
Chrome. The phone is reached through a stand-in `adb` first on `PATH`.
"""

from __future__ import annotations

import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from tests.unit.driver import driver_code

setup_walk = driver_code.load("setup_walk")
narration = driver_code.load("narration")

LINK = "http://127.0.0.1:42002/setup?key=admin-key"
PAIR = "http://192.168.1.20:42000/go/pair?code=4711"
DOCTOR_SAYS = "core  ✓ healthy (doctor ran)"


# ── The stand-ins ─────────────────────────────────────────────────────────

class StubInstance:
    """An instance's `./stack` answers: the family, the admin link, and doctor."""

    host = None
    name = "stub"

    def __init__(self, people: list[str]):
        self.people = people

    def stack(self, *args: str) -> dict:
        if args[0] == "family":
            return {"people": self.people}
        assert args[:2] == ("devices", "admin-link"), args
        return {"link": LINK}

    def run(self, command: str) -> subprocess.CompletedProcess:
        assert command.startswith("./stack doctor"), command
        return subprocess.CompletedProcess(command, 0, DOCTOR_SAYS + "\n", "")


class Page:
    """A setup page showing `step`, which never moves on by itself.

    `reached` is the phone step's "the phone opened the link" mark;
    `stuck_first_run` makes the first run's name field never appear.
    """

    url = LINK

    def __init__(self, step: str | None, reached: bool = False, stuck_first_run: bool = False):
        self.step = step
        self.reached = reached
        self.stuck_first_run = stuck_first_run

    def goto(self, url: str) -> None:
        pass

    def screenshot(self, path: str, full_page: bool) -> None:
        Path(path).write_bytes(b"png")

    def inner_text(self, selector: str) -> str:
        return f"the page shows {self.step}"

    def locator(self, selector: str) -> Element:
        return Element(self, selector)


class Element:
    """What a locator finds on `Page`, by the selector the walk asks for."""

    def __init__(self, page: Page, selector: str):
        self.page, self.selector = page, selector

    @property
    def first(self) -> Element:
        return self

    def count(self) -> int:
        if "data-part=reach" in self.selector:
            return int(self.page.reached)
        return 0 if ":visible" in self.selector else 1

    def wait_for(self, timeout: int) -> None:
        if self.page.stuck_first_run and self.selector == ".fs-moment input":
            raise TimeoutError(f"Timeout {timeout}ms exceeded waiting for {self.selector}")

    def get_attribute(self, name: str) -> str | None:
        return {"data-step": self.page.step, "aria-label": f"Scan: {PAIR}", "data-secret": "pw-homer"}.get(name)

    def inner_text(self) -> str:
        return f"the page shows {self.page.step}"

    def all_inner_texts(self) -> list[str]:
        return ["http://192.168.1.20:42031", "homer"]

    def click(self) -> None:
        pass

    def fill(self, text: str) -> None:
        pass

    def press(self, key: str) -> None:
        pass


@pytest.fixture
def browser(monkeypatch):
    """Hand the walk a `Page` in place of Chrome; set `browser.page` first."""

    class Browser:
        page: Page

        @contextmanager
        def context(self, instance, language="en"):
            yield type("Context", (), {"new_page": lambda _self: self.page})()

    stand_in = Browser()
    monkeypatch.setattr(setup_walk, "instance_context", stand_in.context)
    monkeypatch.setattr(setup_walk, "POLL_SECONDS", 0)
    monkeypatch.setattr(setup_walk, "REACH_SECONDS", 0)
    return stand_in


@pytest.fixture
def adb(tmp_path, monkeypatch) -> Path:
    """A stand-in `adb` first on PATH that succeeds; the log of its calls."""
    calls = driver_code.command(tmp_path, "adb")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    return calls


def walk(tmp_path: Path, browser, page: Page, people=("Homer", "Marge"), **given) -> tuple[bool, dict]:
    """Walk the stub instance on `page`; whether it passed, and the protocol: `gates` by step and its `text`."""
    browser.page = page
    out = tmp_path / "run"
    passed = setup_walk.walk_setup(StubInstance(list(people)), setup_walk.Walk(out=out, **given),
                                   narration.Narrator())
    protocol = (out / "protocol.md").read_text()
    rows = [line.split("|") for line in protocol.splitlines() if line.startswith("| ") and line[2].isdigit()]
    return passed, {"gates": {row[2].strip(): row[3].strip() for row in rows}, "text": protocol}


# ── A step that raises ────────────────────────────────────────────────────

def test_a_step_that_raises_still_writes_the_protocol(tmp_path, browser):
    """Given the AI step and no `--ai-key`, when the walk reaches it, then
    the protocol is written with the AI step stuck and doctor's answer.
    Without the protocol the gate has nothing to read for the one step
    that went wrong."""
    passed, protocol = walk(tmp_path, browser, Page("ai"))

    assert not passed
    assert protocol["gates"] == {"ai": "**stuck**", "doctor": "passed"}
    assert DOCTOR_SAYS in protocol["text"]


def test_a_first_run_that_times_out_still_writes_the_protocol(tmp_path, browser):
    """Given a new install whose name field never appears, when the 30 s
    wait times out, then the protocol has the family step stuck."""
    passed, protocol = walk(tmp_path, browser, Page(None, stuck_first_run=True), people=())

    assert not passed
    assert protocol["gates"] == {"family": "**stuck**", "doctor": "passed"}


# ── The phone step ────────────────────────────────────────────────────────

def test_a_phone_the_page_never_saw_fails_the_phone_step(tmp_path, browser, adb):
    """Given the phone opened the QR code's link, when the setup page never
    marks it reached, then the phone step is stuck, the walk does not pass,
    and the phone does not go on to sign in."""
    passed, protocol = walk(tmp_path, browser, Page("phone", reached=False), phone=True)

    assert not passed
    assert protocol["gates"] == {"phone": "**stuck**", "doctor": "passed"}
    opened = adb.read_text().splitlines()
    assert opened == [f"shell am start -a android.intent.action.VIEW -d {PAIR}"]


def test_a_refused_sign_in_fails_the_phone_step(tmp_path, browser, adb, monkeypatch):
    """Given the page saw the phone, when Element X answers the sign-in with
    an error, then the phone's sign-in record fails and so does the step."""
    refused = {"signed_in": False, "server_seconds": 3, "sign_in_seconds": 2, "after": "Invalid password | OK"}
    monkeypatch.setattr(setup_walk.phone_app, "sign_in", lambda *args: refused)

    passed, protocol = walk(tmp_path, browser, Page("phone", reached=True), phone=True)

    assert not passed
    assert protocol["gates"] == {"phone sign-in": "**stuck**", "phone": "**stuck**", "doctor": "passed"}
