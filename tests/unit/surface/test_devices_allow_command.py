"""Allowing a waiting browser from the terminal: `stack devices allow <code>`.

A browser the home address does not know shows a code ("K7MP-4XQD") and
waits until a paired device allows it. An admin who has no paired device
yet has no such device: the allow page answers only a paired device or
the Mac's address, which listens on 127.0.0.1 alone. So the admin allows
it with a command, in the terminal or in the Server room (which runs the
same command, `test_server_room_allows_a_browser.py`).

    stack devices allow K7MP-4XQD                      # as the family's admin
    stack devices allow K7MP-4XQD --as marge           # as Marge's device
    stack devices allow K7MP-4XQD --shared "Living room"

Driven through `./stack` as it runs (`stack.cli.main`, `StackCLI`) against
the tools server's FastAPI app, reached over HTTP on 127.0.0.1 through the
stand-in for Docker's port forwarding (`MacAddress`). The waiting browser
is a `Browser` on the home address; whose it became is what the dashboard
shows it on its next page. The host API is the dashboard's stand-in
(`tests/dashboard_host.py`). Homer is the family's admin.
"""

from __future__ import annotations

import json
import re

import pytest

from .conftest import StackCLI, tools_server
from .dsl import Browser
from .mac_address import MacAddress
from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "simpson.local:42000"
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

devices = tools_server("devices")
access = tools_server("access")
server = tools_server("server")


@pytest.fixture
def stack(tmp_path, monkeypatch, httpserver):
    """`./stack` on the Simpsons' instance with the chat running, and core's
    tools server on the Mac's address. No join request is left behind."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    devices.JOINS.forget()
    devices.NOTICES.forget()
    stack_in_state(tmp_path, monkeypatch, httpserver)("chat")
    mac = MacAddress()
    root = tmp_path / "instance"
    toml = root / "stack.toml"
    toml.write_text(toml.read_text().replace("[core]\n", f"[core]\ncards_port = {mac.port}\n", 1))
    yield StackCLI(root, monkeypatch)
    mac.close()
    devices.JOINS.forget()


# ── Words of these tests ─────────────────────────────────────────────────

def waiting() -> tuple[Browser, str]:
    """A laptop that opened the home address unknown, and the code it shows."""
    laptop = Browser(server.app, listener=access.PUBLIC_PORT, host=HOME, user_agent=MAC_CHROME,
                     by_name=True, paired=False)
    page = laptop.opens("/dashboard")
    assert page.status == 401, page
    codes = page.texts("join-code")
    assert len(codes) == 1, page
    return laptop, codes[0]


def who_is(device: Browser) -> str:
    """The name the dashboard's top bar shows on `device`, "" when it does not open."""
    page = device.opens("/dashboard")
    found = re.search(r'<span class="who">([^<]*)</span>', page.body)
    return found[1] if page.status == 200 and found else ""


def shared_names() -> list[str]:
    """The shared devices the Mac's Devices page lists, by name."""
    listed = on_this_mac().opens("/setup/devices")
    return [row.split(" · ", 1)[1] for row in re.findall(r"<b>([^<]+)</b>", listed.body)
            if row.startswith("Shared · ")]


def answer(ran) -> dict:
    return json.loads(ran.out)


# ── Allowing from the terminal ───────────────────────────────────────────

def test_the_admin_allows_a_waiting_browser_from_the_terminal(stack):
    """Given a laptop that waits with its code, when the admin runs `stack
    devices allow <code>` on the Mac, then the command says which browser
    is now whose, and the laptop opens the dashboard as Homer, the family's
    admin, on its next page: the terminal is the admin's own."""
    laptop, code = waiting()

    ran = stack("devices", "allow", code, "--json")

    assert ran.code == 0, ran
    assert answer(ran)["person"] == "homer"
    assert answer(ran)["device"] == "Chrome on a Mac"
    assert who_is(laptop) == "Homer"


def test_as_names_the_person_the_browser_becomes(stack):
    """Given a waiting laptop, `--as marge` makes it Marge's device."""
    laptop, code = waiting()

    ran = stack("devices", "allow", code, "--as", "marge", "--json")

    assert ran.code == 0, ran
    assert answer(ran)["person"] == "marge"
    assert who_is(laptop) == "Marge"


def test_shared_makes_a_shared_device_with_that_name(stack):
    """Given a waiting laptop, `--shared "Living room"` makes it a shared
    device of that name, nobody's in particular."""
    laptop, code = waiting()

    ran = stack("devices", "allow", code, "--shared", "Living room", "--json")

    assert ran.code == 0, ran
    assert answer(ran)["person"] == ""
    assert laptop.opens("/dashboard").status == 200
    assert shared_names() == ["Living room"]


@pytest.mark.parametrize("typed", [str.lower, lambda c: c.replace("-", ""),
                                   lambda c: c.lower().replace("-", "")])
def test_the_code_counts_with_or_without_its_dash_in_any_case(stack, typed):
    """Given the code as a person types it, lower case or without its
    dash, it names the same waiting browser."""
    laptop, code = waiting()

    ran = stack("devices", "allow", typed(code), "--json")

    assert ran.code == 0, ran
    assert who_is(laptop) == "Homer"


def test_a_code_no_browser_waits_with_changes_nothing(stack):
    """Given a waiting laptop, when the admin types another code, the
    command fails with exit 1 and an error, and the laptop still waits."""
    laptop, code = waiting()
    other = "2222-3333" if code != "2222-3333" else "4444-5555"

    ran = stack("devices", "allow", other, "--json")

    assert ran.code == 1, ran
    assert answer(ran)["error"]
    assert who_is(laptop) == ""


def test_an_allowed_code_does_not_allow_a_second_time(stack):
    """Given a code that allowed its laptop, which collected it, when the
    admin runs the command again, it is a code no browser waits with: the
    same error as a wrong one."""
    laptop, code = waiting()
    stack("devices", "allow", code)
    assert who_is(laptop) == "Homer"

    again = stack("devices", "allow", code, "--json")
    wrong = stack("devices", "allow", "2345-6789", "--json")

    assert again.code == 1 and wrong.code == 1
    assert answer(again)["error"] == answer(wrong)["error"]


def test_ten_wrong_codes_make_the_command_wait_a_minute(stack):
    """Given ten wrong codes in a minute, when the admin then types the
    right one, it is refused too: guessing a code is limited from the
    terminal as on the allow page. The laptop still waits."""
    laptop, code = waiting()
    for _ in range(10):
        assert stack("devices", "allow", "2345-6789", "--json").code == 1

    ran = stack("devices", "allow", code, "--json")

    assert ran.code == 1, ran
    assert who_is(laptop) == ""


def test_a_person_not_in_the_family_is_refused(stack):
    """Given `--as ned`, who is not in the family, the command refuses with
    an error that names him, and the laptop still waits."""
    laptop, code = waiting()

    ran = stack("devices", "allow", code, "--as", "ned", "--json")

    assert ran.code == 1, ran
    assert "ned" in answer(ran)["error"]
    assert who_is(laptop) == ""


def test_without_a_code_the_command_says_what_to_type(stack):
    """Given no code, the command fails and names the command with a code."""
    ran = stack("devices", "allow", "--json")

    assert ran.code == 1, ran
    assert "stack devices allow" in answer(ran)["error"]


def test_core_not_answering_is_an_error(stack):
    """Given core's tools server does not answer on the Mac's address, the
    command fails and says to start core."""
    toml = stack.instance / "stack.toml"
    toml.write_text(re.sub(r"cards_port = \d+", "cards_port = 9", toml.read_text()))

    ran = stack("devices", "allow", "2345-6789", "--json")

    assert ran.code == 1, ran
    assert "stack up core" in answer(ran)["error"]
