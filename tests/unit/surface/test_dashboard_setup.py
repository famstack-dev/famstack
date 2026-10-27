"""After the first run: the "Setting up" widget leads the family, chat first.

Right after the family is set up in the browser, the dashboard shows the
setup as seven steps: famstack installed, the family, the chat, everyone
in the chat, what the family needs, a backup, the first letter. The next
step is open; every step's button works from the page, and nothing is
offered that cannot start yet. "Your stack" lists what runs and leaves the
offers to the steps while the setup is shown. A fresh install has no
memory yet, so the activity widget is quiet, not broken, and the intro
does not say the chat runs before it does.

Driven through the tools server's FastAPI app as a phone on the home
network opens it (the Browser DSL), read as served, without JavaScript.
The host API is the stand-in, at its edge: `stack.live.client.call`
answers `list` with what the real `Stack.list()` reports over the repo's
own stacklets (Docker stood in for, as in test_dashboard_stack.py),
`family` as `stack family --json` would, and `chat_users` as
`stack messages users --json` would. The memory is not running: the
surface conftest closes its port.
"""

from __future__ import annotations

import json
import re
import sys

import pytest

from .conftest import LIB, REPO, STACKLETS
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from access import PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402

FAMILY = {"name": "Simpson", "domain": "", "language": "en", "people": [
    {"id": "homer", "name": "Homer Simpson", "relation": "parent", "words": "he",
     "role": "admin", "email": "homer@home.local"},
    {"id": "marge", "name": "Marge Simpson", "relation": "parent", "words": "she",
     "role": "member", "email": "marge@home.local"},
    {"id": "bart", "name": "Bart Simpson", "relation": "child", "words": "he",
     "role": "member", "email": "bart@home.local"},
    {"id": "lisa", "name": "Lisa Simpson", "relation": "child", "words": "she",
     "role": "member", "email": "lisa@home.local"},
]}

STEPS = ["Install famstack", "Tell famstack who is at home", "Start the family chat",
         "Bring everyone in", "Add what your family needs", "Keep a backup",
         "Hand over your first letter"]


def listing(tmp_path, monkeypatch, running: tuple[str, ...], server_name: str = "simpson",
            others: tuple[str, ...] = ()) -> dict:
    """What `stack list --json` answers for an instance where `running`
    were set up and run, as the host API hands it over. `others` run on
    this Mac for another instance: Docker knows them by the same names."""
    import stack.docker
    from stack import Stack

    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "server.lan"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n\n[messages]\nserver_name = "{server_name}"\n')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    for sid in running:
        (tmp_path / ".stack" / f"{sid}.setup-done").touch()
    stck = Stack(REPO, tmp_path / "data", instance_dir=tmp_path)
    with monkeypatch.context() as docker:
        docker.setattr(stack.docker, "project_states",
                       lambda: {sid: "running" for sid in (*running, *others)})
        docker.setattr(stack.docker, "check_health", lambda url, headers=None: True)
        answer = stck.list()
    return json.loads(json.dumps(answer, default=str))


def in_chat(*signed_in: str) -> dict:
    """What `stack messages users --json` answers: every person has an
    account; those named have signed in on a device."""
    return {"users": [{"id": f"@{p['id']}:simpson", "name": p["name"], "admin": p["role"] == "admin",
                       "last_seen": 1761560000000 if p["id"] in signed_in else None}
                      for p in FAMILY["people"]]}


class HostAPI:
    """The host API as the tools server reaches it, noting every request."""

    def __init__(self, stack_list: dict, chat: dict | None = None):
        self.stack_list, self.chat, self.asked = stack_list, chat, []

    def call(self, request: dict, timeout: float) -> dict:
        self.asked.append(request)
        answers = {"list": self.stack_list, "family": FAMILY,
                   "chat_users": self.chat or {"error": "messages is not running"}}
        return answers.get(request.get("cmd"), {"error": "not stood in"})


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    """Right after the first run: core runs, the family is set, nothing else."""
    api = HostAPI(listing(tmp_path, monkeypatch, ("core",)))
    monkeypatch.setattr(client, "call", api.call)
    return api


@pytest.fixture
def chatting(tmp_path, monkeypatch):
    """The chat runs; Homer has signed in, the rest not yet."""
    api = HostAPI(listing(tmp_path, monkeypatch, ("core", "messages")), in_chat("homer"))
    monkeypatch.setattr(client, "call", api.call)
    return api


def phone(language: str = "en") -> Browser:
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)


def steps(page) -> list[tuple[str, str]]:
    """(step id, status) of every step of the setup widget, in order."""
    return [(sid, status) for status, sid in
            re.findall(r'<li class="fs-step" data-status="(\w+)"><details data-step="(\w+)"', page.body)]


def tile(page, sid: str) -> str:
    """The markup of one stacklet's tile, as the page serves it."""
    found = re.search(rf'<article class="tile" data-id="{sid}".*?</article>', page.body, re.S)
    return found[0] if found else ""


# ── Right after the first run ────────────────────────────────────────────

def test_setting_up_leads_with_the_chat(fresh):
    page = phone().opens("/dashboard")
    setup = page.widget("setup")

    assert setup.texts("fs-step-title") == STEPS
    assert steps(page) == [("install", "done"), ("family", "done"), ("chat", "next"),
                           ("people", "waiting"), ("services", "waiting"), ("backup", "waiting"),
                           ("try", "waiting")]
    assert setup.texts("setup-count") == ["2 of 7 done"]
    assert '<details data-step="chat" open>' in page.body


def test_the_chat_can_be_added_from_the_page_because_its_question_is_answered(fresh):
    """Messages asks one question on its first `up`, the server's name, and
    the family already named it."""
    chat = tile(phone().opens("/dashboard"), "messages")

    assert 'class="tile-add"' in chat and 'data-target="messages"' in chat


def test_nothing_is_offered_before_what_it_needs(fresh):
    page = phone().opens("/dashboard")

    for sid, after in (("agent", "Comes after the chat"), ("memory", "Comes after Code")):
        assert 'class="tile-add"' not in tile(page, sid), sid
        assert after in tile(page, sid)


def test_a_question_still_unanswered_shows_the_terminal_command(fresh):
    """Backup asks which disk, which the page cannot answer yet."""
    backup = tile(phone().opens("/dashboard"), "backup")

    assert 'class="tile-add"' not in backup
    assert "<code class=\"tile-command\">stack up backup</code>" in backup


def test_the_tiles_speak_the_dashboards_words(fresh):
    page = phone().opens("/dashboard")

    assert "Every phone backs up its camera roll here, in full quality." in tile(page, "photos")
    assert "—" not in page.body
    assert "Jedes Handy sichert seine Fotos hierher" in tile(phone("de").opens("/dashboard"), "photos")


def test_your_stack_leaves_the_offers_to_the_steps_while_setting_up(fresh):
    stack = phone().opens("/dashboard").widget("stack")

    assert stack.texts("tile-name") == []
    assert stack.texts("stack-name") == ["Core"]


def test_without_the_memory_activity_is_quiet_not_broken(fresh):
    activity = phone().opens("/dashboard").widget("activity")

    assert activity.attribute("fs-widget", "state") == "empty"
    assert activity.texts("fs-empty") == ["Quiet so far. The first letter, photo or memory shows up here."]


def test_the_intro_does_not_say_the_chat_runs_before_it_does(fresh):
    lede = phone().opens("/dashboard").texts("lede")[0]

    assert "the chat runs" not in lede


def test_a_chat_running_for_another_instance_is_not_this_ones(tmp_path, monkeypatch):
    """Containers are named the same in every instance on a Mac, so Docker
    reports the test rig's chat for a new one too. Only what is set up here
    counts."""
    api = HostAPI(listing(tmp_path, monkeypatch, ("core",), others=("messages",)))
    monkeypatch.setattr(client, "call", api.call)

    assert dict(steps(phone().opens("/dashboard")))["chat"] == "next"


def test_opening_the_page_starts_nothing(fresh):
    import live_routes

    phone().opens("/dashboard")

    assert {r["cmd"] for r in fresh.asked} <= {"list", "family", "chat_users"}
    assert list(live_routes.jobs.recent()) == []


# ── Once the chat runs ───────────────────────────────────────────────────

def test_with_the_chat_running_everyone_is_brought_in_next(chatting):
    page = phone().opens("/dashboard")

    assert dict(steps(page))["chat"] == "done"
    assert dict(steps(page))["people"] == "next"
    assert page.widget("setup").texts("setup-count") == ["3 of 7 done"]
    people = page.widget("setup").texts("setup-address")
    assert people == ["@homer:simpson", "@marge:simpson", "@bart:simpson", "@lisa:simpson"]
    assert page.widget("setup").texts("setup-signed") == ["Signed in", "Not yet", "Not yet", "Not yet"]
    assert "the chat runs" in page.texts("lede")[0]


def test_when_everyone_has_signed_in_the_step_is_done(chatting):
    chatting.chat = in_chat("homer", "marge", "bart", "lisa")

    assert dict(steps(phone().opens("/dashboard")))["people"] == "done"


def test_the_setup_reads_as_json_for_its_script(chatting):
    status, view = phone().reads("/dashboard/api/setup")

    assert status == 200
    assert [s["id"] for s in view["steps"]] == ["install", "family", "chat", "people", "services",
                                                "backup", "try"]
    assert view["done"] == 3
