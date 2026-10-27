"""After the first run, in the browser: the "Setting up" widget, chat first.

The family is set up; the dashboard leads on with seven steps. This test
opens it in headless Chrome against core's tools server served by uvicorn,
presses Add on the chat's step and follows it to the end: the chat comes
up live on the step, the step ends done, and "Bring everyone in" opens
with each person and their chat address.

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

Needs the `browser` extra and Google Chrome:

    uv run --extra test --extra browser pytest tests/integration/test_setup_in_the_browser.py

With `SETUP_SHOTS=<dir>` it saves screenshots of each state, at 390 and
1280 px wide. About 10 s a width.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

playwright = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

REPO = Path(__file__).resolve().parents[2]
TOOLS = REPO / "stacklets" / "core" / "tools-server"
SHOTS = os.environ.get("SETUP_SHOTS", "")
WIDTHS = {"390": {"width": 390, "height": 844}, "1280": {"width": 1280, "height": 900}}

FAMILY = {"name": "Simpson", "domain": "", "language": "en", "chat_server": "simpson", "people": [
    {"id": "homer", "name": "Homer", "relation": "parent", "words": "he", "role": "admin",
     "email": "homer@home.local"},
    {"id": "marge", "name": "Marge", "relation": "parent", "words": "she", "role": "member",
     "email": "marge@home.local"},
    {"id": "bart", "name": "Bart", "relation": "child", "words": "he", "role": "member",
     "email": "bart@home.local"},
    {"id": "lisa", "name": "Lisa", "relation": "child", "words": "she", "role": "member",
     "email": "lisa@home.local"},
]}
# What `stack up messages` reports on a first run, as the stand-in plays it.
CHAT_STEPS = ["Rendering environment", "Pulling images", "Starting containers",
              "Creating accounts for everyone", "Opening the family's rooms"]
BACKUP_STEPS = ["Rendering environment", "Checking backup-vault",
                "Keeping the disk's password in the Keychain", "Planning the nightly copy"]

# The backup's forms as `stack configure backup --json` sends them for an
# encrypted disk: the disk, its passphrase, the nightly time.
DISK_FORM = {"id": "backup.disk", "title": "Backup disk", "errors": {},
             "text": "Plug in your external disk (USB or Thunderbolt). It must be "
                     "APFS-formatted and currently mounted under /Volumes/.",
             "schema": {"type": "object", "required": ["disk"], "properties": {"disk": {
                 "type": "string", "title": "Disk volume name", "default": "backup-vault",
                 "description": "Mounted now: backup-vault (apfs), Macintosh HD (apfs).",
                 "examples": ["backup-vault", "Macintosh HD"], "minLength": 1}}}}
PASSPHRASE_FORM = {"id": "backup.passphrase", "title": "Backup disk encryption", "errors": {},
                   "text": "Disk 'backup-vault' is APFS-encrypted. We'll store the passphrase "
                           "in your login Keychain so the backup can unlock the disk unattended.",
                   "schema": {"type": "object", "required": ["passphrase"], "properties": {
                       "passphrase": {"type": "string", "title": "Passphrase for backup-vault",
                                      "writeOnly": True, "minLength": 1}}}}
TIME_FORM = {"id": "backup.schedule", "title": "Backup schedule", "errors": {},
             "text": "When should the nightly backup run?",
             "schema": {"type": "object", "required": ["time"], "properties": {"time": {
                 "type": "string", "title": "Time", "format": "time", "default": "02:00"}}}}
STEP_SECS = 0.7


class HostAPI:
    """The host API as the tools server reaches it, for an instance where
    core runs and nothing else is set up, until the chat is added."""

    def __init__(self, root: Path):
        self.root = root
        self.running = {"core"}
        self.asked: list[dict] = []

    def listing(self) -> dict:
        import stack.docker
        from stack import Stack

        for sid in self.running:
            (self.root / ".stack" / f"{sid}.setup-done").touch()
        stck = Stack(REPO, self.root / "data", instance_dir=self.root)
        saved = stack.docker.project_states, stack.docker.check_health
        stack.docker.project_states = lambda: {sid: "running" for sid in self.running}
        stack.docker.check_health = lambda url, headers=None: True
        try:
            return json.loads(json.dumps(stck.list(), default=str))
        finally:
            stack.docker.project_states, stack.docker.check_health = saved

    def answer(self, request: dict) -> list[dict]:
        self.asked.append(request)
        cmd = request.get("cmd")
        if cmd == "up" and request.get("stream"):
            return self.up(request["stacklet"])
        if cmd == "list":
            return [self.listing()]
        if cmd == "family":
            return [FAMILY]
        if cmd == "configure":
            return [self.configure(request.get("answers", {}))]
        if cmd == "chat_users" and "messages" in self.running:
            return [{"users": [{"id": f"@{p['id']}:simpson", "name": p["name"], "admin": p["role"] == "admin",
                                "last_seen": 1761560000000 if p["id"] == "homer" else None}
                               for p in FAMILY["people"]]}]
        return [{"error": f"{cmd} is not stood in"}]

    def configure(self, answers: dict) -> dict:
        """The backup's forms, one at a time; a disk that is not mounted is
        refused on its field."""
        if answers.get("disk") not in (None, "backup-vault", "Macintosh HD"):
            return {"form": {**DISK_FORM, "errors": {
                "disk": f"Volume /Volumes/{answers['disk']} is not mounted."}}}
        for needed, form in (("disk", DISK_FORM), ("passphrase", PASSPHRASE_FORM), ("time", TIME_FORM)):
            if needed not in answers:
                return {"form": form}
        return {"ready": True}

    def up(self, sid: str) -> list[dict]:
        """The run's events, each a line, the result last; it runs after."""
        events = []
        for i, text in enumerate(CHAT_STEPS if sid == "messages" else BACKUP_STEPS, start=1):
            events.append({"id": i, "type": "step.done", "time": _now(), "data": {"text": text},
                           "_wait": STEP_SECS})
        self.after_up = sid
        events.append({"id": len(events) + 1, "type": "result", "time": _now(),
                       "data": {"ok": True, "stacklet": sid, "healthy": True}})
        return events


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@pytest.fixture
def host_api(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    (root / ".stack").mkdir(parents=True)
    (root / "stack.toml").write_text(
        f'[core]\ndata_dir = "{tmp_path / "data"}"\nhost = "localhost"\n'
        f'extension_dirs = ["{tmp_path / "extensions"}"]\n\n[messages]\nserver_name = "simpson"\n')
    sys.path.insert(0, str(REPO / "lib"))
    api = HostAPI(root)
    listener = socket.create_server(("127.0.0.1", 0))

    def serve(conn):
        with conn, conn.makefile("rb") as reader:
            request = json.loads(reader.readline())
            for line in api.answer(request):
                time.sleep(line.pop("_wait", 0))
                conn.sendall((json.dumps(line) + "\n").encode())
            if getattr(api, "after_up", None):
                api.running.add(api.after_up)
                api.after_up = None

    def accept():
        while True:
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(conn,), daemon=True).start()

    threading.Thread(target=accept, daemon=True).start()
    monkeypatch.setenv("STACK_API_HOST", "127.0.0.1")
    monkeypatch.setenv("STACK_API_PORT", str(listener.getsockname()[1]))
    yield api
    listener.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def dashboard(host_api, monkeypatch):
    monkeypatch.setenv("MEMORY_CARDS_URL", f"http://127.0.0.1:{_free_port()}")
    for path in (REPO / "lib", TOOLS):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    import live_routes
    import server
    from stack.live.hub import Hub
    from stack.live.jobs import Jobs

    hub = Hub()
    monkeypatch.setattr(live_routes, "hub", hub)
    monkeypatch.setattr(live_routes, "jobs", Jobs(hub))
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


def _shot(page, width: str, name: str, *, whole_page: bool = True) -> None:
    """A screenshot of the whole page, or of the window: a sheet is fixed
    over the window, and a whole-page shot cuts its backdrop at the edge."""
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{width}-{name}.png"), full_page=whole_page)


def _step_status(page, sid: str) -> str:
    return page.locator(f'fs-setup .fs-step:has(details[data-step="{sid}"])').get_attribute("data-status")


@pytest.mark.parametrize("width", list(WIDTHS))
def test_the_chat_is_started_from_its_step(chrome, dashboard, host_api, width):
    context = chrome.new_context(viewport=WIDTHS[width], locale="en-US",
                                 device_scale_factor=2 if width == "390" else 1)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(dashboard)

    # Right after the first run: two of seven done, the chat open with Add.
    page.wait_for_selector("fs-setup .setup-count")
    assert page.locator("fs-setup .setup-count").text_content() == "2 of 7 done"
    assert _step_status(page, "chat") == "next"
    add = page.locator('fs-setup details[data-step="chat"] .tile-add[data-target="messages"]')
    assert add.is_visible()
    assert "Start the chat below" in page.locator(".lede").text_content()
    if width == "1280":
        # Beside the steps, "Your stack" keeps its own height.
        stack = page.locator('fs-widget[data-widget="stack"]').bounding_box()
        setup = page.locator('fs-widget[data-widget="setup"]').bounding_box()
        assert stack["height"] < setup["height"] / 2
    page.wait_for_timeout(300)
    _shot(page, width, "1-after-the-first-run")

    # Add: the chat comes up live on its step.
    add.click()
    page.wait_for_selector('fs-setup .tile[data-id="messages"] .tile-step:nth-child(3)', timeout=10000)
    _shot(page, width, "2-chat-starting")

    # The run ends: the chat's step is done, and everyone is brought in next.
    page.wait_for_function(
        "() => document.querySelector('fs-setup .setup-count')?.textContent === '3 of 7 done'",
        timeout=20000)
    assert _step_status(page, "chat") == "done"
    assert _step_status(page, "people") == "next"
    assert page.locator("fs-setup .setup-address").all_text_contents() == [
        "@homer:simpson", "@marge:simpson", "@bart:simpson", "@lisa:simpson"]
    assert page.locator("fs-setup .setup-signed").all_text_contents() == [
        "Signed in", "Not yet", "Not yet", "Not yet"]
    # The intro follows without a reload: the chat runs now.
    assert "the chat runs on it" in page.locator(".lede").text_content()
    page.wait_for_timeout(300)
    _shot(page, width, "3-chat-running-people-open")

    started = [r for r in host_api.asked if r.get("cmd") == "up"]
    assert started == [{"cmd": "up", "stacklet": "messages", "stream": True}]
    assert errors == []
    context.close()


@pytest.mark.parametrize("width", list(WIDTHS))
def test_the_backup_is_added_by_answering_its_forms(chrome, dashboard, host_api, width):
    """On this Mac the backup's Add asks its forms in a sheet: the disk, a
    wrong one refused on its field, the passphrase as a password, the time;
    then the job starts and the step ends done. A sheet closed early applied
    nothing and started nothing."""
    context = chrome.new_context(viewport=WIDTHS[width], locale="en-US",
                                 device_scale_factor=2 if width == "390" else 1)
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(dashboard)
    page.click('fs-setup details[data-step="backup"] summary')
    add = page.locator('fs-setup .tile-add[data-target="backup"][data-forms]')
    sheet = "dialog.fs-sheet"
    go = f'{sheet} .fs-button[data-kind="go"]'

    # Closed before ready: nothing applied, nothing started.
    add.click()
    page.wait_for_selector(f'{sheet} form[data-form="backup.disk"]')
    page.click(f'{sheet} .fs-button[data-kind="soft"]')
    page.wait_for_selector(sheet, state="detached")
    assert not [r for r in host_api.asked if r.get("cmd") == "up"]

    # The disk, from the volumes mounted now.
    add.click()
    page.wait_for_selector(f'{sheet} form[data-form="backup.disk"]')
    page.wait_for_timeout(300)
    _shot(page, width, "4-backup-disk", whole_page=False)
    page.fill(f'{sheet} input[name="disk"]', "old-disk")
    page.click(go)
    page.wait_for_selector(f"{sheet} .fs-form-error")
    assert page.locator(f"{sheet} .fs-form-error").text_content() == "Volume /Volumes/old-disk is not mounted."
    _shot(page, width, "5-backup-wrong-disk", whole_page=False)

    # The passphrase, a password field; then the time.
    page.click(f'{sheet} .fs-form-pick:text-is("backup-vault")')
    page.click(go)
    page.wait_for_selector(f'{sheet} input[name="passphrase"][type="password"]')
    page.fill(f'{sheet} input[name="passphrase"]', "correct horse")
    _shot(page, width, "6-backup-passphrase", whole_page=False)
    page.click(go)
    page.wait_for_selector(f'{sheet} input[name="time"][type="time"]')
    page.click(go)

    # Ready: the sheet closes, the job runs, the step ends done.
    page.wait_for_selector(sheet, state="detached")
    page.wait_for_function(
        "() => document.querySelector('fs-setup .fs-step:has(details[data-step=\"backup\"])')"
        "?.dataset.status === 'done'", timeout=20000)
    page.wait_for_timeout(300)
    _shot(page, width, "7-backup-done")

    answered = [r["answers"] for r in host_api.asked if r.get("cmd") == "configure"]
    assert answered[-1] == {"disk": "backup-vault", "passphrase": "correct horse", "time": "02:00"}
    assert [r for r in host_api.asked if r.get("cmd") == "up"] == [
        {"cmd": "up", "stacklet": "backup", "stream": True}]
    assert errors == []
    context.close()

