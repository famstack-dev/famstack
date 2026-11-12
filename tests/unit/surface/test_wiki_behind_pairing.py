"""The family wiki opens only on paired devices, at its own address.

The wiki (Quartz) shows the text of every document and diary entry. It
keeps its address, port 42070 in port mode and its own name in domain
mode, but core answers there now: its wiki door asks who the browser is
and forwards a paired device's request to the wiki container at the root.
Any other browser gets "This browser does not know you yet", the page of
the home address. The device key reaches port 42070 because a browser
sends a host's cookies to every port of it; in domain mode the cookie
names the family's domain, so the wiki's name gets it too.

Driven through the tools server's FastAPI app as browsers open it (the
Browser DSL), on the wiki door's listener, with a stand-in wiki on
localhost in place of the wiki container. The phones are paired on the
home address as the Mac pairs them, with the host API's shared stand-in
(`tests/dashboard_host.py`) running `stack devices` on its instance.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest
import yaml
from werkzeug import Response as UpstreamResponse

from .conftest import tools_server
from .dsl import Browser
from .setup_stand_in import on_this_mac, stack_in_state

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

REPO = Path(__file__).resolve().parents[3]
HOME = "simpson.local:42000"
WIKI = "simpson.local:42070"
DOMAIN = "simpson.family"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
MAC_CHROME = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
UNKNOWN = "This browser does not know you yet"
# What the stand-in wiki answers: its home page, a person's page, and the
# search index Quartz's search and popovers fetch.
WIKI_HOME = "<html><body><h1>The Simpsons' wiki</h1></body></html>"
HOMERS_PAGE = "<html><body><h1>Homer</h1><p>Works at the power plant.</p></body></html>"

devices = tools_server("devices")
access = tools_server("access")


@pytest.fixture
def wiki(httpserver):
    """The stand-in wiki container, on localhost: what it answers, and
    the headers of every request that reached it."""
    asked: list[dict] = []

    def answer(body: str, kind: str = "text/html"):
        def respond(request):
            asked.append({"path": request.path, "query": request.query_string.decode(),
                          "headers": dict(request.headers)})
            return UpstreamResponse(body, content_type=kind)
        return respond

    httpserver.expect_request("/").respond_with_handler(answer(WIKI_HOME))
    httpserver.expect_request("/people/homer").respond_with_handler(answer(HOMERS_PAGE))
    httpserver.expect_request("/static/contentIndex.json").respond_with_handler(
        answer('{"people/homer": {"title": "Homer"}}', "application/json"))
    return asked


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver, wiki):
    """The Simpsons' stack, its link base on the Mac's `.local` name, and
    core's wiki door pointed at the stand-in wiki."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{HOME}/go")
    monkeypatch.setenv("WIKI_UPSTREAM", httpserver.url_for("").rstrip("/"))
    devices.CODES.forget()
    enter = stack_in_state(tmp_path, monkeypatch, httpserver)
    yield lambda state="chat", **kwargs: enter(state, **kwargs)
    devices.CODES.forget()


def home_browser(host: str = HOME, user_agent: str = IPHONE) -> Browser:
    """A browser on the Wi-Fi, opening the home address by `host`, not paired yet."""
    import server
    return Browser(server.app, listener=access.PUBLIC_PORT, host=host, user_agent=user_agent,
                   by_name=True, paired=False)


def wiki_browser(host: str = WIKI, cookies_of: Browser | None = None) -> Browser:
    """A browser opening the wiki's address by `host`, with the cookies of
    `cookies_of`: the same browser on another port of the same host."""
    import server
    browser = Browser(server.app, listener=access.WIKI_PORT, host=host, by_name=True,
                      paired=False)
    if cookies_of is not None:
        browser.client.cookies = cookies_of.client.cookies
    return browser


def paired_phone(person: str) -> Browser:
    """A phone that opened the QR code of "Add a phone" on `person`."""
    page = on_this_mac().opens(f"/setup/add-a-phone?person={person}")
    found = re.search(r'aria-label="QR code: http://' + re.escape(HOME) + r'([^"]+)"', page.body)
    assert found, page
    phone = home_browser()
    assert phone.opens(found[1].replace("&amp;", "&")).status == 200
    return phone


# ── A browser that is not paired ─────────────────────────────────────────

def test_a_browser_without_a_key_gets_the_unknown_page_and_the_wiki_is_not_asked(instance, wiki):
    """Given the family's wiki, when a browser without a device key opens
    the wiki's address or a page of it, then it gets "This browser does not
    know you yet" with the hint for the Mac, and no request reaches the
    wiki."""
    instance()
    guest = wiki_browser()
    for path in ("/", "/people/homer", "/static/contentIndex.json"):
        page = guest.opens(path)
        assert page.status == 401, (path, page)
        assert UNKNOWN in page.text, path
    assert "127.0.0.1:42002" in guest.opens("/").text
    assert wiki == []


def test_the_unknown_page_on_the_wikis_address_loads_its_styles(instance):
    """Given a browser without a key on the wiki's address, when its
    unknown page loads its styles, then core answers them itself."""
    instance()
    page = wiki_browser().opens("/")
    styles = re.findall(r'<link rel="stylesheet" href="([^"]+)"', page.body)
    assert styles
    for href in styles:
        assert wiki_browser().opens(href).status == 200, href


# ── A paired device ──────────────────────────────────────────────────────

def test_a_paired_phone_opens_the_wiki_its_pages_and_its_search(instance, wiki):
    """Given Homer's phone, paired on the home address, when it opens the
    wiki's address, a person's page and the search index, then each is the
    wiki's own answer, at the root; the wiki gets the query, and never the
    device key."""
    instance()
    phone = wiki_browser(cookies_of=paired_phone("homer"))
    assert phone.opens("/").body == WIKI_HOME
    page = phone.opens("/people/homer?from=chat")
    assert page.status == 200 and page.body == HOMERS_PAGE
    search = phone.opens("/static/contentIndex.json")
    assert search.status == 200 and "Homer" in search.body
    assert [a["path"] for a in wiki] == ["/", "/people/homer", "/static/contentIndex.json"]
    assert wiki[1]["query"] == "from=chat"
    assert not any("stack-device" in a["headers"].get("Cookie", "") for a in wiki)


def test_the_macs_browser_opens_the_wiki_once_it_is_paired(instance):
    """Given the Mac's browser, when it opens localhost:42070 unpaired, then
    it gets the unknown page with the hint for the Mac's address; paired
    by "Use this browser for the family's pages", it opens the wiki."""
    instance()
    unpaired = wiki_browser(host="localhost:42070").opens("/")
    assert unpaired.status == 401 and "127.0.0.1:42002" in unpaired.text
    jump = on_this_mac().opens("/setup/this-browser?as=marge")
    browser = home_browser(user_agent=MAC_CHROME)
    assert browser.opens(jump.location.split(HOME, 1)[1]).status == 200
    assert wiki_browser(cookies_of=browser).opens("/").body == WIKI_HOME


def test_in_domain_mode_a_device_paired_on_the_domain_opens_the_wikis_names(instance, monkeypatch):
    """Given domain mode, when a phone paired on the family's domain opens
    `memory.<domain>` or `wiki.<domain>`, then the wiki opens: the device
    key names the family's domain, so the wiki's names get it too."""
    instance()
    monkeypatch.setenv("LINK_BASE_URL", f"https://{DOMAIN}/go")
    page = on_this_mac().opens("/setup/add-a-phone?person=homer")
    found = re.search(r'aria-label="QR code: https://' + re.escape(DOMAIN) + r'([^"]+)"', page.body)
    assert found, page
    phone = home_browser(host=DOMAIN)
    assert phone.opens(found[1].replace("&amp;", "&")).status == 200
    for name in (f"memory.{DOMAIN}", f"wiki.{DOMAIN}"):
        assert wiki_browser(host=name, cookies_of=phone).opens("/").body == WIKI_HOME, name
    assert wiki_browser(host=f"memory.{DOMAIN}").opens("/").status == 401


# ── Who publishes the wiki's address ─────────────────────────────────────

def test_only_core_publishes_the_wikis_port_and_the_hint_keeps_it():
    """Given the compose files, then the wiki container publishes no port,
    core publishes 42070 to its wiki door, the memory's hint keeps port
    42070, and Caddy sends the wiki's names to core's door."""
    memory = yaml.safe_load((REPO / "stacklets/memory/docker-compose.yml").read_text())
    assert all("ports" not in service for service in memory["services"].values())
    core = yaml.safe_load((REPO / "stacklets/core/docker-compose.yml").read_text())
    published = core["services"]["stack-core-tools"]["ports"]
    assert any(p.endswith(f":42070:{access.WIKI_PORT}") for p in published)
    manifest = tomllib.loads((REPO / "stacklets/memory/stacklet.toml").read_text())
    assert manifest["port"] == 42070
    snippet = (REPO / "stacklets/memory/caddy.snippet").read_text()
    assert "stack-memory-wiki" not in snippet
    assert f"stack-core-tools:{access.WIKI_PORT}" in snippet
