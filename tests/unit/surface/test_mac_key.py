"""The admin key: the Mac's address gives the Mac's rights only to a
browser that holds it.

The Mac's address (`127.0.0.1:42002`) is published on this Mac only, but
every container of the stack reaches it too, through
`host.docker.internal`, and sends whatever Host header it likes. So the
listener alone cannot say "the admin's browser". The admin key can: a
random key the Mac keeps in `.stack/admin-key`, which only `./stack`, the
menu bar app and the installer hand to a browser, once, in a link. The
browser keeps it in a cookie on `127.0.0.1` and loses it from the
address bar at once.

Driven as a browser and as `./stack` meet it: the tools server's FastAPI
app through the Mac's listener (the Browser DSL), the command through
`stack.cli.main` (`StackCLI`). The host API is the dashboard's stand-in
(`tests/dashboard_host.py`).
"""

from __future__ import annotations

import logging
import os
import re
import stat
from urllib.parse import urlsplit

import pytest

from .conftest import StackCLI, tools_server
from .dsl import Browser
from .setup_stand_in import stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

access = tools_server("access")
server = tools_server("server")
devices = tools_server("devices")

MAC = "127.0.0.1:42002"
# The cookie lasts 400 days, the most a browser keeps one.
FOUR_HUNDRED_DAYS = str(400 * 24 * 3600)


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack with the chat running; returns the host API's
    stand-in, which records what it was asked."""
    devices.ADMIN_KEY_TRIES.forget()
    yield stack_in_state(tmp_path, monkeypatch, httpserver)("chat")
    devices.ADMIN_KEY_TRIES.forget()


@pytest.fixture
def stack(instance, tmp_path, monkeypatch):
    """`./stack` on that instance."""
    return StackCLI(tmp_path / "instance", monkeypatch)


def without_the_key(host: str = MAC) -> Browser:
    """A caller of the Mac's listener that holds no admin key: a container
    through `host.docker.internal`, or a browser on the Mac never given
    the key. It sends `host` as its Host header."""
    return Browser(server.app, listener=access.CARDS_PORT, host=host, admin_key=False)


def path_of(link: str) -> str:
    parts = urlsplit(link)
    return parts.path + (f"?{parts.query}" if parts.query else "")


def admin_link(stack, *args: str) -> dict:
    ran = stack("devices", "admin-link", *args, "--json")
    assert ran.code == 0, ran
    import json
    return json.loads(ran.out)


# ── Without the key, the Mac's listener is a stranger's door ─────────────

@pytest.mark.parametrize("host", ["localhost:42002", MAC])
def test_a_caller_without_the_key_gets_no_password(instance, host):
    """Given a container that reaches the Mac's listener and names the Mac
    in its Host header, when it opens the setup's family step, then the
    answer holds no password: the key, not the listener, opens the Mac's
    pages."""
    page = without_the_key(host).opens("/setup?step=family")

    assert page.status in (307, 401)
    assert "data-secret" not in page.body
    assert "homer" not in page.body.lower()


@pytest.mark.parametrize("host", ["localhost:42002", MAC])
def test_a_caller_without_the_key_changes_nothing(instance, host):
    """Given the same container, when it posts a new family, then it is
    refused and the host API never hears of it."""
    status, _ = without_the_key(host).posts("/dashboard/api/family", {"people": []})

    assert status == 401
    assert instance.asked == []


def test_a_caller_without_the_key_sees_no_device_list(instance):
    """Given the same container, when it opens the Mac's list of devices,
    then it is refused."""
    assert without_the_key().opens("/setup/devices").status == 401


def test_the_health_check_and_the_card_door_answer_without_the_key(instance):
    """Given no key, when core's health check or the card door is asked
    (the `./stack` commands and memory's health check reach it with their
    own write token), then they answer as before."""
    assert without_the_key().reads("/dashboard/api/health") == (200, {"ok": True})
    assert without_the_key().opens("/dashboard/static/dashboard.css").status != 401


# ── The admin's link ─────────────────────────────────────────────────────

def test_the_admin_link_opens_the_page_with_the_macs_rights(stack):
    """Given the admin runs `stack devices admin-link`, when the Mac's
    browser opens the printed link, then it lands on the page it asked
    for without the key in its address, and that page holds the
    passwords the Mac's pages show."""
    link = admin_link(stack, "--page", "setup")
    browser = without_the_key()

    first = browser.opens(path_of(link["link"]))

    assert first.status == 303
    assert first.location == "/setup"
    assert link["key"] not in first.location
    landed = browser.opens(first.location + "?step=phone")
    assert landed.status == 200
    assert "data-secret" in landed.body


def test_the_link_names_the_macs_own_host_and_the_key(stack):
    """Given the admin runs `stack devices admin-link`, then the link is
    on `127.0.0.1` at the Mac's port, and the key it carries is printed on
    its own as well, for the field that takes it."""
    link = admin_link(stack)
    parts = urlsplit(link["link"])

    assert (parts.scheme, parts.hostname) == ("http", "127.0.0.1")
    assert parts.port is not None
    assert f"key={link['key']}" in parts.query
    assert len(link["key"]) >= 43   # 256 random bits, URL-safe base64


def test_the_terminal_shows_the_link_and_the_key_under_it(stack):
    """Given the admin runs `stack devices admin-link` in a terminal, over
    ssh too, then it prints the link, then the key on a line below it,
    and the ssh tunnel that reaches the Mac's address."""
    ran = stack("devices", "admin-link")
    key = admin_link(stack)["key"]

    assert ran.code == 0, ran
    lines = ran.out.splitlines()
    at_link = next(i for i, line in enumerate(lines) if "http://127.0.0.1:" in line)
    assert any(key in line for line in lines[at_link + 1:])
    assert "ssh -L" in ran.out


def test_the_key_stays_the_same_across_runs(stack, tmp_path):
    """Given the key was made, when `admin-link` runs again, then it hands
    out the same key: an open browser keeps its rights."""
    assert admin_link(stack)["key"] == admin_link(stack)["key"]


def test_the_cookie_holds_for_400_days_on_the_macs_host_only(stack):
    """Given the browser opened the admin link, then the key's cookie is
    HttpOnly, SameSite=Strict, for every path, with no Domain, so it stays
    on `127.0.0.1`, and lasts 400 days."""
    first = without_the_key().opens(path_of(admin_link(stack)["link"]))

    cookie = first.headers["set-cookie"]
    flags = {part.strip().split("=", 1)[0].lower(): part.strip().partition("=")[2]
             for part in cookie.split(";")}
    assert "httponly" in flags
    assert flags["samesite"].lower() == "strict"
    assert flags["path"] == "/"
    assert "domain" not in flags
    assert flags["max-age"] == FOUR_HUNDRED_DAYS


def test_the_browser_keeps_the_macs_rights_without_the_key_in_the_address(stack):
    """Given the browser opened the admin link once, when it opens the
    Mac's address again later, then it still has the Mac's rights, and the
    cookie is renewed."""
    browser = without_the_key()
    browser.opens(path_of(admin_link(stack)["link"]))

    later = browser.opens("/setup?step=phone")

    assert later.status == 200
    assert "data-secret" in later.body
    assert "set-cookie" in later.headers


def test_another_name_of_the_mac_is_sent_on_to_its_own_host(instance):
    """Given a browser opens the Mac's address as `localhost:42002`, then
    it is sent on to `127.0.0.1:42002` with the same path, because the
    key's cookie lives there and nowhere else."""
    page = without_the_key("localhost:42002").opens("/setup?step=phone")

    assert page.status == 307
    assert page.location == "http://127.0.0.1:42002/setup?step=phone"


def test_famstack_links_no_other_service_at_the_macs_host(stack):
    """Given the Mac's browser holds the key, when it opens the setup,
    then no link on it names `127.0.0.1` at another port than the Mac's
    address: the services open at `localhost`, so the key's cookie never
    reaches them."""
    browser = without_the_key()
    browser.opens(path_of(admin_link(stack)["link"]))

    page = browser.opens("/setup?step=phone")

    assert set(re.findall(r"//127\.0\.0\.1:(\d+)", page.body)) <= {"42002"}


# ── Without the link: the page that asks for the key ─────────────────────

@pytest.mark.parametrize("language", ["en", "de"])
def test_a_browser_without_the_key_is_told_how_to_get_in(instance, language):
    """Given a browser on the Mac without the key, when it opens the Mac's
    address, then the page names the command that prints the link and
    offers a field for the key. Its form sends the page's own Origin: under
    `no-referrer` a browser sends `Origin: null`, which the write check
    refuses."""
    browser = Browser(server.app, listener=access.CARDS_PORT, host=MAC, admin_key=False,
                      language=language)

    page = browser.opens("/setup")

    assert page.status == 401
    assert "stack devices admin-link" in page.body
    assert re.search(r'<input[^>]+name="key"', page.body)
    assert re.search(r'<form[^>]+method="post"', page.body)
    assert page.headers["referrer-policy"] == "same-origin"


def test_the_pasted_key_opens_the_page(stack):
    """Given the page that asks for the key, when the admin pastes the key
    `admin-link` printed, then the browser lands on the page it asked for
    with the Mac's rights."""
    key = admin_link(stack)["key"]
    browser = without_the_key()

    status, location = browser.sends_form("/admin-key", {"key": key, "next": "/setup?step=phone"})

    assert (status, location) == (303, "/setup?step=phone")
    assert "data-secret" in browser.opens(location).body


def test_wrong_keys_count_against_a_limit(stack):
    """Given someone tries keys, when they have used up the wrong tries,
    then even the right key is refused for a while: guessing does not
    scale."""
    key = admin_link(stack)["key"]
    browser = without_the_key()

    wrong = [browser.sends_form("/admin-key", {"key": f"wrong-{n}", "next": "/setup"})[0]
             for n in range(devices.ADMIN_KEY_TRIES.most)]
    after = browser.sends_form("/admin-key", {"key": key, "next": "/setup"})[0]

    assert set(wrong) == {401}
    assert after == 429


def test_a_next_page_elsewhere_is_not_followed(stack):
    """Given the pasted key, when `next` names another site, then the
    browser lands on the Mac's own setup instead."""
    key = admin_link(stack)["key"]

    status, location = without_the_key().sends_form(
        "/admin-key", {"key": key, "next": "//example.com/x"})

    assert (status, location) == (303, "/setup")


# ── The key on the Mac ───────────────────────────────────────────────────

def test_the_key_is_kept_for_the_owner_alone_and_core_reads_only_its_hash(stack, tmp_path):
    """Given the key was made, then `.stack/admin-key` is readable by the
    Mac's user alone, and the folder core mounts holds its hash, never
    the key."""
    key = admin_link(stack)["key"]
    root = tmp_path / "instance"

    kept = root / ".stack" / "admin-key"
    assert stat.S_IMODE(os.stat(kept).st_mode) == 0o600
    mounted = root / ".stack" / "devices"
    assert all(key not in p.read_text() for p in mounted.iterdir() if p.is_file())
    assert any(devices.hash_of(key) in p.read_text() for p in mounted.iterdir() if p.is_file())


def test_the_key_never_reaches_the_log(caplog):
    """Given a browser opens the admin link, when the server logs the
    request, then the log line holds no key."""
    tools_server("devices.admin_key")
    with caplog.at_level(logging.INFO, logger="uvicorn.access"):
        logging.getLogger("uvicorn.access").info(
            '%s - "%s %s HTTP/%s" %d', "127.0.0.1:50000", "GET",
            "/setup?key=Zq3-secret-key&step=phone", "1.1", 303)

    assert "Zq3-secret-key" not in caplog.text
    assert "/setup" in caplog.text

