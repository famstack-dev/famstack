"""A stacklet's web UI in Chrome, through Playwright, on this Mac.

The address is the stacklet's port on the instance's Mac. Element runs in a
browser only on localhost or HTTPS, so for Element on another Mac the driver
opens an ssh tunnel and browses localhost, as the admin guide tells a family
to. One browser profile per person and instance keeps a signed-in member the
same device on the next run: Element asks a second device to confirm itself,
and that cannot be done unattended.
"""

from __future__ import annotations

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
    with _tunnel(instance, port):
        return _open(instance, visit, url, port)


def _address(instance: Instance, target: str) -> tuple[str, int | None]:
    if target.startswith("http"):
        return target, None
    port = next((s["port"] for s in instance.stack("list")["stacklets"]
                 if s["id"] == target and s.get("port")), None)
    if port is None:
        sys.exit(f"driver: stacklet {target!r} has no web port")
    host = "localhost" if port == ELEMENT_PORT else instance.address()
    return f"http://{host}:{port}", port


@contextmanager
def _tunnel(instance: Instance, port: int | None):
    if not (instance.host and port == ELEMENT_PORT):
        yield
        return
    tunnel = subprocess.Popen(["ssh", "-N", "-L", f"{port}:127.0.0.1:{port}", instance.host])
    time.sleep(2)
    try:
        yield
    finally:
        tunnel.terminate()


def _open(instance: Instance, visit: Visit, url: str, port: int | None) -> dict:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        context = _context(playwright, instance, visit)
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


def _context(playwright, instance: Instance, visit: Visit):
    profile = PROFILES / instance.name / (visit.member or "anonymous")
    size = {"width": visit.width, "height": visit.height}
    recording = {"record_video_dir": visit.video, "record_video_size": size} if visit.video else {}
    return playwright.chromium.launch_persistent_context(
        str(profile), channel="chrome", headless=not visit.headed, viewport=size, **recording)


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
