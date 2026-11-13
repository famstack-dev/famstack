"""Pairing a phone: the home address opens only for the family's devices,
and the Mac pairs each one with a one-time QR code.

Every device on the Wi-Fi reaches the home address (port 42000), and a
page there cannot tell a phone of the family from a guest's. So any
browser without a device key sees "This browser does not know you yet"
and nothing else. On the Mac's address, "Add a phone" on a person shows a
QR code of a one-time address (`/go/app?pair=<code>`, 10 minutes, once);
the phone that opens it gets a key in a cookie and opens the dashboard,
the card links and its own person's sign-in from then on, until the Mac
removes it.

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL), on the home address by the Mac's `.local` name with its
cookies kept by host, and on the Mac's address. The host API is the
dashboard's shared stand-in (`tests/dashboard_host.py`), whose `stack
devices` runs for real on its instance, so the list of devices is the
file the CLI writes and the tools server reads.
"""

from __future__ import annotations

import json
import re

import pytest

from .conftest import tools_server
from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, stack_in_state

from tests.dashboard_host import FAMILY, PASSWORDS

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "simpson.local:42000"
LAN_IP = "192.168.1.20:42000"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CARD_LINK = "/go/card/note-7f3a9c21e4"
UNKNOWN = "This browser does not know you yet"

devices = tools_server("devices")
access = tools_server("access")


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running and core's link base on
    the Mac's `.local` name. Their people are in the instance's
    users.toml (the stand-in writes it), so `stack devices add` knows
    them. No pairing code is
    left behind for the next test."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    devices.CODES.forget()
    enter = stack_in_state(tmp_path, monkeypatch, httpserver)
    yield lambda state="chat", **kwargs: enter(state, **kwargs)
    devices.CODES.forget()


def phone(user_agent: str = IPHONE, host: str = HOME) -> Browser:
    """A phone on the Wi-Fi, opening the home address by `host`, not paired yet."""
    import server
    return Browser(server.app, listener=access.PUBLIC_PORT, host=host, user_agent=user_agent,
                   by_name=True, paired=False)


def pairing_address(person: str) -> str:
    """The path of the QR code that "Add a phone" on `person` shows on the Mac."""
    page = on_this_mac().opens(f"/setup/add-a-phone?person={person}")
    assert page.status == 200, page
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', page.body)
    assert found, page
    return found[1].replace("&amp;", "&")


def paired_with(address: str, user_agent: str = IPHONE) -> tuple[Browser, Page]:
    """A phone that opened `address`, and the page it got."""
    device = phone(user_agent)
    return device, device.opens(address)


def paired(person: str, user_agent: str = IPHONE) -> tuple[Browser, Page]:
    """A phone that opened the code of `person`, and the page it got."""
    device = phone(user_agent)
    return device, device.opens(pairing_address(person))


def password_shown(page: Page) -> list[str]:
    """The passwords a page shows, masked or not (`service_card.secret_html`)."""
    return re.findall(r"<code[^>]*data-secret[^>]*>([^<]*)</code>", page.body)


def state_on_the_mac(address: str) -> dict:
    code = address.rsplit("pair=", 1)[-1]
    status, answer = on_this_mac().reads(f"/dashboard/api/devices/pairing/{code}")
    assert status == 200
    return answer


# ── A browser that is not paired ─────────────────────────────────────────

def test_a_browser_without_a_key_sees_that_it_is_not_known_and_nothing_else(instance):
    """Given the family, when a browser without a device key opens the
    dashboard, a card link, the family's list or the phone page on the home
    address, then each answers that this browser is not known yet, with
    the family's name, and names no person but the admins who heard in
    the chat that it waits: the home address shows the family nothing
    until the Mac pairs the device."""
    instance()
    guest = phone()
    for path in ("/dashboard", CARD_LINK, "/dashboard/api/family", "/go/app", "/setup"):
        page = guest.opens(path)
        assert page.status == 401, (path, page)
        assert UNKNOWN in page.body, path
        body = re.sub(r"No paired phone yet\? Homer heard in the chat[^<]*", "", page.body)
        assert not any(p["id"] in body.lower() for p in FAMILY["people"]), path
    shown = guest.opens("/dashboard")
    assert "The Simpsons" in shown.text
    assert "127.0.0.1:42002" in shown.text


def test_the_health_check_and_the_macs_address_need_no_pairing(instance):
    """Given no device paired, when core's health check asks the home
    address and the Mac's browser opens the dashboard on the Mac's
    address with the admin key, then both answer as before, and no list
    of devices is written: the resting state."""
    host = instance()
    assert phone().opens("/dashboard/api/health").status == 200
    assert on_this_mac().opens("/dashboard").status == 200
    assert not (host.root / ".stack" / "devices" / "devices.toml").exists()


def test_before_the_family_is_set_up_the_home_address_says_so(instance):
    """Given nobody in the family yet, when a phone opens the home
    address, then it says famstack is being set up on the Mac."""
    instance("fresh", family={**FAMILY, "people": []})
    page = phone().opens("/dashboard")
    assert page.status == 503
    assert "famstack is being set up" in page.text


# ── Pairing ──────────────────────────────────────────────────────────────

def test_a_phone_that_opens_the_macs_code_is_that_persons_from_then_on(instance):
    """Given "Add a phone" on Homer on the Mac, when Homer's phone opens
    the QR code's address, then it is Homer's: the page says so with
    Homer's own username and password, the Mac's page says the iPhone is
    added, and the dashboard and a card link open on it as Homer."""
    instance()
    address = pairing_address("homer")
    assert address.startswith("/go/app?pair=")
    assert state_on_the_mac(address)["state"] == "waiting"

    homers, page = paired_with(address)
    assert page.status == 200
    assert "This phone is now Homer's" in page.text
    assert "homer" in page.text
    assert password_shown(page) == [PASSWORDS["homer"]]
    mac = state_on_the_mac(address)
    assert mac["state"] == "added" and mac["line"] == "Homer's iPhone is added."

    dashboard = homers.opens("/dashboard")
    assert dashboard.status == 200
    assert re.search(r'<span class="who">Homer</span>', dashboard.body)
    assert homers.opens(CARD_LINK).location == "/card/note-7f3a9c21e4"


WRITE_DOWN = "Write this password down"


def test_the_first_sight_of_the_password_says_to_write_it_down(instance):
    """Given Homer's phone was just paired, the page that shows Homer's
    password for the first time says to write it down, because it is the
    famstack password for the chat, photos and documents. The same
    password on a later visit, and on the dashboard, says nothing more."""
    instance()
    homers, first = paired_with(pairing_address("homer"))
    later = homers.opens("/go/app/sign-in")

    assert WRITE_DOWN in first.text
    assert password_shown(later) == [PASSWORDS["homer"]] and WRITE_DOWN not in later.text
    assert WRITE_DOWN not in homers.opens("/dashboard").text


def test_a_code_works_once(instance):
    """Given Homer's phone opened the code, when another phone opens the
    same address, then the code has expired for it, and it stays unknown."""
    instance()
    address = pairing_address("homer")
    paired_with(address)
    other, page = paired_with(address, ANDROID)
    assert page.status == 410
    assert "This code has expired" in page.text
    assert other.opens("/dashboard").status == 401


def test_a_code_expires_after_ten_minutes(instance, monkeypatch):
    """Given a code shown on the Mac 10 minutes ago, when a phone opens
    it, then it has expired on the phone and on the Mac."""
    instance()
    address = pairing_address("homer")
    made = devices.CODES.clock()
    monkeypatch.setattr(devices.CODES, "clock", lambda: made + 10 * 60)
    _, page = paired_with(address)
    assert page.status == 410
    assert state_on_the_mac(address)["state"] == "expired"


def test_a_code_is_made_only_on_the_macs_address_or_an_admins_device(instance):
    """Given Marge's paired phone (not an admin), when it asks for the
    page "Add a phone" or a code's state, then the home address refuses:
    only whoever sits at the Mac, or an admin's paired device, pairs a
    device for another person (`test_join_a_device.py` has the admin's)."""
    instance()
    marges, _ = paired("marge")
    assert marges.opens("/setup/add-a-phone?person=bart").status == 404
    assert marges.opens("/dashboard/devices/add-a-phone?person=bart").status == 403
    assert marges.opens("/dashboard/api/devices/pairing/anything").status == 403


def test_the_setup_phone_step_pairs_the_admins_own_phone(instance):
    """Given the setup's phone step on the Mac, when the admin's phone
    opens its QR code, then the phone is the admin's (Homer's)."""
    instance()
    step = on_this_mac().opens("/setup?step=phone")
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', step.body)
    assert found and "pair=" in found[1], step
    _, page = paired_with(found[1].replace("&amp;", "&"))
    assert "This phone is now Homer's" in page.text


# ── Passwords ────────────────────────────────────────────────────────────

def test_a_device_shows_only_its_own_persons_password(instance):
    """Given Homer's and Marge's paired phones, a shared device and a
    browser that is not paired, when each opens the chat's sign-in guide,
    then Homer's shows Homer's password, Marge's shows Marge's and not
    Homer's, and the shared device and the unpaired browser show none:
    a password is for its own person's device only."""
    instance()
    homers, _ = paired("homer")
    marges, _ = paired("marge", ANDROID)
    shared = phone(MAC_CHROME)
    jump = on_this_mac().opens("/setup/this-browser?as=shared")
    shared.opens(jump.location.split(HOME, 1)[1])

    assert password_shown(homers.opens("/go/app/sign-in")) == [PASSWORDS["homer"]]
    assert password_shown(marges.opens("/go/app/sign-in")) == [PASSWORDS["marge"]]
    on_shared = shared.opens("/go/app/sign-in")
    assert on_shared.status == 200 and password_shown(on_shared) == []
    stranger = phone().opens("/go/app/sign-in")
    assert stranger.status == 401 and password_shown(stranger) == []


# ── The Mac's Devices ────────────────────────────────────────────────────

def test_the_mac_lists_each_device_and_a_removed_one_opens_nothing(instance):
    """Given Homer's paired phone, when the Mac's Devices page lists it and
    the admin removes it, then Homer's phone's next page is that the
    browser is not known."""
    instance()
    assert "No phone or laptop is added yet" in on_this_mac().opens("/setup/devices").text
    homers, _ = paired("homer")
    listed = on_this_mac().opens("/setup/devices")
    assert "Homer · iPhone" in listed.text
    device_id = re.search(r'data-remove="([^"]+)"', listed.body)[1]

    status, answer = on_this_mac().posts("/dashboard/api/devices/remove", {"id": device_id})
    assert status == 200 and answer["removed"]["person"] == "homer"
    assert UNKNOWN in homers.opens("/dashboard").text


def test_the_macs_browser_pairs_itself_for_the_family_pages(instance):
    """Given the Mac's Devices page, when the admin chooses "Use this
    browser for the family's pages" as Marge, then the browser opens the
    home address paired, as Marge."""
    instance()
    jump = on_this_mac().opens("/setup/this-browser?as=marge")
    assert jump.status == 303 and jump.location.startswith(f"http://{HOME}/dashboard?pair=")
    browser = phone(MAC_CHROME)
    page = browser.opens(jump.location.split(HOME, 1)[1])
    assert page.status == 200
    assert '<span class="who">Marge</span>' in page.body


# ── One host in every link ───────────────────────────────────────────────

def test_a_phone_is_paired_for_the_link_bases_host_only(instance):
    """Given a phone paired on the home address's `.local` name, when it
    opens a chat link on that name, then it opens as paired; on the
    Mac's IP address, the same phone is not known, and the page names the
    address that knows it: a browser sends its key only to the host that
    set it."""
    instance()
    homers, _ = paired("homer")
    assert homers.opens(CARD_LINK).status == 302
    by_ip = homers.at(LAN_IP).opens(CARD_LINK)
    assert by_ip.status == 401
    assert HOME in by_ip.text


# ── The writer ───────────────────────────────────────────────────────────

def test_a_person_who_leaves_the_family_loses_their_devices(stack_cli):
    """Given Marge's paired phone, when `stack family set` writes the
    family without Marge, then `stack devices list` no longer has her
    phone, and Homer's stays."""
    for person in ("homer", "marge"):
        ran = stack_cli("devices", "add", "--person", person, "--name", "iPhone",
                        "--key-hash", ("a" if person == "homer" else "b") * 64, "--json")
        assert ran.code == 0, ran
    staying = {"name": "Simpson", "people": [
        {"name": "Homer Simpson", "relation": "parent", "words": "he", "looks_after": True}]}
    ran = stack_cli("family", "set", "--from", "-", "--json", stdin=json.dumps(staying))
    assert ran.code == 0, ran
    assert [d["person"] for d in stack_cli("devices", "--json").answer["devices"]] == ["homer"]
