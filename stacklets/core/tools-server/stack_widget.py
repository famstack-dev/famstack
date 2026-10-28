"""The "Your stack" widget: what runs, and what the family can add.

The dashboard's second widget, built like the activity widget
(`dashboard/widgets/README.md`). It reads the stack's state from the host
API (`stack list --json`) and shows, by size:

    small    a glance: "6 running"
    medium   what runs, each with its state, and "3 more to add"
    large    what runs, and below it what can be added, as tiles

A stacklet that is set up is listed with its state, in words: running,
started but not answering yet (its health check fails), starting, keeps
restarting, stopped. What can be added is every stacklet not set up whose
stage is stable or beta, never a deprecated one and never one that is
always on. Its tile says what it is in the dashboard's own words (`NAMES`,
`LINES`), not the manifest's description, which is the CLI's. It offers
Add, which starts `stack up` as a job; or, for a stacklet whose first `up`
asks questions (`asks_on_first_up`), only the terminal command: the page
cannot answer those questions yet; or, for one that `requires` a stacklet
not set up, what it comes after, because `stack up` refuses it until then.
Every tile names its command, which is what a page without JavaScript
shows. While the "Setting up" widget is shown (`setup_widget.py`), it
offers the core in its steps, and this widget lists what runs and offers
the rest, optional and beta stacklets, under "Also available". The tiles
the steps offer are here too, marked `data-in-setup` and hidden, for a
device that folds the steps away (`fs-setup.js`); then this widget offers
everything, as it does once the setup is done.

A tile follows its job (the jobs' snapshots, `stack.live.jobs`): while it
runs, its steps; when it ended, for a while, how: running with a link,
started but not answering yet, or did not start, with the error.

`view()` builds what the widget shows as JSON; `html()` renders it, so
the page reads complete without JavaScript; `fs-stack.js` renders the
same view when it refreshes. One wording, in one place.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Collection, Sequence
from datetime import datetime, timedelta
from html import escape

# Stages a stacklet may be offered in. Incubating ones are for whoever
# installs them by hand; deprecated ones are on their way out.
OFFERED = ("stable", "beta")

ALL, MEDIUM_UP, LARGE, SMALL, MEDIUM = ("small medium large", "medium large", "large",
                                        "small", "medium")

# A set-up stacklet's state, as the dot and the words show it.
RUNNING, NOT_ANSWERING, STARTING, FAILING, STOPPED = (
    "running", "not_answering", "starting", "failing", "stopped")
# A tile's state besides those: it can be added, a job adds it, or it failed.
ADD, ADDING, FAILED = "add", "adding", "failed"

# How long a tile shows how its job ended. Long enough for whoever pressed
# Add to come back and read it; after that, the stacklet is listed with
# what runs, and its tile is gone or offers Add again.
OUTCOME_SHOWN = timedelta(minutes=10)


# How the latest job ended, as the small size says it.
DONE_RUNNING, DONE_NOT_ANSWERING = "done_running", "done_not_answering"


# ── Words ─────────────────────────────────────────────────────────────────

WORDS = {
    "en": {
        "title": "Your stack", "empty": "Nothing is set up yet.",
        "error": "The stack does not answer right now.", "retry": "Try again",
        "see_all": "Add more",
        "glance": ("Nothing running", "1 running", "{n} running"),
        "states": {RUNNING: "running", NOT_ANSWERING: "started, not answering yet",
                   STARTING: "starting", FAILING: "keeps restarting", STOPPED: "stopped"},
        "open": "Open", "check_again": "Check again",
        "to_add": "Add to your stack", "more": ("1 more to add", "{n} more to add"),
        "also": "Also available",
        "add": "Add {name}", "in_terminal": "In the terminal", "beta": "beta",
        "after": "Comes after {names}", "and": "and",
        "attended": "macOS asks for your permission along the way, so this is set up in the "
                    "terminal on this Mac.",
        "in_terminal_here": "In the terminal on this Mac",
        "forms": {"label": "Add to your stack", "continue": "Continue", "not_now": "Not now",
                  "close": "Close", "yes": "Yes", "no": "No", "docs": "Read more",
                  "on_this_mac": "This question asks for a secret, so it is answered on the Mac "
                                 "famstack runs on: open the dashboard there, or run this in "
                                 "its terminal.",
                  "failed": "Not saved: {error}"},
        "adding": "Adding…", "failed": "Did not start: {error}",
        "latest": {DONE_RUNNING: "{name} was added",
                   DONE_NOT_ANSWERING: "{name} was added, not answering yet",
                   FAILED: "{name} did not start"},
    },
    "de": {
        "title": "Euer Stack", "empty": "Noch ist nichts eingerichtet.",
        "error": "Der Stack antwortet gerade nicht.", "retry": "Erneut versuchen",
        "see_all": "Mehr hinzufügen",
        "glance": ("Nichts läuft", "1 läuft", "{n} laufen"),
        "states": {RUNNING: "läuft", NOT_ANSWERING: "gestartet, antwortet noch nicht",
                   STARTING: "startet", FAILING: "startet immer wieder neu", STOPPED: "angehalten"},
        "open": "Öffnen", "check_again": "Erneut prüfen",
        "to_add": "Zum Stack hinzufügen", "more": ("Noch 1 zum Hinzufügen", "Noch {n} zum Hinzufügen"),
        "also": "Außerdem verfügbar",
        "add": "{name} hinzufügen", "in_terminal": "Im Terminal", "beta": "Beta",
        "after": "Kommt nach {names}", "and": "und",
        "attended": "macOS fragt unterwegs nach deiner Erlaubnis, darum wird dies im Terminal "
                    "auf diesem Mac eingerichtet.",
        "in_terminal_here": "Im Terminal auf diesem Mac",
        "forms": {"label": "Zum Stack hinzufügen", "continue": "Weiter", "not_now": "Nicht jetzt",
                  "close": "Schließen", "yes": "Ja", "no": "Nein", "docs": "Mehr dazu",
                  "on_this_mac": "Diese Frage will ein Geheimnis wissen, also wird sie auf dem "
                                 "Mac beantwortet, auf dem famstack läuft: Öffne die Übersicht "
                                 "dort, oder führe dies in seinem Terminal aus.",
                  "failed": "Nicht gespeichert: {error}"},
        "adding": "Wird hinzugefügt…", "failed": "Nicht gestartet: {error}",
        "latest": {DONE_RUNNING: "{name} wurde hinzugefügt",
                   DONE_NOT_ANSWERING: "{name} wurde hinzugefügt, antwortet noch nicht",
                   FAILED: "{name} ist nicht gestartet"},
    },
}


# What the stacklets the stack ships are called on the dashboard, and what
# each is for, in a sentence a family reads. A stacklet not listed keeps its
# manifest's name and description.
NAMES = {
    "en": {"messages": "Chat", "photos": "Photos", "docs": "Documents", "ai": "Local AI",
           "memory": "Family memory", "code": "Code", "agent": "Agent", "backup": "Backup",
           "core": "Core"},
    "de": {"messages": "Chat", "photos": "Fotos", "docs": "Dokumente", "ai": "Lokale KI",
           "memory": "Familiengedächtnis", "code": "Code", "agent": "Agent", "backup": "Backup",
           "core": "Core"},
}
LINES = {
    "en": {
        "messages": "Your family's own chat, on this Mac. Also where you hand things over.",
        "photos": "Every phone backs up its camera roll here, in full quality.",
        "docs": "Snap a letter in the chat. It is read, filed and summarised.",
        "ai": "Reads letters, writes down voice notes, answers questions.",
        "memory": "A page for each of you, the diary, and answers from it.",
        "code": "Where the family memory is kept, with all its history.",
        "agent": "Ask in the chat, and it answers from your own notes.",
        "backup": "Every night, a copy of everything on a disk you plug in.",
    },
    "de": {
        "messages": "Euer eigener Chat, auf diesem Mac. Hier gebt ihr auch Dinge ab.",
        "photos": "Jedes Handy sichert seine Fotos hierher, in voller Qualität.",
        "docs": "Einen Brief im Chat fotografieren: Er wird gelesen, abgelegt und zusammengefasst.",
        "ai": "Liest Briefe, schreibt Sprachnachrichten mit, beantwortet Fragen.",
        "memory": "Eine Seite für jeden von euch, das Tagebuch und Antworten daraus.",
        "code": "Hier liegt das Familiengedächtnis, mit seiner ganzen Geschichte.",
        "agent": "Im Chat fragen, und er antwortet aus euren eigenen Notizen.",
        "backup": "Jede Nacht eine Kopie von allem auf einer Festplatte, die du ansteckst.",
    },
}
# How "comes after ..." names a requirement, where it reads better than the name.
AFTER = {"en": {"messages": "the chat"}, "de": {"messages": "dem Chat"}}


def words(language: str) -> dict:
    return WORDS["de" if language == "de" else "en"]


def name_of(s: dict, language: str) -> str:
    """A stacklet's name on the dashboard."""
    return NAMES["de" if language == "de" else "en"].get(s["id"]) or s.get("name") or s["id"]


def line_of(s: dict, language: str) -> str:
    """What a stacklet is for, in the dashboard's words."""
    return LINES["de" if language == "de" else "en"].get(s["id"]) or s.get("description") or ""


# ── The view ──────────────────────────────────────────────────────────────

def view(listing: dict | None, *, language: str, base: str, now: datetime,
         jobs: Sequence[dict] = (), size: str = "", setup_offers: Collection[str] = (),
         here: bool = False) -> dict:
    """What the widget shows for `stack list --json`'s answer (`listing`;
    None when the host API did not answer) and the jobs' snapshots
    (`jobs`, oldest first), at `now` (aware). `base` is the dashboard's
    path, for its own links; `size` the size the page fixed, if any.
    `setup_offers`: the stacklets the "Setting up" widget's steps offer
    while it is shown; their tiles are marked `in_setup`, and the rest are
    what this widget offers besides.
    `here`: the page was opened on this Mac (`open_link`)."""
    w = words(language)
    stacklets = (listing or {}).get("stacklets", [])
    shown = shown_jobs(jobs, now)
    running = [_row(s, w, language, here) for s in stacklets if s.get("enabled")]
    to_add = [{**tile(s, stacklets, shown.get(s["id"]), language, here=here),
               "in_setup": s["id"] in setup_offers}
              for s in stacklets if _offered(s) or s["id"] in shown]
    besides = [t for t in to_add if not t["in_setup"]]
    state = "error" if listing is None else "content" if running or to_add else "empty"
    # A stacklet that does not answer yet still runs; its row says the rest.
    count = sum(r["state"] in (RUNNING, NOT_ANSWERING) for r in running)
    return {
        "state": state, "language": language, "title": w["title"],
        "empty": w["empty"], "error": w["error"], "retry": w["retry"],
        "src": f"{base}/api/stack", "retry_href": base,
        "see_all": ({"label": w["see_all"], "href": f"{base}?size=large", "show": "small medium"}
                    if besides and size != "large" else None),
        "glance": {"text": w["glance"][min(count, 2)].format(n=count), "show": SMALL},
        "latest": _latest(jobs, stacklets, w),
        "running": running,
        "setting_up": bool(setup_offers),
        "to_add_title": w["also"] if setup_offers else w["to_add"],
        "to_add_title_all": w["to_add"],
        "to_add": to_add,
        "more": ({"text": w["more"][min(len(besides), 2) - 1].format(n=len(besides)), "show": MEDIUM}
                 if besides else None),
        "check_again": w["check_again"], "adding": w["adding"], "forms": w["forms"],
    }


def shown_jobs(jobs: Sequence[dict], now: datetime) -> dict[str, dict]:
    """The last `up` job of each stacklet that a tile still shows, by id."""
    last = {j["target"]: j for j in jobs if j.get("action") == "up"}
    return {sid: j for sid, j in last.items() if _shown(j, now)}


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


def _row(s: dict, w: dict, language: str, here: bool) -> dict:
    """A set-up stacklet: its state in words, a link when it serves a page,
    and the terminal command that says more when it does not run well."""
    state = _state_of(s)
    command = (f"stack logs {s['id']}" if state in (NOT_ANSWERING, FAILING)
               else f"stack up {s['id']}" if state == STOPPED else "")
    return {"id": s["id"], "name": name_of(s, language), "state": state,
            "label": w["states"][state],
            "href": open_link(s, here=here) if state in (RUNNING, NOT_ANSWERING) else "",
            "open": w["open"], "command": command, "in_terminal": w["in_terminal"]}


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
    first `up` asks questions, or what it comes after, when it requires a
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
    # for permission along the way) is never started from a page, whatever
    # its questions are: nobody could answer, and the job would hang.
    attended = bool(s.get("attended_setup"))
    addable = state in (ADD, FAILED) and bool(answerable) and not waits_for and not attended
    error = ((job or {}).get("outcome") or {})
    label = _tile_label(state, w, error)
    if state == ADD and waits_for:
        label = w["after"].format(names=_listed(waits_for, w["and"]))
    elif state == ADD and attended:
        label = w["attended"]
    return {"id": s["id"], "name": name, "description": line_of(s, language),
            "beta": w["beta"] if s.get("stage") == "beta" else "",
            "state": state, "label": label,
            "href": open_link(s, here=here) if state in (RUNNING, NOT_ANSWERING) else "",
            "open": w["open"], "job": (job or {}).get("id", "") if state == ADDING else "",
            "steps": (job or {}).get("steps", []) if state == ADDING else [],
            "command": command,
            "in_terminal": w["in_terminal_here"] if attended else w["in_terminal"],
            "add": w["add"].format(name=name) if addable else "",
            "forms": bool(addable and s.get("asks_on_first_up"))}


def _waits_for(s: dict, stacklets: list[dict], language: str) -> list[str]:
    """The requirements of `s` that are not set up, named for "comes after"."""
    by_id = {x["id"]: x for x in stacklets}
    after = AFTER["de" if language == "de" else "en"]
    return [after.get(r) or name_of(by_id.get(r, {"id": r}), language)
            for r in s.get("requires", []) if not by_id.get(r, {}).get("enabled")]


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


def _latest(jobs: Sequence[dict], stacklets: list[dict], w: dict) -> dict | None:
    """The last job that ended, in a sentence, for the small size."""
    ended = [j for j in jobs if j.get("action") == "up" and j.get("state") != "running"]
    if not ended:
        return None
    job = ended[-1]
    names = {s["id"]: s.get("name", s["id"]) for s in stacklets}
    healthy = (job.get("outcome") or {}).get("healthy", True)
    how = (FAILED if job["state"] != "done" else DONE_RUNNING if healthy else DONE_NOT_ANSWERING)
    return {"text": w["latest"][how].format(name=names.get(job["target"], job["target"])),
            "show": SMALL}


# ── The HTML ──────────────────────────────────────────────────────────────

def html(v: dict) -> str:
    """The widget's content as the page serves it: the large size's
    content, which the smaller sizes show less of."""
    setting_up = " data-setting-up" if v.get("setting_up") else ""
    parts = [f'<fs-stack data-adding="{_e(v["adding"])}" '
             f'data-forms-words="{_e(json.dumps(v["forms"]))}"{setting_up}>',
             f'<p class="stack-glance" data-show="{v["glance"]["show"]}">{_e(v["glance"]["text"])}</p>']
    if latest := v["latest"]:
        parts.append(f'<p class="stack-latest" data-show="{latest["show"]}">{_e(latest["text"])}</p>')
    if v["running"]:
        parts.append(f'<ul class="stack-list" data-show="{MEDIUM_UP}">')
        parts += [_row_html(r, v) for r in v["running"]]
        parts.append("</ul>")
    if more := v["more"]:
        parts.append(f'<p class="stack-more" data-show="{more["show"]}">{_e(more["text"])}</p>')
    if v["to_add"]:
        only_setup = " data-only-setup" if all(t.get("in_setup") for t in v["to_add"]) else ""
        parts.append(f'<section class="stack-add" data-show="{LARGE}"{only_setup}>'
                     f'<h3 class="stack-heading">{_heading_html(v)}</h3><div class="tiles">')
        parts += [tile_html(t, v) for t in v["to_add"]]
        parts.append("</div></section>")
    parts.append("</fs-stack>")
    return "".join(parts)


def _row_html(r: dict, v: dict) -> str:
    link = (f'<a class="stack-open" href="{_e(r["href"])}">{_e(r["open"])}</a>'
            if r["href"] else "")
    check = (f'<a class="fs-retry stack-check" href="{_e(v["retry_href"])}">{_e(v["check_again"])}</a>'
             if r["state"] == NOT_ANSWERING else "")
    command = (f'<p class="stack-command"><span>{_e(r["in_terminal"])}</span> '
               f'<code>{_e(r["command"])}</code></p>' if r["command"] else "")
    return (f'<li class="stack-row" data-id="{_e(r["id"])}" data-status="{r["state"]}">'
            f'<span class="dot" data-status="{r["state"]}" aria-hidden="true"></span>'
            f'<span class="stack-name">{_e(r["name"])}</span>'
            f'<span class="stack-state">{_e(r["label"])}</span>{link}{check}{command}</li>')


def _heading_html(v: dict) -> str:
    """The offers' heading. While the setup leads, "Also available", and the
    heading for all offers beside it, which the page shows once the steps
    are folded away (fs-stack.css)."""
    if not v.get("setting_up"):
        return _e(v["to_add_title"])
    return (f'<span class="stack-heading-setup">{_e(v["to_add_title"])}</span>'
            f'<span class="stack-heading-all">{_e(v["to_add_title_all"])}</span>')


def tile_html(t: dict, v: dict) -> str:
    """One tile's markup; `v` holds the words it shares with its widget
    (`retry_href`, `check_again`). A tile the setup's steps offer is marked
    `data-in-setup`."""
    beta = f' <span class="tile-beta">{_e(t["beta"])}</span>' if t["beta"] else ""
    job = f' data-job="{_e(t["job"])}"' if t["job"] else ""
    led = " data-in-setup" if t.get("in_setup") else ""
    return (f'<article class="tile" data-id="{_e(t["id"])}"{led} data-status="{t["state"]}"{job}>'
            f'<h4 class="tile-top"><span class="tile-name">{_e(t["name"])}</span>{beta}</h4>'
            f'<p class="tile-description">{_e(t["description"])}</p>'
            f'<div class="tile-act">{_tile_act_html(t, v)}</div></article>')


def _tile_act_html(t: dict, v: dict) -> str:
    """What a tile offers or shows, by its state."""
    dot = {ADDING: "busy"}.get(t["state"], t["state"])
    label = (f'<p class="tile-state"><span class="dot" data-status="{dot}" aria-hidden="true"></span>'
             f'{_e(t["label"])}</p>' if t["label"] else "")
    if t["state"] == ADDING:
        steps = "".join(f'<li class="tile-step" data-status="{_e(s["state"])}">{_e(s["text"])}</li>'
                        for s in t["steps"])
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
