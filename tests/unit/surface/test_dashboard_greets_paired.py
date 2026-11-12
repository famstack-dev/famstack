"""The dashboard greets the person a device is paired as.

Since pairing, the tools server knows each request's person
(`devices.who`): a phone paired as Homer, a shared device of the
household, or the Mac's own address. That answer is the one source of who
looks. Homer's phone greets Homer and offers no picker, so nobody holding
it can pick another name; a shared device greets the household with no
picker; only the Mac's address keeps the picker and its hint, with the
choice remembered in that browser.

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL): a phone paired as Homer through the Mac's "Add a phone", a
browser paired for the household, and the Mac's own browser. The host API
is the dashboard's shared stand-in (`tests/dashboard_host.py`). What the
family bar does with the page's data in a browser (the pills' words, a tap
that remembers nobody) is `family-bar.js`; these tests pin what the server
serves it.
"""

from __future__ import annotations

import json
import re

import pytest

from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, paired_phone, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HINT = "Tap your name, and this device greets you"


@pytest.fixture
def simpsons(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running, core's links on the
    Mac's home-network address, so the Mac can pair a phone."""
    monkeypatch.setenv("LINK_BASE_URL", "http://192.168.1.20:42000/go")
    stack_in_state(tmp_path, monkeypatch, httpserver)("chat")


def shared_device() -> Browser:
    """A browser on the home address that the Mac paired for the household."""
    import server
    from access import PUBLIC_PORT
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def device_of(page: Page) -> dict | None:
    """Who the page tells its scripts is looking (`#fs-page-data`)."""
    found = re.search(r'<script type="application/json" id="fs-page-data">(.*?)</script>',
                      page.body, re.S)
    assert found, page
    return json.loads(found[1])["device"]


def test_a_phone_paired_as_homer_greets_homer_and_offers_no_picker(simpsons):
    """Given Homer's paired phone, when it opens the dashboard, then the
    served page greets Homer, shows no hint to tap one's name, and tells
    the family bar that Homer is looking: the device knows its person, so
    nobody can pick another name on it."""
    page = paired_phone("homer").opens("/dashboard")

    assert page.status == 200, page
    assert page.texts("greeting") == ["Welcome home, Homer."]
    assert page.texts("fs-pills-hint") == []
    assert device_of(page) == {"person": "homer"}


def test_a_shared_device_greets_the_household_and_offers_no_picker(simpsons):
    """Given the household's shared device, when it opens the dashboard,
    then it greets the Simpsons, shows no hint, and tells the family bar
    the device is shared: picking a person there is the later PIN, not a
    tap."""
    page = shared_device().opens("/dashboard")

    assert page.status == 200, page
    assert page.texts("greeting") == ["Welcome home, Simpsons."]
    assert page.texts("fs-pills-hint") == []
    assert page.texts("fs-pill-label") == ["parent", "parent", "child", "child"]
    assert device_of(page) == {"shared": True}


def test_the_macs_address_keeps_the_picker(simpsons):
    """Given the Mac's own browser, when it opens the dashboard, then the
    hint to tap one's name is there and the page names no device: the
    family bar keeps the picker and its memory in this browser."""
    page = on_this_mac().opens("/dashboard")

    assert page.status == 200, page
    assert page.texts("greeting") == ["Welcome home, Simpsons."]
    assert page.texts("fs-pills-hint") == [HINT]
    assert device_of(page) is None


def test_in_german_homers_phone_greets_him_in_german(simpsons):
    """Given Homer's paired phone asking for German, then the served
    greeting names him in German and there is no hint either."""
    page = paired_phone("homer", language="de").opens("/dashboard")

    assert page.texts("greeting") == ["Willkommen zu Hause, Homer."]
    assert page.texts("fs-pills-hint") == []


# ── What the Mac is, as the reader's device says it ─────────────────────

def test_the_home_address_calls_the_server_the_mac_at_home(simpsons):
    """Given Homer's paired phone and the Mac's own browser, when each opens
    the dashboard, then the phone's line under the greeting names "the Mac
    at home" and the Mac's keeps "This Mac": on a phone, "This Mac" would
    name the phone."""
    on_phone = paired_phone("homer").opens("/dashboard")
    on_mac = on_this_mac().opens("/dashboard")

    assert on_phone.texts("lede")[0].startswith("The Mac at home is your family's own server")
    assert on_mac.texts("lede")[0].startswith("This Mac is now your family's own server")


def test_in_german_the_home_address_says_der_mac_zu_hause(simpsons):
    """Given Homer's paired phone asking for German, then the line and the
    chat's how item name "der Mac zu Hause"."""
    page = paired_phone("homer", language="de").opens("/dashboard")

    assert page.texts("lede")[0].startswith("Der Mac zu Hause ist euer Familienserver. Auf ihm läuft der Chat")
    assert "Jede Nachricht geht nur an den Mac zu Hause." in page.text


def test_the_chat_goes_to_the_mac_at_home_on_the_home_address(simpsons):
    """Given Homer's paired phone and the Mac's own browser, then the
    chat's how item says the messages go to "the Mac at home" on the
    phone and to "this Mac" on the Mac."""
    on_phone = paired_phone("homer").opens("/dashboard")
    on_mac = on_this_mac().opens("/dashboard")

    assert "Every message goes to the Mac at home and nowhere else." in on_phone.text
    assert "Every message goes to this Mac and nowhere else." in on_mac.text
    assert "this Mac" not in on_phone.text
