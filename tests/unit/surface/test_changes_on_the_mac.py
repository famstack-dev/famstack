"""Changes to the server happen on the Mac that runs famstack, and nowhere else.

The tools server has two addresses for browsers. The home address
(`http://<mac>:42000`) is published to the home network; the Mac's address
(`http://127.0.0.1:42002`) only on 127.0.0.1, so only the Mac reaches it.
Every change to the server answers only on the Mac's address: starting a
stacklet, setting the family, answering a stacklet's setup questions,
connecting an AI server and asking for the AI's first words. On the
home address each of them answers 403 before the host API hears of it,
whatever Host header the request sends, and `/setup` answers with a page
that says where to go. The dashboard there shows each service's state,
without Start and without the link into the setup.

Driven through the tools server's FastAPI app as a browser reaches it, on
the listener of each address (the Browser DSL). The host API is the
dashboard's stand-in (`tests/dashboard_host.py`) at the client's edge,
noting every request.
"""

from __future__ import annotations

import pytest

from .dsl import Browser
from .setup_stand_in import on_this_mac, stack_in_state
from .test_dashboard_status_pills import listing_says

from access import PUBLIC_PORT  # noqa: E402  (on the path through setup_stand_in)
from tests.dashboard_host import FAMILY  # noqa: E402

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

# Each change, with a body the page would send it.
CHANGES = {
    "start a stacklet": ("/dashboard/api/jobs", {"action": "up", "target": "photos"}),
    "start everything": ("/dashboard/api/jobs", {"action": "up", "target": "all"}),
    "set the family": ("/dashboard/api/family", FAMILY),
    "answer a setup question": ("/dashboard/api/configure/photos", {"answers": {}}),
    "ask for the AI's welcome": ("/dashboard/api/ai/first-words/welcome", {}),
    "connect an AI server": ("/dashboard/api/ai/switch",
                             {"address": "http://192.168.1.30:11434/v1", "key": "sk-simpsons"}),
}
# Host headers a device on the home network can send to the home address.
# The first three name the Mac itself; a header is the caller's claim.
CLAIMED_HOSTS = ["localhost:42000", "127.0.0.1:42000", "[::1]:42000", "192.168.1.20:42000"]

ON_THE_MAC = "This happens on the Mac that runs famstack"


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """Put the Simpsons' stack in a named state (`setup_stand_in.stack_in_state`)."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)


@pytest.fixture
def own_jobs(monkeypatch):
    """A job store of this test's own, so a start it makes does not show on
    the tiles of a later test in the same worker."""
    import live_routes
    from stack.live.hub import Hub
    from stack.live.jobs import Jobs

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))


def through_the_home_address(host: str = "192.168.1.20:42000", language: str = "en") -> Browser:
    """A browser on the home network that opens the home address and sends
    `host` as its Host header."""
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host=host, language=language)


# ── A change through the home address runs nothing ───────────────────────

@pytest.mark.parametrize("host", CLAIMED_HOSTS)
@pytest.mark.parametrize("change", list(CHANGES))
def test_a_change_through_the_home_address_is_refused_before_the_host_api_hears_of_it(
        instance, change, host):
    """Given any device on the home address, with any Host header, when it
    makes a change, then it gets 403 with where to go, and the host API is
    never asked: no `stack` command, no file written, no speech."""
    host_api = instance("chat")
    path, body = CHANGES[change]

    status, answer = through_the_home_address(host).posts(path, body)

    assert status == 403
    assert ON_THE_MAC in answer["error"]
    assert answer["mac"] == "http://127.0.0.1:42002/setup"
    assert host_api.asked == []


def test_a_start_on_the_macs_address_runs(instance, own_jobs):
    """Given the Mac's browser on the Mac's address, when it starts a
    stacklet, then the start runs as a job."""
    instance("chat")

    status, job = on_this_mac().posts("/dashboard/api/jobs", {"action": "up", "target": "photos"})

    assert status == 202, job
    assert job["target"] == "photos"


def test_the_family_set_on_the_macs_address_is_written(instance):
    """Given the Mac's browser on the Mac's address, when it sets the
    family, then the host API is asked to write it."""
    host_api = instance("chat")

    status, _ = on_this_mac().posts("/dashboard/api/family", FAMILY)

    assert status == 200
    assert {"cmd": "family", "action": "set", "family": FAMILY} in host_api.asked


def test_setup_answers_on_the_macs_address_reach_the_host_api(instance):
    """Given the Mac's browser on the Mac's address, when it answers a
    stacklet's setup questions, then the host API gets the answers as sent."""
    host_api = instance("chat")

    status, answer = on_this_mac().posts("/dashboard/api/configure/photos",
                                         {"answers": {"library": "/Volumes/Photos"}})

    assert status == 200, answer
    assert {"cmd": "configure", "stacklet": "photos",
            "answers": {"library": "/Volumes/Photos"}} in host_api.asked


# ── The setup page says where to go ──────────────────────────────────────

@pytest.mark.parametrize("host", CLAIMED_HOSTS)
def test_the_setup_through_the_home_address_says_it_happens_on_the_mac(instance, host):
    """Given a device on the home address, when it opens /setup, then it
    gets the page that says where the setup is, with the way back to the
    dashboard, and no step of the setup."""
    instance("chat")

    page = through_the_home_address(host).opens("/setup?step=phone")

    assert page.status == 403
    assert "text/html" in page.kind
    assert page.texts("only-on-the-mac-title") == [ON_THE_MAC]
    assert page.texts("only-on-the-mac-line") == [
        "Setting up, starting services and passwords are only on the Mac, so no other "
        "device at home can change famstack."]
    assert page.texts("only-on-the-mac-how") == [
        "On the Mac: click famstack in the menu bar, then Setup…",
        "Or open 127.0.0.1:42002 in the Mac's browser."]
    assert ("Back to the dashboard", "/dashboard") in page.links()
    assert 'class="setup-step"' not in page.body
    assert "data-secret" not in page.body


def test_the_page_speaks_the_browsers_language(instance):
    """Given a German browser on the home address, when it opens /setup,
    then the page is in German."""
    instance("chat")

    page = through_the_home_address(language="de").opens("/setup")

    assert page.texts("only-on-the-mac-title") == ["Das passiert auf dem Mac, auf dem famstack läuft"]
    assert ("Zurück zur Übersicht", "/dashboard") in page.links()


def test_the_setup_on_the_macs_address_is_the_setup(instance):
    """Given the Mac's browser on the Mac's address, when it opens /setup,
    then it gets the setup itself."""
    instance("chat")

    page = on_this_mac().opens("/setup")

    assert page.status == 200
    assert 'class="setup-step"' in page.body
    assert ON_THE_MAC not in page.body


# ── The dashboard on the home address shows, and changes nothing ─────────

def test_the_dashboard_through_the_home_address_has_no_start_and_no_setup(instance, monkeypatch):
    """Given the dashboard on the home address, with a service stopped and
    the setup not finished, then each service shows its state, the stopped
    one reads "Stopped · start it on the Mac", and there is no Start, no
    setup form and no link into the setup."""
    instance("services")
    listing_says(monkeypatch, "docs", online=False)

    page = through_the_home_address().opens("/dashboard")

    assert page.status == 200
    assert "Stopped · start it on the Mac" in page.text
    assert "tile-add" not in page.body
    assert "data-forms" not in page.body
    assert not [href for _, href in page.links() if href.startswith("/setup")]


def test_the_dashboard_on_the_macs_address_keeps_the_setup_link(instance, monkeypatch):
    """Given the dashboard on the Mac's address with the setup not finished,
    then the link into the setup is there, and a stopped service reads
    "Stopped"."""
    instance("services")
    listing_says(monkeypatch, "docs", online=False)

    page = on_this_mac().opens("/dashboard")

    assert [href for _, href in page.links() if href.startswith("/setup")]
    assert "start it on the Mac" not in page.text


def test_the_dashboard_through_the_home_address_is_no_first_run(instance):
    """Given the family is empty, when a device on the home address opens
    the dashboard, then it gets no first run: the family is set on the Mac."""
    instance("fresh", family={"name": "", "people": []})

    page = through_the_home_address().opens("/dashboard")

    assert page.status == 200
    assert "data-first-run" not in page.body
    assert "first-run.js" not in page.body


# ── Reads stay open ───────────────────────────────────────────────────────

@pytest.mark.parametrize("path", ["/dashboard", "/dashboard/api/stack", "/dashboard/api/activity",
                                  "/dashboard/api/family", "/dashboard/api/health"])
def test_reads_through_the_home_address_answer_as_before(instance, path):
    """Resting state: every read on the home address answers."""
    instance("chat")

    page = through_the_home_address().opens(path)

    assert page.status == 200, page
