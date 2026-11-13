"""Step 3 of the setup page warns when the Mac's firewall keeps phones out.

Part 1 of step 3, "Your phone reaches famstack", waits for a phone to open
the phone page. When the Mac's firewall blocks all incoming connections,
blocks OrbStack or does not allow downloaded signed apps, no phone ever
will, so the part says so at once, with the fix in System Settings. A
firewall that is off or lets OrbStack in, or a state that cannot be read,
changes nothing on the page.

Driven through the tools server's FastAPI app as the Mac's browser opens
it (the Browser DSL), read as served. The host API is the dashboard's
shared stand-in (`tests/dashboard_host.py`), whose `firewall` answers as
`stack core firewall --json` does.
"""

from __future__ import annotations

import re

import pytest

from .dsl import Page
from .setup_stand_in import on_this_mac, stack_in_state

import phone_reach  # noqa: E402  (on the path through setup_stand_in)

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

LAN = "192.168.1.20"
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running, and a function that sets
    what the host API says of the firewall. No reach is left behind."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{LAN}:42000/go")
    phone_reach.forget()
    host = stack_in_state(tmp_path, monkeypatch, httpserver)("chat", mac=LAN)

    def firewall(answer: dict) -> None:
        host.firewall = answer
    yield firewall
    phone_reach.forget()


def warning(page: Page) -> str:
    """The firewall's warning in part 1 of step 3, its text; "" when none."""
    part = re.search(r'<li class="setup-part" data-part="reach".*?</li>', page.body, re.S)
    found = re.search(r'<p class="setup-warning"[^>]*>(.*?)</p>', part[0] if part else "", re.S)
    return re.sub(r"<[^>]+>", "", found[1]) if found else ""


@pytest.mark.parametrize("blocks, fix", [
    ("block-all", "Block all incoming connections"),
    ("orbstack", "allow OrbStack"),
    ("signed", "allow OrbStack"),
])
def test_a_firewall_that_keeps_phones_out_is_named_with_its_fix(instance, blocks, fix):
    """Given the firewall keeps phones out, when the admin opens step 3,
    then part 1 warns at once, not after a while, and names the setting
    to change in System Settings › Network › Firewall."""
    instance({"on": True, "blocks": blocks})

    shown = warning(on_this_mac().opens("/setup?step=phone"))

    assert "Firewall" in shown and fix in shown


def test_the_warning_is_in_german_for_a_german_browser(instance):
    instance({"on": True, "blocks": "orbstack"})

    shown = warning(on_this_mac("de").opens("/setup?step=phone"))

    assert "Systemeinstellungen" in shown and "OrbStack" in shown


@pytest.mark.parametrize("answer", [
    {"on": False, "blocks": ""},
    {"on": True, "blocks": ""},
    {"error": "socketfilterfw --getglobalstate did not answer"},
])
def test_a_firewall_that_lets_phones_in_or_cannot_be_read_changes_nothing(instance, answer):
    """Given the firewall is off, lets OrbStack in, or cannot be read, then
    step 3 shows no warning and is otherwise the same: part 1 waits and
    holds the likely causes, as without a firewall."""
    instance(answer)

    page = on_this_mac().opens("/setup?step=phone")

    assert page.status == 200
    assert warning(page) == ""
    assert len(re.findall(r'<li class="setup-cause"', page.body)) == 4


def test_once_a_phone_reached_the_mac_the_warning_is_gone(instance):
    """A phone that opened the phone page got through the firewall, so
    part 1 is done and warns of nothing."""
    from .test_phone_check_page import device
    instance({"on": True, "blocks": "orbstack"})

    device(ANDROID).opens("/go/app")

    assert warning(on_this_mac().opens("/setup?step=phone")) == ""
