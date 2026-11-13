"""After the first run, in the browser: a start on /setup shows on the dashboard.

The family is set up; the setup goes on on `/setup`, and the family's
dashboard shows the stack as status pills. This test opens both in
headless Chrome against core's tools server served by uvicorn: Start on
the chat's service card on `/setup` starts the chat, and the dashboard,
open in another tab, follows the run live: the chat gets a pill that says
Starting, then Running, and the intro says the chat runs. Its pill opens
the chat's service card in a pane at the side; Close brings the focus back
to the pill.

The host API is a stand-in on a loopback port, speaking its protocol: one
JSON request a line, one reply, or for `up` with `stream` the run's events
a line each. Its `list` is the real `Stack.list()` over this checkout's
stacklets, with Docker's answers stood in for; `up messages` plays the
steps `stack up messages` reports and then its result, after which the chat
runs. Not real Docker: any stand-in with the id `messages` on this Mac
would share the test rig's compose project (`stack-messages`), so its `up`
would add a container to the rig's chat, a teardown's `down
--remove-orphans` could remove it, and `stack list` would read the rig's
Synapse as its own.

A second test leaves step 3, "Chat on this Mac", open while it waits for
the admin's sign-in: once the stand-in's chat lists the sign-in, the open
page goes on to the phone's step without a reload.

uvicorn serves the app as the Mac's address serves it (127.0.0.1:42002
with the admin key), because the setup opens there only.

Needs the `browser` extra and Google Chrome:

    uv run --extra test --extra browser pytest tests/integration/test_setup_in_the_browser.py

With `SETUP_SHOTS=<dir>` it saves screenshots of each state, at 390 and
1280 px wide. About 10 s a width.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.dashboard_host import DashboardHost, on_the_macs_address

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "stacklets" / "core" / "tools-server"
SHOTS = os.environ.get("SETUP_SHOTS", "")
WIDTHS = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 900}}

@pytest.fixture
def host_api(tmp_path, monkeypatch):
    """The host API for an instance where core runs and nothing else is set
    up, until the chat is started."""
    sys.path.insert(0, str(REPO / "lib"))
    api = DashboardHost(tmp_path / "instance")
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(api.serve()))
    yield api
    api.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Core:
    """Core's tools server as the browser reaches it, which a test can stop
    and start again as `stack restart core` does: the same address, every
    connection dropped meanwhile, a new event hub after. uvicorn runs on a
    port of its own behind a relay on the address the browser opens; a stop
    closes the relay's port and its connections, because an in-process
    uvicorn keeps an open event stream through its own stop."""

    def __init__(self, app, monkeypatch):
        self.monkeypatch = monkeypatch
        self.port = _free_port()
        self.url = f"http://127.0.0.1:{self.port}/dashboard"
        self.inner = _free_port()
        self.listener = None
        self.open: list[socket.socket] = []
        self._new_hub()
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=self.inner,
                                                    log_level="warning"))
        threading.Thread(target=self.server.run, daemon=True).start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.05)

    def _new_hub(self) -> None:
        import live_routes
        from stack.live.hub import Hub
        from stack.live.jobs import Jobs

        hub = Hub()
        self.monkeypatch.setattr(live_routes, "hub", hub)
        self.monkeypatch.setattr(live_routes, "jobs", Jobs(hub))

    def start(self) -> None:
        """Listen on the browser's address again, with a new event hub."""
        if self.listener is not None:
            self._new_hub()
        self.listener = socket.create_server(("127.0.0.1", self.port))
        threading.Thread(target=self._accept, args=(self.listener,), daemon=True).start()

    def stop(self) -> None:
        """Close the browser's address and every connection through it."""
        self.listener.close()
        for conn in self.open:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            conn.close()
        self.open = []

    def close(self) -> None:
        self.stop()
        self.server.should_exit = True

    def _accept(self, listener: socket.socket) -> None:
        while True:
            try:
                client, _ = listener.accept()
            except OSError:
                return
            upstream = socket.create_connection(("127.0.0.1", self.inner))
            self.open += [client, upstream]
            for a, b in ((client, upstream), (upstream, client)):
                threading.Thread(target=_pipe, args=(a, b), daemon=True).start()


def _pipe(source: socket.socket, target: socket.socket) -> None:
    """Copy bytes one way until either side closes."""
    try:
        while data := source.recv(65536):
            target.sendall(data)
    except OSError:
        pass
    for conn in (source, target):
        try:
            conn.close()
        except OSError:
            pass


@pytest.fixture
def core(host_api, monkeypatch):
    monkeypatch.setenv("MEMORY_CARDS_URL", f"http://127.0.0.1:{_free_port()}")
    for path in (REPO / "lib", TOOLS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import server
    from access import CARDS_PORT
    from stack.devices import admin_key

    # The Mac's address opens to a browser with the admin key only, as the
    # menu bar app's "Setup…" leaves it in the cookie.
    monkeypatch.setenv("DEVICES_FILE", str(host_api.root / ".stack" / "devices" / "devices.toml"))
    served = Core(on_the_macs_address(server.app, CARDS_PORT, admin_key.ensure(host_api.root)),
                  monkeypatch)
    served.start()
    yield served
    served.close()


@pytest.fixture
def dashboard(core):
    return core.url


@pytest.fixture(scope="module")
def chrome():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # no Chrome on this Mac
            pytest.skip(f"Google Chrome is needed: {e}")
        yield browser
        browser.close()


def _shot(page, width: str, name: str, *, whole_page: bool = True) -> None:
    """A screenshot of the whole page, or of the window: a sheet is fixed
    over the window, and a whole-page shot cuts its backdrop at the edge."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{width}-{name}.png"), full_page=whole_page)


def _pill(page, sid: str):
    return page.locator(f'fs-status .fs-status[data-service="{sid}"]')


@pytest.mark.parametrize("width", list(WIDTHS))
def test_a_start_on_setup_shows_live_on_the_dashboards_pills(chrome, dashboard, host_api, width):
    context = chrome.new_context(viewport=WIDTHS[width], locale="en-US",
                                 device_scale_factor=2 if width == "390" else 1)
    home, setup = context.new_page(), context.new_page()
    errors: list[str] = []
    for page in (home, setup):
        page.on("pageerror", lambda e: errors.append(str(e)))
    home.goto(dashboard)
    setup.goto(dashboard.replace("/dashboard", "/setup"))

    # Right after the first run: no pill yet, no setup on the dashboard,
    # one link on to the chat's step.
    home.wait_for_selector("fs-status .status-left")
    assert _pill(home, "messages").count() == 0
    assert home.locator("fs-setup, .tile").count() == 0
    assert home.locator("fs-status .status-left").get_attribute("data-left") == "chat"
    assert "Start the chat below" in home.locator(".lede").text_content()
    home.wait_for_timeout(300)
    _shot(home, width, "1-dashboard-after-the-first-run")

    # Start on /setup: the dashboard's chat pill says Starting, live.
    setup.locator('fs-setup-page .tile-add[data-target="messages"]').click()
    home.wait_for_selector('fs-status .fs-status[data-service="messages"] '
                           '.service-card-status[data-status="starting"]', timeout=10000)
    _shot(home, width, "2-dashboard-chat-starting")

    # The run ends: the pill says Running, the link names the next step,
    # and the intro follows without a reload.
    home.wait_for_selector('fs-status .fs-status[data-service="messages"] '
                           '.service-card-status[data-status="running"]', timeout=20000)
    assert home.locator("fs-status .status-left").get_attribute("data-left") == "phone"
    assert "the chat runs on it" in home.locator(".lede").text_content()

    # The pill opens the chat's service card in a pane; Close returns.
    _pill(home, "messages").click()
    pane = home.locator("fs-status dialog.service-card-pane")
    assert pane.is_visible()
    card = pane.locator('.service-card[data-service="messages"]')
    assert card.is_visible()
    assert card.locator(".service-card-open").get_attribute("target") == "_blank"
    assert pane.locator('.service-card[data-service]:visible').count() == 1
    home.wait_for_timeout(400)
    _shot(home, width, "3-dashboard-chat-service-card", whole_page=False)
    pane.locator(".service-card-close").click()
    assert not pane.is_visible()
    assert home.evaluate("document.activeElement.dataset.service") == "messages"

    started = [r["stacklet"] for r in host_api.asked if r.get("cmd") == "up"]
    assert started == ["messages"]
    assert errors == []
    context.close()


def test_the_phone_steps_fallback_opens_the_macs_sign_in_and_finishes_the_step(chrome, dashboard,
                                                                              host_api):
    """Given the chat runs and the phone step waits on an open page, when
    the admin opens the fallback for a phone that cannot reach famstack,
    then it shows the sign-in on this Mac; and when the admin signs in in
    the browser, the open page shows the step done, without a reload."""
    host_api.jump("chat")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    errors: list[str] = []
    setup.on("pageerror", lambda e: errors.append(str(e)))
    setup.goto(dashboard.replace("/dashboard", "/setup"))
    setup.wait_for_selector('.setup-step[data-step="phone"] .setup-part[data-part="app"][data-watch]')
    assert not setup.locator(".setup-fallback code", has_text="localhost:42031").is_visible()

    setup.click(".setup-fallback summary")

    assert setup.locator(".setup-fallback code", has_text="localhost:42031").is_visible()
    setup.evaluate("window.notReloaded = true")

    host_api.signed_in.add("homer")

    setup.wait_for_selector('.setup-rail-step[data-step="phone"][data-status="done"]', timeout=10000)
    assert setup.evaluate("window.notReloaded") is True
    assert errors == []
    context.close()


def test_the_chat_on_the_phone_goes_on_once_the_app_signed_in(chrome, dashboard, host_api):
    """Given step 3, "Chat on your phone", waits on an open page and the
    admin went past step 3 without signing in on the Mac, when the admin
    signs in with Element X on the phone, then the open page shows "Signed
    in with the app" done, without a reload."""
    from tests.dashboard_host import SIGN_INS

    host_api.jump("chat")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    errors: list[str] = []
    setup.on("pageerror", lambda e: errors.append(str(e)))
    setup.goto(dashboard.replace("/dashboard", "/setup?step=phone"))
    setup.wait_for_selector('.setup-part[data-part="app"][data-watch]')
    setup.evaluate("window.notReloaded = true")

    host_api.sign_ins["homer"] = [SIGN_INS["phone"]]

    setup.wait_for_selector('.setup-part[data-part="app"][data-status="done"]', timeout=10000)
    assert setup.evaluate("window.notReloaded") is True
    assert setup.locator('.setup-rail-step[data-step="phone"]').get_attribute("data-status") == "done"
    assert errors == []
    context.close()


def test_continue_on_the_ai_step_starts_the_ai_as_a_tile_does(chrome, dashboard, host_api):
    """Given step 4 with the oMLX app connected and every model on it, when
    the admin presses Continue, then the AI's tile at once shows that the
    AI starts and the run's steps, and once the run ended the open page
    shows the AI ready, without a reload."""
    from tests.dashboard_ai import AIStandIn

    host_api.jump("services")
    ai = AIStandIn(host_api)
    try:
        ai.enter(connected=True, on_mac=True)
        context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
        setup = context.new_page()
        errors: list[str] = []
        setup.on("pageerror", lambda e: errors.append(str(e)))
        setup.goto(dashboard.replace("/dashboard", "/setup?step=ai"))
        setup.wait_for_selector(".ai-download:visible")
        # Continue is the start here: the tile, with the terminal's route
        # for a setup without the app, is not shown before the run.
        assert not setup.locator('.tile[data-id="ai"]').is_visible()
        assert not setup.locator(".tile-terminal").is_visible()
        setup.evaluate("window.notReloaded = true")

        setup.click(".ai-download")

        setup.wait_for_selector('.tile[data-id="ai"][data-status="adding"] .tile-steps li', timeout=3000)
        setup.wait_for_selector('.setup-rail-step[data-step="ai"][data-status="done"]', timeout=15000)
        assert setup.evaluate("window.notReloaded") is True
        assert errors == []
        context.close()
    finally:
        ai.close()


def test_the_page_comes_back_by_itself_after_core_restarted(chrome, core, host_api):
    """Given the setup page is open, when core goes away, then the page says
    that famstack restarts and comes back by itself; and once core answers
    again, the page reads itself and shows what changed meanwhile, without
    a reload by hand."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    errors: list[str] = []
    setup.on("pageerror", lambda e: errors.append(str(e)))
    setup.goto(core.url.replace("/dashboard", "/setup?step=services"))
    setup.wait_for_selector('.setup-step[data-step="services"]')
    setup.evaluate("window.notReloaded = true")

    core.stop()
    setup.wait_for_selector(".fs-offline", timeout=15000)
    assert "startet gerade neu" in setup.locator(".fs-offline").text_content()
    host_api.jump("services")
    core.start()

    setup.wait_for_selector('.setup-rail-step[data-step="services"][data-status="done"]', timeout=30000)
    assert setup.evaluate("window.notReloaded") is True
    assert not setup.locator(".fs-offline").is_visible()
    context.close()


def test_the_page_tries_again_when_its_first_read_after_the_restart_fails(chrome, core,
                                                                          host_api):
    """Given core came back but the stack does not answer yet, so the page's
    first read gets the page that says so, then the setup page tries again
    and shows the step once the stack answers."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    setup.goto(core.url.replace("/dashboard", "/setup?step=services"))
    setup.wait_for_selector('.setup-step[data-step="services"]')

    core.stop()
    setup.wait_for_selector(".fs-offline", timeout=15000)
    host_api.down = True
    host_api.jump("services")
    asked = len(host_api.asked)
    core.start()
    # The page's first read after the return reaches the silent stack.
    for _ in range(300):
        if any(r.get("cmd") == "list" for r in host_api.asked[asked:]):
            break
        time.sleep(0.05)
    host_api.down = False

    setup.wait_for_selector('.setup-rail-step[data-step="services"][data-status="done"]', timeout=30000)
    context.close()


def test_the_dashboard_tries_again_when_its_first_read_after_the_restart_fails(chrome, core,
                                                                               host_api):
    """Given the dashboard is open and core came back while the stack does
    not answer yet, then its widgets try again and show what changed once
    the stack answers, without a reload by hand."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    home = context.new_page()
    home.goto(core.url)
    home.wait_for_selector('fs-status .fs-status[data-service="messages"]')
    home.evaluate("window.notReloaded = true")

    core.stop()
    home.wait_for_selector(".fs-offline", timeout=15000)
    host_api.down = True
    host_api.jump("services")
    asked = len(host_api.asked)
    core.start()
    for _ in range(300):
        if any(r.get("cmd") == "list" for r in host_api.asked[asked:]):
            break
        time.sleep(0.05)
    host_api.down = False

    home.wait_for_selector('fs-status .fs-status[data-service="photos"] '
                           '.service-card-status[data-status="running"]', timeout=30000)
    assert home.evaluate("window.notReloaded") is True
    context.close()


def test_a_refused_stream_is_no_restart(chrome, core, host_api):
    """Given core answers but refuses the page's stream for a while (429, too
    many streams open), then the page does not say that famstack restarts."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    setup.route("**/api/events*", lambda route: route.fulfill(status=429, body=""))
    setup.goto(core.url.replace("/dashboard", "/setup?step=services"))
    setup.wait_for_selector('.setup-step[data-step="services"]')

    setup.wait_for_timeout(6000)   # three of the stream's retries, and the line's own wait

    assert setup.locator(".fs-offline").count() == 0
    context.close()


def test_a_restart_reads_the_page_once(chrome, core, host_api):
    """Given the setup page is open, when core restarts, then the page reads
    itself once when the stream is back, not once for the stream and once
    more for the server's resync."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    setup.goto(core.url.replace("/dashboard", "/setup?step=services"))
    setup.wait_for_selector('.setup-step[data-step="services"]')
    # A run first, so the page holds a cursor of the old server's events.
    start = setup.locator("fs-setup-page .tile-add").first
    started = start.get_attribute("data-target")
    start.click()
    setup.wait_for_selector(f'.service-card[data-service="{started}"] '
                            '.service-card-status[data-status="running"]', timeout=20000)
    reads: list[str] = []
    setup.on("request", lambda r: reads.append(r.url) if "/setup?step=services" in r.url else None)

    core.stop()
    setup.wait_for_selector(".fs-offline", timeout=15000)
    host_api.jump("services")
    core.start()
    setup.wait_for_selector('.setup-rail-step[data-step="services"][data-status="done"]', timeout=30000)
    setup.wait_for_timeout(1500)   # a second read would follow the first at once

    assert len(reads) == 1, reads
    context.close()


def test_a_gateways_error_is_no_answer_of_the_server(chrome, core, host_api):
    """Given famstack behind its proxy (domain mode), which answers the
    health check with 502 while core restarts, when core goes away, then
    the page says that famstack restarts: only an answer of the tools
    server itself counts as the server being there."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    setup = context.new_page()
    setup.goto(core.url.replace("/dashboard", "/setup?step=services"))
    setup.wait_for_selector('.setup-step[data-step="services"]')
    setup.route("**/api/health", lambda route: route.fulfill(status=502, body="Bad Gateway"))

    core.stop()

    setup.wait_for_selector(".fs-offline", timeout=15000)
    core.start()
    context.close()


def test_a_widget_that_keeps_failing_stops_reading_after_a_resync(chrome, dashboard, host_api):
    """Given a dashboard widget whose endpoint keeps failing (a service set
    up but stopped), when the page's stream asks every view to read itself
    again, then the widget tries a few times and stops; it does not read
    every few seconds for as long as the page is open."""
    host_api.jump("services")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE")
    home = context.new_page()
    home.clock.install()
    home.goto(dashboard)
    widget = home.locator("fs-widget[data-widget='activity']")
    widget.wait_for(state="attached")
    src = widget.get_attribute("src").split("?")[0]
    reads: list[str] = []
    home.route(f"**{src}*", lambda route: (reads.append(route.request.url),
                                          route.fulfill(status=503, body="{}")))

    home.evaluate("import('/dashboard/static/live.js').then((m) => m.connect().resync({type: 'resync'}))")
    home.clock.run_for(10 * 60 * 1000)   # ten minutes of the page's own timers

    refreshes = 10 * 60 // int(widget.get_attribute("refresh") or 10 ** 9)
    assert len(reads) <= 4 + refreshes, reads
    context.close()


def test_copy_copies_the_command_in_plain_characters(chrome, dashboard, host_api):
    """Given the AI step shows the command for the Mac's terminal, and the
    page carries an invisible character in it (a soft hyphen, a no-break
    space, as a browser's hyphenation or a translation may add), when the
    admin presses Copy, then the clipboard holds the command as typed."""
    host_api.jump("signed")
    context = chrome.new_context(viewport=WIDTHS["1280"], locale="de-DE",
                                 permissions=["clipboard-read", "clipboard-write"])
    setup = context.new_page()
    setup.goto(dashboard.replace("/dashboard", "/setup?step=ai&ai=install"))
    command = setup.locator(".setup-terminal-command")
    assert command.text_content() == "./stack up ai"
    command.evaluate("node => { node.textContent = '\\u200b./stack\\u00a0up \\u00adai '; }")

    setup.locator(".setup-terminal .setup-copy").click()
    setup.wait_for_function("navigator.clipboard.readText().then(t => t.length > 0)")

    assert setup.evaluate("navigator.clipboard.readText()") == "./stack up ai"
    context.close()
