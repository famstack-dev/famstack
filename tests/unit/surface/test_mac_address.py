"""The Mac's address: only the address a request came through says whether
it comes from the Mac that runs famstack, never a header it sends.

The tools server has two addresses for browsers. The home address
(`http://<mac>:42000`) is published to the home network, and Docker's port
forwarding shows every request through it alike. The Mac's address
(`http://127.0.0.1:42002`) is published on 127.0.0.1 only, so only a
browser on the Mac reaches it. What only the Mac may see (the family's
passwords, the AI's key field, a setup answer that is a secret) shows on
the Mac's address and nowhere else. A Host header is the caller's own
claim: any device on the Wi-Fi can send `Host: localhost:42000`, so it
decides nothing.

Driven through the tools server's FastAPI app as a browser opens it, on
the listener of each address (the Browser DSL), with the dashboard's
shared stand-in for the host API (`setup_stand_in`).
"""

from __future__ import annotations

import re

import pytest

from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, stack_in_state

from access import PUBLIC_PORT  # noqa: E402  (on the path through setup_stand_in)

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

# Host headers a device on the home network can send to the home address.
# The first three name the Mac itself; a header is the caller's claim.
CLAIMED_HOSTS = ["localhost:42000", "127.0.0.1:42000", "[::1]:42000", "192.168.1.20:42000"]

MAC_SETUP = "http://127.0.0.1:42002/setup"


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """Put the Simpsons' stack in a named state (`setup_stand_in.stack_in_state`)."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)


def through_the_home_address(host: str) -> Browser:
    """A browser that opens the home address and sends `host` as its Host header."""
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host=host)


def step_part(page: Page) -> Page:
    """The setup page's shown step, read on its own."""
    found = re.search(r'<section class="setup-step".*?</section>', page.body, re.S)
    return Page(200, "text/html", found[0] if found else "")


def service_card_of(page: Page, sid: str) -> Page | None:
    """The service card of `sid` on a page, read on its own."""
    found = re.search(rf'<article class="service-card" data-service="{sid}".*?</article>',
                      page.body, re.S)
    return Page(200, "text/html", found[0]) if found else None


# ── The Mac's address shows what only the Mac may see ────────────────────

def test_the_phone_step_holds_the_password_only_on_the_macs_address(instance):
    """Given the chat runs, when the Mac's browser opens the phone step on
    the Mac's address, then the sign-in holds the admin's password; the
    same request through the home address, with the Mac's name in its Host
    header, holds none."""
    instance("chat", mac="192.168.1.20")

    here = step_part(on_this_mac().opens("/setup?step=phone"))
    claimed = step_part(through_the_home_address("localhost:42000").opens("/setup?step=phone"))

    assert "data-secret" in here.body
    assert "data-secret" not in claimed.body


@pytest.mark.parametrize("host", CLAIMED_HOSTS)
def test_no_host_header_brings_a_password_through_the_home_address(instance, host):
    """Given any Host header, a page on the home address holds no password:
    not the setup's steps 3 and 4, not a service card on the dashboard."""
    instance("services", mac="192.168.1.20")
    browser = through_the_home_address(host)

    for path in ("/setup?step=mac", "/setup?step=phone"):
        assert "data-secret" not in step_part(browser.opens(path)).body, path
    card = service_card_of(browser.opens("/dashboard"), "photos")
    assert card is not None and "data-secret" not in card.body


def test_a_service_card_on_the_macs_address_signs_in_the_admin(instance):
    instance("services", mac="192.168.1.20")

    card = service_card_of(on_this_mac().opens("/dashboard"), "photos")

    assert card is not None and "data-secret" in card.body


# ── The home address sends the Mac's browser to the Mac's address ────────

@pytest.mark.parametrize("host", ["localhost:42000", "192.168.1.20:42000"])
def test_the_setup_on_the_home_address_sends_the_browser_to_the_macs_address(instance, host):
    """Given the Mac's browser on an old bookmark of localhost:42000, it is
    the home address like any device's: the setup there says to open
    127.0.0.1:42002 in the Mac's browser."""
    instance("chat", mac="192.168.1.20")

    page = through_the_home_address(host).opens("/setup")

    assert page.status == 403
    assert "Or open 127.0.0.1:42002 in the Mac's browser." in page.texts("only-on-the-mac-how")


def test_the_setup_on_the_macs_address_has_no_link_to_itself(instance):
    instance("chat", mac="192.168.1.20")

    page = on_this_mac().opens("/setup")

    assert MAC_SETUP not in [href for _, href in page.links()]


# ── The Mac's address serves every route ─────────────────────────────────

def test_the_macs_address_serves_the_dashboard_the_setup_and_the_links(instance):
    instance("chat", mac="192.168.1.20")
    mac = on_this_mac()

    assert mac.opens("/dashboard").status == 200
    assert mac.opens("/setup").status == 200
    assert mac.reads("/dashboard/api/stack")[0] == 200
