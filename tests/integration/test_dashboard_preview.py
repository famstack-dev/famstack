"""The dashboard preview, as a person and an agent run it.

`tools/dashboard_preview.py` serves the real dashboard against the made-up
family of `tests/dashboard_host.py`, for one state of their setup: in a
browser, or as screenshots. These tests run it as a command, the way it is
used, and read what it serves and writes. With `--controls` the setup
pages carry a panel (`tools/setup_controls.py`); its tests post each
control as the panel's form does and read the page it leads to. Nothing
runs Docker or reaches the network.

Needs the `browser` extra; the screenshots need Google Chrome too:

    uv run --extra test --extra browser pytest tests/integration/test_dashboard_preview.py
"""

from __future__ import annotations

import base64
import http.cookiejar
import json
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[2]
PREVIEW = REPO / "tools" / "dashboard_preview.py"
# famstack's voice, recorded: what the stand-in speech server answers.
VOICE = REPO / "tests" / "preview_voice"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _page(url: str, timeout: float = 20) -> str:
    """The page at `url`, once the preview answers."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return response.read().decode()
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.2)


def test_a_browser_gets_the_dashboard_in_the_state_asked_for():
    """Signed in, in German, as a phone on the home network sees it: the
    chat has its pill, which opens the chat's card in a pane; there is no
    link to continue the setup, which happens on the Mac, and no link to
    the chat's web page, which a phone cannot open over plain HTTP."""
    port = _free_port()
    preview = subprocess.Popen([sys.executable, str(PREVIEW), "signed", "--de", "--phone",
                                "--port", str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        page = _page(f"http://127.0.0.1:{port}/dashboard")
    finally:
        preview.terminate()
        preview.wait(timeout=10)

    assert '<html lang="de">' in page
    assert 'class="fs-status" href="#messages" data-service="messages" aria-haspopup="dialog"' in page
    assert "data-left=" not in page
    assert ":42030" not in page


def test_an_agent_gets_screenshots_of_every_state(tmp_path):
    pytest.importorskip("playwright.sync_api")
    ran = subprocess.run([sys.executable, str(PREVIEW), "all", "--shots", str(tmp_path)],
                         capture_output=True, text=True, timeout=180)
    if ran.returncode and "Executable doesn't exist" in ran.stderr + ran.stdout:
        pytest.skip("Google Chrome is needed")

    assert ran.returncode == 0, ran.stderr
    written = sorted(p.name for p in tmp_path.glob("*.png"))
    dashboards = {f"{state}-en-mac-{width}.png"
                  for state in ("fresh", "chat", "signed", "services", "done")
                  for width in ("390", "1280")}
    # Every state's dashboard, and beside it the setup page's shots.
    assert dashboards <= set(written)
    assert all(name.startswith("setup-") for name in set(written) - dashboards)
    assert sorted(ran.stdout.splitlines()) == sorted(str(tmp_path / name) for name in written)


# ── The setup page's controls (`--controls`) ─────────────────────────────

class Preview:
    """The preview running as a command with the setup page's controls, as
    a browser reaches it: `opens` a page, `sets` the controls as the panel
    does (a form posted to the preview, then the page it leads to)."""

    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.cookies = http.cookiejar.CookieJar()
        self.browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies))

    def opens(self, path: str) -> str:
        with self.browser.open(self.base + path, timeout=30) as response:
            return response.read().decode()

    def sets(self, **controls) -> str:
        """The page the panel's form leads to, after posting `controls`; a
        page that answers with an error status is read as a browser shows it."""
        body = urllib.parse.urlencode(controls).encode()
        try:
            with self.browser.open(self.base + "/_preview/controls", data=body, timeout=60) as response:
                return response.read().decode()
        except urllib.error.HTTPError as refused:
            return refused.read().decode()

    def posts(self, path: str) -> dict:
        request = urllib.request.Request(self.base + path, data=b"{}", method="POST",
                                         headers={"Content-Type": "application/json"})
        with self.browser.open(request, timeout=60) as response:
            return json.loads(response.read())


@pytest.fixture(scope="module")
def preview():
    """The preview in the "services" state with its controls, for every
    test of this section: starting it imports the tools server and the
    stand-in, a few seconds that each control would pay again."""
    port = _free_port()
    running = subprocess.Popen([sys.executable, str(PREVIEW), "services", "--controls",
                                "--port", str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _page(f"http://127.0.0.1:{port}/setup")
        yield Preview(port)
    finally:
        running.terminate()
        running.wait(timeout=10)


def test_a_jump_shows_that_step_in_its_family_state(preview):
    """Step 3 is the chat on the phone, shown and marked in the rail, also
    when the jump goes back from a later state."""
    preview.sets(jump="6")
    page = preview.sets(jump="3")

    assert 'data-step="phone"' in page and 'aria-current="step"' in page
    assert step_shown(page) == "phone"


def test_the_phone_step_shows_a_real_qr_code_to_the_macs_home_network_address(preview):
    page = preview.sets(jump="3")

    assert step_shown(page) == "phone"
    assert "<svg" in page and 'aria-label="QR code: http://192.168.1.20' in page
    assert 'aria-label="QR code: http://localhost' not in page


def test_another_device_gets_no_key_field(preview):
    """On this Mac step 4c asks for the AI server's address and key; another
    device gets the page that says the setup happens on the Mac, and no key
    field, as a key must not cross the home network."""
    assert 'name="api_key"' in preview.sets(jump="4c", device="mac")
    page = preview.sets(jump="4c", device="iphone")
    assert 'name="api_key"' not in page and "only-on-the-mac" in page
    preview.sets(device="mac")


def test_the_first_words_play_famstacks_recorded_voice(preview):
    """Play and Yes answer with the words and the audio of the recorded
    clips, the introduction for the Mac's memory."""
    page = preview.sets(jump="4f", memory="32")
    assert 'class="first-words"' in page

    hello = preview.posts("/dashboard/api/ai/first-words/welcome")
    intro = preview.posts("/dashboard/api/ai/first-words/intro")

    assert base64.b64decode(hello["audio"]) == (VOICE / "voice-hello.mp3").read_bytes()
    assert base64.b64decode(intro["audio"]) == (VOICE / "voice-intro-32.mp3").read_bytes()
    assert "32 GB" in intro["text"]


def step_shown(page: str) -> str:
    """The step the page's main column shows."""
    found = re.search(r'<section class="setup-step[^"]*" data-step="(\w+)"', page)
    return found.group(1) if found else ""
