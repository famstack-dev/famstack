"""The dashboard's status pills: what runs, one pill per set-up service.

The family's dashboard shows the stack as a row of status pills under
"Your stack", and one link to what is left. The setup itself (its steps,
tiles, forms and terminal commands) is on `/setup` (`setup_page.py`); the
dashboard only says how the stack is and where to go on:

    Your stack
    (Chat ● Running) (Photos ◐ Starting) (Documents ● Running)
    Continue setup: Your AI ›

A pill is a service's name and its status: the four status words the
setup page shares (Running, Starting, Not answering, Stopped), each with
its mark (`service_card.status_html`). A pill opens the service's service
card (`service_card.py`) in a pane at the side, the same service card
`/setup` shows; without a script it is a link to "Your stack" on
`/setup`.

**Which services have a pill.** Every stacklet a page shows
(`stack_widget.on_screens`, so not Code) that is set up, besides the
always-on core; and a stacklet whose start is going on, so a first start
shows as Starting while it runs, wherever it was started (the terminal,
the Server room, `/setup`): every start is a job the tools server
follows. The services come in the order of `service_card.SERVICES`, the
rest after them in the listing's order.

**What is left** is one link to `/setup`:

    the setup's current step is open    "Continue setup: Your AI ›"
    every step is done, n can be added  "Set up 2 more ›"
    every step done, nothing to add     no link

The current step is `setup_page.current_step`; what can be added is what
the setup offered as tiles (`stack_widget.offered`).

**Whose service card.** On the Mac that runs famstack the service cards
name the admin's accounts and carry the first password, as `/setup` does;
the admin sets the stack up there. On another device the page does not
know who looks, so a service card offers Open and the app, without a
sign-in.

    v = view(listing, jobs=[], now=now, language="en", base="/dashboard",
             here=False, family=simpsons, current="ai")
    [(p["id"], p["status"]) for p in v["pills"]]   # [("messages", "running")]
    v["left"]["text"]                              # "Continue setup: Your AI ›"
    html(v)                                        # '<fs-status ...><nav class="statuses" ...'

`view()` builds what the pills show as JSON, with the markup already
rendered (`nav_html`, `cards_html`), so the page's script
(`dashboard/widgets/fs-status.js`) puts a refreshed answer of
`/dashboard/api/stack` in place without a second renderer.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from html import escape

import service_card
import setup_page
import stack_widget

__all__ = ["view", "html", "words"]

WORDS = {
    "en": {"title": "Your stack", "empty": "Nothing is set up yet.",
           "error": "The stack does not answer right now.", "retry": "Try again",
           "close": "Close", "continue": "Continue setup: {step} ›",
           "more": ("Set up 1 more ›", "Set up {n} more ›")},
    "de": {"title": "Euer Stack", "empty": "Noch ist nichts eingerichtet.",
           "error": "famstack antwortet gerade nicht.", "retry": "Erneut versuchen",
           "close": "Schließen", "continue": "Weiter einrichten: {step} ›",
           "more": ("Noch 1 Dienst einrichten ›", "Noch {n} Dienste einrichten ›")},
}


def words(language: str) -> dict:
    """The pills' words in `language`: German for "de", English otherwise.

        words("de")["close"]    # "Schließen"
    """
    return WORDS["de" if language == "de" else "en"]


# ── The view ──────────────────────────────────────────────────────────────

def view(listing: dict | None, *, jobs: Sequence[dict], now: datetime, language: str, base: str,
         here: bool, family: dict | None, current: str, device: str = "") -> dict:
    """What the pills show for `stack list --json`'s answer (`listing`, None
    when the host API did not answer) and the jobs' snapshots (`jobs`), at
    `now` (aware). `here` says the page was opened on the Mac that runs
    famstack; `family` is what `stack family` shows, for the admin's
    sign-in there; `current` the setup's current step
    (`setup_page.current_step`: a step, "ready", or "" when unknown);
    `device` the device that looks, for the apps.

    Returns the shell's fields (`state`, `title`, `src`, ...), `pills`
    (each `id`, `name`, `status`, `status_word`), `left` (`text`, `href`,
    and `step` or `more`; None when nothing is left), and the markup:
    `nav_html` (the pills and the link) and `cards_html` (a service card
    per pill).

        view(None, jobs=[], now=now, language="en", base="/dashboard",
             here=False, family=None, current="")["state"]    # "error"
    """
    w = words(language)
    stacklets = stack_widget.on_screens((listing or {}).get("stacklets", []))
    going = {sid: j for sid, j in stack_widget.shown_jobs(jobs, now).items()
             if j.get("state") == "running"}
    person = _admin(family) if here else None
    cards = [service_card.view(stack_widget.tile(s, stacklets, going.get(s["id"]), language,
                                                 here=here),
                               s, person, language=language, here=here, device=device)
             for s in _shown(stacklets, going)]
    pills = [{"id": c["id"], "name": c["name"], "status": c["status"],
              "status_word": c["status_word"]} for c in cards if c["status"]]
    more = sum(stack_widget.offered(s) and s["id"] not in going for s in stacklets)
    left = _left(current, more, w, language)
    state = "error" if listing is None else "content" if pills or left else "empty"
    return {
        "state": state, "language": language, "title": w["title"], "empty": w["empty"],
        "error": w["error"], "retry": w["retry"], "src": f"{base}/api/stack", "retry_href": base,
        "pills": pills, "left": left,
        "nav_html": _nav_html(pills, left, w),
        "cards_html": "".join(service_card.html(c) for c in cards if c["status"]),
        "starting": service_card.words(language)["status"]["starting"],
        "adding": stack_widget.words(language)["adding"], "close": w["close"],
    }


def html(v: dict) -> str:
    """The pills as the page serves them: the pills and the link, then the
    pane their service cards open in, closed.

        html(view(...))    # '<fs-status data-starting="Starting" ...><nav class="statuses" ...'
    """
    return (f'<fs-status data-starting="{_e(v["starting"])}" data-adding="{_e(v["adding"])}">'
            f'{v["nav_html"]}'
            f'<dialog class="service-card-pane" aria-label="{_e(v["title"])}">'
            f'<div class="service-card-pane-top"><button class="setup-mini service-card-close" '
            f'type="button">{_e(v["close"])}</button></div>'
            f'<div class="service-card-pane-body">{v["cards_html"]}</div></dialog></fs-status>')


# ── Which services have a pill ────────────────────────────────────────────

def _shown(stacklets: list[dict], going: dict) -> list[dict]:
    """The stacklets with a pill, services first in their order: set up or
    starting, never the always-on core."""
    with_pill = [s for s in stacklets
                 if (s.get("enabled") or s["id"] in going) and not s.get("always_on")]
    order = list(service_card.SERVICES)
    return sorted(with_pill, key=lambda s: order.index(s["id"]) if s["id"] in order else len(order))


def _admin(family: dict | None) -> dict | None:
    """The family's admin, whose accounts the service cards name on the Mac."""
    people = (family or {}).get("people", [])
    return next((p for p in people if p.get("role") == "admin"), people[0] if people else None)


# ── What is left ──────────────────────────────────────────────────────────

def _left(current: str, more: int, w: dict, language: str) -> dict | None:
    """The link to what is left: the open step, else how many can be added."""
    if current and current != "ready":
        title = setup_page.words(language)["steps"][current][0]
        return {"step": current, "text": w["continue"].format(step=title), "href": setup_page.PATH}
    if current == "ready" and more:
        return {"more": more, "text": w["more"][min(more, 2) - 1].format(n=more),
                "href": setup_page.href(setup_page.STACK)}
    return None


# ── The markup ────────────────────────────────────────────────────────────

def _nav_html(pills: list[dict], left: dict | None, w: dict) -> str:
    """The pills, each a link to "Your stack" on /setup that the script
    turns into the opener of its service card, then the link to what is left."""
    stack = setup_page.href(setup_page.STACK)
    items = "".join(
        f'<a class="fs-status" href="{_e(stack)}" data-service="{_e(p["id"])}" '
        f'aria-haspopup="dialog"><span class="fs-status-name">{_e(p["name"])}</span>'
        f'{service_card.status_html(p["status"], p["status_word"])}</a>' for p in pills)
    if left:
        said = left.get("step") or f'more:{left["more"]}'
        items += (f'<a class="fs-link status-left" href="{_e(left["href"])}" '
                  f'data-left="{_e(said)}">{_e(left["text"])}</a>')
    return f'<nav class="statuses" aria-label="{_e(w["title"])}">{items}</nav>'


def _e(text: str) -> str:
    return escape(str(text), quote=True)
