"""Joining by a code: a browser the home address does not know shows a
short code, and a paired device, an admin's device or the Mac allows it.

A laptop or a screen at home has no camera to read the Mac's QR code, and
a Mac in a cupboard has no screen to show one. So a browser without a
device key shows a short code ("K7MP-4XQD") and the same code as a QR
code, as a TV does when it signs in (the device flow of RFC 8628). A
person's paired phone allows it for that person; an admin's paired device
or the Mac allows it for anyone, or as the household's shared device. The
code only names the request: the waiting browser holds a secret of its
own in a cookie, and the device key goes only to that browser, so whoever
reads the code over a shoulder gets nothing.

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL): every phone and laptop on the home address by the Mac's
`.local` name, with its cookies kept by host, and the Mac on its own
address. The host API is the dashboard's shared stand-in
(`tests/dashboard_host.py`), whose `stack devices` runs for real on its
instance. Homer is the family's admin; Marge, Bart and Lisa are not.
"""

from __future__ import annotations

import re

import pytest

from .conftest import tools_server
from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "simpson.local:42000"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
UNKNOWN = "This browser does not know you yet"
# The code as a person reads it: 8 letters and digits in two groups.
CODE = re.compile(r"^[A-Z2-9]{4}-[A-Z2-9]{4}$")
ALLOW = "/dashboard/api/devices/allow"
JOIN_STATE = "/dashboard/api/devices/join"

devices = tools_server("devices")


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running and core's link base on
    the Mac's `.local` name. No pairing code and no join request is left
    behind for the next test."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    devices.CODES.forget()
    devices.JOINS.forget()
    devices.NOTICES.forget()
    enter = stack_in_state(tmp_path, monkeypatch, httpserver)
    yield lambda state="chat", **kwargs: enter(state, **kwargs)
    devices.CODES.forget()
    devices.JOINS.forget()


# ── Words of these tests ─────────────────────────────────────────────────

def browser(user_agent: str = MAC_CHROME, language: str = "en") -> Browser:
    """A browser on the Wi-Fi that the home address does not know yet."""
    import server
    return Browser(server.app, listener=8001, host=HOME, user_agent=user_agent, language=language,
                   by_name=True, paired=False)


def paired(person: str, user_agent: str = IPHONE) -> Browser:
    """A phone the Mac paired as `person`, by the QR code of "Add a phone"."""
    page = on_this_mac().opens(f"/setup/add-a-phone?person={person}")
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', page.body)
    assert found, page
    device = browser(user_agent)
    assert device.opens(found[1].replace("&amp;", "&")).status == 200
    return device


def waiting(user_agent: str = MAC_CHROME) -> tuple[Browser, str, str]:
    """A browser that opened the home address unknown: itself, the code it
    shows, and the path its QR code holds."""
    laptop = browser(user_agent)
    page = laptop.opens("/dashboard")
    assert page.status == 401, page
    return laptop, shown_code(page), qr_path(page)


def shown_code(page: Page) -> str:
    codes = page.texts("join-code")
    assert len(codes) == 1, page
    return codes[0]


def qr_path(page: Page) -> str:
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', page.body)
    assert found, page
    return found[1].replace("&amp;", "&")


def who_is(device: Browser) -> str:
    """The name the dashboard's top bar shows on `device`, "" when it does not open."""
    page = device.opens("/dashboard")
    found = re.search(r'<span class="who">([^<]*)</span>', page.body)
    return found[1] if page.status == 200 and found else ""


def password_shown(page: Page) -> list[str]:
    return re.findall(r"<code[^>]*data-secret[^>]*>([^<]*)</code>", page.body)


def device_names(person: str) -> list[str]:
    """`stack devices`' list, by the Mac's Devices page: the rows of `person`."""
    listed = on_this_mac().opens("/setup/devices")
    return [row.split(" · ", 1)[1] for row in re.findall(r"<b>([^<]+)</b>", listed.body)
            if row.startswith(person + " · ")]


# ── A browser the home address does not know ─────────────────────────────

def test_an_unknown_browser_shows_a_short_code_and_its_qr_code(instance):
    """Given the family, when a browser without a key opens the home
    address, then it shows a code of 8 letters and digits, a QR code of
    the page that allows it, and the same code again after a reload: the
    code names this browser's one request."""
    instance()
    laptop = browser()
    page = laptop.opens("/dashboard")
    assert page.status == 401
    assert UNKNOWN in page.text
    code = shown_code(page)
    assert CODE.match(code), code
    assert qr_path(page) == f"/dashboard/devices/allow?code={code.replace('-', '')}"
    assert shown_code(laptop.opens("/go/app")) == code
    assert shown_code(browser().opens("/dashboard")) != code


def test_a_page_script_asking_for_the_state_gets_waiting(instance):
    """Given a browser that shows its code, when its page asks for the
    request's state, then it is waiting; a browser with no request has
    none to wait for."""
    instance()
    laptop, _, _ = waiting()
    assert laptop.reads(JOIN_STATE) == (200, {"state": "waiting"})
    assert browser().reads(JOIN_STATE) == (200, {"state": "expired"})


# ── Allowing it ──────────────────────────────────────────────────────────

def test_homers_phone_allows_a_browser_by_its_code_and_only_that_browser_gets_in(instance):
    """Given a laptop that shows its code, and someone else's browser that
    read the code too, when Homer's paired phone reads the QR code and
    allows it, then the laptop opens the dashboard as Homer, and the other
    browser is still unknown: the key goes only to the browser that asked."""
    instance()
    homers = paired("homer")
    laptop, code, qr = waiting()
    onlooker = browser(FIREFOX_LINUX)
    assert onlooker.opens(qr).status == 401

    asked = homers.opens(qr)
    assert asked.status == 200, asked
    assert "Chrome on a Mac" in asked.text
    status, answer = homers.posts(ALLOW, {"code": code, "as": "homer"})
    assert status == 200, answer
    assert answer["person"] == "homer" and answer["device"] == "Chrome on a Mac"

    assert laptop.reads(JOIN_STATE) == (200, {"state": "allowed"})
    assert who_is(laptop) == "Homer"
    assert onlooker.opens("/dashboard").status == 401
    assert onlooker.reads(JOIN_STATE)[1] != {"state": "allowed"}
    assert device_names("Homer") == ["iPhone", "Chrome on a Mac"]


def test_a_person_types_the_code_on_add_a_device(instance):
    """Given Marge's paired phone, when she opens "Add a device" and sends
    the code as she typed it (lower case, no dash), then the laptop is
    Marge's: the person's own device allows for that person."""
    instance()
    marges = paired("marge", ANDROID)
    form = marges.opens("/dashboard/devices/add")
    assert form.status == 200 and 'name="code"' in form.body
    assert re.search(r'<input [^>]*name="code"[^>]*autofocus', form.body), "the cursor is not in the code"
    laptop, code, _ = waiting()
    status, answer = marges.posts(ALLOW, {"code": code.replace("-", "").lower()})
    assert status == 200, answer
    assert answer["person"] == "marge"
    assert who_is(laptop) == "Marge"


def test_a_code_allows_once(instance):
    """Given a laptop Homer allowed, when the code is sent again, then it
    is not waiting any more."""
    instance()
    homers = paired("homer")
    _, code, _ = waiting()
    assert homers.posts(ALLOW, {"code": code})[0] == 200
    status, answer = homers.posts(ALLOW, {"code": code})
    assert status == 410 and "error" in answer


def test_only_an_admin_allows_for_another_person_or_as_shared(instance):
    """Given Marge's paired phone (not an admin), when she allows a laptop
    as Bart's or as shared, then both are refused and the laptop keeps
    waiting; a person allows only for themselves."""
    instance()
    marges = paired("marge", ANDROID)
    laptop, code, qr = waiting()
    assert marges.posts(ALLOW, {"code": code, "as": "bart"})[0] == 403
    assert marges.posts(ALLOW, {"code": code, "as": "shared", "name": "Living room"})[0] == 403
    assert laptop.reads(JOIN_STATE)[1] == {"state": "waiting"}
    asked = marges.opens(qr)
    assert "Add this browser as Marge's?" in asked.text
    assert "allow-as" not in asked.body


def test_an_admins_phone_allows_for_anyone(instance):
    """Given Homer's paired phone (an admin), when the QR code of Bart's
    laptop opens there, then the page offers the family and Shared, and
    allowed as Bart the laptop opens as Bart."""
    instance()
    homers = paired("homer")
    laptop, code, qr = waiting()
    asked = homers.opens(qr)
    assert asked.status == 200
    offered = re.findall(r'data-allow-as="([^"]*)"', asked.body)
    assert offered == ["homer", "marge", "bart", "lisa", "shared"]
    assert homers.posts(ALLOW, {"code": code, "as": "bart"})[1]["person"] == "bart"
    assert who_is(laptop) == "Bart"


def test_a_shared_device_reads_as_the_household_and_shows_no_password(instance):
    """Given the living room's screen waiting, when Homer allows it as
    shared and names it "Living room", then its top bar says Living room,
    its sign-in guide shows no password, the service cards say where each
    person's sign-in is, and the screen itself cannot allow a device."""
    instance()
    homers = paired("homer")
    screen, code, _ = waiting(FIREFOX_LINUX)
    status, answer = homers.posts(ALLOW, {"code": code, "as": "shared", "name": "Living room"})
    assert status == 200, answer
    assert answer["person"] == "" and answer["device"] == "Living room"
    assert who_is(screen) == "Living room"
    guide = screen.opens("/go/app/sign-in")
    assert guide.status == 200 and password_shown(guide) == []
    cards = screen.reads("/dashboard/api/stack")[1]["cards_html"]
    assert "service-card-shared" in cards
    _, other, _ = waiting()
    assert screen.posts(ALLOW, {"code": other})[0] == 403


def test_the_mac_lists_a_waiting_browser_and_allows_it(instance):
    """Given a laptop that waits, when the Mac's Devices page lists it with
    its code and the Mac allows it as Lisa, then the laptop opens as Lisa
    and the list has Lisa's laptop."""
    instance()
    laptop, code, _ = waiting()
    listed = on_this_mac().opens("/setup/devices")
    assert re.search(r"Chrome on a Mac · " + code + r" · since \d\d:\d\d", listed.text), listed
    assert "lisa" in re.findall(r'data-allow-as="([^"]*)"', listed.body)
    status, answer = on_this_mac().posts(ALLOW, {"code": code, "as": "lisa"})
    assert status == 200, answer
    assert who_is(laptop) == "Lisa"
    assert device_names("Lisa") == ["Chrome on a Mac"]
    assert code not in on_this_mac().opens("/setup/devices").text


def test_not_me_ends_the_request(instance):
    """Given a laptop that waits, when Homer's phone answers "Not me",
    then the laptop's code has ended and nobody can allow it."""
    instance()
    homers = paired("homer")
    laptop, code, _ = waiting()
    assert homers.posts("/dashboard/api/devices/decline", {"code": code})[0] == 200
    assert laptop.reads(JOIN_STATE)[1] == {"state": "expired"}
    assert homers.posts(ALLOW, {"code": code})[0] == 410


def test_not_me_from_a_person_who_is_not_an_admin_leaves_the_request_waiting(instance):
    """Given a laptop that waits, when Marge's phone (not an admin)
    answers "Not me" eleven times, then the laptop still waits, she is
    told nothing changed, and no wrong code was counted: she can still
    allow it for herself. A request names no person yet, so only an
    admin's device or the Mac may end the one an admin may be about to
    allow for someone else."""
    instance()
    marges = paired("marge", ANDROID)
    laptop, code, _ = waiting()
    for _ in range(11):
        status, answer = marges.posts("/dashboard/api/devices/decline", {"code": code})
        assert status == 200 and answer["declined"] is False and answer["line"], answer
    assert laptop.reads(JOIN_STATE)[1] == {"state": "waiting"}
    assert marges.posts(ALLOW, {"code": code})[0] == 200
    assert who_is(laptop) == "Marge"


# ── Codes that do not count ──────────────────────────────────────────────

def test_a_wrong_code_is_not_waiting_and_ten_in_a_minute_wait(instance, monkeypatch):
    """Given Homer's phone, when it sends a code nobody shows, then that
    code is not waiting; after ten wrong codes in a minute even a right
    one waits a minute, and a minute later it works."""
    instance()
    homers = paired("homer")
    laptop, code, _ = waiting()
    status, answer = homers.posts(ALLOW, {"code": "AAAA-AAAA"})
    assert status == 410 and "error" in answer
    for _ in range(9):
        homers.posts(ALLOW, {"code": "AAAA-AAAA"})
    assert homers.posts(ALLOW, {"code": code})[0] == 429
    page = homers.opens(f"/dashboard/devices/allow?code={code}")
    assert page.status == 429
    now = devices.JOINS.clock()
    monkeypatch.setattr(devices.JOINS, "clock", lambda: now + 61)
    assert homers.posts(ALLOW, {"code": code})[0] == 200
    assert who_is(laptop) == "Homer"


def test_a_request_ends_after_ten_minutes_and_leaves_nothing(instance, monkeypatch):
    """Given a laptop that has waited 10 minutes, then its page's state is
    expired, its code is not waiting on Homer's phone, the Mac lists no
    waiting browser, and the list of devices has only Homer's phone: the
    resting state."""
    instance()
    homers = paired("homer")
    laptop, code, _ = waiting()
    made = devices.JOINS.clock()
    monkeypatch.setattr(devices.JOINS, "clock", lambda: made + 10 * 60)
    assert laptop.reads(JOIN_STATE)[1] == {"state": "expired"}
    assert homers.posts(ALLOW, {"code": code})[0] == 410
    assert code not in on_this_mac().opens("/setup/devices").text
    assert device_names("Homer") == ["iPhone"]


def test_a_restart_forgets_open_requests_and_keeps_paired_devices(instance):
    """Given a laptop that waits and Homer's paired phone, when core
    restarts, then the laptop's request has ended and Homer's phone is
    still Homer's."""
    instance()
    homers = paired("homer")
    laptop, _, _ = waiting()
    devices.JOINS.forget()
    assert laptop.reads(JOIN_STATE)[1] == {"state": "expired"}
    assert who_is(homers) == "Homer"


# ── Your devices ─────────────────────────────────────────────────────────

def test_a_person_sees_and_removes_their_own_devices(instance):
    """Given Homer's phone and a laptop he allowed, and Marge's phone,
    when Homer's laptop opens "Your devices", then it lists Homer's two
    devices only, marks itself, and removing itself leaves it unknown,
    with the note that it was removed; Homer cannot remove Marge's."""
    instance()
    homers = paired("homer")
    marges = paired("marge", ANDROID)
    laptop, code, _ = waiting()
    homers.posts(ALLOW, {"code": code})
    mine = laptop.opens("/dashboard/devices")
    assert mine.status == 200
    rows = re.findall(r'data-device="([^"]+)"', mine.body)
    assert len(rows) == 2
    this = re.search(r'data-device="([^"]+)" data-this', mine.body)
    assert this, mine
    marges_id = re.search(r'data-device="([^"]+)"', marges.opens("/dashboard/devices").body)[1]
    assert marges_id not in rows
    assert homers.posts("/dashboard/api/devices/remove", {"id": marges_id})[0] == 404

    status, _ = laptop.posts("/dashboard/api/devices/remove", {"id": this[1]})
    assert status == 200
    gone = laptop.opens("/dashboard?removed=1")
    assert gone.status == 401 and "This device is removed." in gone.text
    assert who_is(homers) == "Homer"


def test_an_admins_phone_shows_add_a_phone_for_another_person(instance):
    """Given Homer's paired phone (an admin), when he chooses "Add a
    phone" on Marge, then it shows the Mac's screen for Marge's phone, and
    the phone that opens its code is Marge's; Marge's own phone offers no
    "Add a phone" for anyone and is refused it."""
    instance()
    homers = paired("homer")
    family = homers.opens("/dashboard/devices")
    assert "/dashboard/devices/add-a-phone?person=marge" in family.body
    add = homers.opens("/dashboard/devices/add-a-phone?person=marge")
    assert add.status == 200 and "Add Marge's phone" in add.text
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', add.body)
    marges = browser(ANDROID)
    assert marges.opens(found[1].replace("&amp;", "&")).status == 200
    assert who_is(marges) == "Marge"
    assert "add-a-phone" not in marges.opens("/dashboard/devices").body
    assert marges.opens("/dashboard/devices/add-a-phone?person=bart").status == 403


# ── German ───────────────────────────────────────────────────────────────

def test_the_code_page_speaks_german(instance):
    """Given a browser that asks for German, then the page for an unknown
    browser is German and shows the code the same way."""
    instance()
    page = browser(language="de").opens("/dashboard")
    assert "Dieser Browser kennt dich noch nicht" in page.text
    assert CODE.match(shown_code(page))
