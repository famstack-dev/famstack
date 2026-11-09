"""The dashboard preview, as a person and an agent run it.

`tools/dashboard_preview.py` serves the real dashboard against the made-up
family of `tests/dashboard_host.py`, for one state of their setup: in a
browser, or as screenshots. These tests run it as a command, the way it is
used, and read what it serves and writes. Nothing runs Docker or reaches
the network.

Needs the `browser` extra; the screenshots need Google Chrome too:

    uv run --extra test --extra browser pytest tests/integration/test_dashboard_preview.py
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[2]
PREVIEW = REPO / "tools" / "dashboard_preview.py"


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
    chat app step is done, and there is no link to the chat's web page,
    which a phone cannot open over plain HTTP."""
    port = _free_port()
    preview = subprocess.Popen([sys.executable, str(PREVIEW), "signed", "--de", "--phone",
                                "--port", str(port)],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        page = _page(f"http://127.0.0.1:{port}/dashboard")
    finally:
        preview.terminate()
        preview.wait(timeout=10)

    assert "Die Chat-App einrichten" in page
    assert "Du bist angemeldet" in page
    assert ":42030" not in page


def test_an_agent_gets_screenshots_of_every_state(tmp_path):
    pytest.importorskip("playwright.sync_api")
    ran = subprocess.run([sys.executable, str(PREVIEW), "all", "--shots", str(tmp_path)],
                         capture_output=True, text=True, timeout=180)
    if ran.returncode and "Executable doesn't exist" in ran.stderr + ran.stdout:
        pytest.skip("Google Chrome is needed")

    assert ran.returncode == 0, ran.stderr
    written = sorted(p.name for p in tmp_path.glob("*.png"))
    assert written == sorted(f"{state}-en-mac-{width}.png"
                             for state in ("fresh", "chat", "signed", "services", "done")
                             for width in ("390", "1280"))
    assert ran.stdout.splitlines() == [str(tmp_path / name) for name in
                                       (f"{state}-en-mac-{width}.png"
                                        for state in ("fresh", "chat", "signed", "services", "done")
                                        for width in ("390", "1280"))]
