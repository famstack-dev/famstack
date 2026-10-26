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
always on. Its tile offers Add, which starts `stack up` as a job, or, for
a stacklet whose first `up` asks questions (`asks_on_first_up`), only the
terminal command: the page cannot answer those questions yet. Every tile
names its command, which is what a page without JavaScript shows.

A tile follows its job (the jobs' snapshots, `stack.live.jobs`): while it
runs, its steps; when it ended, for a while, how: running with a link,
started but not answering yet, or did not start, with the error.

`view()` builds what the widget shows as JSON; `html()` renders it, so
the page reads complete without JavaScript; `fs-stack.js` renders the
same view when it refreshes. One wording, in one place.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
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
        "add": "Add {name}", "in_terminal": "In the terminal", "beta": "beta",
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
        "add": "{name} hinzufügen", "in_terminal": "Im Terminal", "beta": "Beta",
        "adding": "Wird hinzugefügt…", "failed": "Nicht gestartet: {error}",
        "latest": {DONE_RUNNING: "{name} wurde hinzugefügt",
                   DONE_NOT_ANSWERING: "{name} wurde hinzugefügt, antwortet noch nicht",
                   FAILED: "{name} ist nicht gestartet"},
    },
}


def words(language: str) -> dict:
    return WORDS["de" if language == "de" else "en"]


# ── The view ──────────────────────────────────────────────────────────────

def view(listing: dict | None, *, language: str, base: str, now: datetime,
         jobs: Sequence[dict] = (), size: str = "") -> dict:
    """What the widget shows for `stack list --json`'s answer (`listing`;
    None when the host API did not answer) and the jobs' snapshots
    (`jobs`, oldest first), at `now` (aware). `base` is the dashboard's
    path, for its own links; `size` the size the page fixed, if any."""
    w = words(language)
    stacklets = (listing or {}).get("stacklets", [])
    last = {j["target"]: j for j in jobs if j.get("action") == "up"}
    shown = {sid: j for sid, j in last.items() if _shown(j, now)}
    running = [_row(s, w) for s in stacklets if s.get("enabled")]
    to_add = [_tile(s, w, shown.get(s["id"])) for s in stacklets
              if _offered(s) or s["id"] in shown]
    state = "error" if listing is None else "content" if running or to_add else "empty"
    # A stacklet that does not answer yet still runs; its row says the rest.
    count = sum(r["state"] in (RUNNING, NOT_ANSWERING) for r in running)
    return {
        "state": state, "language": language, "title": w["title"],
        "empty": w["empty"], "error": w["error"], "retry": w["retry"],
        "src": f"{base}/api/stack", "retry_href": base,
        "see_all": ({"label": w["see_all"], "href": f"{base}?size=large", "show": "small medium"}
                    if to_add and size != "large" else None),
        "glance": {"text": w["glance"][min(count, 2)].format(n=count), "show": SMALL},
        "latest": _latest(jobs, stacklets, w),
        "running": running,
        "to_add_title": w["to_add"],
        "to_add": to_add,
        "more": ({"text": w["more"][min(len(to_add), 2) - 1].format(n=len(to_add)), "show": MEDIUM}
                 if to_add else None),
        "check_again": w["check_again"], "adding": w["adding"],
    }


def _offered(s: dict) -> bool:
    return (not s.get("enabled") and s.get("stage", "stable") in OFFERED
            and not s.get("always_on"))


def _row(s: dict, w: dict) -> dict:
    """A set-up stacklet: its state in words, a link when it serves a page,
    and the terminal command that says more when it does not run well."""
    state = _state_of(s)
    command = (f"stack logs {s['id']}" if state in (NOT_ANSWERING, FAILING)
               else f"stack up {s['id']}" if state == STOPPED else "")
    return {"id": s["id"], "name": s.get("name", s["id"]), "state": state,
            "label": w["states"][state],
            "href": s.get("url", "") if state in (RUNNING, NOT_ANSWERING) else "",
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


def _tile(s: dict, w: dict, job: dict | None) -> dict:
    """A stacklet on a tile: offered with Add (or only its command, when its
    first `up` asks questions), followed while its job runs, and how the
    job ended for a while after."""
    name = s.get("name", s["id"])
    state = _tile_state(s, job)
    command = (f"stack logs {s['id']}" if state in (NOT_ANSWERING, FAILING) else
               f"stack up {s['id']}")
    addable = state in (ADD, FAILED) and not s.get("asks_on_first_up")
    error = ((job or {}).get("outcome") or {})
    return {"id": s["id"], "name": name, "description": s.get("description", ""),
            "beta": w["beta"] if s.get("stage") == "beta" else "",
            "state": state, "label": _tile_label(state, w, error),
            "href": s.get("url", "") if state in (RUNNING, NOT_ANSWERING) else "",
            "open": w["open"], "job": (job or {}).get("id", "") if state == ADDING else "",
            "steps": (job or {}).get("steps", []) if state == ADDING else [],
            "command": command, "in_terminal": w["in_terminal"],
            "add": w["add"].format(name=name) if addable else ""}


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
    parts = [f'<fs-stack data-adding="{_e(v["adding"])}">',
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
        parts.append(f'<section class="stack-add" data-show="{LARGE}">'
                     f'<h3 class="stack-heading">{_e(v["to_add_title"])}</h3><div class="tiles">')
        parts += [_tile_html(t, v) for t in v["to_add"]]
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
    return (f'<li class="stack-row" data-id="{_e(r["id"])}" data-state="{r["state"]}">'
            f'<span class="dot" data-state="{r["state"]}" aria-hidden="true"></span>'
            f'<span class="stack-name">{_e(r["name"])}</span>'
            f'<span class="stack-state">{_e(r["label"])}</span>{link}{check}{command}</li>')


def _tile_html(t: dict, v: dict) -> str:
    beta = f' <span class="tile-beta">{_e(t["beta"])}</span>' if t["beta"] else ""
    job = f' data-job="{_e(t["job"])}"' if t["job"] else ""
    return (f'<article class="tile" data-id="{_e(t["id"])}" data-state="{t["state"]}"{job}>'
            f'<h4 class="tile-top"><span class="tile-name">{_e(t["name"])}</span>{beta}</h4>'
            f'<p class="tile-description">{_e(t["description"])}</p>'
            f'<div class="tile-act">{_tile_act_html(t, v)}</div></article>')


def _tile_act_html(t: dict, v: dict) -> str:
    """What a tile offers or shows, by its state."""
    dot = {ADDING: "busy"}.get(t["state"], t["state"])
    label = (f'<p class="tile-state"><span class="dot" data-state="{dot}" aria-hidden="true"></span>'
             f'{_e(t["label"])}</p>' if t["label"] else "")
    if t["state"] == ADDING:
        steps = "".join(f'<li class="tile-step" data-state="{_e(s["state"])}">{_e(s["text"])}</li>'
                        for s in t["steps"])
        return f'{label}<ol class="tile-steps">{steps}</ol>'
    add = (f'<button class="tile-add" type="button" data-target="{_e(t["id"])}">{_e(t["add"])}</button>'
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
