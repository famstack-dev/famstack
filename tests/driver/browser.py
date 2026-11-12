"""A stacklet's web UI in Chrome, through Playwright, on this Mac.

The address is the stacklet's port on the instance's Mac. Element runs in a
browser only on localhost or HTTPS, and the setup page's Mac address is
`127.0.0.1:42002`, so for another Mac the browser goes through an ssh SOCKS
proxy to it: there, `localhost` is the instance's Mac. A port tunnel
(`ssh -L 42030`) would not do, because this Mac may run a stack of its own on
the same ports, and the browser would then reach that one without a word.
One browser profile per person and instance keeps a signed-in member the
same device on the next run: Element asks a second device to confirm itself,
and that cannot be done unattended.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from instance import Instance

ELEMENT_PORT = 42030
PROFILES = Path.home() / ".cache" / "famstack-driver" / "browsers"
DISMISSABLE = ("Dismiss", "Later", "Not now", "OK", "Ok")


@dataclass
class Visit:
    """One look at a web UI: which one, as whom, and what to bring back.

    `browse` turns it into a browser session: it finds the address, signs in
    to Element as `member` when asked, opens `room`, and saves a screenshot or
    a video.

        browse(rig, Visit(target="messages", member="lisa", room="Lisa Notes", shot="lisa.png"))
        browse(rig, Visit(target="memory", shot="wiki.png"))
    """

    target: str                    # a stacklet id or a URL
    member: str | None = None      # signs in to Element as this family member
    password: str | None = None    # default: the member's name, the installer's default
    room: str | None = None        # opened after signing in
    shot: str | None = None
    video: str | None = None
    settle: float = 2.0            # seconds the page gets before the screenshot
    width: int = 1280
    height: int = 820
    headed: bool = False


def browse(instance: Instance, visit: Visit) -> dict:
    url, port = _address(instance, visit.target)
    with instance_proxy(instance) as proxy:
        return _open(instance, visit, url, port, proxy)


@contextmanager
def instance_context(instance: Instance, language: str = "en", headed: bool = False):
    """A fresh Chrome context that sees the instance's Mac as its own localhost.

    Fresh means a first visit: no cookie, no paired device, no Element
    session. `language` is the browser's, which the setup page follows.
    Playwright sets `navigator.webdriver`, which keeps the setup page silent.

        with instance_context(rig, language="de") as context:
            context.new_page().goto("http://127.0.0.1:42002/setup")
    """
    from playwright.sync_api import sync_playwright

    with instance_proxy(instance) as proxy, sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=not headed, proxy=proxy)
        context = browser.new_context(locale=language, viewport={"width": 1280, "height": 900})
        try:
            yield context
        finally:
            browser.close()


@contextmanager
def instance_proxy(instance: Instance):
    """Playwright's proxy setting for the instance's Mac, or None for this one.

    An `ssh -D` SOCKS proxy on a free port of this Mac. Playwright keeps
    loopback addresses on the proxy (`<-loopback>`), so `localhost` and
    `127.0.0.1` resolve on the instance's Mac.
    """
    if not instance.host:
        yield None
        return
    port = _free_port()
    tunnel = subprocess.Popen(["ssh", "-N", "-o", "ExitOnForwardFailure=yes",
                               "-D", f"127.0.0.1:{port}", instance.host])
    try:
        _wait_for_port(port, tunnel)
        yield {"server": f"socks5://127.0.0.1:{port}"}
    finally:
        tunnel.terminate()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_for_port(port: int, tunnel: subprocess.Popen, seconds: float = 15) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if tunnel.poll() is not None:
            sys.exit("driver: the ssh proxy to the instance's Mac ended at once")
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    sys.exit("driver: the ssh proxy to the instance's Mac did not open")


def _address(instance: Instance, target: str) -> tuple[str, int | None]:
    if target.startswith("http"):
        return target, None
    port = next((s["port"] for s in instance.stack("list")["stacklets"]
                 if s["id"] == target and s.get("port")), None)
    if port is None:
        sys.exit(f"driver: stacklet {target!r} has no web port")
    host = "localhost" if port == ELEMENT_PORT else instance.address()
    return f"http://{host}:{port}", port



def _open(instance: Instance, visit: Visit, url: str, port: int | None, proxy: dict | None) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = _context(playwright, instance, visit, proxy)
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(url)
        if visit.member and port == ELEMENT_PORT:
            _sign_in(page, visit.member, visit.password or visit.member)
        if visit.room:
            _open_room(page, visit.room)
        time.sleep(visit.settle)
        if visit.shot:
            page.screenshot(path=visit.shot)
        report = {"url": url, "shot": visit.shot, "text": page.inner_text("body")}
        video = page.video
        context.close()
    report["video"] = video.path() if visit.video and video else None
    return report


def _context(playwright, instance: Instance, visit: Visit, proxy: dict | None):
    profile = PROFILES / instance.name / (visit.member or "anonymous")
    size = {"width": visit.width, "height": visit.height}
    recording = {"record_video_dir": visit.video, "record_video_size": size} if visit.video else {}
    return playwright.chromium.launch_persistent_context(
        str(profile), channel="chrome", headless=not visit.headed, viewport=size, proxy=proxy, **recording)


# ── Element ──────────────────────────────────────────────────────────────

def _sign_in(page, member: str, password: str) -> None:
    """Sign in, unless this profile already is."""
    signed_in = page.locator(".mx_SpacePanel")
    sign_in = page.get_by_text("Sign in", exact=True)
    signed_in.or_(sign_in).first.wait_for(timeout=40000)
    if signed_in.is_visible():
        return
    sign_in.first.click()
    page.get_by_label("Username").fill(member)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()
    _wait_until_in(page, member)
    _dismiss_prompts(page)


def _wait_until_in(page, member: str) -> None:
    confirm = page.get_by_text("Confirm your digital identity")
    page.locator(".mx_SpacePanel").or_(confirm).first.wait_for(timeout=60000)
    if not confirm.is_visible():
        return
    shot = Path(tempfile.gettempdir()) / f"driver-element-{member}-confirm.png"
    page.screenshot(path=str(shot))
    sys.exit(f"driver: Element asks {member} to confirm this device (\"Confirm your digital "
             f"identity\"), which cannot be done unattended. Screenshot: {shot}")


def _dismiss_prompts(page) -> None:
    for label in DISMISSABLE:
        button = page.get_by_role("button", name=label, exact=True)
        if button.count() and button.first.is_visible():
            button.first.click()


def _open_room(page, room: str) -> None:
    """Open a room by its name in the room list, inside the family space if need be."""
    item = page.locator(".mx_LeftPanel").get_by_text(room, exact=True).first
    time.sleep(1.5)
    if not item.is_visible():
        page.locator(".mx_SpacePanel").get_by_label("Family", exact=True).first.click()
        time.sleep(1.5)
    item.click()
    time.sleep(2)
