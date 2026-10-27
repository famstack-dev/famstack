"""The first run in the browser, end to end, against a local tools server.

The terminal installs core and opens the dashboard; everything else happens
in the browser. This test plays that part of a new install for real: an
instance as the installer leaves it (stack.toml, no users.toml), the host
API running this checkout's `./stack` on it, core's tools server served by
uvicorn on a free port, and headless Chrome. It answers the moments as a
person does, sets up the family on "Who else is at home?", and holds the
outcome to account: users.toml and stack.toml hold the family the page
set up, the dashboard enters with the family's pills, and this device
remembers the one who looks after the server.

It also measures the two hand-overs, where one screen leaves and the next
enters under it (the rule in components/moment.js): from the first frame
every element of the entering screen is at opacity 0, and none is shown,
taken away and shown again.

Needs the `browser` extra and Google Chrome:

    uv run --extra test --extra browser pytest tests/integration/test_first_run_in_the_browser.py

With `FIRST_RUN_SHOTS=<dir>` it saves a screenshot of each step, at 390
and at 1280 px wide. About 25 s a width: the moments are timed for people.
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import tomllib
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "stacklets" / "core" / "tools-server"
SHOTS = os.environ.get("FIRST_RUN_SHOTS", "")
WIDTHS = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 860}}

# What the page measures from the click (or the moment's leaving) on:
# every frame, the opacity each part of the entering screen has on screen,
# its own times its ancestors' up to the screen.
MEASURE = """
({ name, screen, start }) => {
  const parts = () => [...document.querySelectorAll(screen)].flatMap((root) =>
    [...root.children, ...root.querySelectorAll(".fs-pill, fs-widget, .first-run-card > *")]);
  const seen = (el) => {
    let o = 1;
    for (let at = el; at && at !== document.body; at = at.parentElement) {
      const style = getComputedStyle(at);
      if (style.display === "none") return 0;
      o *= Number(style.opacity);
    }
    return o;
  };
  window[name] = new Promise((resolve) => {
    const go = () => {
      const frames = [], t0 = performance.now();
      const sample = () => {
        frames.push(parts().map(seen));
        if (performance.now() - t0 < 4200) requestAnimationFrame(sample);
        else resolve(frames);
      };
      requestAnimationFrame(sample);
    };
    if (start === "click") {
      document.addEventListener("click", (e) => { if (e.target.closest(".fs-moment-go")) go(); },
                                { capture: true, once: true });
    } else {
      new MutationObserver((changes, observer) => {
        if (document.querySelector(".fs-moment.fs-leaving")) { observer.disconnect(); go(); }
      }).observe(document.body, { subtree: true, attributes: true, attributeFilter: ["class"] });
    }
  });
}
"""


def handed_over_cleanly(frames: list[list[float]]) -> tuple[bool, str]:
    """At the first frame nothing of the new screen shows; afterwards no part
    that was shown goes away and comes back."""
    if not frames or not frames[0]:
        return False, "nothing was measured"
    if max(frames[0]) > 0.05:
        return False, f"first frame shows parts at {max(frames[0]):.2f}"
    for part in range(len(frames[0])):
        shown = False
        for frame in frames:
            if part >= len(frame):
                break
            if frame[part] > 0.5:
                shown = True
            elif shown and frame[part] < 0.05:
                return False, f"part {part} was shown, then taken away"
    return True, f"{len(frames)} frames, {len(frames[0])} parts"


# ── The instance, the host API and the tools server ──────────────────────

@pytest.fixture
def instance(tmp_path, monkeypatch):
    """An instance as `./stack` leaves it before the family: stack.toml with
    nobody named, the admin password, no users.toml."""
    sys.path.insert(0, str(REPO / "lib"))
    from stack import installer

    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    installer.write_stack_toml("", "", "Europe/Berlin", "en", root=root)
    toml = root / "stack.toml"
    toml.write_text(toml.read_text().replace('data_dir = "~/famstack-data"',
                                             f'data_dir = "{tmp_path / "data"}"'))
    (root / ".stack" / "secrets.toml").write_text('global__ADMIN_PASSWORD = "s-test"\n')
    monkeypatch.setenv("STACK_DIR", str(root))
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    return root


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def host_api(instance, monkeypatch):
    """The host API behind a loopback listener: the checkout's own `./stack`,
    on the instance in `STACK_DIR`."""
    path = REPO / "stacklets" / "core" / "famstack-api.py"
    loader = importlib.machinery.SourceFileLoader("famstack_api_first_run", str(path))
    module = importlib.util.module_from_spec(
        importlib.util.spec_from_file_location("famstack_api_first_run", path, loader=loader))
    loader.exec_module(module)
    listener = socket.create_server(("127.0.0.1", 0))

    def serve():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=module.handle_client, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(listener.getsockname()[1]))
    yield
    listener.close()


@pytest.fixture
def dashboard(host_api, monkeypatch):
    """Core's tools server, served by uvicorn on a free port. The memory is
    not running, so the activity widget says so; nothing here needs it."""
    monkeypatch.setenv("MEMORY_CARDS_URL", f"http://127.0.0.1:{_free_port()}")
    for path in (REPO / "lib", TOOLS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import server

    port = _free_port()
    running = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=running.run, daemon=True)
    thread.start()
    for _ in range(100):
        if running.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/dashboard"
    running.should_exit = True
    thread.join(timeout=5)


@pytest.fixture(scope="module")
def chrome():
    with playwright.sync_playwright() as p:
        try:
            browser = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # no Chrome on this Mac
            pytest.skip(f"Google Chrome is needed: {e}")
        yield browser
        browser.close()


# ── The first run ────────────────────────────────────────────────────────

def _visible(page, selector: str, timeout: float = 15000) -> None:
    """Wait until `selector` is fully faded in."""
    page.wait_for_function(
        "(s) => { const e = document.querySelector(s); return e && getComputedStyle(e).opacity === '1'; }",
        arg=selector, timeout=timeout)


def _shot(page, width: str, name: str, *, whole_page: bool = False) -> None:
    """A screenshot of the window, or of the whole page. A moment is fixed over
    the window, and a whole-page shot is slow enough to catch it leaving."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{width}-{name}.png"), full_page=whole_page)


@pytest.mark.parametrize("width", list(WIDTHS))
def test_the_family_is_set_up_in_the_browser(chrome, dashboard, instance, width):
    context = chrome.new_context(viewport=WIDTHS[width], locale="en-US",
                                 device_scale_factor=2 if width == "390" else 1)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(dashboard)

    # hello.: the name, then the greeting, which moves on by itself.
    _visible(page, ".fs-moment-go")
    _shot(page, width, "1-opening")
    page.fill(".fs-moment input", "Homer")
    page.click(".fs-moment-go")
    page.wait_for_function("() => document.querySelector('.fs-moment-line')?.textContent.includes('Homer')")
    page.evaluate(MEASURE, {"name": "greetHandover", "screen": ".first-run", "start": "leave"})
    # The greeting leaves by itself shortly after its sentence is in: the
    # shot is taken between the two.
    page.wait_for_function(
        "() => { const m = document.querySelector('.fs-moment'), s = m?.querySelector('.fs-moment-sub');"
        " return s && !m.classList.contains('fs-leaving') && Number(getComputedStyle(s).opacity) > 0.9; }",
        polling="raf")
    _shot(page, width, "2-greeting")

    # Who else is at home?
    page.wait_for_selector(".first-run-card")
    greet_frames = page.evaluate("window.greetHandover")
    page.wait_for_timeout(3600)   # the entrance
    page.fill(".first-run input[name=family]", "Simpson")
    for name in ("Marge", "Bart", "Lisa"):
        page.fill(".first-run-add input", name)
        page.press(".first-run-add input", "Enter")
    page.click(".first-run-people .fs-pill:nth-child(4)")
    page.click(".fs-family-editor [role=radio][data-value=she]")
    page.wait_for_timeout(700)
    _shot(page, width, "3-family-editor", whole_page=True)
    page.click(".first-run-foot .fs-button")

    # Welcome home, then the dashboard enters under the fading moment.
    page.wait_for_function("() => document.querySelector('.fs-moment-line')?.textContent.includes('Welcome home')")
    _visible(page, ".fs-moment-go")
    _shot(page, width, "4-welcome")
    page.evaluate(MEASURE, {"name": "welcomeHandover", "screen": "main.dashboard", "start": "click"})
    page.click(".fs-moment-go")
    welcome_frames = page.evaluate("window.welcomeHandover")
    page.wait_for_timeout(800)
    _shot(page, width, "5-dashboard", whole_page=True)

    # The files hold the family the page set up.
    with open(instance / "users.toml", "rb") as f:
        people = {u["id"]: u for u in tomllib.load(f)["users"]}
    assert list(people) == ["homer", "marge", "bart", "lisa"]
    assert people["homer"]["role"] == "admin"
    assert [p["relation"] for p in people.values()] == ["parent", "parent", "child", "child"]
    assert people["lisa"]["words"] == "she"
    with open(instance / "stack.toml", "rb") as f:
        config = tomllib.load(f)
    assert config["core"]["stack_owner"] == "Simpson"
    assert config["core"]["language"] == "en"
    assert config["messages"]["server_name"] == "simpson"

    # The dashboard: the family's pills, the one who looks after the server
    # remembered and greeted by name.
    assert page.locator(".family-bar .fs-pill-name").all_text_contents() == ["Homer", "Marge", "Bart", "Lisa"]
    assert page.locator(".family-bar .fs-pill[aria-pressed=true] .fs-pill-name").text_content() == "Homer"
    # As Homer says it: Bart has no words chosen, so he is a child, not a son.
    assert page.locator(".family-bar .fs-pill-label").all_text_contents() == [
        "you", "partner", "child", "daughter"]
    assert "Homer." in page.locator(".greeting").text_content()
    assert page.evaluate("localStorage.getItem('fs-viewer')") == "homer"

    clean, said = handed_over_cleanly(greet_frames)
    assert clean, f"greeting to setup: {said}"
    clean, said = handed_over_cleanly(welcome_frames)
    assert clean, f"welcome to dashboard: {said}"
    if SHOTS:
        (Path(SHOTS) / f"{width}-handover.json").write_text(json.dumps({
            "greeting_to_setup": {"frames": len(greet_frames), "parts": len(greet_frames[0]),
                                  "first_frame_max_opacity": max(greet_frames[0])},
            "welcome_to_dashboard": {"frames": len(welcome_frames), "parts": len(welcome_frames[0]),
                                     "first_frame_max_opacity": max(welcome_frames[0])},
        }, indent=2))
    assert errors == []
    context.close()
