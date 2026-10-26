"""The "Your stack" widget: what runs, and what the family can add.

The dashboard's second widget. It reads the stack's state from the host
API (`stack list --json`), lists what runs with its state and a link,
and offers what may be added: stable and beta stacklets, never a
deprecated one, never one that is always on. A stacklet whose first `up`
asks questions (`asks_on_first_up`) is offered with its terminal command,
not with an Add button.

Driven through the tools server's FastAPI app as a phone on the home
network opens it (the Browser DSL). The host API is the stand-in, at its
edge: `stack.live.client.call` answers `list` with what the real
`Stack.list()` reports over the repo's own stacklets, with Docker stood
in for (which containers run, which health checks pass). The page is
read as served, without JavaScript.
"""

from __future__ import annotations

import json
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

# Messages and documents run, the memory runs but its check fails; the
# rest were never set up.
RUNNING = ("core", "messages", "docs", "memory")
NOT_ANSWERING = ("memory",)


def listing(tmp_path, monkeypatch, running=RUNNING, not_answering=NOT_ANSWERING) -> dict:
    """What `stack list --json` answers for an instance where `running`
    were set up and run, as the host API would hand it over."""
    import stack.docker
    from stack import Stack

    (tmp_path / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "server.lan"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n')
    (tmp_path / ".stack").mkdir(exist_ok=True)
    for sid in running:
        (tmp_path / ".stack" / f"{sid}.setup-done").touch()
    stck = Stack(REPO, tmp_path / "data", instance_dir=tmp_path)
    failing_checks = {s["manifest"].get("health", {}).get("url")
                      for s in stck.discover() if s["id"] in not_answering}
    with monkeypatch.context() as docker:
        docker.setattr(stack.docker, "project_states", lambda: {sid: "running" for sid in running})
        docker.setattr(stack.docker, "check_health", lambda url, headers=None: url not in failing_checks)
        answer = stck.list()
    return json.loads(json.dumps(answer, default=str))


@pytest.fixture
def phone(tmp_path, monkeypatch):
    """A phone on the home network, with a host API that answers `list`."""
    import server

    answer = listing(tmp_path, monkeypatch)
    monkeypatch.setattr(client, "call", lambda request, timeout: answer
                        if request == {"cmd": "list"} else {"error": "not stood in"})
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")


def rows(page) -> list[tuple[str, str]]:
    """(name, state) of every stacklet the widget lists as set up."""
    return list(zip(page.texts("stack-name"), page.texts("stack-state")))


# ── What runs ────────────────────────────────────────────────────────────

def test_the_widget_lists_what_is_set_up_with_its_state(phone):
    page = phone.opens("/dashboard")

    assert rows(page) == [("Core", "running"), ("Documents", "running"),
                          ("Memory", "started, not answering yet"), ("Messages", "running")]


def test_a_running_stacklet_links_to_where_it_is_opened(phone):
    page = phone.opens("/dashboard")

    assert ("Open", "http://server.lan:42020") in page.links("stack-row")


def test_the_glance_says_how_many_run(phone):
    assert phone.opens("/dashboard").texts("stack-glance") == ["4 running"]


# ── What can be added ────────────────────────────────────────────────────

def test_what_can_be_added_is_offered_as_tiles(phone):
    """Stable and beta, never deprecated (`chatai`), never always on
    (`infra`), never incubating (`web`)."""
    page = phone.opens("/dashboard")

    assert page.texts("tile-name") == ["Agent", "AI", "Backup", "Code", "Photos"]


def test_a_tile_offers_add_and_names_the_terminal_command(phone):
    """The button is for the browser; the command is what a page without
    JavaScript shows, and what the button does."""
    page = phone.opens("/dashboard")

    assert page.texts("tile-add") == ["Add Agent", "Add Code", "Add Photos"]
    assert "stack up photos" in page.texts("tile-command")


def test_a_stacklet_that_asks_on_its_first_up_offers_only_its_command(phone):
    page = phone.opens("/dashboard")

    commands = page.texts("tile-command")
    assert {"stack up ai", "stack up backup"} <= set(commands)
    assert "Add AI" not in page.texts("tile-add")
    assert "Add Backup" not in page.texts("tile-add")


def test_the_medium_size_says_how_many_more_there_are_to_add(phone):
    assert phone.opens("/dashboard").texts("stack-more") == ["5 more to add"]


# ── The endpoint, and when the stack does not answer ─────────────────────

def test_the_widget_reads_the_same_view_as_json(phone):
    status, view = phone.reads("/dashboard/api/stack")

    assert status == 200
    assert view["state"] == "content"
    assert [r["name"] for r in view["running"]] == ["Core", "Documents", "Memory", "Messages"]
    assert [t["id"] for t in view["to_add"]] == ["agent", "ai", "backup", "code", "photos"]


def test_when_the_host_api_does_not_answer_the_widget_says_so(monkeypatch):
    """The unit lane's default: nothing listens where the host API should."""
    import server

    phone = Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000")
    status, view = phone.reads("/dashboard/api/stack")
    page = phone.opens("/dashboard")

    assert (status, view["state"]) == (503, "error")
    assert page.status == 200
    assert "The stack does not answer right now." in page.text


def test_the_page_loads_the_widgets_script_and_styles(phone):
    """What renders the widget in the browser, and makes Add work."""
    page = phone.opens("/dashboard")

    assert "/dashboard/static/widgets/fs-stack.js" in page.scripts()
    for asset in ("widgets/fs-stack.js", "widgets/fs-stack.css"):
        assert phone.opens(f"/dashboard/static/{asset}").status == 200, asset
