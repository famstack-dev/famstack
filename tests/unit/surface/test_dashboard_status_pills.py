"""The dashboard's status pills: what runs, one pill per set-up service.

After the install moved to `/setup`, the family's dashboard no longer
carries the admin's setup (its steps, tiles, forms and terminal commands).
"Your stack" is one status pill per set-up service, each with its status
word: Running, Starting (while its run goes on), Not answering, Stopped.
A pill opens the service's service card. One link says what is left:
"Continue setup: <next step>" while steps are open, "Set up <n> more" after,
nothing once everything offered runs. It leads to `/setup`.

Driven through the tools server's FastAPI app as a browser opens it, read
as served, without JavaScript, with the host API in one of the dashboard
stand-in's named states (`tests/dashboard_host.py`) through the setup
page's stand-in (`setup_stand_in.py`). A start that is going on is a real
job in the tools server's job store (the `starting` fixture of
`test_setup_service_cards.py`).
"""

from __future__ import annotations

import re

import pytest

from .dsl import Page
from .setup_stand_in import on_this_mac, phone, stack_in_state
from .test_setup_service_cards import starting  # noqa: F401  (a fixture)

from stack.live import client  # noqa: E402  (on the path through setup_stand_in)

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """Put the Simpsons' stack in a named state: `instance("services")`."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)


def pills(page: Page) -> list[tuple[str, str]]:
    """(service, status) of every status pill, in page order."""
    return re.findall(r'<a class="fs-status" [^>]*data-service="(\w+)"[^>]*>.*?'
                      r'class="service-card-status" data-status="(\w+)"', page.body, re.S)


def left(page: Page) -> tuple[str, str] | None:
    """The link that says what is left: (what it names, where it leads).
    What it names is the next step's id, or `more:<n>`."""
    found = re.search(r'<a class="fs-link status-left" href="([^"]*)" data-left="([^"]*)"',
                      page.body)
    return (found[2], found[1]) if found else None


def service_card_of(page: Page, sid: str) -> Page | None:
    """The service card a pill opens, read on its own."""
    found = re.search(rf'<article class="service-card" data-service="{sid}".*?</article>',
                      page.body, re.S)
    return Page(200, "text/html", found[0]) if found else None


def listing_says(monkeypatch, sid: str, **fields) -> None:
    """Let `stack list` say `fields` of `sid`: `degraded=True` for a
    container that runs and fails its health check, `online=False` for one
    that is stopped."""
    asked = client.call

    def call(request: dict, timeout: float) -> dict:
        answer = asked(request, timeout)
        if request.get("cmd") == "list":
            for s in answer.get("stacklets", []):
                if s["id"] == sid:
                    s.update(fields)
        return answer

    monkeypatch.setattr(client, "call", call)


def everything_offered_set_up(monkeypatch) -> None:
    """Let `stack list` say every stacklet runs."""
    asked = client.call

    def call(request: dict, timeout: float) -> dict:
        answer = asked(request, timeout)
        if request.get("cmd") == "list":
            for s in answer.get("stacklets", []):
                s.update(enabled=True, online=True, degraded=False)
        return answer

    monkeypatch.setattr(client, "call", call)


# ── The pills ─────────────────────────────────────────────────────────────

def test_your_stack_is_one_pill_per_set_up_service_with_its_status(instance):
    """Given the chat, Photos, Documents and the memory run, when anyone
    opens the dashboard, then each has a pill saying it runs, in the order
    of the services, and nothing that is not set up has one. Core and Code
    are the stack's own and have none."""
    instance("services")

    assert pills(phone().opens("/dashboard")) == [
        ("messages", "running"), ("photos", "running"), ("docs", "running"),
        ("memory", "running")]


def test_a_service_starting_shows_as_starting_while_its_run_goes_on(instance, starting):  # noqa: F811
    """Given Photos is being started (from the terminal, the Server room or
    /setup: every start is a job the tools server follows), when the
    dashboard opens, then Photos has a pill that says it starts, before its
    first start is done."""
    instance("chat")
    starting("photos", ["Rendering environment", "Pulling images"])

    assert ("photos", "starting") in pills(phone().opens("/dashboard"))


def test_a_service_that_does_not_answer_or_is_stopped_says_so(instance, monkeypatch):
    """Given Photos runs and fails its health check and Documents is
    stopped, then their pills say "not answering" and "stopped"."""
    instance("services")
    listing_says(monkeypatch, "photos", degraded=True)
    listing_says(monkeypatch, "docs", online=False)

    shown = dict(pills(phone().opens("/dashboard")))

    assert (shown["photos"], shown["docs"]) == ("not_answering", "stopped")


def test_a_pill_opens_its_service_card(instance):
    """Given Photos runs, its pill opens Photos' service card, with its
    status and its Open; without a script the pill leads to "Your stack" on
    /setup, where the same service cards are."""
    instance("services")
    page = on_this_mac().opens("/dashboard")

    pill = re.search(r'<a class="fs-status" href="([^"]*)"[^>]*data-service="photos"', page.body)
    card = service_card_of(page, "photos")

    assert pill and pill[1] == "/setup?step=stack"
    assert card is not None
    assert 'class="service-card-status" data-status="running"' in card.body
    assert card.links("service-card-act")


def test_on_this_mac_a_service_card_signs_in_the_admin(instance):
    """On the Mac that runs famstack the service card names the admin's
    account and carries the first password, as on /setup; on a phone there
    is no password."""
    instance("services")

    here = service_card_of(on_this_mac().opens("/dashboard"), "photos")
    there = service_card_of(phone().opens("/dashboard"), "photos")

    assert here and "data-secret" in here.body
    assert there and "data-secret" not in there.body


# ── What is left ──────────────────────────────────────────────────────────

def test_while_steps_are_open_one_link_continues_the_setup(instance):
    """Given the chat runs and nobody signed in yet, then on the Mac's
    address one link names the next step, the chat on this Mac, and leads
    to /setup. The home address has none (`test_changes_on_the_mac.py`)."""
    instance("chat")

    assert left(on_this_mac().opens("/dashboard")) == ("mac", "/setup")


def test_once_the_setup_is_done_the_link_counts_what_can_be_added(instance):
    """Given every step is done (the admin said the phone is signed in),
    then the link counts the stacklets that can still be added and leads to
    /setup, on the Mac's address."""
    instance("done")
    browser = on_this_mac()
    browser.opens("/setup?phone=signed")

    what, where = left(browser.opens("/dashboard"))

    assert what.startswith("more:") and int(what.split(":")[1]) > 0
    assert where == "/setup?step=stack"


def test_when_everything_offered_runs_there_is_no_link(instance, monkeypatch):
    """Given every step is done and every stacklet runs, then nothing is left
    and there is no link."""
    instance("done")
    everything_offered_set_up(monkeypatch)
    browser = phone()
    browser.opens("/setup?phone=signed")

    assert left(browser.opens("/dashboard")) is None


# ── What left the dashboard ──────────────────────────────────────────────

def test_the_setup_is_no_longer_on_the_dashboard(instance):
    """Given a fresh stack, the dashboard shows no "Setting up", no tile,
    no forms and no terminal command: those are /setup's."""
    instance("fresh")
    page = phone().opens("/dashboard")

    assert page.widget("setup").text == ""
    assert 'class="tile"' not in page.body
    assert "tile-command" not in page.body
    assert not any("fs-setup" in src for src in page.scripts())


def test_the_activity_stays(instance):
    """The activity widget is where it was."""
    instance("services")

    assert phone().opens("/dashboard").widget("activity").attribute("fs-widget", "state")


def test_the_pills_read_the_same_view_as_json(instance):
    """The page's script re-reads the pills from `/dashboard/api/stack`, the
    one endpoint the page already had: the same pills and the same link."""
    instance("chat")

    status, view = on_this_mac().reads("/dashboard/api/stack")

    assert status == 200
    assert [(p["id"], p["status"]) for p in view["pills"]] == [("messages", "running")]
    assert view["left"]["step"] == "mac"
