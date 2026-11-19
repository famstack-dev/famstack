"""The integration lane's pytest hooks, and the fixtures its browser tests share."""

import os
import socket
import threading
import time
from contextlib import contextmanager

import pytest

# A failed test keeps the logs of the containers it ran against.
from tests.failure_logs import pytest_runtest_makereport, pytest_runtest_setup  # noqa: F401

# Each test gets its own list of paired devices (`DEVICES_FILE`), as in the
# unit lane: the surface DSL's browser pairs itself by adding to it.
from tests.unit.surface.conftest import a_list_of_devices  # noqa: F401

# ── The memory's pages in a browser ───────────────────────────────────────
#
# The browser tests of the memory's pages (`test_memory_*_in_the_browser.py`)
# share these two fixtures. Each test module defines its own `memory`
# fixture (memory's test instance with the cards it needs); `pages` serves
# core's tools server against it, and `chrome` is headless Google Chrome.
# Both import the `browser` extra only when a test asks for them, so the
# rest of the lane runs without it.


@contextmanager
def _environment(**values: str):
    """Set `values` in the process's environment, and put back what was
    there before when the block ends. The tools server reads its settings
    from the environment at each request, so a module-wide server needs
    them set for as long as it runs."""
    before = {name: os.environ.get(name) for name in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for name, value in before.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def pages(memory):
    """Core's tools server on a loopback port, reading the module's
    `memory`; its address, `http://127.0.0.1:<port>`."""
    uvicorn = pytest.importorskip("uvicorn")
    from tests.unit.surface.conftest import tools_server

    with _environment(MEMORY_CARDS_URL=memory.curator, TIMEZONE="UTC"):
        server = tools_server("server")
        port = _free_port()
        running = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
        thread = threading.Thread(target=running.run, daemon=True)
        thread.start()
        for _ in range(100):
            if running.started:
                break
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
        running.should_exit = True
        thread.join(timeout=5)


@pytest.fixture(scope="module")
def chrome():
    """Headless Google Chrome; the test skips on a Mac without it."""
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # no Chrome on this Mac
            pytest.skip(f"Google Chrome is needed: {e}")
        yield browser
        browser.close()
