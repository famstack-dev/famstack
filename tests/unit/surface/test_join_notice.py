"""The Server room hears of every new device, so an admin can allow a
browser from their phone.

A browser that the home address does not know shows a code. When its
person has no paired phone, nobody would know that it waits until an
admin looked at the Mac's Devices page, and a Mac in a cupboard has no
screen. So famstack tells the Server room, where only the admins are,
with a chat message that mentions each admin: "A browser waits to join
famstack: Chrome on a Mac, since 21:14, code K7MP-4XQD. If it belongs to
the family: Allow it". The link opens famstack's own page on an admin's
paired device; the page allows, never the tap. At most one such message
goes out in 10 minutes, whatever the number of browsers or reloads. A
device that was added is told too: "Homer added a device: Chrome on a
Mac, allowed from Homer's iPhone."

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL), as in the join tests. The notice is caught where it leaves
the tools server: the host API's `join_notice`, answered by the
dashboard's stand-in (`tests/dashboard_host.py`), which writes the text
with the real `stack devices notice` code and stands in for the chat:
what it would send lands in `host.posted`. Homer is the family's admin;
Marge, Bart and Lisa are not.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import re
import sys

import pytest

from tests.host_api_tokens import give_tokens

from .conftest import LIB, STACKLETS, tools_server
from .test_host_api_family import WRAPPER, ask, called
from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

HOME = "simpson.local:42000"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:132.0) Gecko/20100101 Firefox/132.0"
UNKNOWN = "This browser does not know you yet"
HEARD = "No paired phone yet? Homer heard in the chat that this browser waits. Tell them the code."
NOT_HEARD = "Show this code to someone who looks after famstack, or open Devices on the Mac."
ONLY_ADMIN = "Only someone who looks after famstack can allow a browser for another person."
NOT_WAITING = "This code is not waiting any more. Show a new one on the new device."
ALLOW = "/dashboard/api/devices/allow"

devices = tools_server("devices")


class Clock:
    """The time the codes and the notices are measured by, moved by a test."""

    def __init__(self) -> None:
        self.now = 1762892040.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    clock = Clock()
    monkeypatch.setattr(devices.JOINS, "clock", clock)
    monkeypatch.setattr(devices.CODES, "clock", clock)
    monkeypatch.setattr(devices.NOTICES, "clock", clock)
    return clock


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver, clock):
    """The Simpsons' stack with core's link base on the Mac's `.local`
    name. No code, request or notice is left behind for the next test."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    for kept in (devices.CODES, devices.JOINS, devices.NOTICES):
        kept.forget()
    enter = stack_in_state(tmp_path, monkeypatch, httpserver)
    # `[core] host` on the same name, so the Mac writes links to the home
    # address that core's `LINK_BASE_URL` names.
    yield lambda state="chat": enter(state, mac="simpson.local")
    for kept in (devices.CODES, devices.JOINS, devices.NOTICES):
        kept.forget()


# ── Words of these tests ─────────────────────────────────────────────────

def browser(user_agent: str = MAC_CHROME) -> Browser:
    """A browser on the Wi-Fi that the home address does not know yet."""
    import server
    return Browser(server.app, listener=8001, host=HOME, user_agent=user_agent,
                   by_name=True, paired=False)


def paired(person: str, user_agent: str = IPHONE) -> Browser:
    """A phone the Mac paired as `person`, by the QR code of "Add a phone"."""
    page = on_this_mac().opens(f"/setup/add-a-phone?person={person}")
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', page.body)
    assert found, page
    device = browser(user_agent)
    assert device.opens(found[1].replace("&amp;", "&")).status == 200
    return device


def waiting(user_agent: str = MAC_CHROME) -> tuple[Browser, Page, str]:
    """A browser that opened the home address unknown: itself, its page
    and the code it shows."""
    laptop = browser(user_agent)
    page = laptop.opens("/dashboard")
    assert page.status == 401, page
    codes = page.texts("join-code")
    assert len(codes) == 1, page
    return laptop, page, codes[0]


def waits_notices(host) -> list[dict]:
    return [p for p in host.posted if "waits to join" in p["text"]]


def chat_link(notice: dict) -> str:
    """The path of the notice's "Allow it" link on the home address."""
    found = re.search(r'<a href="http://' + re.escape(HOME) + r'([^"]+)">Allow it</a>',
                      notice["html"])
    assert found, notice
    return found[1].replace("&amp;", "&")


def who_is(device: Browser) -> str:
    page = device.opens("/dashboard")
    found = re.search(r'<span class="who">([^<]*)</span>', page.body)
    return found[1] if page.status == 200 and found else ""


# ── A browser that waits ─────────────────────────────────────────────────

def test_a_waiting_browser_tells_the_server_room_with_its_code_and_a_link(instance):
    """Given no notice in the last 10 minutes, when a browser starts to
    wait, then the Server room gets one message with the browser, the
    time, the code and an "Allow it" link to famstack's page, which
    mentions each admin; the tools server sent only fields."""
    host = instance()
    _, page, code = waiting()
    [notice] = host.posted
    assert re.fullmatch(
        r"A browser waits to join famstack: Chrome on a Mac, since \d\d:\d\d, code "
        + code + r"\. If it belongs to the family: Allow it \(http://" + re.escape(HOME)
        + r"/dashboard/devices/allow\?code=" + code.replace("-", "") + r"&from=chat\)",
        notice["text"]), notice["text"]
    assert chat_link(notice) == f"/dashboard/devices/allow?code={code.replace('-', '')}&from=chat"
    assert notice["mentions"] == ["homer"]
    [sent] = [a for a in host.asked if a.get("cmd") == "join_notice"]
    assert set(sent) == {"cmd", "kind", "browser", "system", "code", "since"}
    assert (sent["browser"], sent["system"]) == ("Chrome", "Mac")
    assert HEARD in page.text


def test_at_most_one_notice_goes_out_in_10_minutes(instance, clock):
    """Given a notice went out, when the browser reloads twenty times and
    two more browsers wait within 10 minutes, then the Server room has
    still one, and only the first browser, whose code it names, says
    the admins heard of it; the others say how else to get in. A
    browser that starts to wait after 10 minutes makes the next."""
    host = instance()
    laptop, _, first = waiting()
    for _ in range(20):
        assert laptop.opens("/dashboard").status == 401
    waiting(FIREFOX_LINUX)
    _, page, third = waiting(IPHONE)
    [notice] = waits_notices(host)
    assert first in notice["text"] and third not in notice["text"]
    assert HEARD in laptop.opens("/dashboard").text
    assert HEARD not in page.text and NOT_HEARD in page.text
    clock.now += 10 * 60 + 1
    waiting(FIREFOX_LINUX)
    assert len(waits_notices(host)) == 2
    assert "Firefox on Linux" in waits_notices(host)[1]["text"]


def test_without_the_chat_nothing_is_posted_and_the_page_names_nobody(instance):
    """Given the chat does not run, when a browser waits, then nothing is
    posted, and its page leaves out the line about the chat."""
    host = instance("fresh")
    _, page, code = waiting()
    assert host.posted == []
    assert UNKNOWN in page.text and code in page.text
    assert "heard in the chat" not in page.text and NOT_HEARD in page.text


@pytest.fixture
def host_api(tmp_path, monkeypatch):
    """The host API module, with `./stack` the wrapper that runs this
    checkout's CLI on a throwaway instance (as in the family's tests)."""
    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    (root / "stack.toml").write_text(f'[core]\ndata_dir = "{tmp_path / "data"}"\n')
    return api_on(tmp_path, root, monkeypatch)


def api_on(tmp_path, root, monkeypatch):
    """The host API module, loaded from its file, `./stack` the wrapper."""
    path = STACKLETS / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api", str(path))
    spec = importlib.util.spec_from_file_location("famstack_api", path, loader=loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    wrapper = tmp_path / "stack"
    wrapper.write_text(WRAPPER.format(python=sys.executable, lib=str(LIB), instance=str(root),
                                      home=str(tmp_path / "home")))
    wrapper.chmod(0o755)
    monkeypatch.setattr(module, "STACK_BIN", wrapper)
    give_tokens(root)
    monkeypatch.setenv("STACK_DIR", str(root))
    return module


def test_the_host_api_notice_takes_no_text_from_a_browser(host_api, tmp_path):
    """Given a `join_notice` with a browser that is not on the fixed
    list, then `stack devices notice` refuses it and nothing is sent; a
    field that reads as an option never reaches the command line."""
    reply = ask(host_api, {"cmd": "join_notice", "kind": "waits", "browser": "Free money!",
                           "system": "Mac", "code": "K7MP4XQD", "since": "21:14"})
    assert "Chrome, Edge, Firefox, Opera, Safari" in reply["error"], reply
    assert called(tmp_path)["argv"][:3] == ["devices", "notice", "--kind"]
    (tmp_path / "called").unlink()
    reply = ask(host_api, {"cmd": "join_notice", "kind": "waits", "browser": "--json",
                           "code": "K7MP4XQD", "since": "21:14"})
    assert "error" in reply and called(tmp_path) is None


# ── The time a browser asked ─────────────────────────────────────────────

@pytest.mark.parametrize(("zone", "said"), [("America/New_York", "15:14"),
                                            ("Nowhere/Unknown", "20:14")])
def test_the_time_a_browser_asked_is_said_in_the_familys_zone(instance, monkeypatch,
                                                              zone, said):
    """Given the family's time zone and a browser that asked at 20:14 UTC,
    when the Server room hears of it, an admin's phone opens the link and
    the Mac opens Devices, then all three say the time in that zone; a
    zone the system does not know reads as UTC."""
    monkeypatch.setenv("TIMEZONE", zone)
    host = instance()
    _, _, code = waiting()
    [notice] = waits_notices(host)
    assert f"Chrome on a Mac, since {said}, code {code}." in notice["text"], notice["text"]
    allow = paired("homer").opens(chat_link(notice))
    assert f"Chrome on a Mac, waiting since {said}." in allow.text, allow.text
    listed = on_this_mac().opens("/setup/devices")
    assert f"Chrome on a Mac · {code} · since {said}" in listed.text, listed.text


# ── The link's page ──────────────────────────────────────────────────────

def test_the_link_on_an_admins_phone_shows_the_request_and_allows_nothing(instance):
    """Given the Server room's link, when Homer's paired phone opens it,
    then "Add this browser?" shows the browser, the time and the code,
    with "Allow as" the family and Shared; the tap alone allows nothing,
    and Allow then lets the laptop in."""
    host = instance()
    homers = paired("homer")
    laptop, _, code = waiting()
    page = homers.opens(chat_link(waits_notices(host)[0]))
    assert page.status == 200, page
    assert "Add this browser?" in page.text
    assert re.search(r"Chrome on a Mac, waiting since \d\d:\d\d\.", page.text), page.text
    assert code in page.text
    assert re.findall(r'data-allow-as="([^"]*)"', page.body) == [
        "homer", "marge", "bart", "lisa", "shared"]
    assert who_is(laptop) == ""
    assert homers.posts(ALLOW, {"code": code, "as": "bart"})[0] == 200
    assert who_is(laptop) == "Bart"


def test_the_link_on_a_non_admins_device_allows_nothing(instance):
    """Given the link forwarded to Marge, when her paired phone opens it,
    then it says only an admin can allow; a device that is not paired
    gets "This browser does not know you yet"."""
    host = instance()
    marges = paired("marge")
    laptop, _, _ = waiting()
    link = chat_link(waits_notices(host)[0])
    page = marges.opens(link)
    assert page.status == 403 and ONLY_ADMIN in page.text
    assert "Allow" not in re.sub(r"<[^>]+>", " ", page.body).replace(ONLY_ADMIN, "")
    assert UNKNOWN in browser(FIREFOX_LINUX).opens(link).text
    assert who_is(laptop) == ""


def test_the_link_after_the_request_ended_says_it_does_not_wait(instance):
    """Given the request was allowed, when someone opens the link, then
    the code is not waiting any more."""
    host = instance()
    homers = paired("homer")
    _, _, code = waiting()
    link = chat_link(waits_notices(host)[0])
    assert homers.posts(ALLOW, {"code": code, "as": "lisa"})[0] == 200
    page = homers.opens(link)
    assert page.status == 410 and NOT_WAITING in page.text


# ── A device that was added ──────────────────────────────────────────────

def test_a_persons_own_phone_adding_a_laptop_tells_the_server_room(instance):
    """Given Homer's paired phone, when it allows his new laptop, then the
    Server room gets "Homer added a device: Chrome on a Mac, allowed from
    Homer's iPhone.", mentioning each admin, without a link."""
    host = instance()
    homers = paired("homer")
    laptop, _, code = waiting()
    assert homers.posts(ALLOW, {"code": code})[0] == 200
    assert who_is(laptop) == "Homer"
    added = [p for p in host.posted if "added" in p["text"]]
    assert added[-1]["text"] == "Homer added a device: Chrome on a Mac, allowed from Homer's iPhone."
    assert added[-1]["mentions"] == ["homer"]
    assert "<a " not in added[-1]["html"]


def test_with_no_browser_waiting_nothing_is_posted(instance):
    """Resting state: the Mac opens its pages and nothing waits, so the
    Server room gets nothing."""
    host = instance()
    assert on_this_mac().opens("/setup/devices").status == 200
    assert host.posted == []
