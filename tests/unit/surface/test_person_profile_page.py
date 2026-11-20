"""A person's profile page: one stable address per person, drawn for whoever looks.

    /dashboard/person/homer              Homer's profile
    /dashboard/me                        one's own profile
    /dashboard/api/person/homer          what the dashboard's panel reads

Each person of the family has a profile page: the header block (name,
relation, "Looks after famstack", the memory link), the browsers that wait
to join, Settings, the person's devices and, on the Mac, "On this Mac".
What it shows depends only on who looks, as the request's paired device
or the Mac's address says (`devices.who`):

    who looks                  own page                   another person's page
    an admin's phone           all, password included     all but the password
    a person's phone           settings, password, devices header block and one line why
    a shared device            (none)                     header block and one line why
    the Mac's address          all but the password       all but the password

The safety rules are the point of these tests: no person's password or
devices show on a page another person's phone opens, the viewer never
comes from the address, the panel's JSON shows no more than the page, and
"Not me" forgets only the device that asks.

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL): phones the Mac paired by the QR code of "Add a phone", a
browser paired for the household, and the Mac's own browser. The host API
is the dashboard's shared stand-in (`tests/dashboard_host.py`), whose
`stack devices` runs for real on its instance. Homer is the family's
admin; Marge, Bart and Lisa are not.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from tests.dashboard_host import PASSWORDS

from .conftest import tools_server
from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, paired_phone, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "192.168.1.20:42000"
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
REMOVE = "/dashboard/api/devices/remove"

devices = tools_server("devices")


@pytest.fixture
def simpsons(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running and core's links on the
    Mac's home-network address, so the Mac can pair a phone. No join
    request is left behind for the next test."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    devices.JOINS.forget()
    devices.NOTICES.forget()
    host = stack_in_state(tmp_path, monkeypatch, httpserver)("chat")
    yield host
    devices.CODES.forget()
    devices.JOINS.forget()


# ── Words of these tests ─────────────────────────────────────────────────

def shared_device() -> Browser:
    """A browser on the home address that the Mac paired for the household."""
    import server
    return Browser(server.app, listener=8001, host=HOME)


def a_waiting_browser() -> Browser:
    """A laptop on the Wi-Fi that opened the home address unknown, and
    now waits to join with a code."""
    import server
    laptop = Browser(server.app, listener=8001, host=HOME, user_agent=MAC_CHROME, paired=False)
    assert laptop.opens("/dashboard").status == 401
    return laptop


def a_device_unused_since(person: str, days: int) -> None:
    """A device of `person` that the Mac paired `days` ago and that has
    not opened famstack since, added by the list's one writer."""
    import os
    from pathlib import Path

    from stack import devices as device_list
    instance = Path(os.environ["DEVICES_FILE"]).parents[2]
    added = device_list.add(instance, person=person, name="iPad", key_hash="0" * 64,
                            now=datetime.now().astimezone() - timedelta(days=days))
    assert added.get("ok"), added


def sections(page: Page) -> list[str]:
    """The ids of the page's sections, in page order."""
    return page.attributes("sect", "id")


def device_rows(page: Page) -> list[str]:
    """The ids of the devices the page lists."""
    return re.findall(r'<li[^>]* data-device="([^"]+)"', page.body)


def passwords_in(page: Page) -> list[str]:
    """Every password of the family that the page carries, in any form."""
    return [p for p in PASSWORDS.values() if p in page.body]


def why(page: Page) -> list[str]:
    """The line that says why the page hides devices and the password."""
    return page.texts("p-why")


def this_device_id(page: Page) -> str:
    found = re.search(r'<li[^>]* data-device="([^"]+)"[^>]* data-this', page.body)
    return found[1] if found else ""


# ── Criterion 2, first: another person's phone sees no device and no password ──

def test_marges_phone_on_homers_page_shows_no_device_and_no_password(simpsons):
    """Given Homer's paired phone and Marge's, when Marge's phone opens
    Homer's profile, then it shows the header block and one line why,
    and no device, no password, no Settings and no waiting browser:
    Marge must not read or remove what is Homer's."""
    paired_phone("homer")
    marges = paired_phone("marge")
    a_waiting_browser()

    page = marges.opens("/dashboard/person/homer")

    assert page.status == 200, page
    assert page.texts("p-name") == ["Homer"]
    assert page.texts("badge"), "Homer looks after famstack"
    assert ("Homer's memory", "/memory/person/homer") in page.links("p-meta")
    assert sections(page) == []
    assert device_rows(page) == []
    assert passwords_in(page) == []
    assert len(why(page)) == 1 and "Homer" in why(page)[0]


def test_a_shared_device_shows_the_header_block_and_the_shared_line(simpsons):
    """Given the household's shared screen, when it opens Bart's profile,
    then it shows his header block and the shared line, no device and no
    password: a shared screen is nobody's own."""
    paired_phone("bart")
    page = shared_device().opens("/dashboard/person/bart")

    assert page.status == 200, page
    assert page.texts("p-name") == ["Bart"]
    assert sections(page) == []
    assert device_rows(page) == []
    assert passwords_in(page) == []
    assert len(why(page)) == 1 and "Bart" not in why(page)[0]


def test_the_viewer_never_comes_from_the_address(simpsons):
    """Given Marge's phone, when it opens Homer's profile and his panel's
    JSON with a query that names Homer or the Mac, then it reads exactly
    what it reads without one: only the device says who looks."""
    paired_phone("homer")
    marges = paired_phone("marge")
    plain = marges.opens("/dashboard/person/homer")
    forged = marges.opens("/dashboard/person/homer?as=homer&viewer=mac&person=homer&admin=1")

    assert sections(forged) == sections(plain) == []
    assert passwords_in(forged) == []
    assert marges.reads("/dashboard/api/person/homer?as=homer&viewer=mac")[1]["devices"] is None


# ── Criterion 1 and 3: one's own page ────────────────────────────────────

def test_homers_own_page_shows_its_sections_in_order_with_his_password(simpsons):
    """Given Homer's paired phone (an admin) and a browser that waits,
    when it opens Homer's profile, then the sections come in the page's
    order (waiting, Settings, devices), Settings holds his email, his own
    password and his word, and his phone is marked as this device."""
    homers = paired_phone("homer")
    a_waiting_browser()

    page = homers.opens("/dashboard/person/homer")

    assert page.status == 200, page
    assert sections(page) == ["waiting", "settings", "devices"]
    assert page.texts("p-relation") == ["You"]
    assert ("Your memory", "/memory/person/homer") in page.links("p-meta")
    settings = page.within("id", "settings")
    assert "homer@home.local" in settings.text
    assert passwords_in(page) == [PASSWORDS["homer"]]
    assert re.search(r'id="password"', page.body)
    assert len(device_rows(page)) == 1 and this_device_id(page) == device_rows(page)[0]
    assert page.headers.get("cache-control") == "no-store"


def test_the_page_loads_its_scripts_and_styles(simpsons):
    """Given Homer's paired phone, when it opens his profile, then every
    script and stylesheet the page names is served, and every module those
    scripts import: the masked password, the allow step, Not me and the
    colours depend on them."""
    homers = paired_phone("homer")
    page = homers.opens("/dashboard/person/homer")
    styles = re.findall(r'<link rel="stylesheet" href="([^"]+)"', page.body)
    imported = [script.rsplit("/", 1)[0] + "/" + name.removeprefix("./")
                for script in page.scripts()
                for name in re.findall(r'^import .* from "(\./[^"]+)";',
                                       homers.opens(script).body, flags=re.M)]

    assert any(s.endswith("/profile.js") for s in page.scripts())
    assert any(s.endswith("/allow-step.js") for s in imported)
    assert any(s.endswith("/profile.css") for s in styles)
    for asset in page.scripts() + imported + styles:
        assert homers.opens(asset).status == 200, asset


def test_a_persons_own_page_has_no_waiting_section(simpsons):
    """Given Marge's phone (not an admin) and a browser that waits, when
    she opens her own profile, then it shows Settings with her password
    and her devices, and no waiting browser: only an admin allows one."""
    marges = paired_phone("marge")
    a_waiting_browser()

    page = marges.opens("/dashboard/person/marge")

    assert sections(page) == ["settings", "devices"]
    assert passwords_in(page) == [PASSWORDS["marge"]]


def test_homers_phone_on_marges_page_shows_her_devices_without_her_password(simpsons):
    """Given Homer's phone (an admin) and Marge's, when Homer opens
    Marge's profile, then it shows the waiting browsers to allow as
    Marge or as a shared device, in two steps, Settings without a password, her devices with Remove and "Add
    Marge's phone"; no password, because a password shows only on its
    person's own device."""
    homers = paired_phone("homer")
    marges = paired_phone("marge")
    a_waiting_browser()

    page = homers.opens("/dashboard/person/marge")

    assert sections(page) == ["waiting", "settings", "devices"]
    assert passwords_in(page) == []
    assert 'id="password"' not in page.body
    assert page.attributes("fs-pill", "data-choose") == ["marge", "shared"]
    assert page.within("id", "waiting").attributes("fs-button", "data-allow-step") == [""]
    assert 'class="allow-step" hidden' in page.body and "data-decline" in page.body
    rows = device_rows(page)
    assert rows == device_rows(marges.opens("/dashboard/person/marge"))
    assert re.findall(r'data-remove="([^"]+)"', page.body) == rows
    assert this_device_id(page) == ""
    assert "/dashboard/devices/add-a-phone?person=marge" in page.body


def test_the_mac_shows_every_section_without_a_password(simpsons):
    """Given the Mac's own browser and a browser that waits, when it
    opens Homer's profile, then it shows waiting, Settings, Homer's
    devices and "On this Mac", and no password (the setup page has them)."""
    paired_phone("homer")
    a_waiting_browser()

    page = on_this_mac().opens("/dashboard/person/homer")

    assert page.status == 200, page
    assert sections(page) == ["waiting", "settings", "devices", "this-mac"]
    assert passwords_in(page) == []
    assert len(device_rows(page)) == 1
    assert "/setup/add-a-phone?person=homer" in page.body


# ── Criterion 4, 5 and 11: the device rows ───────────────────────────────

def test_a_device_not_seen_for_30_days_says_since_when(simpsons):
    """Given a device of Lisa's not seen for 40 days and her phone, when
    she opens her profile, then the old one's row is marked stale and
    names the day it was last seen, and her phone's is not."""
    lisas = paired_phone("lisa")
    a_device_unused_since("lisa", 40)

    page = lisas.opens("/dashboard/person/lisa")

    stale = page.texts("stale")
    assert len(stale) == 1
    day = (datetime.now().astimezone() - timedelta(days=40)).date()
    assert f"{day.day} {day.strftime('%B')}" in stale[0]


def test_not_me_forgets_only_the_device_that_asks(simpsons):
    """Given Homer with two phones, when the first opens his profile and
    says Not me, then its row carries Not me in place of Remove, the
    question names Homer, and the removal forgets that phone and leaves
    the other one his."""
    first = paired_phone("homer")
    second = paired_phone("homer")

    page = first.opens("/dashboard/person/homer")
    this = this_device_id(page)
    assert len(device_rows(page)) == 2 and this
    not_me = re.search(r'<button[^>]* data-remove="' + this + r'"[^>]*>', page.body)[0]
    assert "data-this" in not_me and "Homer" in re.search(r'data-ask="([^"]*)"', not_me)[1]

    assert first.posts(REMOVE, {"id": this})[0] == 200
    assert first.opens("/dashboard").status == 401
    assert second.opens("/dashboard/person/homer").status == 200
    assert this not in device_rows(second.opens("/dashboard/person/homer"))


def test_an_admin_removes_another_persons_device_and_a_person_cannot(simpsons):
    """Given Homer's phone (an admin), Marge's and Bart's, when Bart asks
    to remove Marge's phone he is refused; when Homer asks, it is
    removed: the profile offers Remove to an admin."""
    homers = paired_phone("homer")
    marges = paired_phone("marge")
    barts = paired_phone("bart")
    marges_phone = device_rows(homers.opens("/dashboard/person/marge"))[0]

    assert barts.posts(REMOVE, {"id": marges_phone})[0] == 404
    assert homers.posts(REMOVE, {"id": marges_phone})[0] == 200
    assert marges.opens("/dashboard").status == 401


def test_a_person_without_a_device_says_so(simpsons):
    """Given Bart without a paired device, when Homer's phone opens his
    profile, then the devices section says he has none and lists none."""
    homers = paired_phone("homer")

    page = homers.opens("/dashboard/person/bart")

    assert device_rows(page) == []
    assert page.within("id", "devices").texts("quiet") == ["Bart has no device yet."]


# ── Criterion 7, 8 and 9: the addresses ──────────────────────────────────

def test_me_opens_the_paired_persons_own_page(simpsons):
    """Given Lisa's phone, when it opens /dashboard/me, then it is sent to
    her own profile; a shared screen is sent to the dashboard."""
    assert paired_phone("lisa").opens("/dashboard/me").location == "/dashboard/person/lisa"
    shared = shared_device().opens("/dashboard/me")
    assert shared.status == 303 and shared.location == "/dashboard"


def test_me_on_the_mac_opens_the_person_picked_in_that_browser(simpsons):
    """Given the Mac's own browser, when it opens /dashboard/me, then the
    page takes the person picked in this browser from its script, and
    without a script it links to the dashboard: the pick lives in the
    browser, not on the server."""
    page = on_this_mac().opens("/dashboard/me")
    assert page.status == 200, page
    assert any(src.endswith("/profile.js") for src in page.scripts())
    assert ("Back to the dashboard", "/dashboard") in page.links()


def test_an_unknown_id_is_nobody_here(simpsons):
    """Given Homer's phone, when it opens the profile of nobody in the
    family, then it answers 404 with the page that says so."""
    page = paired_phone("homer").opens("/dashboard/person/ned")
    assert page.status == 404
    assert page.texts("p-name") == ["Nobody here by that name"]


def test_the_sections_carry_their_anchors(simpsons):
    """Given Homer's own page, then #waiting, #settings, #password and
    #devices name its parts, so a link opens the page at that part."""
    homers = paired_phone("homer")
    a_waiting_browser()
    body = homers.opens("/dashboard/person/homer").body
    for anchor in ("waiting", "settings", "password", "devices"):
        assert f'id="{anchor}"' in body


def test_the_old_devices_page_leads_to_ones_own_devices(simpsons):
    """Given Marge's phone, when it opens the old /dashboard/devices, then
    it is sent to /dashboard/me#devices."""
    page = paired_phone("marge").opens("/dashboard/devices")
    assert page.status in (303, 307) and page.location == "/dashboard/me#devices"


# ── Criterion 10: the panel's JSON, by the same rule as the page ─────────

VIEWERS = ["homer", "marge", "shared", "mac"]


def viewer(name: str) -> Browser:
    if name == "shared":
        return shared_device()
    if name == "mac":
        return on_this_mac()
    return paired_phone(name)


@pytest.mark.parametrize("looking", VIEWERS)
@pytest.mark.parametrize("page_of", ["homer", "marge"])
def test_the_page_and_its_json_follow_one_table(simpsons, looking, page_of):
    """Given each viewer of the table and a browser that waits, when it
    opens a profile and the same person's JSON, then both show devices,
    the password and the waiting browsers exactly as the table says, and
    the JSON never carries a password."""
    browser = viewer(looking)
    for person in ("homer", "marge"):
        if person != looking:
            paired_phone(person)
    a_waiting_browser()
    own = looking == page_of
    admin = looking in ("homer", "mac")

    page = browser.opens(f"/dashboard/person/{page_of}")
    status, read = browser.reads(f"/dashboard/api/person/{page_of}")

    shows_devices = own or admin
    assert ("devices" in sections(page)) is shows_devices
    assert (read["devices"] is not None) is shows_devices
    if shows_devices:
        assert sorted(d["id"] for d in read["devices"]) == sorted(device_rows(page))
    assert bool(passwords_in(page)) is (own and looking != "mac")
    assert ("waiting" in sections(page)) is admin
    assert read["waiting"] == (1 if admin else None)
    assert status == 200 and read["id"] == page_of and read["name"] == page_of.title()
    assert not [p for p in PASSWORDS.values() if p in str(read)]


def test_the_json_of_an_unknown_id_is_not_found(simpsons):
    status, read = paired_phone("homer").reads("/dashboard/api/person/ned")
    assert status == 404 and "error" in read
