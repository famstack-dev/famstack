"""A stacklet as the pages show it: its name, its state, and its tile.

What the dashboard and the setup page say about one stacklet, from the
host API's `stack list --json`: its name and what it is for in the
dashboard's own words (`NAMES`, `LINES`), not the manifest's, which are
the CLI's; its state in words (running, started but not answering yet,
starting, keeps restarting, stopped); where a browser opens it
(`open_link`); and its tile (`tile`), which the setup page's service cards
are built on (`service_card.py`) and the dashboard's status pills read
(`status_pills.py`).

    t = tile(photos, stacklets, job=None, language="en")
    t["state"], t["add"]       # ("add", "Start Photos")
    tile_html(t, shared)       # '<article class="tile" data-id="photos" ...'

What can be added is every stacklet not set up whose stage is stable or
beta, never a deprecated one and never one that is always on (`offered`).
A stacklet whose manifest says `dashboard = false` (Code, which the memory
sets up by itself) is on no page (`on_screens`). A tile offers Start,
which starts `stack up` as a job; or, for a stacklet whose first `up` asks
questions that are not forms (`asks_on_first_up`), only the terminal
command; or, for one that `requires` a stacklet not set up that asks
questions, which stacklet it needs, because `stack up` refuses it until
then (`waits_for` in the listing). A requirement that asks nothing is set
up by the stacklet's own start, so it holds back no tile. Every tile names
its command, which is what a page without JavaScript shows.

A tile follows its job (the jobs' snapshots, `stack.live.jobs`): while it
runs, its steps; when it ended, for a while, how: running with a link,
started but not answering yet, or did not start, with the error.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from html import escape

# Stages a stacklet may be offered in. Incubating ones are for whoever
# installs them by hand; deprecated ones are on their way out.
OFFERED = ("stable", "beta")

# A set-up stacklet's state, as the dot and the words show it.
RUNNING, NOT_ANSWERING, STARTING, FAILING, STOPPED = (
    "running", "not_answering", "starting", "failing", "stopped")
# A tile's state besides those: it can be added, a job adds it, or it failed.
ADD, ADDING, FAILED = "add", "adding", "failed"

# How long a tile shows how its job ended. Long enough for whoever pressed
# Add to come back and read it; after that, the stacklet is listed with
# what runs, and its tile is gone or offers Add again.
OUTCOME_SHOWN = timedelta(minutes=10)


# ── Words ─────────────────────────────────────────────────────────────────

WORDS = {
    "en": {
        "states": {RUNNING: "running", NOT_ANSWERING: "started, not answering yet",
                   STARTING: "starting", FAILING: "keeps restarting", STOPPED: "stopped"},
        # What a tile says for ten minutes after its start ended.
        "started": {RUNNING: "{name} started successfully",
                    NOT_ANSWERING: "{name} started, not answering yet"},
        "open": "Open", "check_again": "Check again",
        "add": "Start {name}", "in_terminal": "In the terminal", "beta": "beta",
        "after": ("Needs the {names} stacklet", "Needs the {names} stacklets"), "and": "and",
        "attended": "macOS asks for your permission along the way, so this is set up in the "
                    "terminal on this Mac.",
        "in_terminal_here": "In the terminal on this Mac",
        "forms": {"label": "Add to your stack", "continue": "Continue", "not_now": "Not now",
                  "close": "Close", "yes": "Yes", "no": "No", "docs": "Read more",
                  "on_this_mac": "This question asks for a secret, so it is answered on the Mac "
                                 "famstack runs on: open the dashboard there, or run this in "
                                 "its terminal.",
                  "failed": "Not saved: {error}"},
        "adding": "Starting…", "failed": "Did not start: {error}",
    },
    "de": {
        "states": {RUNNING: "läuft", NOT_ANSWERING: "gestartet, antwortet noch nicht",
                   STARTING: "wird gestartet", FAILING: "startet immer wieder neu", STOPPED: "gestoppt"},
        "started": {RUNNING: "{name} wurde gestartet",
                    NOT_ANSWERING: "{name} wurde gestartet, antwortet aber noch nicht"},
        "open": "Öffnen", "check_again": "Erneut prüfen",
        "add": "{name} starten", "in_terminal": "Im Terminal", "beta": "Beta",
        "after": ("Braucht das Stacklet „{names}“", "Braucht die Stacklets {names}"), "and": "und",
        "attended": "macOS fragt dabei nach Berechtigungen. Deshalb richtest du das im "
                    "Terminal auf diesem Mac ein.",
        "in_terminal_here": "Im Terminal auf diesem Mac",
        "forms": {"label": "Zu eurem Stack hinzufügen", "continue": "Weiter", "not_now": "Nicht jetzt",
                  "close": "Schließen", "yes": "Ja", "no": "Nein", "docs": "Mehr dazu",
                  "on_this_mac": "Hier geht es um ein Passwort oder einen Schlüssel. "
                                 "Beantworte die Frage deshalb auf dem Mac, auf dem famstack "
                                 "läuft: Öffne dort die Übersicht oder führe das im Terminal "
                                 "aus.",
                  "failed": "Nicht gespeichert: {error}"},
        "adding": "Wird gestartet…", "failed": "Start fehlgeschlagen: {error}",
    },
}


# What the stacklets the stack ships are called on the dashboard, and what
# each is for, in a sentence a family reads. A stacklet not listed keeps its
# manifest's name and description.
NAMES = {
    "en": {"messages": "Chat", "photos": "Photos", "docs": "Documents", "ai": "Local AI",
           "memory": "Memory", "code": "Code", "agent": "Agent", "backup": "Backup",
           "core": "Core"},
    "de": {"messages": "Chat", "photos": "Fotos", "docs": "Dokumente", "ai": "Lokale KI",
           "memory": "Gedächtnis", "code": "Code", "agent": "Agent", "backup": "Backup",
           "core": "Core"},
}
LINES = {
    "en": {
        "messages": "Your family's own chat, on this Mac. Here you send famstack letters, notes "
                    "and questions.",
        "photos": "Every phone backs up its camera roll here, in full quality.",
        "docs": "Send a photo of a letter to the chat. famstack reads it and files it, with a "
                "title and a summary.",
        "ai": "The AI runs on this Mac, not in a cloud. It reads letters, writes down voice "
              "messages and answers questions.",
        "memory": "Everything you file, as a wiki: a page for each of you, your topics and the "
                  "family diary.",
        "code": "Keeps your memory as a Git repository, with every change. You can edit it "
                "there too.",
        "agent": "Ask in the chat: the agent answers from your notes and keeps your lists.",
        "backup": "Every night, a copy of your data on a disk you plug in. Nothing on it is "
                  "ever overwritten.",
    },
    "de": {
        "messages": "Euer eigener Chat auf diesem Mac. Hier schickt ihr famstack Briefe, "
                    "Notizen und Fragen.",
        "photos": "Jedes Handy sichert seine Fotos hierher, in voller Qualität.",
        "docs": "Fotografiere einen Brief und schick ihn in den Chat. famstack liest ihn und "
                "legt ihn mit Titel und Zusammenfassung ab.",
        "ai": "Die KI läuft auf diesem Mac, nicht in der Cloud. Sie liest Briefe, schreibt "
              "Sprachnachrichten mit und beantwortet Fragen.",
        "memory": "Alles, was ihr ablegt, als Wiki: eine Seite für jeden von euch, eure Themen "
                  "und das Familientagebuch.",
        "code": "Hier liegt euer Gedächtnis als Git-Repository, mit jeder Änderung. Ihr könnt "
                "es dort auch selbst bearbeiten.",
        "agent": "Frag im Chat: Der Agent antwortet aus euren Notizen und führt eure Listen.",
        "backup": "Jede Nacht eine Kopie eurer Daten auf einer Festplatte, die ihr anschließt. "
                  "Was dort liegt, wird nie überschrieben.",
    },
}
# Why a setup that needs a person at this Mac (`attended_setup`) runs in its
# terminal, for a stacklet whose reason is not macOS asking for permission
# (`attended` in WORDS). The local AI's first start asks before it installs
# its engine and downloads its models, which takes long, and a question no
# terminal answers counts as no.
IN_TERMINAL_BECAUSE = {
    "en": {"ai": "The AI stacklet is a larger install, so it is still set up in the terminal. "
                 "Installing everything can take about 20 to 30 minutes."},
    "de": {"ai": "Das KI-Stacklet ist etwas umfangreicher, deshalb wird das noch im Terminal "
                 "durchgeführt. Es kann etwa 20–30 Minuten dauern, bis alles installiert ist."},
}

# The app each tile starts, beside its name, so whoever sets up the stack
# knows what it installs. The agent and the backup are the stack's own and
# name none.
PRODUCTS = {"messages": "Matrix, Element", "photos": "Immich", "docs": "Paperless-ngx",
            "ai": "oMLX", "memory": "Quartz", "code": "Forgejo Git"}

# The chat is no add-on: famstack needs it, so its button starts it. Every other
# stacklet's button adds it to the stack.
START = {"en": {"messages": "Start the chat now"}, "de": {"messages": "Chat jetzt starten"}}


def words(language: str) -> dict:
    return WORDS["de" if language == "de" else "en"]


def name_of(s: dict, language: str) -> str:
    """A stacklet's name on the dashboard."""
    return NAMES["de" if language == "de" else "en"].get(s["id"]) or s.get("name") or s["id"]


def line_of(s: dict, language: str) -> str:
    """What a stacklet is for, in the dashboard's words."""
    return LINES["de" if language == "de" else "en"].get(s["id"]) or s.get("description") or ""


# ── A stacklet ──────────────────────────────────────────────────────────────

def shown_jobs(jobs: Sequence[dict], now: datetime) -> dict[str, dict]:
    """The last `up` job of each stacklet that a tile still shows, by id."""
    last = {j["target"]: j for j in jobs if j.get("action") == "up"}
    return {sid: j for sid, j in last.items() if _shown(j, now)}


def on_screens(stacklets: list[dict]) -> list[dict]:
    """The stacklets of a listing that a page shows: all but those whose
    manifest says `dashboard = false` (the listing's `dashboard`).

        on_screens([{"id": "memory"}, {"id": "code", "dashboard": False}])
        # [{"id": "memory"}]
    """
    return [s for s in stacklets if s.get("dashboard", True)]


def offered(s: dict) -> bool:
    """Whether a stacklet is one to offer: not set up, stable or beta, not
    deprecated, not always on."""
    return _offered(s)


def _offered(s: dict) -> bool:
    return (not s.get("enabled") and s.get("stage", "stable") in OFFERED
            and not s.get("always_on"))


def open_link(s: dict, *, here: bool) -> str:
    """Where a browser opens a stacklet's page. On this Mac (`here`) at its
    `browser_url`, localhost in port mode. Elsewhere at its `url`, unless
    its web app needs a secure context and that address is plain HTTP:
    there it never loads (Element, for WebCrypto), so there is no link."""
    if here:
        return s.get("browser_url") or s.get("url", "")
    url = s.get("url", "")
    return "" if s.get("secure_context") and not url.startswith("https://") else url


def _state_of(s: dict) -> str:
    if s.get("failing"):
        return FAILING
    if s.get("starting"):
        return STARTING
    if s.get("online"):
        return NOT_ANSWERING if s.get("degraded") else RUNNING
    return STOPPED


def _shown(job: dict, now: datetime) -> bool:
    """Whether a tile shows this job: while it runs, and for a while after."""
    if job.get("state") == "running":
        return True
    ended = job.get("ended")
    return bool(ended) and now - datetime.fromisoformat(ended) <= OUTCOME_SHOWN


def tile(s: dict, stacklets: list[dict], job: dict | None, language: str, *,
         here: bool = False) -> dict:
    """A stacklet on a tile: offered with Add (or only its command, when its
    first `up` asks questions, or which stacklet it needs, when it requires a
    stacklet not set up), followed while its job runs, and how the job
    ended for a while after. `stacklets` is the whole listing, for the
    requirements; `here` as for `view`."""
    w = words(language)
    name = name_of(s, language)
    state = _tile_state(s, job)
    command = (f"stack logs {s['id']}" if state in (NOT_ANSWERING, FAILING) else
               f"stack up {s['id']}")
    waits_for = _waits_for(s, stacklets, language)
    # A stacklet that still asks is added from the page when its questions are
    # forms (`asks_in_forms`): its Add opens them (fs-stack.js, setup_forms.py).
    answerable = not s.get("asks_on_first_up") or s.get("asks_in_forms")
    # A setup that needs a person at this Mac (`attended_setup`: macOS asks
    # for permission along the way, or a long first start asks in the
    # terminal) is never started from a page, whatever
    # its questions are: nobody could answer, and the job would hang.
    attended = bool(s.get("attended_setup"))
    addable = state in (ADD, FAILED) and bool(answerable) and not waits_for and not attended
    error = ((job or {}).get("outcome") or {})
    label = _tile_label(state, w, error)
    if job and job.get("state") == "done" and state in w["started"]:
        label = w["started"][state].format(name=name)
    elif state == ADD and waits_for:
        label = w["after"][len(waits_for) > 1].format(names=_listed(waits_for, w["and"]))
    elif state == ADD and attended:
        label = IN_TERMINAL_BECAUSE["de" if language == "de" else "en"].get(s["id"], w["attended"])
    return {"id": s["id"], "name": name, "product": PRODUCTS.get(s["id"], ""),
            "description": line_of(s, language),
            "beta": w["beta"] if s.get("stage") == "beta" else "",
            "state": state, "label": label,
            "href": open_link(s, here=here) if state in (RUNNING, NOT_ANSWERING) else "",
            "open": w["open"], "job": (job or {}).get("id", "") if state == ADDING else "",
            "steps": (job or {}).get("steps", []) if state == ADDING else [],
            "command": command,
            "in_terminal": w["in_terminal_here"] if attended else w["in_terminal"],
            "add": (START["de" if language == "de" else "en"].get(s["id"]) or w["add"].format(name=name))
                   if addable else "",
            "forms": bool(addable and s.get("asks_on_first_up"))}


def _waits_for(s: dict, stacklets: list[dict], language: str) -> list[str]:
    """The requirements `s` waits for, by their names: not set up, and
    asking questions, so its own start cannot set them up (`waits_for`)."""
    by_id = {x["id"]: x for x in stacklets}
    return [name_of(by_id.get(r, {"id": r}), language) for r in s.get("waits_for", [])]


def _listed(names: list[str], and_: str) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} {and_} {names[-1]}"


def _tile_state(s: dict, job: dict | None) -> str:
    if job is None:
        return ADD if not s.get("enabled") else _state_of(s)
    if job["state"] == "running":
        return ADDING
    if job["state"] != "done":
        return FAILED
    return _state_of(s) if s.get("enabled") else FAILED


def _tile_label(state: str, w: dict, outcome: dict) -> str:
    if state == ADD:
        return ""
    if state == ADDING:
        return w["adding"]
    if state == FAILED:
        return w["failed"].format(error=outcome.get("error") or outcome.get("cancelled") or "")
    return w["states"][state]


# ── The HTML ──────────────────────────────────────────────────────────────

def tile_html(t: dict, v: dict) -> str:
    """One tile's markup; `v` holds the words it shares with its widget
    (`retry_href`, `check_again`). A tile the setup's steps offer is marked
    `data-in-setup`."""
    product = f' <span class="tile-product">{_e(t["product"])}</span>' if t.get("product") else ""
    beta = f' <span class="tile-beta">{_e(t["beta"])}</span>' if t["beta"] else ""
    job = f' data-job="{_e(t["job"])}"' if t["job"] else ""
    led = " data-in-setup" if t.get("in_setup") else ""
    return (f'<article class="tile" data-id="{_e(t["id"])}"{led} data-status="{t["state"]}"{job}>'
            f'<h4 class="tile-top"><span class="tile-name">{_e(t["name"])}</span>{product}{beta}</h4>'
            f'<p class="tile-description">{_e(t["description"])}</p>'
            f'<div class="tile-act">{_tile_act_html(t, v)}</div></article>')


def _step_id(step: dict) -> str:
    """The cursor of the event that began a step, as `data-step`: an event
    the page then also gets for that step finds its line (fs-stack.js)."""
    return f' data-step="{_e(str(step["id"]))}"' if step.get("id") else ""


def _tile_act_html(t: dict, v: dict) -> str:
    """What a tile offers or shows, by its state."""
    dot = {ADDING: "busy"}.get(t["state"], t["state"])
    label = (f'<p class="tile-state"><span class="dot" data-status="{dot}" aria-hidden="true"></span>'
             f'{_e(t["label"])}</p>' if t["label"] else "")
    if t["state"] == ADDING:
        steps = "".join(f'<li class="tile-step" data-status="{_e(s["state"])}"'
                        f'{_step_id(s)}>{_e(s["text"])}</li>' for s in t["steps"])
        return f'{label}<ol class="tile-steps">{steps}</ol>'
    forms = " data-forms" if t.get("forms") else ""
    add = (f'<button class="tile-add" type="button" data-target="{_e(t["id"])}"{forms}>'
           f'{_e(t["add"])}</button>'
           if t["add"] else "")
    link = (f'<a class="stack-open" href="{_e(t["href"])}">{_e(t["open"])}</a>' if t["href"] else "")
    check = (f'<a class="fs-retry stack-check" href="{_e(v["retry_href"])}">{_e(v["check_again"])}</a>'
             if t["state"] == NOT_ANSWERING else "")
    terminal = ("" if t["state"] == RUNNING else
                f'<p class="tile-terminal"><span>{_e(t["in_terminal"])}</span> '
                f'<code class="tile-command">{_e(t["command"])}</code></p>')
    return f'{label}{add}{link}{check}{terminal}'



def _e(text: str) -> str:
    return escape(str(text), quote=True)
