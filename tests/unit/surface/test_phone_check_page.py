"""The phone page: step 4's QR code opens `/go/app` on the phone, which
offers the chat app for that phone and tells the Mac that the phone
reached it.

Step 4 of the setup page, "Chat on your phone", shows a QR code that holds
a famstack address on the Mac's home network, never a store link. The
page behind it offers Element X on Google Play to an Android phone, on
the App Store to an iPhone or iPad, and Element for the computer to
anything else, with the chat server's address and the admin's username,
never a password. Once another device has opened it, part 1 of step 4,
"Your phone reaches famstack", is done on the Mac.

Driven through the tools server's FastAPI app as browsers open it on the
published port (the Browser DSL), read as served, without JavaScript. The
host API is the dashboard's shared stand-in (`tests/dashboard_host.py`)
in a named state, reached at the client's edge.
"""

from __future__ import annotations

import re

import pytest

from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, stack_in_state

from tests.dashboard_host import SIGN_INS

import live_routes  # noqa: E402  (on the path through setup_stand_in)
import phone_reach  # noqa: E402
from access import PUBLIC_PORT  # noqa: E402

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

LAN = "192.168.1.20"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
IPAD = ("Mozilla/5.0 (iPad; CPU OS 17_6 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.6 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
LAPTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/131.0.0.0 Safari/537.36")
PLAY = "play.google.com/store/apps/details?id=io.element.android.x"
APP_STORE = "apps.apple.com/app/id1631335820"
ELEMENT_DOWNLOAD = "element.io/download"


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack in a named state, with core's link base on the
    Mac's home-network address, as core's `.env` renders it. No reach is
    left behind for the next test of the worker."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{LAN}:42000/go")
    phone_reach.forget()
    yield stack_in_state(tmp_path, monkeypatch, httpserver)
    phone_reach.forget()


def device(user_agent: str) -> Browser:
    """A device on the home network, opening the Mac by its address."""
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host=f"{LAN}:42000", user_agent=user_agent)


def links(page: Page) -> list[str]:
    return re.findall(r'<a [^>]*href="([^"]+)"', page.body)


def sign_in(page: Page) -> list[str]:
    """The values of the box "Sign in with", in order."""
    return re.findall(r'<code class="setup-value[^"]*">([^<]*)</code>', page.body)


def step(page: Page) -> Page:
    found = re.search(r'<section class="setup-step".*?</section>', page.body, re.S)
    return Page(200, "text/html", found[0] if found else "")


def guide_in_app_part(page: Page) -> tuple[str, list[str], list[str]]:
    """Part 2 of step 4, "Signed in with the app": the app its sign-in
    steps are for, the kinds of the steps, and the values they give to
    enter, the password aside; ("", [], []) when it has no steps. The
    steps must come after the box "Sign in with"."""
    part = Page(page.status, "", "")
    part._root.children = page._root.find(
        lambda n: n.tag == "li" and n.attrs.get("data-part") == "app")
    ol = part._root.find(lambda n: n.tag == "ol" and "sign-in-steps" in n.classes)
    if not ol:
        return "", [], []
    found = re.search(r'data-part="app".*?(setup-box).*?(sign-in-steps)', page.body, re.S)
    assert found, "the sign-in steps are not under the box \"Sign in with\""
    kinds = [n.attrs.get("data-step", "") for n in ol[0].find(lambda n: "sign-in-step" in n.classes)]
    values = [n.text for n in ol[0].find(
        lambda n: "setup-value" in n.classes and "data-secret" not in n.attrs)]
    return ol[0].attrs.get("data-app", ""), kinds, values


def account_step(page: Page) -> str:
    """The markup of the sign-in steps' account step, "" when there is none."""
    found = re.search(r'<li class="sign-in-step" data-step="account">.*?</li>', page.body, re.S)
    return found[0] if found else ""


def reach(page: Page) -> str:
    """The status of part 1 of step 4, "Your phone reaches famstack"."""
    found = re.search(r'<li class="setup-part" data-part="reach" data-status="(\w+)"', page.body)
    return found[1] if found else ""


# ── The page on the phone ─────────────────────────────────────────────────

@pytest.mark.parametrize("agent, store", [(ANDROID, PLAY), (IPHONE, APP_STORE), (IPAD, APP_STORE)])
def test_a_phone_is_offered_element_x_from_its_own_store_and_the_sign_in(instance, agent, store):
    """Given the chat runs, when a phone opens /go/app at the Mac's
    address, then the page offers Element X from that phone's store and
    no other, and names the chat server on the home network and the
    admin's username. An App Store link does nothing on an Android."""
    instance("chat", mac=LAN)
    page = device(agent).opens("/go/app")

    assert page.status == 200
    assert [link for link in links(page) if store in link]
    assert not [link for link in links(page)
                if (PLAY in link or APP_STORE in link or ELEMENT_DOWNLOAD in link) and store not in link]
    assert sign_in(page) == [f"http://{LAN}:42031", "homer"]


def test_a_laptop_is_offered_element_for_the_computer(instance):
    """Given the chat runs in port mode, when a laptop opens /go/app, then
    it is offered Element for the computer and no phone store. The
    browser is not offered: Element Web refuses plain HTTP outside
    localhost."""
    instance("chat", mac=LAN)
    page = device(LAPTOP).opens("/go/app")

    assert page.status == 200
    assert [link for link in links(page) if ELEMENT_DOWNLOAD in link]
    assert not [link for link in links(page) if PLAY in link or APP_STORE in link]
    assert f"http://{LAN}:42030" not in links(page)
    assert sign_in(page) == [f"http://{LAN}:42031", "homer"]


def test_the_page_shows_no_password_and_needs_no_script(instance):
    """The page is on the public port for any device in the house: it
    never holds the admin's password, and it works in a browser that runs
    no JavaScript."""
    instance("chat", mac=LAN)
    page = device(ANDROID).opens("/go/app")

    assert page.status == 200
    assert "data-secret" not in page.body
    assert "<script" not in page.body


def test_the_other_links_are_unchanged(instance):
    """The persistent links beside it still resolve as before: a document
    link still redirects to Paperless."""
    instance("chat", mac=LAN)
    page = device(ANDROID).opens("/go/docs/42")

    assert page.status in (302, 404)
    assert "Element X" not in page.body


# ── Step 4 on the Mac ─────────────────────────────────────────────────────

def test_step_4_shows_a_qr_code_of_the_phone_page_and_no_store_links(instance):
    """Given the chat runs, step 4 on the Mac shows a QR code that holds
    the phone page's address on the home network; the stores are on that
    page, not here."""
    instance("chat", mac=LAN)
    shown = step(on_this_mac().opens("/setup?step=phone"))

    assert '<svg class="setup-qr-code"' in shown.body
    assert f'aria-label="QR code: http://{LAN}:42000/go/app"' in shown.body
    assert not [link for link in links(shown) if PLAY in link or APP_STORE in link]


def test_opening_the_page_on_a_phone_marks_step_4_as_reached(instance):
    """Given step 4 waits for the phone, when a phone opens /go/app, then
    part 1, "Your phone reaches famstack", is done on the Mac, before any
    app is installed; part 2 still waits for the sign-in."""
    instance("chat", mac=LAN)
    mac = on_this_mac()
    assert reach(mac.opens("/setup?step=phone")) == "waiting"

    device(ANDROID).opens("/go/app")

    assert reach(mac.opens("/setup?step=phone")) == "done"


ELEMENT_X_STEPS = ["install", "open", "server", "warning", "account", "verify", "notifications",
                   "threads"]


def test_once_the_phone_reaches_famstack_step_4_shows_the_sign_in_steps(instance):
    """Given step 4 on the Mac, when a phone opens /go/app, then part 2
    shows the sign-in steps for Element X under the box "Sign in with",
    with the server on the home network and the username, and no store
    button: the admin reads them on the Mac's screen while installing the
    app on the phone. Before a phone reached famstack there are none."""
    instance("chat", mac=LAN)
    mac = on_this_mac()
    assert guide_in_app_part(mac.opens("/setup?step=phone")) == ("", [], [])

    device(IPHONE).opens("/go/app")
    shown = mac.opens("/setup?step=phone")

    assert guide_in_app_part(shown) == ("element-x", ELEMENT_X_STEPS, [f"http://{LAN}:42031", "homer"])
    assert not [link for link in links(step(shown)) if PLAY in link or APP_STORE in link]


def test_on_the_mac_the_account_step_holds_the_password_with_show_and_copy(instance):
    """Given step 4 on the Mac with the phone reached, then the account
    step holds the password as the box above does: a secret the page's
    script masks, with Show and Copy. The admin types it into the phone
    and needs it right under the username."""
    instance("chat", mac=LAN)
    device(ANDROID).opens("/go/app")
    account = account_step(on_this_mac().opens("/setup?step=phone"))

    assert re.findall(r'<code class="setup-value" data-secret>([^<]*)</code>', account) == ["homer"]
    assert "setup-show" in account and "setup-copy" in account


def test_from_another_device_the_steps_hold_no_password(instance):
    """Given the setup page opened from another device, then no password
    in the steps, as in the box: the account step says where it is, on
    the setup page on the Mac, as the guide on the phone does."""
    instance("chat", mac=LAN)
    device(ANDROID).opens("/go/app")
    elsewhere = device(LAPTOP).opens("/setup?step=phone")
    on_phone = device(ANDROID).opens("/go/app/sign-in")

    assert guide_in_app_part(elsewhere)[0] == "element-x"
    assert "data-secret" not in account_step(elsewhere)
    assert step(elsewhere).texts("sign-in-password") == on_phone.texts("sign-in-password")
    assert "data-secret" not in on_phone.body


@pytest.mark.parametrize("answer", ["signed in with the app", "skipped"])
def test_once_the_phone_is_signed_in_or_skipped_the_steps_go(instance, answer):
    """Given a phone reached famstack, once the phone is signed in or the
    admin skipped the step, the sign-in steps go with the rest of the
    waiting state."""
    host = instance("chat", mac=LAN)
    device(ANDROID).opens("/go/app")
    mac = on_this_mac()
    if answer == "skipped":
        mac.opens("/setup?phone=skipped")
    else:
        host.sign_ins["homer"] = [SIGN_INS["phone"]]

    assert guide_in_app_part(mac.opens("/setup?step=phone")) == ("", [], [])


def test_opening_the_page_on_this_mac_proves_nothing(instance):
    """The Mac opening /go/app at localhost does not show that the home
    network carries a phone to it: part 1 still waits."""
    instance("chat", mac=LAN)
    on_this_mac().opens("/go/app")

    assert reach(on_this_mac().opens("/setup?step=phone")) == "waiting"


def test_the_reach_tells_the_macs_page_to_read_again(instance):
    """When a phone opens /go/app, the dashboard's live stream carries a
    `phone.reached` event, which the setup page's script reads again on."""
    instance("chat", mac=LAN)
    start = live_routes.hub.cursor

    device(IPHONE).opens("/go/app")
    status, answer = on_this_mac().reads(f"/dashboard/api/poll?after={start}")

    assert status == 200
    assert [(e["type"], e["subject"]) for e in answer["events"]] == [("phone.reached", "setup/phone")]


def test_the_admins_browser_keeps_the_reach_across_a_restart_of_core(instance):
    """Given a phone reached the Mac and the admin's browser has seen it,
    when core restarts and forgets it, then step 4 still shows it done in
    that browser."""
    instance("chat", mac=LAN)
    mac = on_this_mac()
    device(ANDROID).opens("/go/app")
    mac.opens("/setup?step=phone")

    phone_reach.forget()

    assert reach(mac.opens("/setup?step=phone")) == "done"


def test_while_no_phone_reached_step_4_names_the_likely_causes(instance):
    """Given no phone opened the page, step 4 holds the likely causes
    (guest Wi-Fi, VPN, iOS's Local Network, the Mac's firewall), for the
    page's script to show after a while; once reached, they are gone."""
    instance("chat", mac=LAN)
    waiting = step(on_this_mac().opens("/setup?step=phone"))
    assert len(re.findall(r'<li class="setup-cause"', waiting.body)) == 4

    device(ANDROID).opens("/go/app")

    assert "setup-cause" not in step(on_this_mac().opens("/setup?step=phone")).body
