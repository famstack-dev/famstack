"""The service card: each service of the stack on /setup, as the admin meets it.

A service card shows one service (the chat, photos, documents, the family
memory) in the same layout everywhere on the page: its name, its app, its
status word with its mark; while it starts, the current step and a closed
"Show details" with every step; once it runs, "Open". Below that, how to
sign in (the address, the username, and the password only on the Mac that
runs famstack), the app for the device that looks, and a place for
famstack's guides.

The service cards stand in steps 2 (the chat) and 6 (photos, documents and
the family memory), and under "Your stack", one per set-up service.

Driven through the tools server's FastAPI app as a browser opens it, read
as served, without JavaScript, with the host API in one of the dashboard
stand-in's named states (`tests/dashboard_host.py`), as in
`test_setup_page.py`, through the same stand-in (`setup_stand_in.py`). A start that is
going on is a real job in the tools server's job store, whose run stands
in for `stack up` and holds after its first steps.
"""

from __future__ import annotations

import re
import threading
import time

import pytest

from .dsl import Browser, Page
from .setup_stand_in import on_this_mac, phone, stack_in_state

from access import PUBLIC_PORT  # noqa: E402  (on the path through setup_stand_in)
from stack.live import client  # noqa: E402

pytestmark = pytest.mark.usefixtures("docker_with_no_containers")

IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) AppleWebKit/605.1.15 "
          "(KHTML, like Gecko) Version/18.1 Mobile/15E148 Safari/604.1")
ANDROID = ("Mozilla/5.0 (Linux; Android 15; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/131.0.0.0 Mobile Safari/537.36")
LAN = "192.168.1.20"


@pytest.fixture
def instance(tmp_path, monkeypatch, httpserver):
    """Put the Simpsons' stack in a named state: `instance("services")`."""
    return stack_in_state(tmp_path, monkeypatch, httpserver)


def an_iphone() -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host=f"{LAN}:42000", user_agent=IPHONE)


def an_android_phone() -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host=f"{LAN}:42000", user_agent=ANDROID)


def service_cards(page: Page) -> dict[str, Page]:
    """Each service card of the page by its service, read on its own."""
    found = re.findall(r'(<article class="service-card" data-service="(\w+)".*?</article>)',
                       page.body, re.S)
    return {sid: Page(200, "text/html", html) for html, sid in found}


def status(card: Page) -> str:
    """The status the service card shows: running, starting, not_answering, stopped, or ""."""
    found = re.search(r'class="service-card-status" data-status="(\w+)"', card.body)
    return found[1] if found else ""


def sign_in(card: Page) -> dict[str, str]:
    """The sign-in rows of a service card: what each row's value is."""
    return dict(zip(card.texts("service-card-field"), card.texts("setup-value")))


def a_password(card: Page) -> bool:
    """Whether the service card carries a password (masked by the script, but in the page)."""
    return "data-secret" in card.body


def details(card: Page) -> tuple[bool, list[str]]:
    """Whether "Show details" is open, and the steps it holds."""
    found = re.search(r"<details([^>]*)>.*?</details>", card.body, re.S)
    if not found:
        return False, []
    inner = Page(200, "text/html", found[0])
    return "open" in found[1], inner.texts("tile-step")


# ── A start that is going on ──────────────────────────────────────────────

@pytest.fixture
def starting(monkeypatch):
    """Start `stack up <sid>` as a job of the tools server that has done
    `steps` and holds there until the test ends: `starting("photos", [...])`."""
    import live_routes
    from stack.live.envelope import STEP_DONE, Event
    from stack.live.hub import Hub
    from stack.live.jobs import Jobs

    jobs = Jobs(Hub())
    monkeypatch.setattr(live_routes, "jobs", jobs)
    release = threading.Event()

    def start(sid: str, steps: list[str]):
        def run():
            for i, text in enumerate(steps, start=1):
                yield Event.now(i, STEP_DONE, {"text": text})
            release.wait(10)

        job = jobs.start("up", sid, run)
        deadline = time.monotonic() + 5
        while len(job.snapshot()["steps"]) < len(steps) and time.monotonic() < deadline:
            time.sleep(0.01)
        return job

    yield start
    release.set()


def not_answering(monkeypatch, host, sid: str) -> None:
    """Let the listing say `sid` runs but its health check fails, as
    `stack list` says of a container that started and does not answer."""
    asked = client.call

    def call(request: dict, timeout: float) -> dict:
        answer = asked(request, timeout)
        if request.get("cmd") == "list":
            for s in answer.get("stacklets", []):
                if s["id"] == sid:
                    s["degraded"] = True
        return answer

    monkeypatch.setattr(client, "call", call)


# ── Step 2: the chat ──────────────────────────────────────────────────────

def test_before_the_chat_runs_its_service_card_offers_start_and_no_status(instance):
    """Given only core runs, the chat's step holds the chat's service card,
    with Start and without a status: there is nothing to say about a
    service that is not set up."""
    instance("fresh")
    page = on_this_mac().opens("/setup")

    cards = service_cards(page)
    assert list(cards) == ["messages"]
    assert status(cards["messages"]) == ""
    assert 'class="tile-add" type="button" data-target="messages"' in cards["messages"].body


def test_while_the_chat_starts_its_service_card_shows_the_current_step_and_closed_details(
        instance, starting):
    """Given a start of the chat that has done two steps, the service card
    says Starting, shows the newest step, and keeps every step behind a
    closed "Show details"."""
    instance("fresh")
    starting("messages", ["Pulling images", "Creating accounts for everyone"])
    card = service_cards(on_this_mac().opens("/setup"))["messages"]

    assert status(card) == "starting"
    assert card.texts("service-card-now") == ["Creating accounts for everyone"]
    assert details(card) == (False, ["Pulling images", "Creating accounts for everyone"])
    assert "data-job=" in card.body


def test_on_this_mac_the_chats_service_card_opens_it_and_shows_the_password(instance):
    """Given the chat runs, on the Mac that runs famstack, its service card
    says Running, opens the chat at localhost, and names the server, the
    admin's username and the first password; its apps are steps 3 and 4."""
    instance("chat")
    card = service_cards(on_this_mac().opens("/setup?step=chat"))["messages"]

    assert status(card) == "running"
    [(_, opens)] = card.links("service-card-open")
    assert opens.startswith("http://localhost:42030")
    rows = sign_in(card)
    assert list(rows.values())[:2] == ["http://localhost:42031", "homer"]
    assert a_password(card)
    assert [href for _, href in card.links("service-card-apps")] == ["/setup?step=mac",
                                                                     "/setup?step=phone"]


def test_on_another_device_the_service_card_shows_no_password(instance):
    """Given the chat runs, on a phone's dashboard (the setup is on the Mac
    only), the service card holds no password and no sign-in: the page does
    not know who looks."""
    instance("chat", mac=LAN)
    card = service_cards(phone().opens("/dashboard"))["messages"]

    assert not a_password(card)
    assert sign_in(card) == {}


# ── Step 6: photos, documents and the family memory ──────────────────────

def test_photos_and_documents_are_three_service_cards_each_with_start(instance):
    instance("signed")
    cards = service_cards(on_this_mac().opens("/setup?step=services"))

    assert list(cards) == ["photos", "docs", "memory"]
    for sid, card in cards.items():
        assert status(card) == ""
        assert f'data-target="{sid}"' in card.body


def test_while_photos_starts_its_service_card_follows_the_run(instance, starting):
    instance("signed")
    starting("photos", ["Rendering environment", "Pulling images"])
    cards = service_cards(on_this_mac().opens("/setup?step=services"))

    assert status(cards["photos"]) == "starting"
    assert cards["photos"].texts("service-card-now") == ["Pulling images"]
    assert details(cards["photos"]) == (False, ["Rendering environment", "Pulling images"])
    assert status(cards["docs"]) == ""


def test_running_photos_signs_in_with_the_admins_email_on_this_mac(instance):
    """Given Photos runs, on this Mac, its service card opens Immich and
    names the admin's own account: their email, as Immich asks for it, and
    the first password. The stack's own Immich account is never shown."""
    instance("services")
    page = on_this_mac().opens("/setup?step=services")
    card = service_cards(page)["photos"]

    assert status(card) == "running"
    assert card.links("service-card-open")[0][1].startswith("http://localhost:42010")
    assert list(sign_in(card).values())[:2] == ["http://localhost:42010", "homer@home.local"]
    assert a_password(card)
    assert "stackadmin" not in page.body
    # A Mac has no Immich app.
    assert not card.links("service-card-apps")


@pytest.mark.parametrize("browser, store", [(an_iphone, "apps.apple.com"),
                                            (an_android_phone, "play.google.com")])
def test_on_a_phone_photos_offers_the_immich_app_for_that_phone(instance, browser, store):
    """On a phone's dashboard (the setup is on the Mac only), Photos offers
    the Immich app for that phone and no password."""
    instance("services", mac=LAN)
    card = service_cards(browser().opens("/dashboard"))["photos"]

    [(_, link)] = card.links("service-card-apps")
    assert store in link
    assert not a_password(card)


def test_documents_signs_in_the_admin_and_offers_no_app(instance):
    """Only admin-role people have a Paperless account, and the setup page
    is the admin's: Documents names the admin's username and offers no app,
    because the family hands documents in through the chat."""
    instance("services", mac=LAN)
    card = service_cards(on_this_mac().opens("/setup?step=services"))["docs"]

    assert status(card) == "running"
    assert card.links("service-card-open")
    assert list(sign_in(card).values()) == ["http://localhost:42020", "homer"]
    assert not card.links("service-card-apps")


def test_the_family_memory_opens_the_wiki_with_no_sign_in_and_no_app(instance):
    instance("services")
    card = service_cards(on_this_mac().opens("/setup?step=services"))["memory"]

    assert status(card) == "running"
    assert card.links("service-card-open")[0][1].startswith("http://localhost:42070")
    assert sign_in(card) == {}
    assert not card.links("service-card-apps")


def test_a_service_that_does_not_answer_says_so_and_offers_no_open(instance, monkeypatch):
    """Given Photos started and its health check fails, its service card
    says Not answering, names the command that tells more, and keeps the
    sign-in: the account exists whether or not Immich answers now."""
    host = instance("services")
    not_answering(monkeypatch, host, "photos")
    card = service_cards(on_this_mac().opens("/setup?step=services"))["photos"]

    assert status(card) == "not_answering"
    assert not card.links("service-card-open")
    assert "stack logs photos" in card.texts("tile-command")
    assert sign_in(card)


def test_every_service_card_of_a_set_up_service_has_a_place_for_guides(instance):
    """The guides do not exist yet: the place is there, with no link in it."""
    instance("services")
    cards = service_cards(on_this_mac().opens("/setup?step=services"))

    assert list(cards) == ["photos", "docs", "memory"]
    for card in cards.values():
        assert card.texts("service-card-guides")
        assert not card.links("service-card-guides")


# ── Your stack ────────────────────────────────────────────────────────────

def test_your_stack_shows_one_service_card_per_set_up_service(instance):
    """Given the chat, photos, documents, code and the memory are set up,
    "Your stack" holds a service card for each service a person uses: Code
    is the memory's and has none, the AI is not set up and has none."""
    instance("services")
    page = on_this_mac().opens("/setup?step=stack")
    set_up = {sid: c for sid, c in service_cards(page).items() if sid not in also_available(page)}

    assert list(set_up) == ["messages", "photos", "docs", "memory"]
    assert all(status(c) == "running" for c in set_up.values())


def test_the_rail_leads_to_your_stack_once_a_service_is_set_up(instance):
    instance("fresh")
    assert "/setup?step=stack" not in [h for _, h in on_this_mac().opens("/setup").links("setup-rail")]

    instance("chat")
    assert "/setup?step=stack" in [h for _, h in on_this_mac().opens("/setup").links("setup-rail")]


# ── Your stack: also available ────────────────────────────────────────────
#
# What no step of the setup offers (Backup, the agent, an extension) is
# offered under "Also available" on "Your stack", each with its line and
# Start, or the terminal command when its setup needs a person at the Mac.

KETTLE = 'id = "kettle"\nname = "Kettle"\ndescription = "Boils water."\ntype = "host"\n'


def a_mac(tmp_path, *, profile: str = "large", kettle: bool = True) -> None:
    """The instance's `stack.toml` before the stand-in writes its own: the
    Mac's memory profile (`[core] profile`), and the extension Kettle in
    its extension dir, unless `kettle` is False."""
    root = tmp_path / "instance"
    root.mkdir()
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{root / "data"}"\nhost = "localhost"\nprofile = "{profile}"\n'
        f'extension_dirs = ["{root / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n')
    if kettle:
        (root / "extensions" / "kettle").mkdir(parents=True)
        (root / "extensions" / "kettle" / "stacklet.toml").write_text(KETTLE)


def also_available(page: Page) -> dict[str, Page]:
    """The service cards under "Also available", by service; {} without it."""
    found = re.search(r'<section class="setup-also".*?</section>', page.body, re.S)
    return service_cards(Page(200, "text/html", found[0])) if found else {}


def test_your_stack_offers_what_no_step_offers(instance, tmp_path):
    """Given every step is done and the agent, Backup and the extension
    Kettle are not set up, "Your stack" offers them under "Also available":
    the agent and Kettle with their line and Start, Backup with its
    terminal command, because its setup needs a person at the Mac. Code,
    which the memory sets up, is not offered."""
    a_mac(tmp_path)
    instance("done")
    page = on_this_mac().opens("/setup?step=stack")

    also = also_available(page)
    assert set(also) == {"agent", "backup", "kettle"}
    assert page.texts("setup-also-heading") == ["Also available"]
    for sid in ("agent", "kettle"):
        assert status(also[sid]) == ""
        assert also[sid].texts("tile-add") == ["Start"]
        assert also[sid].texts("service-card-text")
    assert also["kettle"].texts("service-card-text") == ["Boils water."]
    assert not also["backup"].texts("tile-add")
    assert also["backup"].texts("tile-command") == ["stack up backup"]
    assert 'data-service="code"' not in page.body


def test_a_start_under_also_available_is_followed_on_its_service_card(instance, tmp_path,
                                                                      starting):
    a_mac(tmp_path)
    instance("done")
    starting("agent", ["Rendering environment", "Pulling images"])
    card = service_cards(on_this_mac().opens("/setup?step=stack"))["agent"]

    assert status(card) == "starting"
    assert card.texts("service-card-now") == ["Pulling images"]


def test_with_everything_set_up_there_is_nothing_more_to_offer(instance, tmp_path):
    """Given the agent and Backup are set up too, "Your stack" shows their
    service cards with the others, and no "Also available"."""
    a_mac(tmp_path, kettle=False)
    host = instance("done")
    host.running |= {"agent", "backup"}
    page = on_this_mac().opens("/setup?step=stack")

    assert "setup-also" not in page.body
    assert {"agent", "backup"} <= set(service_cards(page))
    assert 'data-service="code"' not in page.body
