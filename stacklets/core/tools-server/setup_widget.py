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
    people     set up the chat app: the server address the app asks for,
               shown first because it is the one people get wrong, the
               admin's username and password, the apps and the sign-in
               steps. Done once the admin has signed in anywhere (`stack
               messages users`, read-only, knows when an account was last
               used, not from which device); the others join later
    services   start the services: the core's tiles (`CORE`), and the
               tiles of what they require (`requires` in the listing:
               Code, for the memory)
    try        post the first document: done once Documents has filed one
               while an AI can read it (`ai_reads`); Documents files a
               document without one too, but only by its file name

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
# What "start the services" leads through, in this order: the core
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
            "chat": "Start the family chat", "people": "Set up the chat app",
            "services": "Start the services",
            "try": "Post your first document",
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
        "chat_needed": "famstack needs the chat. It connects everything, and you reach it through "
                       "the apps on your computer and phone.",
        "chat_body_off": "The chat is where your family talks, and where you hand things to "
                         "famstack: a letter, a voice note, a question. Its server is named "
                         "{server} for good.",
        "chat_body_on": "The chat runs. Next, set up the app and sign in.",
        "username": "Username", "password": "Password", "open_chat": "Open the chat",
        "people_sub_off": "Requires \"Chat\"",
        "people_sub_on": "Try it yourself first",
        "people_sub_done": "You are signed in",
        "people_body_off": "Once the chat runs, you sign in with the app on your phone and your "
                           "computer.",
        "people_body_on": "Try the chat yourself first, on your phone and your computer. The others "
                          "can join later, the same way.",
        "apps": ("Element X for iPhone", "Element X for Android", "Element for Mac and Windows"),
        "server_label": "Server address for the app",
        "server_note": "Enter it exactly like this, in full. The app needs this address, not the "
                       "address of the chat's web page.",
        "the_server": "the chat server's address",
        "change_password": "Change the password after you sign in.",
        "sign_in": ("Open the app and choose Sign in.",
                    "When it asks for the server, choose Edit or Other and enter {server}.",
                    "If the app warns that the connection is not HTTPS, continue: on your home "
                    "network that is expected.",
                    "Sign in with the username and password above.",
                    "Encryption: on your first device, choose Reset identity. Verify every other "
                    "device with the first one.",
                    "Allow notifications, so that new messages reach you.",
                    "On the phone, turn on threads: Settings > Labs > Threads, then close and "
                    "reopen the app."),
        "others": "Everyone else signs in the same way later, with their first name, lowercase, "
                  "as username and first password.",
        "services_sub_off": "Photos, documents, AI and the memory",
        "services_sub_on": "{names} running",
        "services_body": "Start what you want to use. Each one takes a few minutes, and you can "
                         "start the rest any time.",
        "try_sub_off": "Requires \"Documents\"",
        "try_sub_no_ai": "Requires \"{ai}\"",
        "try_body_no_ai": "famstack needs the AI to read a document: it gives it a title, tags "
                          "and a summary. Start it, then post your first document.",
        "try_sub_waiting": "Waiting for a document in the Documents room",
        "try_sub_done": "Your first document was filed",
        "try_body_off": "Once Documents is added, photograph a document, such as a letter, and "
                        "send it to the Documents room. A minute later it is filed, with a title "
                        "and a summary.",
        "try_body_waiting": "Photograph any document, such as a letter, and send it to the "
                            "Documents room in the chat. A minute later it shows up on this page.",
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
            "chat": "Den Familienchat starten", "people": "Die Chat-App einrichten",
            "services": "Die Dienste starten",
            "try": "Das erste Dokument posten",
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
        "chat_needed": "famstack braucht den Chat. Er verbindet alles, und ihr erreicht ihn über die "
                       "Apps auf Computer und Handy.",
        "chat_body_off": "Im Chat redet eure Familie, und hier gebt ihr famstack Dinge: einen "
                         "Brief, eine Sprachnachricht, eine Frage. Sein Server heißt für immer "
                         "{server}.",
        "chat_body_on": "Der Chat läuft. Richte als Nächstes die App ein und melde dich an.",
        "username": "Benutzername", "password": "Passwort", "open_chat": "Chat öffnen",
        "people_sub_off": "Erfordert „Chat“",
        "people_sub_on": "Probier es zuerst selbst aus",
        "people_sub_done": "Du bist angemeldet",
        "people_body_off": "Sobald der Chat läuft, meldest du dich mit der App auf Handy und "
                           "Computer an.",
        "people_body_on": "Probier den Chat zuerst selbst aus, auf dem Handy und am Computer. Die "
                          "anderen kommen später auf demselben Weg dazu.",
        "apps": ("Element X für iPhone", "Element X für Android", "Element für Mac und Windows"),
        "server_label": "Serveradresse für die App",
        "server_note": "Gib sie genau so und vollständig ein. Die App braucht diese Adresse, nicht "
                       "die der Chat-Webseite.",
        "the_server": "die Adresse des Chat-Servers",
        "change_password": "Ändere das Passwort, sobald du angemeldet bist.",
        "sign_in": ("Öffne die App und wähle Anmelden.",
                    "Wenn sie nach dem Server fragt, wähle Ändern und gib {server} ein.",
                    "Warnt die App, dass die Verbindung kein HTTPS nutzt: Im Heimnetz ist das "
                    "normal. Fahre fort.",
                    "Melde dich mit dem Benutzernamen und dem Passwort von oben an.",
                    "Verschlüsselung: Setze auf deinem ersten Gerät die Identität zurück. "
                    "Bestätige jedes weitere Gerät mit dem ersten.",
                    "Erlaube Mitteilungen, damit dich neue Nachrichten erreichen.",
                    "Schalte auf dem Handy Threads ein: Einstellungen > Labs > Threads, dann schließe "
                    "die App und öffne sie wieder."),
        "others": "Alle anderen melden sich später genauso an, mit ihrem Vornamen in "
                  "Kleinbuchstaben als Benutzername und erstes Passwort.",
        "services_sub_off": "Fotos, Dokumente, KI und das Gedächtnis",
        "services_sub_on": "{names} laufen",
        "services_body": "Startet, was ihr nutzen wollt. Jeder Dienst dauert ein paar Minuten, "
                         "den Rest könnt ihr jederzeit später starten.",
        "try_sub_off": "Erfordert „Dokumente“",
        # The tile's name in quotes, so it needs no case ending: „Lokale KI“.
        "try_sub_no_ai": "Erfordert „{ai}“",
        "try_body_no_ai": "famstack braucht die KI, um ein Dokument zu lesen: Sie gibt ihm Titel, "
                          "Schlagwörter und eine Zusammenfassung. Starte sie, dann poste dein "
                          "erstes Dokument.",
        "try_sub_waiting": "Wartet auf ein Dokument im Dokumente-Raum",
        "try_sub_done": "Euer erstes Dokument ist abgelegt",
        "try_body_off": "Sobald Dokumente dabei ist, fotografiere ein Dokument, etwa einen Brief, "
                        "und schick es in den Dokumente-Raum. Eine Minute später ist es abgelegt, "
                        "mit Titel und Zusammenfassung.",
        "try_body_waiting": "Fotografiere ein Dokument, etwa einen Brief, und schick es in den "
                            "Dokumente-Raum im Chat. Eine Minute später erscheint es auf dieser "
                            "Seite.",
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
    """What "start the services" offers: the core, then whatever it
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


def ai_reads(ai: dict) -> bool:
    """Whether an AI reads what Documents files, as `stack list` reports the
    ai stacklet: set up and online here, or a server the stack uses and does
    not manage (`stack ai switch <url>`, on another machine or this Mac),
    which the listing gives as its `remote` address."""
    return bool(ai.get("enabled") and ai.get("online")) or bool(ai.get("remote"))


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
                    "body_html": _p(w["chat_body_on"])}
        adding = tiles.get("messages", {}).get("state") == stack_widget.ADDING
        body = _p(w["chat_needed"]) + (_p(w["chat_body_off"].format(server=server)) if server else "")
        return {"done": False, "sub": w["chat_sub_adding"] if adding else w["chat_sub_off"],
                "body_html": body + _tiles_html([tiles.get("messages")], shared)}
    if sid == "people":
        if not on("messages") or chat is None:
            return {"done": False, "sub": w["people_sub_off"], "body_html": _p(w["people_body_off"])}
        admin = next((p for p in people if p.get("role") == "admin"), people[0] if people else None)
        seen = {u["id"].split(":", 1)[0].lstrip("@"): u for u in chat.get("users", [])}
        signed = bool(admin and (seen.get(admin["id"]) or {}).get("last_seen"))
        return {"done": signed, "sub": w["people_sub_done" if signed else "people_sub_on"],
                "body_html": _app_html(admin, by_id.get("messages", {}), w, here)}
    if sid == "services":
        names = [stack_widget.name_of(by_id[x], language) for x in leads if set_up(x)]
        offered = [x for x in leads if x in tiles]
        return {"done": bool(names),
                "sub": w["services_sub_on"].format(names=_listed(names, w["and"])) if names
                else w["services_sub_off"],
                "body_html": _p(w["services_body"]) + _tiles_html([tiles[x] for x in offered], shared)}
    # try: the first document, which Documents files into the memory.
    filed = any("document" in (c.get("types") or {}) or
                any(card.get("type") == "document" for card in c.get("cards", []))
                for c in (history or {}).get("changes", []))
    if not set_up("docs"):
        return {"done": False, "sub": w["try_sub_off"], "body_html": _p(w["try_body_off"])}
    if not ai_reads(by_id.get("ai", {})):
        ai = stack_widget.name_of(by_id.get("ai", {"id": "ai"}), language)
        return {"done": False, "sub": w["try_sub_no_ai"].format(ai=ai),
                "body_html": _p(w["try_body_no_ai"]) + _tiles_html([tiles.get("ai")], shared)}
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


def _app_html(admin: dict | None, messages: dict, w: dict, here: bool) -> str:
    """The chat app's step: the server address first, then the admin's
    username and first password, the apps, the sign-in steps, and the
    others in one line. The address is the chat server's (`synapse` in the
    listing's `urls`), not the chat's web page: it is the one people enter
    wrong, so it stands out, and the step that asks for it names it again."""
    address = (messages.get("urls") or {}).get("synapse", "")
    who = admin["id"] if admin else ""
    server = (f'<div class="setup-server-box"><p class="setup-server-label">{_e(w["server_label"])}</p>'
              f'<code class="setup-server">{_e(address)}</code>'
              f'<p>{_e(w["server_note"])}</p></div>') if address else ""
    creds = (f'<dl class="setup-creds"><dt>{_e(w["username"])}</dt><dd><code>{_e(who)}</code></dd>'
             f'<dt>{_e(w["password"])}</dt><dd><code>{_e(who)}</code></dd></dl>'
             + _p(w["change_password"])) if who else ""
    apps = "".join(f'<a href="{_e(href)}" target="_blank" rel="noopener">{_e(label)}</a>'
                   for label, href in zip(w["apps"], APPS))
    named = (f'<code class="setup-server-step">{_e(address)}</code>' if address
             else _e(w["the_server"]))
    steps = "".join(f"<li>{_e(s).replace('{server}', named)}</li>" for s in w["sign_in"])
    # Where a browser can open the chat (on this Mac, or anywhere over
    # HTTPS), beside the apps: one more way to sign in.
    href = stack_widget.open_link(messages, here=here)
    if href:
        apps += f'<a class="stack-open" href="{_e(href)}">{_e(w["open_chat"])}</a>'
    return (_p(w["people_body_on"]) + server + creds + f'<p class="setup-apps">{apps}</p>'
            + f'<ol class="setup-sign-in">{steps}</ol>' + _p(w["others"]))


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
