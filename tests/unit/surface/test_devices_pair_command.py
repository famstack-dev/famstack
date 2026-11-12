"""Pairing a phone from the terminal: `stack devices pair <person>`.

A Mac in a cupboard has no screen for the Mac's page "Add a phone"; its
admin reaches only its terminal, over ssh. `stack devices pair homer`
asks core's tools server on the Mac's address for a pairing code, prints
its QR code as text and its address, and waits until Homer's phone opened
it, the code expired or the admin pressed Ctrl-C.

Driven through `./stack` as it runs (`stack.cli.main`, `StackCLI`) against
the tools server's FastAPI app. The command reaches the app over HTTP on
127.0.0.1, through a small stand-in for Docker's port forwarding: a
listener on a free port that hands each request to the app as arriving on
the Mac's address (`access.CARDS_PORT`), as `127.0.0.1:42002` hands it to
the container. The instance's `[core] cards_port` names that port. The
phone is a `Browser` on the home address; it opens the printed address in
a thread of its own while the command waits. The host API is the
dashboard's stand-in (`tests/dashboard_host.py`), whose `stack devices`
runs for real on the instance.
"""

from __future__ import annotations

import _thread
import re
import threading
import time

import pytest

from .conftest import StackCLI, tools_server
from .dsl import Browser
from .mac_address import MacAddress
from .setup_stand_in import stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "simpson.local:42000"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
# The QR code's text: Unicode half blocks, segno's compact form.
QR_LINE = re.compile(r"^\s*[█▀▄ ]{25,}$", re.MULTILINE)
LINK = re.compile(r"http://" + re.escape(HOME) + r"/go/app\?pair=[\w-]+")

devices = tools_server("devices")
access = tools_server("access")
server = tools_server("server")


@pytest.fixture
def stack(tmp_path, monkeypatch, httpserver):
    """`./stack` on the Simpsons' instance with the chat running, core's
    link base on the Mac's `.local` name, and core's tools server on the
    Mac's address. The command asks every 50 ms instead of every second."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    devices.CODES.forget()
    stack_in_state(tmp_path, monkeypatch, httpserver)("chat")
    mac = MacAddress()
    root = tmp_path / "instance"
    toml = root / "stack.toml"
    toml.write_text(toml.read_text().replace("[core]\n", f"[core]\ncards_port = {mac.port}\n", 1))
    monkeypatch.setattr("stack.devices.pairing.ASK_EVERY_SECS", 0.05)
    cli = StackCLI(root, monkeypatch)
    cli.mac = mac  # type: ignore[attr-defined]
    yield cli
    mac.close()
    devices.CODES.forget()
    devices.CODES.clock = time.time


def phone() -> Browser:
    """Homer's phone on the Wi-Fi, opening the home address, not paired yet."""
    return Browser(server.app, listener=access.PUBLIC_PORT, host=HOME, user_agent=IPHONE,
                   by_name=True, paired=False)


def meanwhile(mac: MacAddress, then) -> threading.Thread:
    """Run `then(answer)` in a thread once the command made its code."""
    def run():
        assert mac.made.wait(10), "the command made no code"
        then(mac.answer)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def path_of(link: str) -> str:
    return link.split(HOME, 1)[1]


# ── Pairing from the terminal ────────────────────────────────────────────

def test_the_terminal_shows_a_qr_code_that_pairs_homers_phone(stack):
    """Given the terminal on the Mac, when the admin runs `stack devices
    pair homer` and Homer's phone opens the printed address, then the
    terminal said what to do, showed the QR code, the address, how long it
    works and that it waits, and ends with "Homer's iPhone is added.";
    the phone is Homer's from then on."""
    homers = phone()
    opened = {}
    thread = meanwhile(stack.mac, lambda answer: opened.update(
        page=homers.opens(path_of(answer["link"]))))

    ran = stack("devices", "pair", "homer")
    thread.join(10)

    assert ran.code == 0, ran
    printed = ran.out
    for line in ("Add Homer's phone",
                 "Open the camera on Homer's phone and point it at this code.",
                 "The code works once, for 10 minutes.",
                 "Waiting for the phone…",
                 "Homer's iPhone is added."):
        assert line in printed, (line, printed)
    assert len(QR_LINE.findall(printed)) >= 15, printed
    assert LINK.search(printed)[0] == stack.mac.answer["link"]
    assert opened["page"].status == 200, opened["page"]
    assert "This phone is now Homer's" in opened["page"].text
    assert homers.opens("/dashboard").status == 200


def test_after_ten_minutes_the_code_has_expired(stack):
    """Given the command waits, when 10 minutes pass, then it says the
    code has expired and how to get a new one, and ends with 1; the code
    no longer pairs a phone."""
    later = lambda: time.time() + 10 * 60 + 1  # noqa: E731
    meanwhile(stack.mac, lambda _answer: setattr(devices.CODES, "clock", later))

    ran = stack("devices", "pair", "homer")

    assert ran.code == 1, ran
    assert "This code has expired. Run stack devices pair homer again." in ran.out
    assert phone().opens(path_of(stack.mac.answer["link"])).status == 410


def test_ctrl_c_ends_the_code(stack):
    """Given the command waits, when the admin presses Ctrl-C, then the
    code has expired, exit 1, and a phone that opens it is not paired."""
    meanwhile(stack.mac, lambda _answer: stack.mac.asked.wait(10) and _thread.interrupt_main())

    ran = stack("devices", "pair", "homer")

    assert ran.code == 1, ran
    assert "This code has expired. Run stack devices pair homer again." in ran.out
    late = phone()
    assert late.opens(path_of(stack.mac.answer["link"])).status == 410
    assert late.opens("/dashboard").status == 401


def test_with_json_it_prints_the_address_and_the_end_and_does_not_wait(stack):
    """Given `--json`, then the address and the code's end time, without
    the QR code's text; nothing waits, and the code still works."""
    ran = stack("devices", "pair", "homer", "--json")

    assert ran.code == 0, ran
    answer = ran.answer
    assert set(answer) == {"person", "link", "expires"}
    assert answer["person"] == "homer"
    assert LINK.fullmatch(answer["link"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", answer["expires"])
    assert phone().opens(path_of(answer["link"])).status == 200


# ── What it refuses ──────────────────────────────────────────────────────

def test_a_person_not_in_the_family_gets_one_line_and_no_code(stack):
    ran = stack("devices", "pair", "ned")

    assert ran.code == 1, ran
    assert "ned is not in the family." in ran.out + ran.err
    assert "stack family" in ran.out + ran.err
    assert not stack.mac.made.is_set()


def test_without_core_it_says_to_start_core(stack, tmp_path):
    from .conftest import a_closed_port

    toml = stack.instance / "stack.toml"
    toml.write_text(re.sub(r"cards_port = \d+", f"cards_port = {a_closed_port()}", toml.read_text()))

    ran = stack("devices", "pair", "homer")

    assert ran.code == 1, ran
    said = ran.out + ran.err
    assert "stack up core" in said
    assert len([line for line in said.splitlines() if line.strip()]) == 1, said


# ── The route behind it ──────────────────────────────────────────────────

def test_the_route_that_makes_codes_answers_only_on_the_macs_address(stack):
    """A device on the home address, paired or not, gets no code: the
    route answers 403 there ("This happens on the Mac")."""
    paired = Browser(server.app, listener=access.PUBLIC_PORT, host=HOME, by_name=True)

    status, _ = paired.posts("/dashboard/api/devices/pairing", {"person": "homer"})

    assert status == 403
