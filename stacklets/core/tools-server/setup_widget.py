"""The "Setting up" widget: after the first run, setup step by step, chat first.

The dashboard's widget for a stack that is not set up yet, built like the
others (`dashboard/widgets/README.md`). It is shown above "Your stack"
until every step is done, and folds into it when someone hides it for
now (`fs-setup.js`). It leads through the core of the stack, the chat,
Photos, Documents, AI and the memory; what is optional or not finished
(a beta stacklet such as the backup or the agent) is offered in "Your
stack" instead, never as a step. Six steps, the next one open:

    install    famstack is installed: core runs (always done)
    family     who is at home (`stack family`)
    chat       start the family chat: the messages tile, Add from the page
    people     bring everyone in: the apps, the server they ask for, each
               person's chat address, and whether they have signed in yet
               (`stack messages users`, read-only)
    services   add what the family needs: the core's tiles (`CORE`), and the
               tiles of what they require (`requires` in the listing:
               Code, for the memory)
    try        hand over the first letter: done once Documents has filed one

A step whose data does not exist yet says what it is waiting for ("comes
with the chat"), never a button that cannot work. Every tile is
`stack_widget.tile`, so Add, requirements, questions and a running job
read the same here as in "Your stack".

`view()` builds the widget as JSON, each step's body already rendered as
HTML (`body_html`), so its script builds the steps with the `steps`
component and drops the bodies in; `html()` renders the same for a page
without JavaScript. One wording, in one place, English and German.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from html import escape

import stack_widget

# Where everyone gets the chat's app, in the order of WORDS' "apps": Element
# X on phones, the Element desktop app on computers, as the admin guide
# recommends (§ Connect your phones). Links out; nothing is loaded from them.
APPS = ("https://apps.apple.com/app/id1631335820",
        "https://play.google.com/store/apps/details?id=io.element.android.x",
        "https://element.io/download")

# The steps, in order: the order is the setup's.
STEPS = ("install", "family", "chat", "people", "services", "try")
# What "add what your family needs" leads through, in this order: the core
# of the stack besides the chat, which has a step of its own. AI is in it
# because Documents needs a model to read a letter. Whatever these require
# follows them (`led`); everything else is offered in "Your stack".
CORE = ("photos", "docs", "ai", "memory")
CHAT = "messages"

WORDS = {
    "en": {
        "title": "Setting up", "empty": "Everything is set up.",
        "error": "The stack does not answer right now.", "retry": "Try again",
        "count": "{n} of {total} done", "hide": "Hide for now", "continue": "Continue",
        "left": ("1 step left. Next: {next}.", "{n} steps left. Next: {next}."),
        "steps": {
            "install": "Install famstack", "family": "Tell famstack who is at home",
            "chat": "Start the family chat", "people": "Bring everyone in",
            "services": "Add what your family needs",
            "try": "Hand over your first letter",
        },
        "install_sub": "Core, started in the terminal",
        "install_body": "Core runs: this page, the scheduler and the bots' home. "
                        "Everything else is set up here.",
        "family_sub": ("1 person", "{n} people"),
        "parents": ("{names} is a parent", "{names} are the parents"),
        "looks_after": "looks after the server",
        "relations": {"parent": "parent", "child": "child", "grandparent": "grandparent",
                      "other": "family"},
        "family_body": "Change the family any time with stack family set in the terminal.",
        "chat_sub_off": "Your family's own chat, on this Mac",
        "chat_sub_adding": "Starting…",
        "chat_sub_on": "Running, addresses like {address}",
        "chat_body_off": "The chat is where your family talks, and where you hand things to "
                         "famstack: a letter, a voice note, a question. Its server is named "
                         "{server} for good.",
        "chat_body_on": "The chat runs. Sign in on this Mac first, then bring everyone in. "
                        "Change the password right after.",
        "username": "Username", "password": "Password", "open_chat": "Open the chat",
        "people_sub_off": "Comes with the chat",
        "people_sub_on": "{n} of {total} in the chat",
        "people_body_off": "Once the chat runs, everyone has an account and signs in on "
                           "their phone.",
        "people_body_on": "Everyone installs Element X on their phone, or Element on a computer, "
                          "and signs in with their first name, lowercase, as username and first "
                          "password. Each of you changes the password after.",
        "apps": ("Element X for iPhone", "Element X for Android", "Element for Mac and Windows"),
        "server": "The app asks for the server: enter {address} in full.",
        "signed": "Signed in", "not_yet": "Not yet",
        "services_sub_off": "Photos, documents, AI, the family memory",
        "services_sub_on": "{names} running",
        "services_body": "Pick what your family needs now. Each one takes a few minutes, and "
                         "you can add the rest any time.",
        "try_sub_off": "Comes with Documents",
        "try_sub_waiting": "Waiting for a letter in the Documents room",
        "try_sub_done": "Your first letter was filed",
        "try_body_off": "Once Documents is added, photograph a letter and send it to the "
                        "Documents room. A minute later it is filed, with a title and a summary.",
        "try_body_waiting": "Photograph any letter and send it to the Documents room in the "
                            "chat. A minute later it shows up on this page.",
        "try_body_done": "That is how everything comes in.",
        "and": "and",
    },
    "de": {
        "title": "Einrichten", "empty": "Alles ist eingerichtet.",
        "error": "Der Stack antwortet gerade nicht.", "retry": "Erneut versuchen",
        "count": "{n} von {total} erledigt", "hide": "Erst einmal ausblenden", "continue": "Weiter",
        "left": ("Noch 1 Schritt. Als Nächstes: {next}.", "Noch {n} Schritte. Als Nächstes: {next}."),
        "steps": {
            "install": "famstack installieren", "family": "famstack sagen, wer zu Hause wohnt",
            "chat": "Den Familienchat starten", "people": "Alle dazuholen",
            "services": "Hinzufügen, was eure Familie braucht",
            "try": "Den ersten Brief abgeben",
        },
        "install_sub": "Core, im Terminal gestartet",
        "install_body": "Core läuft: diese Seite, der Zeitplaner und das Zuhause der Bots. "
                        "Alles andere wird hier eingerichtet.",
        "family_sub": ("1 Person", "{n} Personen"),
        "parents": ("{names} ist ein Elternteil", "{names} sind die Eltern"),
        "looks_after": "kümmert sich um den Server",
        "relations": {"parent": "Elternteil", "child": "Kind", "grandparent": "Großelternteil",
                      "other": "Familie"},
        "family_body": "Die Familie lässt sich jederzeit mit stack family set im Terminal ändern.",
        "chat_sub_off": "Euer eigener Chat, auf diesem Mac",
        "chat_sub_adding": "Startet…",
        "chat_sub_on": "Läuft, Adressen wie {address}",
        "chat_body_off": "Im Chat redet eure Familie, und hier gebt ihr famstack Dinge: einen "
                         "Brief, eine Sprachnachricht, eine Frage. Sein Server heißt für immer "
                         "{server}.",
        "chat_body_on": "Der Chat läuft. Melde dich zuerst auf diesem Mac an, dann hol alle "
                        "dazu. Ändere das Passwort gleich danach.",
        "username": "Benutzername", "password": "Passwort", "open_chat": "Chat öffnen",
        "people_sub_off": "Kommt mit dem Chat",
        "people_sub_on": "{n} von {total} im Chat",
        "people_body_off": "Sobald der Chat läuft, hat jede und jeder ein Konto und meldet sich "
                           "auf dem Handy an.",
        "people_body_on": "Alle installieren Element X auf dem Handy oder Element auf dem "
                          "Computer und melden sich mit ihrem Vornamen in Kleinbuchstaben an, als "
                          "Benutzername und erstes Passwort. Das Passwort ändert jede und jeder "
                          "danach.",
        "apps": ("Element X für iPhone", "Element X für Android", "Element für Mac und Windows"),
        "server": "Die App fragt nach dem Server: Gib {address} vollständig ein.",
        "signed": "Angemeldet", "not_yet": "Noch nicht",
        "services_sub_off": "Fotos, Dokumente, KI, das Familiengedächtnis",
        "services_sub_on": "{names} laufen",
        "services_body": "Wählt, was eure Familie jetzt braucht. Jedes dauert ein paar Minuten, "
                         "den Rest könnt ihr jederzeit dazunehmen.",
        "try_sub_off": "Kommt mit den Dokumenten",
        "try_sub_waiting": "Wartet auf einen Brief im Dokumente-Raum",
        "try_sub_done": "Euer erster Brief ist abgelegt",
        "try_body_off": "Sobald Dokumente dabei ist, fotografiere einen Brief und schick ihn in "
                        "den Dokumente-Raum. Eine Minute später ist er abgelegt, mit Titel und "
                        "Zusammenfassung.",
        "try_body_waiting": "Fotografiere einen Brief und schick ihn in den Dokumente-Raum im "
                            "Chat. Eine Minute später erscheint er auf dieser Seite.",
        "try_body_done": "So kommt alles herein.",
        "and": "und",
    },
}


def words(language: str) -> dict:
    return WORDS["de" if language == "de" else "en"]


# ── The view ──────────────────────────────────────────────────────────────

def view(listing: dict | None, family: dict | None, chat: dict | None, *, history: dict | None,
         language: str, base: str, now: datetime, jobs: Sequence[dict] = (),
         here: bool = False) -> dict:
    """What the widget shows. `listing` is `stack list --json`, `family`
    what `stack family` shows, `chat` what `stack messages users` shows
    (None while the chat does not run or did not answer), `history` the
    memory's recent changes (None without the memory), `jobs` the jobs'
    snapshots, oldest first, `here` whether the page was opened on this
    Mac (`stack_widget.open_link`). None for `listing` or `family` is an
    error."""
    w = words(language)
    common = {"title": w["title"], "empty": w["empty"], "error": w["error"], "retry": w["retry"],
              "src": f"{base}/api/setup", "retry_href": base, "see_all": None, "language": language}
    if listing is None or family is None:
        return {**common, "state": "error", "steps": [], "done": 0, "total": len(STEPS)}
    stacklets = listing.get("stacklets", [])
    by_id = {s["id"]: s for s in stacklets}
    leads = led(stacklets)
    jobs_shown = stack_widget.shown_jobs(jobs, now)
    tiles = {s["id"]: stack_widget.tile(s, stacklets, jobs_shown.get(s["id"]), language, here=here)
             for s in stacklets if stack_widget.offered(s) or s["id"] in jobs_shown}
    shared = {"retry_href": base, "check_again": stack_widget.words(language)["check_again"]}
    made = {sid: _step(sid, w, by_id, family, chat, history, tiles, shared, language, here, leads)
            for sid in STEPS}
    nxt = next((sid for sid in STEPS if not made[sid]["done"]), None)
    steps = [{**made[sid], "id": sid, "title": w["steps"][sid],
              "status": "done" if made[sid]["done"] else "next" if sid == nxt else "waiting"}
             for sid in STEPS]
    done = sum(s["done"] for s in steps)
    left = len(STEPS) - done
    return {
        **common, "state": "content" if nxt else "empty",
        "steps": steps, "done": done, "total": len(STEPS),
        "count": w["count"].format(n=done, total=len(STEPS)),
        "hide": w["hide"], "continue": w["continue"],
        "left": (w["left"][min(left, 2) - 1].format(n=left, next=w["steps"][nxt].lower())
                 if nxt else ""),
        "adding": stack_widget.words(language)["adding"],
        "forms": stack_widget.words(language)["forms"],
        # What its steps offer: "Your stack" leaves these to them.
        "offers": [CHAT, *leads],
    }


def led(stacklets: list[dict]) -> list[str]:
    """What "add what your family needs" offers: the core, then whatever it
    requires, and whatever that requires, as `stack list` reports it."""
    by_id = {s["id"]: s for s in stacklets}
    leads: list[str] = []
    todo = list(CORE)
    while todo:
        sid = todo.pop(0)
        if sid in leads or sid == CHAT or sid not in by_id:
            continue
        leads.append(sid)
        todo += by_id[sid].get("requires", [])
    return leads


def setting_up(v: dict) -> bool:
    """Whether the widget is shown: a step is left. When its sources did not
    answer it is not shown, and "Your stack" offers instead."""
    return v["state"] == "content"


def _step(sid: str, w: dict, by_id: dict, family: dict, chat: dict | None,
          history: dict | None, tiles: dict, shared: dict, language: str, here: bool,
          leads: list[str]) -> dict:
    """One step's `done`, `sub` and `body_html`."""
    # Runs: set up here and online. Online alone is what Docker says of a
    # container by its name, which another instance's can share.
    set_up = lambda x: bool(by_id.get(x, {}).get("enabled"))  # noqa: E731
    on = lambda x: set_up(x) and bool(by_id.get(x, {}).get("online"))  # noqa: E731
    people = family.get("people", [])
    if sid == "install":
        return {"done": True, "sub": w["install_sub"], "body_html": _p(w["install_body"])}
    if sid == "family":
        parents = [_first(p["name"]) for p in people if p.get("relation") == "parent"]
        sub = w["family_sub"][min(len(people), 2) - 1].format(n=len(people)) if people else ""
        if parents:
            sub += " · " + w["parents"][min(len(parents), 2) - 1].format(
                names=_listed(parents, w["and"]))
        return {"done": bool(people), "sub": sub, "body_html": _family_html(people, w)}
    if sid == "chat":
        server = family.get("chat_server") or _server(chat)
        admin = next((p for p in people if p.get("role") == "admin"), people[0] if people else None)
        address = f"@{admin['id']}:{server}" if admin and server else ""
        if on("messages"):
            return {"done": True, "sub": w["chat_sub_on"].format(address=address),
                    "body_html": _chat_on_html(admin, by_id.get("messages", {}), w, here)}
        adding = tiles.get("messages", {}).get("state") == stack_widget.ADDING
        body = _p(w["chat_body_off"].format(server=server)) if server else ""
        return {"done": False, "sub": w["chat_sub_adding"] if adding else w["chat_sub_off"],
                "body_html": body + _tiles_html([tiles.get("messages")], shared)}
    if sid == "people":
        if not on("messages") or chat is None:
            return {"done": False, "sub": w["people_sub_off"], "body_html": _p(w["people_body_off"])}
        seen = {u["id"].split(":", 1)[0].lstrip("@"): u for u in chat.get("users", [])}
        signed = [p for p in people if (seen.get(p["id"]) or {}).get("last_seen")]
        return {"done": bool(people) and len(signed) == len(people),
                "sub": w["people_sub_on"].format(n=len(signed), total=len(people)),
                "body_html": _people_html(people, seen, _server(chat), by_id.get("messages", {}), w)}
    if sid == "services":
        names = [stack_widget.name_of(by_id[x], language) for x in leads if set_up(x)]
        offered = [x for x in leads if x in tiles]
        return {"done": bool(names),
                "sub": w["services_sub_on"].format(names=_listed(names, w["and"])) if names
                else w["services_sub_off"],
                "body_html": _p(w["services_body"]) + _tiles_html([tiles[x] for x in offered], shared)}
    # try: the first letter, which Documents files into the memory.
    filed = any("document" in (c.get("types") or {}) or
                any(card.get("type") == "document" for card in c.get("cards", []))
                for c in (history or {}).get("changes", []))
    if not set_up("docs"):
        return {"done": False, "sub": w["try_sub_off"], "body_html": _p(w["try_body_off"])}
    if not filed:
        return {"done": False, "sub": w["try_sub_waiting"], "body_html": _p(w["try_body_waiting"])}
    return {"done": True, "sub": w["try_sub_done"], "body_html": _p(w["try_body_done"])}


# ── The HTML ──────────────────────────────────────────────────────────────

# The tick and the chevron, drawn as components/steps.js draws them.
_CHECK = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
          '<path d="m5 12.5 4.5 4.5L19 7.5"/></svg>')
_CHEVRON = ('<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
            'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
            '<path d="m6 9 6 6 6-6"/></svg>')


def html(v: dict) -> str:
    """The widget's content as the page serves it: the markup the `steps`
    component builds, with each step's body in it."""
    if v["state"] == "error":
        return "<fs-setup></fs-setup>"
    segments = "".join(f'<i data-status="{s["status"]}"></i>' for s in v["steps"])
    items = "".join(
        f'<li class="fs-step" data-status="{s["status"]}"><details data-step="{s["id"]}"'
        f'{" open" if s["status"] == "next" else ""}><summary>'
        f'<span class="fs-step-tick">{_CHECK if s["done"] else i + 1}</span>'
        f'<span class="fs-step-text"><b class="fs-step-title">{_e(s["title"])}</b>'
        f'<span class="fs-step-sub">{_e(s["sub"])}</span></span>'
        f'<span class="fs-step-chevron">{_CHEVRON}</span></summary>'
        f'<div class="fs-step-body">{s["body_html"]}</div></details></li>'
        for i, s in enumerate(v["steps"]))
    return (f'<fs-setup data-adding="{_e(v["adding"])}" data-hide="{_e(v["hide"])}" '
            f'data-forms-words="{_e(json.dumps(v["forms"]))}" '
            f'data-left="{_e(v["left"])}" data-continue="{_e(v["continue"])}">'
            f'<div class="setup-head"><p class="setup-count">{_e(v["count"])}</p>'
            f'<button class="setup-hide" type="button" hidden>{_e(v["hide"])}</button></div>'
            f'<div class="fs-steps-block"><div class="fs-progress" aria-hidden="true">{segments}</div>'
            f'<ol class="fs-steps">{items}</ol></div></fs-setup>')


def _p(text: str) -> str:
    return f"<p>{_e(text)}</p>" if text else ""


def _tiles_html(tiles: list[dict | None], shared: dict) -> str:
    shown = [t for t in tiles if t]
    if not shown:
        return ""
    return '<div class="tiles">' + "".join(stack_widget.tile_html(t, shared) for t in shown) + "</div>"


def _family_html(people: list[dict], w: dict) -> str:
    rows = []
    for p in people:
        about = [w["relations"].get(p.get("relation") or "", "")]
        if p.get("role") == "admin":
            about.append(w["looks_after"])
        said = " · ".join(x for x in about if x)
        rows.append(f'<li><b>{_e(p["name"])}</b> <span>{_e(said)}</span></li>')
    return f'<ul class="setup-people">{"".join(rows)}</ul>' + _p(w["family_body"])


def _chat_on_html(admin: dict | None, messages: dict, w: dict, here: bool) -> str:
    who = admin["id"] if admin else ""
    creds = (f'<dl class="setup-creds"><dt>{_e(w["username"])}</dt><dd><code>{_e(who)}</code></dd>'
             f'<dt>{_e(w["password"])}</dt><dd><code>{_e(who)}</code></dd></dl>') if who else ""
    href = stack_widget.open_link(messages, here=here)
    link = f'<a class="stack-open" href="{_e(href)}">{_e(w["open_chat"])}</a>' if href else ""
    return _p(w["chat_body_on"]) + creds + link


def _people_html(people: list[dict], seen: dict, server: str, messages: dict, w: dict) -> str:
    rows = []
    for p in people:
        signed = bool((seen.get(p["id"]) or {}).get("last_seen"))
        address = "@" + p["id"] + ":" + server
        status = "done" if signed else "waiting"
        rows.append(f'<li><b>{_e(_first(p["name"]))}</b>'
                    f'<span class="setup-address">{_e(address)}</span>'
                    f'<span class="setup-signed" data-status="{status}">'
                    f'{_e(w["signed"] if signed else w["not_yet"])}</span></li>')
    return (_p(w["people_body_on"]) + _apps_html(messages, w)
            + f'<ul class="setup-people">{"".join(rows)}</ul>')


def _apps_html(messages: dict, w: dict) -> str:
    """The apps' links, and the address the app asks for: the chat server's
    (`synapse` in the listing's `urls`), not the chat's web page."""
    links = "".join(f'<a href="{_e(href)}" target="_blank" rel="noopener">{_e(label)}</a>'
                    for label, href in zip(w["apps"], APPS))
    address = (messages.get("urls") or {}).get("synapse", "")
    server = ""
    if address:
        code = f'<code class="setup-server">{_e(address)}</code>'
        server = "<p>" + _e(w["server"]).replace("{address}", code) + "</p>"
    return f'<p class="setup-apps">{links}</p>' + server


def _server(chat: dict | None) -> str:
    """The chat server's name, from an address the chat gave a user."""
    users = (chat or {}).get("users") or []
    return users[0]["id"].split(":", 1)[1] if users and ":" in users[0]["id"] else ""


def _first(name: str) -> str:
    return name.split()[0] if name.split() else name


def _listed(names: list[str], and_: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {and_} {names[-1]}"


def _e(text: str) -> str:
    return escape(str(text), quote=True)
