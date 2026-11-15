"""setup_walk: a first-time admin walks the setup page, step by step.

    driver --host famstack-e2e tty "./stack" --answer "Start the installation=y"
    driver --host famstack-e2e setup --language de --out run/ --ai-key - --document letter.jpg \
        [--phone] [--one-by-one]

The release gate's install test. After `./stack` has started core, this walks
what a person does next: the dashboard link from the terminal, the first run
(name, family, people), then every step the setup page shows (the chat, the
phone, the AI, photos and documents, the first document, done). Each
step leaves a screenshot, its visible text and its time in `<out>/protocol.md`,
and a gate per step: the page moved on by itself, within the step's time.

A step that does not move on is the finding, so the walk stops there and says
which step, with the page's text and `./stack doctor`'s answer. It never
works around a stuck step, because a person could not either. A step that
raises (a wait that times out, a missing `--ai-key`, a phone screen that does
not come) is a stuck step too: the protocol is written all the same, because
the protocol is what the release gate reads.

It finds its way by the page's structure (`section.setup-step[data-step]`,
`button.tile-add`, `[data-secret]`, `[data-watch]`), never by its words, so
the same walk runs in every language the stack speaks. The words are what
the protocol records for a person to read.

With `--phone`, the phone on this Mac's USB does the phone step: the QR
code's link opens on it through adb, as a scan would, and Element X signs in
with the server, user name and password the step shows (`phone.py`), timed.
Without it the walk takes the step's "later". `--one-by-one` starts photos,
documents and memory one at a time, each once core answers again after the
previous start, instead of "Start all three".

What it does not do, and the protocol's manual part covers
(`release-gate.md`): install the AI app, confirm a new device's identity in
Element X, judge the copy, and hear the voice. The first document is posted
with the stack's chat command as the admin, because a browser session of
the walk's own would be a new device that Element asks to confirm.

The AI key comes from stdin and goes only into the page's field: it is never
written to the protocol, a log or the command line.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

import chat
import phone as phone_app
from browser import instance_context
from instance import Instance
from narration import Narrator, first_line

# How long each step may take before it counts as stuck. A start pulls images
# on a first install; the first document waits for OCR and the model.
STEP_SECONDS = {"family": 60, "chat": 300, "phone": 600, "ai": 300,
                "services": 900, "try": 900, "ready": 30}
POLL_SECONDS = 2
# Starting photos or documents recreates core; core counts as back once its
# dashboard has answered for this long without a break.
CORE_SETTLED_SECONDS = 20
CORE_BACK_SECONDS = 300
# How long the setup page may take to see the phone open the QR code's link.
REACH_SECONDS = 60


class StepFailed(Exception):
    """A step's own check failed; the message says what the page or the phone showed."""


# ── The walk ─────────────────────────────────────────────────────────────

@dataclass
class Walk:
    """What a walk is given, and what it found.

        walk = Walk(out=Path("run"), language="de", admin="Homer", family="Simpson",
                    people=["Marge", "Bart", "Lisa"], ai_key="...", document=Path("letter.jpg"))
        walk_setup(rig, walk, Narrator())   # walk.steps: one record per step
    """

    out: Path
    language: str = "en"
    admin: str = "Homer"
    family: str = "Simpson"
    people: list[str] = field(default_factory=lambda: ["Marge", "Bart", "Lisa"])
    ai_key: str | None = None
    document: Path | None = None
    phone: bool = False
    one_by_one: bool = False
    until: str | None = None        # stop before this step, with the protocol written
    instance: Instance | None = None
    steps: list[dict] = field(default_factory=list)


def walk_setup(instance: Instance, walk: Walk, log: Narrator) -> bool:
    """Walk the install from the dashboard link to "done"; True when every gate passed.

    Writes `<out>/protocol.md` and `<out>/NN-<step>.png`, also when a step
    fails. Changes the instance as a person would: it sets up the family,
    starts services and posts one document.
    """
    walk.out.mkdir(parents=True, exist_ok=True)
    walk.instance = instance
    try:
        passed = _walk(instance, walk, log)
    except (Exception, SystemExit) as error:
        # Outside any step: the link, the browser or the ssh proxy. Steps
        # record their own failures with a screenshot (`_steps`).
        walk.steps.append({"step": "walk", "ok": False, "seconds": 0, "text": "", "note": _reason(error)})
        log.failed(f"the walk ended: {_reason(error)}")
        passed = False
    clean, said = _doctor(instance)
    walk.steps.append({"step": "doctor", "ok": clean, "seconds": 0, "text": said, "note": ""})
    log.ok("doctor: no errors") if clean else log.failed("doctor names an error")
    _write_protocol(walk)
    return passed and walk.steps[-1]["ok"]


def _walk(instance: Instance, walk: Walk, log: Narrator) -> bool:
    """The browser part of the walk: the first run or the setup page, then the steps."""
    family_set = bool(instance.stack("family").get("people"))
    page = "setup" if family_set else "dashboard"
    link = instance.stack("devices", "admin-link", "--page", page)["link"]
    with instance_context(instance, language=walk.language) as context:
        setup = context.new_page()
        if family_set:
            # A walk that stopped earlier goes on where the install is: the
            # first run is done once the family has people.
            log.when("the family is set up already: the admin opens the setup page")
            setup.goto(link)
            return _steps(setup, walk, log)
        return _first_run(setup, link, walk, log) and _steps(setup, walk, log)


def _first_run(page, link: str, walk: Walk, log: Narrator) -> bool:
    """The dashboard's first run: your name, the family, the people, then "Start"."""
    log.when(f"the admin opens the terminal's dashboard link and names the family {walk.family}")
    started = time.monotonic()
    try:
        ok = _name_the_family(page, link, walk)
    except (Exception, SystemExit) as error:
        _record(page, walk, "family", started, False, log, note=_reason(error))
        return False
    # Recorded before the welcome is clicked away, so the protocol shows it.
    _record(page, walk, "family", started, ok, log)
    welcome = page.locator(".fs-moment-go")
    if ok and welcome.is_visible():
        welcome.click()
    if ok and not _until(lambda: "/setup" in page.url, 30):
        _record(page, walk, "setup page", time.monotonic(), False, log,
                note="the welcome did not lead to the setup page")
        return False
    return ok


def _name_the_family(page, link: str, walk: Walk) -> bool:
    """Fill the first run and press Start; True once the welcome or the setup page shows."""
    page.goto(link)
    name = page.locator(".fs-moment input")
    name.wait_for(timeout=30000)
    name.fill(walk.admin)
    name.press("Enter")
    page.locator("input[name=family]").wait_for(timeout=30000)
    page.locator("input[name=family]").fill(walk.family)
    for person in walk.people:
        page.locator(".first-run-add input").fill(person)
        page.locator(".first-run-add input").press("Enter")
    page.locator(".first-run-foot button[data-kind=go]").click()
    welcome = page.locator(".fs-moment-go")
    return _until(lambda: welcome.is_visible() or "/setup" in page.url, STEP_SECONDS["family"])


def _steps(setup, walk: Walk, log: Narrator) -> bool:
    """Every step of the setup page in turn, until "ready" or a stuck step."""
    done: set[str] = set()
    while True:
        step = _current(setup)
        if step == "ready":
            _record(setup, walk, "ready", time.monotonic(), True, log)
            return True
        if step == walk.until:
            _record(setup, walk, step, time.monotonic(), True, log, note="the walk stops here (--until)")
            return True
        if step in done or step not in ACTIONS:
            _record(setup, walk, step or "unknown", time.monotonic(), False, log,
                    note="the page shows this step again" if step in done else "no step on the page")
            return False
        log.when(f"the admin does step {step!r}")
        started = time.monotonic()
        try:
            ACTIONS[step](setup, walk, log)
            ok = _until(lambda: _current(setup) != step, STEP_SECONDS[step], keep_going=lambda: _go_on(setup))
        except (Exception, SystemExit) as error:
            _record(setup, walk, step, started, False, log, note=_reason(error))
            return False
        _record(setup, walk, step, started, ok, log)
        if not ok:
            return False
        done.add(step)


# ── One step each ────────────────────────────────────────────────────────

def _start_chat(setup, walk: Walk, log: Narrator) -> None:
    setup.locator("section.setup-step button.tile-add").first.click()


def _phone(setup, walk: Walk, log: Narrator) -> None:
    """Open the QR code's link on the phone and sign in to Element X, else "later"."""
    if not walk.phone:
        setup.locator("a.setup-later").first.click()
        return
    label = setup.locator(".setup-qr svg").first.get_attribute("aria-label") or ""
    link = label.split(": ", 1)[-1]
    subprocess.run(["adb", "shell", "am", "start", "-a", "android.intent.action.VIEW", "-d", link],
                   check=True, capture_output=True)
    log.detail(f"the phone opened {link}")
    reached = setup.locator("li.setup-part[data-part=reach][data-status=done]")
    if not _until(lambda: reached.count() > 0, REACH_SECONDS):
        raise StepFailed(f"the phone opened the link, and the page did not mark it reached in {REACH_SECONDS}s")
    server = next(v for v in setup.locator("section.setup-step .setup-value").all_inner_texts()
                  if v.startswith("http"))
    secret = setup.locator("section.setup-step [data-secret]").first
    password = secret.get_attribute("data-secret") or secret.inner_text()
    timing = phone_app.sign_in(server, walk.admin.lower(), password, log)
    walk.steps.append({"step": "phone sign-in", "ok": timing["signed_in"], "seconds": timing["server_seconds"]
                       + timing["sign_in_seconds"], "text": timing["after"],
                       "note": f"server screen {timing['server_seconds']}s, "
                               f"sign-in {timing['sign_in_seconds']}s"})
    if not timing["signed_in"]:
        raise StepFailed(f"Element X did not sign in: {timing['after']}")


def _ai(setup, walk: Walk, log: Narrator) -> None:
    """Use the AI app that is on the Mac, with its key."""
    if not walk.ai_key:
        raise SystemExit("driver: the AI step needs the app's key: --ai-key - and the key on stdin")
    setup.locator('a[href*="ai=connect"]').first.click()
    key = setup.locator("input[name=api_key]")
    key.wait_for(timeout=15000)
    key.fill(walk.ai_key)
    key.press("Enter")


def _services(setup, walk: Walk, log: Narrator) -> None:
    """Start photos, documents and memory: all three at once, or one by one."""
    if not walk.one_by_one:
        setup.locator(".setup-start-all").first.click()
        return
    for card in setup.locator("section.setup-step article[data-id]").all():
        service = card.get_attribute("data-id")
        start = card.locator("button.tile-add")
        if not start.count():
            continue
        start.first.click()
        running = _until(lambda: card.get_attribute("data-status") == "running", STEP_SECONDS["services"])
        if not running:
            raise StepFailed(f"{service} did not start in {STEP_SECONDS['services']}s")
        log.detail(f"{service}: running")
        _until_core_settled(setup)


def _until_core_settled(page) -> None:
    """Wait until core's dashboard has answered for `CORE_SETTLED_SECONDS` in a row.

    Raises `StepFailed` when it has not within `CORE_BACK_SECONDS`.
    """
    health = page.url.split("/setup")[0] + "/dashboard/api/health"
    since = None
    deadline = time.monotonic() + CORE_BACK_SECONDS
    while time.monotonic() < deadline:
        try:
            ok = page.request.get(health, timeout=3000).ok
        except Exception:
            ok = False
        since = (since or time.monotonic()) if ok else None
        if since and time.monotonic() - since >= CORE_SETTLED_SECONDS:
            return
        time.sleep(POLL_SECONDS)
    raise StepFailed(f"core did not answer steadily for {CORE_SETTLED_SECONDS}s within {CORE_BACK_SECONDS}s")


def _first_document(setup, walk: Walk, log: Narrator) -> None:
    """Post the document in the documents room as the admin."""
    if not walk.document or not walk.instance:
        raise SystemExit("driver: the try step needs a document: --document <photo or PDF>")
    sent = chat.send(walk.instance, walk.admin.lower(), "documents", walk.document)
    log.detail(f"posted {walk.document.name} in documents as {walk.admin.lower()}: {sent['event_id']}")


ACTIONS = {"chat": _start_chat, "phone": _phone, "ai": _ai, "services": _services,
           "try": _first_document}


# ── Reading the page ─────────────────────────────────────────────────────

def _current(page) -> str | None:
    """The step the page shows now, by its section's `data-step`.

    Waits for the section, because the page is still loading right after a
    step moved on; None only when no step appears at all.
    """
    section = page.locator("section.setup-step").first
    try:
        section.wait_for(timeout=15000)
    except Exception:
        return None
    return section.get_attribute("data-step")


def _go_on(page) -> None:
    """Follow a step's own "next" once the step says it is done.

    Some steps finish in place ("Your AI is ready") and wait for a click on
    their go button; a person clicks it, so the walk does too.
    """
    go = page.locator("section.setup-step .setup-act a.fs-button[data-kind=go]:visible")
    if go.count():
        go.first.click()


def _until(done, seconds: int, keep_going=None) -> bool:
    """Poll `done` until it holds or `seconds` pass; `keep_going` runs between polls."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if done():
            return True
        if keep_going:
            keep_going()
        time.sleep(POLL_SECONDS)
    return bool(done())


def _record(page, walk: Walk, step: str, started: float, ok: bool, log: Narrator, note: str = "") -> None:
    """One step into the protocol: its screenshot, text, time and gate.

    A page that cannot be read any more (a closed browser) still gives a
    record, without screenshot or text, because the record is the finding.
    """
    number = len(walk.steps) + 1
    shot = walk.out / f"{number:02d}-{step}.png"
    try:
        page.screenshot(path=str(shot), full_page=True)
        main = page.locator("section.setup-step, .first-run, .fs-moment").first
        text = (main.inner_text() if main.count() else page.inner_text("body")).strip()
    except Exception as error:
        shot, text = None, f"(the page could not be read: {_reason(error)})"
    seconds = round(time.monotonic() - started)
    walk.steps.append({"step": step, "ok": ok, "seconds": seconds, "shot": shot.name if shot else "",
                       "text": text, "note": note})
    (log.ok if ok else log.failed)(f"{step}: {'moved on' if ok else 'stuck'} after {seconds}s"
                                   + (f" ({note})" if note else "") + f" | {first_line(text)}")


def _reason(error: BaseException) -> str:
    """What a raised step says, in one line, without the driver's prefix."""
    said = str(error.code if isinstance(error, SystemExit) else error) or type(error).__name__
    return first_line(said.removeprefix("driver: "))


def _doctor(instance: Instance) -> tuple[bool, str]:
    """`./stack doctor` after the walk, as the admin reads it: (no error, its text).

    Read in its human form, because that is what the admin gets; an error is
    a line marked ✗, or a failing exit code.
    """
    result = instance.run("./stack doctor --pretty")
    said = (result.stdout + result.stderr).strip()
    return result.returncode == 0 and "\u2717" not in said, said


# ── The protocol ─────────────────────────────────────────────────────────

def _write_protocol(walk: Walk) -> None:
    """`<out>/protocol.md`: the gates as a table, then each step's words."""
    rows = [f"| {i} | {s['step']} | {'passed' if s['ok'] else '**stuck**'} | {s['seconds']} s | "
            f"{s.get('shot', '')} | {s['note']} |" for i, s in enumerate(walk.steps, 1)]
    words = [f"### {i}. {s['step']}\n\n```\n{s['text']}\n```\n" for i, s in enumerate(walk.steps, 1) if s["text"]]
    lines = ["# Install walk", "", f"Language: {walk.language}. Family: {walk.family}, "
             f"{walk.admin} and {', '.join(walk.people)}.", "",
             "| # | Step | Gate | Took | Screenshot | Note |", "|---|---|---|---|---|---|", *rows, "",
             "## What each step said", "", *words]
    (walk.out / "protocol.md").write_text("\n".join(lines), encoding="utf-8")
