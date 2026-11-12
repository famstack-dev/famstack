"""The setup page's tests' stand-in: the Simpsons' stack in a named state,
and the browsers that open /setup.

Shared by `test_setup_page.py` and `test_setup_service_cards.py`, which
drive the same page in the same states. The host API is the dashboard's
stand-in (`tests/dashboard_host.py`), reached at the client's edge; the
memory's history is served on localhost by `pytest-httpserver`.

    @pytest.fixture
    def instance(tmp_path, monkeypatch, httpserver):
        return stack_in_state(tmp_path, monkeypatch, httpserver)

    instance("chat", mac="192.168.1.20")
    page = phone().opens("/setup")
"""

from __future__ import annotations

import importlib
import sys

from .conftest import LIB, STACKLETS, a_closed_port
from .dsl import Browser

TOOLS = STACKLETS / "core" / "tools-server"
for _path in (LIB, TOOLS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import re  # noqa: E402

from access import CARDS_PORT, PUBLIC_PORT  # noqa: E402
from stack.live import client  # noqa: E402
from tests.dashboard_host import FAMILY, DashboardHost  # noqa: E402

__all__ = ["stack_in_state", "phone", "on_this_mac", "paired_phone"]


def stack_in_state(tmp_path, monkeypatch, httpserver):
    """A function that puts the stack in one of the stand-in's states:
    `enter("chat")`, with `family` answering `stack family` in place of the
    Simpsons and `mac` the Mac's home-network address (`[core] host`).
    Returns the stand-in, for a test to read what was asked."""
    def enter(state: str, *, family: dict | None = None, mac: str = "") -> DashboardHost:
        root = tmp_path / "instance"
        if mac:
            # The Mac's address on the home network, as `[core] host` names it.
            root.mkdir()
            (root / "stack.toml").write_text(
                f'[core]\ndata_dir = "{root / "data"}"\nhost = "{mac}"\n'
                f'extension_dirs = ["{root / "extensions"}"]\n\n'
                f'[messages]\nserver_name = "{FAMILY["chat_server"]}"\n')
        host = DashboardHost.in_state(state, root=root)

        def call(request: dict, timeout: float) -> dict:
            if family is not None and request.get("cmd") == "family":
                return family
            return host.answer(request)[0]

        monkeypatch.setattr(client, "call", call)
        # The list of paired devices, as `stack devices` writes it on this
        # instance, where the tools server reads it.
        monkeypatch.setenv("DEVICES_FILE", str(root / ".stack" / "devices" / "devices.toml"))
        # The AI step looks for the oMLX app on this Mac: nowhere, unless a
        # test stands one in, so what runs on the Mac's port 8000 decides nothing.
        running_server = importlib.import_module("stacklets.ai.running_server")
        monkeypatch.setattr(running_server, "RUNNING_SERVERS",
                            (f"http://127.0.0.1:{a_closed_port()}/v1",))
        # Nor does Homebrew have oMLX, whatever the Mac that runs the suite has.
        monkeypatch.setattr(importlib.import_module("stacklets.ai.homebrew_omlx"),
                            "LIST_COMMAND", ("false",))
        httpserver.expect_request("/history").respond_with_json(host.history)
        monkeypatch.setenv("MEMORY_CARDS_URL", httpserver.url_for("").rstrip("/"))
        return host
    return enter


def phone(language: str = "en") -> Browser:
    """A phone on the home network, opening the Mac by its address."""
    import server
    return Browser(server.app, listener=PUBLIC_PORT, host="192.168.1.20:42000", language=language)


def on_this_mac(language: str = "en") -> Browser:
    """A browser on the Mac that runs famstack, on the Mac's address
    (`http://127.0.0.1:42002`, published on 127.0.0.1 only)."""
    import server
    return Browser(server.app, listener=CARDS_PORT, host="127.0.0.1:42002", language=language)


def paired_phone(person: str = "homer", *, user_agent: str = "", host: str = "192.168.1.20:42000",
                 language: str = "en") -> Browser:
    """A phone on the home network that the Mac paired as `person`: it
    opened the QR code of "Add a phone" (`/go/app?pair=<code>`), as the
    home address requires of every device. Opening it counts as the
    phone's reach, as it does on a real phone."""
    import server
    add = on_this_mac().opens(f"/setup/add-a-phone?person={person}")
    found = re.search(r'aria-label="QR code: https?://[^/"]+([^"]+)"', add.body)
    assert found, add
    device = Browser(server.app, listener=PUBLIC_PORT, host=host, language=language,
                     user_agent=user_agent, paired=False)
    assert device.opens(found[1].replace("&amp;", "&")).status == 200
    return device
