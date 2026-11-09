"""The sign-in guide: `/go/app/sign-in` explains, step by step, how the
chat app on this device signs in to the family's own chat server.

Signing in to a server of one's own means changing the server in the app,
the step early users got stuck on. The phone page (`/go/app`) offers the
app and leads on to this guide. It shows the steps for the app the device
gets: Element X on an Android phone or an iPhone, Element for the computer
on anything else. The step that changes the server holds the chat
server's address on the home network, the step that signs in holds the
admin's username, and the password is never on the page. A phone's last
step turns on threads, because famstack answers in threads.

Driven through the tools server's FastAPI app as devices on the home
network open it on the published port (the Browser DSL), read as served,
without JavaScript. The host API is the dashboard's shared stand-in in a
named state.
"""

from __future__ import annotations

import pytest

from .dsl import Browser, Page
from .setup_stand_in import stack_in_state

import phone_reach  # noqa: E402  (on the path through setup_stand_in)
from access import PUBLIC_PORT  # noqa: E402

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

LAN = "192.168.1.20"
GUIDE = "/go/app/sign-in"
IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
LAPTOP = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
          "Chrome/131.0.0.0 Safari/537.36")
PLAY = "play.google.com/store/apps/details?id=io.element.android.x"
APP_STORE = "apps.apple.com/app/id1631335820"
ELEMENT_DOWNLOAD = "element.io/download"


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """The Simpsons' stack in a named state, core's links on the Mac's
    home-network address. No phone reach is left for the next test."""
    monkeypatch.setenv("LINK_BASE_URL", f"http://{LAN}:42000/go")
    phone_reach.forget()
    yield stack_in_state(tmp_path, monkeypatch, httpserver)
    phone_reach.forget()


def device(user_agent: str, language: str = "en") -> Browser:
    """A device on the home network, opening the Mac by its address."""
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host=f"{LAN}:42000",
                   user_agent=user_agent, language=language)


def guide(page: Page) -> str:
    """The app the guide's steps are for, as the page marks it."""
    return page.attribute("ol", "data-app")


def steps(page: Page) -> list[str]:
    """The kinds of the guide's steps, in order."""
    return [n.attrs.get("data-step", "") for n in page._root.find(
        lambda n: n.tag == "li" and "sign-in-step" in n.classes)]


def values(page: Page) -> list[str]:
    """The values the guide gives to enter, in order."""
    return page.texts("setup-value")


# ── The steps for the app this device gets ───────────────────────────────

@pytest.mark.parametrize("agent, store", [(ANDROID, PLAY), (IPHONE, APP_STORE)])
def test_a_phone_gets_element_xs_steps_ending_with_threads(instance, agent, store):
    """Given the chat runs, when a phone opens the guide, then it shows the
    steps for Element X: install it from this phone's store, change the
    server to the family's own on the home network, sign in with the
    admin's username, and last turn on threads, because famstack answers
    in threads."""
    instance("chat", mac=LAN)
    page = device(agent).opens(GUIDE)

    assert page.status == 200
    assert guide(page) == "element-x"
    assert [href for _, href in page.links() if store in href]
    assert values(page) == [f"http://{LAN}:42031", "homer"]
    assert steps(page)[0] == "install"
    assert steps(page)[-1] == "threads"
    assert steps(page).index("server") < steps(page).index("account")


def test_a_laptop_gets_the_steps_for_element_on_the_computer(instance):
    """Given the chat runs, when a laptop opens the guide, then it shows
    the steps for Element for the computer, with the same server and
    username and no phone store."""
    instance("chat", mac=LAN)
    page = device(LAPTOP).opens(GUIDE)

    assert page.status == 200
    assert guide(page) == "element"
    assert [href for _, href in page.links() if ELEMENT_DOWNLOAD in href]
    assert not [href for _, href in page.links() if PLAY in href or APP_STORE in href]
    assert values(page) == [f"http://{LAN}:42031", "homer"]


def test_the_guide_never_holds_a_password_and_reads_without_a_script(instance):
    """The guide is on the public port for any device in the house: it
    says where the password is and never holds it, and every value is in
    the page as served, so it reads complete in a browser that runs no
    script. Copy is the only thing a script adds."""
    instance("chat", mac=LAN)
    page = device(ANDROID).opens(GUIDE)

    assert "data-secret" not in page.body
    assert "setup-show" not in page.body
    assert [n for n in page._root.find(lambda n: "sign-in-password" in n.classes)]
    assert [n for n in page._root.find(lambda n: "setup-copy" in n.classes and "hidden" in n.attrs)]


def test_the_guide_speaks_german_to_a_german_phone(instance):
    """A phone set to German reads the guide in German."""
    instance("chat", mac=LAN)
    page = device(ANDROID, language="de").opens(GUIDE)

    assert page.attribute("html", "lang") == "de"
    assert values(page) == [f"http://{LAN}:42031", "homer"]


def test_while_the_chat_does_not_run_the_guide_has_no_server_to_give(instance):
    """Given the chat is not started, the guide says so and gives no
    server and no username."""
    instance("fresh", mac=LAN)
    page = device(ANDROID).opens(GUIDE)

    assert page.status == 200
    assert values(page) == []
    assert steps(page) == []


def test_the_script_that_shows_copy_is_served_to_the_phone(instance):
    """Every script the guide loads, and the copy fallback it imports, is
    served on the public port, so Copy appears on the phone."""
    instance("chat", mac=LAN)
    phone = device(ANDROID)
    scripts = phone.opens(GUIDE).scripts()

    assert scripts
    for src in scripts + ["/dashboard/static/copy-value.js"]:
        assert phone.opens(src).status == 200, src


# ── Reaching it ──────────────────────────────────────────────────────────

def test_the_phone_page_leads_on_to_the_guide(instance):
    """The phone page, after "Get the app", links to the guide."""
    instance("chat", mac=LAN)
    page = device(ANDROID).opens("/go/app")

    assert [href for _, href in page.links() if href.endswith("app/sign-in")]
