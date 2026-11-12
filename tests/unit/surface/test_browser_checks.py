"""What a browser may do with core's tools server, beyond which listener it uses.

The listener decides who a request is (`access.py`); the browser checks only
refuse. Each listener answers only the names it is reached by, so a page on
another site whose name resolves to the Mac (DNS rebinding) reaches nothing.
A write that a page of another site sends is refused by its Origin, before
any route runs. Every page of core comes with a Content Security Policy,
`frame-ancestors 'none'` and `nosniff`, and still loads no script the policy
would block. The cookies core sets are `HttpOnly` and `SameSite=Lax`, and
`Secure` over HTTPS. Open event streams and polls are capped, so the `/go`
links and core's health check always find a worker thread.

Driven through the tools server's FastAPI app, one `TestClient` per listener
(the Browser DSL), as each browser reaches it. The host API is a stand-in at
its edge, `stack.live.client`, noting every request.
"""

from __future__ import annotations

import contextlib
import re
import sys

import pytest

from .conftest import LIB, STACKLETS
from .dsl import Browser
from .setup_stand_in import stack_in_state
from .stack_listing import FAMILY

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import CARDS_PORT, INTERNAL_PORT, PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402
from stack.live.hub import Hub  # noqa: E402
from stack.live.jobs import Jobs  # noqa: E402

# The home address as a phone on the Wi-Fi opens it, and as the Mac's own
# browser and core's health check open it.
HOME_NAMES = ["192.168.1.20:42000", "localhost:42000", "127.0.0.1:42000"]
MAC_NAMES = ["localhost:42002", "127.0.0.1:42002", "[::1]:42002"]
# A name on the internet that its owner points at the Mac's address.
REBOUND = "rebind.example"


class HostAPI:
    """The host API at its edge, noting every request: a family set
    returns the family; anything else does not answer."""

    def __init__(self):
        self.asked: list[dict] = []

    def call(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        if request.get("cmd") == "family":
            return FAMILY
        return {"error": "not stood in"}


@pytest.fixture
def api(monkeypatch) -> HostAPI:
    import live_routes

    stand_in = HostAPI()
    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
    monkeypatch.setattr(live_routes, "HEARTBEAT_SECS", 0.1)
    monkeypatch.setattr(live_routes, "POLL_WAIT_SECS", 0.1)
    monkeypatch.setattr(client, "call", stand_in.call)
    return stand_in


def browser(listener: int, host: str) -> Browser:
    import server
    return Browser(server.app, listener=listener, host=host)


def opens(listener: int, host: str, path: str, **headers):
    """The raw answer to a GET of `path`, for its headers and cookies."""
    b = browser(listener, host)
    return b.client.get(path, headers={"host": host, **headers}, follow_redirects=False)


# ── Host: each listener answers only its own names ───────────────────────

@pytest.mark.parametrize("listener,port", [(PUBLIC_PORT, 42000), (CARDS_PORT, 42002),
                                           (INTERNAL_PORT, 8000)])
@pytest.mark.parametrize("path", ["/dashboard", "/dashboard/api/family", "/go/app"])
def test_a_page_reached_by_a_name_of_another_site_is_refused_before_any_route_runs(
        api, listener, port, path):
    """Given a page on rebind.example, whose name now resolves to the Mac,
    when it reads the tools server through its own name, then nothing
    answers but a refusal, and the host API hears of nothing: the page
    cannot read the family's dashboard as if it were on the home network."""
    status, _ = browser(listener, f"{REBOUND}:{port}").reads(path)

    assert status == 400
    assert api.asked == []


@pytest.mark.parametrize("host", HOME_NAMES)
def test_the_home_address_answers_by_the_macs_address_and_localhost(api, host):
    """Core's health check and the Mac's browser use localhost:42000; a
    phone uses the Mac's address on the home network."""
    assert browser(PUBLIC_PORT, host).reads("/dashboard/api/health") == (200, {"ok": True})


def test_the_home_address_answers_by_the_name_core_links_to(api, monkeypatch):
    """Given domain mode, where core's links name the family's domain,
    then the home address answers by that name."""
    monkeypatch.setenv("LINK_BASE_URL", "https://simpson.family/go")

    assert browser(PUBLIC_PORT, "simpson.family").reads("/dashboard/api/health")[0] == 200


def test_the_home_address_answers_by_the_macs_name_on_the_home_network(api):
    """A Mac's name on the home network (Bonjour, `.local`) is answered only
    there, so no site on the internet can point it at the Mac."""
    assert browser(PUBLIC_PORT, "simpsons-mac.local:42000").reads("/dashboard/api/health")[0] == 200


@pytest.mark.parametrize("host", MAC_NAMES)
def test_the_macs_address_answers_by_its_loopback_names(api, host):
    assert browser(CARDS_PORT, host).reads("/dashboard/api/health") == (200, {"ok": True})


def test_the_macs_address_does_not_answer_by_the_home_networks_address(api):
    """The Mac's address is published on 127.0.0.1 only; a request that
    names another address came through a name somebody else chose."""
    assert browser(CARDS_PORT, "192.168.1.20:42002").reads("/dashboard/api/health")[0] == 400


def test_the_internal_listener_answers_by_the_containers_name(api):
    """Open WebUI's tool calls reach core as stack-core-tools:8000."""
    assert browser(INTERNAL_PORT, "stack-core-tools:8000").reads("/dashboard/api/health")[0] == 200


# ── Origin: a write from another site's page is refused ──────────────────

def posts(listener: int, host: str, path: str, body: dict, *, origin: str | None, method="POST"):
    headers = {"host": host, "content-type": "application/json"}
    if origin is not None:
        headers["origin"] = origin
    b = browser(listener, host)
    return b.client.request(method, path, json=body, headers=headers)


@pytest.mark.parametrize("origin", ["http://other.example", "https://localhost.other.example",
                                    "http://localhost:42000", "null"])
def test_a_write_from_another_sites_page_on_the_macs_address_is_refused(api, origin):
    """Given a page of another site open in the Mac's browser, when it posts
    the family as JSON to the Mac's address, then the answer is 403 and the
    host API hears of nothing. The home address is another origin too."""
    r = posts(CARDS_PORT, "localhost:42002", "/dashboard/api/family", FAMILY, origin=origin)

    assert r.status_code == 403
    assert api.asked == []


def test_a_write_from_another_sites_page_to_the_card_door_is_refused(api):
    r = posts(CARDS_PORT, "localhost:42002", "/cards/api/cards/marge-tent", {"title": "Tent"},
              origin="http://other.example", method="PATCH")

    assert r.status_code == 403


def test_a_write_from_another_sites_page_on_the_home_address_is_refused(api):
    r = posts(PUBLIC_PORT, "192.168.1.20:42000", "/dashboard/api/jobs",
              {"action": "up", "target": "photos"}, origin="http://other.example")

    assert r.status_code == 403
    assert api.asked == []


@pytest.mark.parametrize("origin", ["http://localhost:42002", "http://LOCALHOST:42002"])
def test_a_write_from_famstacks_own_page_goes_through(api, origin):
    r = posts(CARDS_PORT, "localhost:42002", "/dashboard/api/family", FAMILY, origin=origin)

    assert r.status_code == 200, r.text
    assert [a["cmd"] for a in api.asked] == ["family"]


def test_a_write_without_an_origin_goes_through(api):
    """`./stack` commands and other programs send no Origin; a browser
    always sends one with a write."""
    r = posts(CARDS_PORT, "localhost:42002", "/dashboard/api/family", FAMILY, origin=None)

    assert r.status_code == 200, r.text


# ── The headers of every page ────────────────────────────────────────────

# Every script that runs inline: no `src`, and not a JSON data block.
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>", re.I)

PAGES = [(PUBLIC_PORT, "192.168.1.20:42000", "/dashboard"),
         (PUBLIC_PORT, "192.168.1.20:42000", "/go/app"),
         (PUBLIC_PORT, "192.168.1.20:42000", "/go/app/sign-in"),
         (CARDS_PORT, "127.0.0.1:42002", "/dashboard"),
         (CARDS_PORT, "127.0.0.1:42002", "/setup"),
         (CARDS_PORT, "127.0.0.1:42002", "/cards")]


@pytest.fixture
def signed(tmp_path, monkeypatch, httpserver, docker_with_no_containers):
    """The Simpsons' stack with the chat set up and the admin signed in."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)("signed")


@pytest.mark.parametrize("listener,host,path", PAGES)
def test_every_page_comes_with_a_policy_that_lets_only_its_own_files_run(
        signed, listener, host, path):
    """Given any page of core, then the browser runs only scripts from core
    itself, shows the page in no frame, and never guesses a file's type;
    and the page carries no inline script that the policy would block."""
    r = opens(listener, host, path)

    assert r.status_code == 200, r.text[:200]
    policy = _directives(r.headers["content-security-policy"])
    assert policy["frame-ancestors"] == ["'none'"]
    assert policy["script-src"] == ["'self'"]
    assert policy["object-src"] == ["'none'"]
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert INLINE_SCRIPT.findall(r.text) == []


def test_an_answer_that_is_not_a_page_is_never_sniffed(api):
    r = opens(PUBLIC_PORT, "192.168.1.20:42000", "/dashboard/api/health")

    assert r.headers["x-content-type-options"] == "nosniff"


def test_no_answer_tells_the_next_site_its_address(api):
    """Given any answer, then a link on it sends no `Referer`: the allow
    page's and the phone page's addresses carry a code."""
    r = opens(PUBLIC_PORT, "192.168.1.20:42000", "/dashboard/api/health")

    assert r.headers["referrer-policy"] == "no-referrer"


def _directives(policy: str) -> dict[str, list[str]]:
    parts = (p.split() for p in policy.split(";") if p.strip())
    return {name: values for name, *values in parts}


# ── Cookies ──────────────────────────────────────────────────────────────

def _cookies(response) -> dict[str, str]:
    """Each Set-Cookie of `response`, by the cookie's name, lowercased."""
    return {c.split("=", 1)[0]: c.lower() for c in response.headers.get_list("set-cookie")}


def test_the_setup_cookies_are_for_the_server_alone(signed):
    """Given the admin answers the phone question on the Mac, then the
    cookie that keeps the answer is HttpOnly and SameSite=Lax: no script
    reads it, and no other site's request carries it."""
    r = opens(CARDS_PORT, "127.0.0.1:42002", "/setup?phone=signed")

    cookie = _cookies(r)["fs-setup-phone"]
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "secure" not in cookie


def test_the_setup_cookies_are_secure_over_https(signed):
    """Behind the proxy that serves HTTPS, the browser sends them only over
    HTTPS."""
    r = opens(CARDS_PORT, "127.0.0.1:42002", "/setup?phone=signed", **{"x-forwarded-proto": "https"})

    cookie = _cookies(r)["fs-setup-phone"]
    assert "httponly" in cookie and "samesite=lax" in cookie and "secure" in cookie


# ── The cap on live streams ──────────────────────────────────────────────

def test_streams_over_the_cap_are_refused_and_the_links_still_answer(api):
    """Given one device holds as many live streams as the cap allows, when
    it opens one more, then that one is refused; a poll is refused too; and
    a `/go` link, core's health check and another device's dashboard still
    answer, because the cap leaves worker threads for them."""
    import live_routes

    device = browser(PUBLIC_PORT, "192.168.1.20:42000")
    with contextlib.ExitStack() as held:
        for _ in range(live_routes.STREAMS_CAP):
            stream = held.enter_context(device.follows("/dashboard/api/events"))
            assert stream.status == 200

        with device.follows("/dashboard/api/events") as one_more:
            assert one_more.status == 429
        assert device.reads("/dashboard/api/poll")[0] == 429

        other = browser(PUBLIC_PORT, "192.168.1.21:42000")
        assert other.reads("/go/marge-tent-for-four")[0] == 404  # an empty memory: no such card
        assert other.reads("/dashboard/api/health") == (200, {"ok": True})
        assert other.opens("/dashboard").status == 200

    with device.follows("/dashboard/api/events") as again:
        assert again.status == 200


def test_a_household_with_a_stream_per_screen_follows_as_before(api):
    """Resting state: a few screens, one stream each, all answered."""
    screens = [browser(PUBLIC_PORT, f"192.168.1.{n}:42000") for n in (20, 21, 22, 23)]
    with contextlib.ExitStack() as held:
        streams = [held.enter_context(s.follows("/dashboard/api/events")) for s in screens]
        assert [s.status for s in streams] == [200] * 4
        assert streams[0].next_frame().comment == "live"
