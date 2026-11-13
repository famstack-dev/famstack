"""The service card: everything about one service of the stack, in one place.

A service is what a person uses: the family chat, photos, documents, the
family memory, the AI. Its service card shows that service as a person
meets it, in the same layout for every service:

    head       its name, its app ("Immich"), its status word with its mark
    live       starting: the current step, every step behind a closed
               "Show details"; running: "Open", in a new tab
    sign in    the address, the username, and the password only on the
               Mac that runs famstack; elsewhere, where to find it
    app        the download for the device that looks, where there is one
    guides     a place for famstack's guides on using the service well,
               empty until the guides exist

Always the full name, "service card": a card alone is a file in the
family memory. The setup page (`setup_page.py`) shows them in its steps
and under "Your stack"; the dashboard behind its status pills
(`status_pills.py`).

    sc = view(tile, stacklet, homer, language="en", who=MAC, device="mac",
              password="otter-maple-cloud-lamp")
    sc["status"]                 # "running"
    sc["sign_in"]["username"]    # "homer@home.local", Immich asks for the email
    html(sc)                     # '<article class="service-card" data-service="photos" ...'

**Built on the tile.** A service card reads the stacklet's tile
(`stack_widget.tile`): its state, its job and the job's steps, its link,
its Start and its command. So a start from any surface (the dashboard,
the terminal, this page) shows on the service card as it shows on the
tile, and the page's script follows it live with the dashboard's
machinery (`dashboard/setup-page.js` extends `FsStack`). For that the
service card keeps the tile's markup where the script reads it: Start is
a `tile-add` button, the steps a `tile-steps` list.

**Who has an account follows the service.** Every family member has an
account in the chat and in Photos; only admin-role people have one in
Documents (its install hook makes no other); the family memory has none
(its wiki opens on a paired device without one). A person without an
account gets no "Open" and no sign-in. Nobody named (`person` None: a
shared device, or a device whose person the page does not know) gets
"Open" and no sign-in. The stack's own accounts (Immich's `stackadmin`)
are never shown: the service card names only the person who looks.

**The password is the stored one,** which the caller reads through the
host API (`stack.passwords.stored`) and passes as `password`; a person
who changed it in a service knows the new one. A card shows it only on
the Mac that runs famstack (`who.here`), because a page on another device
can be read over a shoulder or kept in a browser's cache.

**The AI's key is named on the Mac only.** The oMLX the stack installed
before keys were generated takes the key `local`, which every device at
home can send. While it does, the AI's service card on the Mac that runs
famstack says so, with "Make a new key" (`placeholder_key`, from `stack
ai key show`); the page's script posts it and shows the new key. Anywhere
else the card shows neither: a key read on a phone can be read over a
shoulder, and only the Mac changes the server.

**The app follows the device** (`device_of`): the browser's user agent
names an iPhone, an Android phone, or neither. Only an app that exists
for that device is offered: Immich has phone apps and no Mac app.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlsplit, urlunsplit

import stack_widget

if TYPE_CHECKING:
    from devices import Who

__all__ = ["SERVICES", "Service", "view", "html", "status_html", "sign_in_html", "value_html", "secret_html",
           "chat_server",
           "device_of", "words"]


@dataclass(frozen=True)
class Service:
    """What a service card knows of one service beyond its tile.

    `app` is the app's name beside the service's name, "" for none.
    `username` is what its sign-in asks for: "id" (the username), "email",
    or "" when it has no accounts. `address` is "server" when the apps ask
    for the chat server's address, "page" for the service's own page.
    `admins_only` says only admin-role people have an account. `open` is
    the key of its "Open" in `WORDS`, `apps` the download per device.

        SERVICES["docs"].admins_only    # True: only admins have Paperless
    """
    app: str
    username: str = ""
    address: str = "page"
    admins_only: bool = False
    open: str = "open"
    apps: tuple[tuple[str, str], ...] = ()


# The services a person uses, in the order "Your stack" lists them. Code is
# not one: the memory keeps its pages there, and nobody signs in to it.
SERVICES = {
    "messages": Service("Element", username="id", address="server"),
    "ai": Service("oMLX"),
    "photos": Service("Immich", username="email", apps=(
        ("iphone", "https://apps.apple.com/app/immich/id1613945652"),
        ("android", "https://play.google.com/store/apps/details?id=app.alextran.immich"))),
    "docs": Service("Paperless-ngx", username="id", admins_only=True, open="open_paperless"),
    "memory": Service("", open="open_wiki"),
}

# A tile's state as the service card's status: the four status words the
# setup page and the dashboard's pills share. A tile that can be added, or
# whose start failed, shows no status: it is not set up.
_STATUS = {stack_widget.RUNNING: "running", stack_widget.ADDING: "starting",
           stack_widget.STARTING: "starting", stack_widget.NOT_ANSWERING: "not_answering",
           stack_widget.FAILING: "not_answering", stack_widget.STOPPED: "stopped"}

WORDS = {
    "en": {
        "status": {"running": "Running", "starting": "Starting", "not_answering": "Not answering",
                   "stopped": "Stopped"},
        # A stopped service on the home address, which takes no change.
        "stopped_elsewhere": "Stopped · start it on the Mac",
        "names": {"memory": "Family memory", "ai": "Your AI"},
        "show_details": "Show details",
        "open": "Open", "open_paperless": "Open Paperless", "open_wiki": "Open the wiki",
        "sign_in_with": "Sign in with", "server": "Server", "address": "Address",
        "username": "Username", "email": "Email", "password": "Password",
        "elsewhere": "your famstack password; your own phone shows it under “Your sign-in”",
        "your_sign_in": "Your sign-in",
        "on_own_phone": "Each person's sign-in is on their own phone.",
        "show": "Show", "hide": "Hide", "copy": "Copy", "copied": "Copied",
        "app": "App", "apps": {"iphone": "{app} on the App Store", "android": "{app} on Google Play"},
        "guides": "Guides", "guides_later": "famstack's guides on using {name} well come here later.",
        "in_terminal": "In the terminal",
        "key_placeholder": "Key: local. Every device at home can use the AI with it.",
        "new_key": "Make a new key",
        "new_key_done": "The AI's new key: {key}. Apps on other computers that use this AI need it too.",
    },
    "de": {
        "status": {"running": "Läuft", "starting": "Wird gestartet", "not_answering": "Antwortet nicht",
                   "stopped": "Gestoppt"},
        "stopped_elsewhere": "Gestoppt · starte den Dienst auf dem Mac",
        "names": {"memory": "Familiengedächtnis", "ai": "Eure KI"},
        "show_details": "Details anzeigen",
        "open": "Öffnen", "open_paperless": "Paperless öffnen", "open_wiki": "Wiki öffnen",
        "sign_in_with": "Anmelden mit", "server": "Server", "address": "Adresse",
        "username": "Benutzername", "email": "E-Mail", "password": "Passwort",
        "elsewhere": "dein famstack-Passwort; dein eigenes Handy zeigt es unter „Deine Anmeldung“",
        "your_sign_in": "Deine Anmeldung",
        "on_own_phone": "Die Anmeldung jeder Person steht auf ihrem eigenen Handy.",
        "show": "Anzeigen", "hide": "Ausblenden", "copy": "Kopieren", "copied": "Kopiert",
        "app": "App", "apps": {"iphone": "{app} im App Store", "android": "{app} bei Google Play"},
        "guides": "Anleitungen",
        "guides_later": "Hier findest du später Anleitungen, wie ihr {name} am besten nutzt.",
        "in_terminal": "Im Terminal",
        "key_placeholder": ("Schlüssel: local. Jedes Gerät bei euch zu Hause kann die KI "
                            "damit nutzen."),
        "new_key": "Neuen Schlüssel erstellen",
        "new_key_done": ("Der neue Schlüssel eurer KI: {key}. Apps auf anderen Computern, "
                         "die diese KI nutzen, brauchen ihn auch."),
    },
}


def words(language: str) -> dict:
    """The service card's words in `language`: German for "de", English otherwise.

        words("de")["status"]["running"]    # "Läuft"
    """
    return WORDS["de" if language == "de" else "en"]


def device_of(user_agent: str) -> str:
    """The device a browser runs on, by its user agent: "iphone",
    "android", or "" for anything else (a Mac, a PC, a tablet's desktop
    mode). Only the app for that device is offered.

        device_of("Mozilla/5.0 (iPhone; CPU iPhone OS 18_1 like Mac OS X) ...")   # "iphone"
        device_of("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...")          # ""
    """
    agent = user_agent.lower()
    if "iphone" in agent:
        return "iphone"
    return "android" if "android" in agent else ""


def view(tile: dict, stacklet: dict, person: dict | None, *, language: str, who: Who,
         device: str = "", apps: list[tuple[str, str]] | None = None,
         describe: str = "", placeholder_key: bool = False,
         password: str | None = None, guide: str = "") -> dict:
    """What one service card shows to `who` (`devices.Who`) on `device`,
    with `person`'s sign-in.

    `tile` is the stacklet's tile (`stack_widget.tile`) and `stacklet` its
    entry in the listing; `person` None is nobody named: "Open", no
    sign-in. `apps`, as (label, link) pairs, replaces the service's
    own downloads (the setup page links the chat's apps to its own steps);
    `describe` is a sentence under the head. A stacklet not in `SERVICES`
    gets a service card without sign-in and app. A shared device shows no
    sign-in, and says where each person's is (`on_own_phone`).
    `placeholder_key` says the AI's key is still `local` (`stack ai key
    show`): the AI's card then names it with "Make a new key", on the
    Mac's address only (`key`). `password` is `person`'s stored password,
    which the card shows on the Mac's address only; None, or anywhere
    else, it shows none. `guide` is the sign-in guide's address
    (`/go/app/sign-in`): on a device paired as `person` the card shows
    that person's address and username and "Your sign-in" to the guide in
    place of the password, because the guide is the one page on the home
    address that carries a password.

    Returns `id`, `name`, `app`, `status` ("" while it is not set up),
    `status_word`, `tile` (for Start, the job and the command), `now` (the
    current step while it starts), `steps`, `open` and `open_label`,
    `sign_in` (address, username, password or None; None when the person
    has no account or it is not set up), `apps`, `guides`, `key` (True
    when the AI's card names its placeholder key) and the words.

        view(photos_tile, photos, marge, language="en", who=Who("person", "marge"),
             device="iphone", guide="/go/app/sign-in")
        # {"status": "running", "sign_in": {"username": "marge@home.local",
        #   "password": None, "guide": "/go/app/sign-in", ...},
        #  "apps": [("Immich on the App Store", "https://apps...")], ...}
    """
    w = words(language)
    service = SERVICES.get(tile["id"], Service(""))
    status = _STATUS.get(tile["state"], "")
    here = who.here
    status_word = (w["stopped_elsewhere"] if status == "stopped" and not here
                   else w["status"].get(status, ""))
    set_up = status in ("running", "not_answering", "stopped")
    has_account = bool(person) and (not service.admins_only or person.get("role") == "admin")
    name = w["names"].get(tile["id"]) or tile["name"]
    return {"id": tile["id"], "name": name, "app": service.app, "describe": describe,
            "status": status, "status_word": status_word, "tile": tile,
            "now": _current_step(tile), "steps": tile["steps"],
            "open": tile["href"] if status == "running" and (person is None or has_account) else "",
            "open_label": w[service.open],
            "sign_in": (_sign_in(service, stacklet, person or {}, here=here,
                                 password=password if here else None,
                                 guide=guide if person and who.sees_password_of(person["id"])
                                 else "")
                        if set_up and has_account and service.username else None),
            "apps": (apps if apps is not None else _apps(service, device, w)) if set_up else [],
            "guides": w["guides_later"].format(name=name) if set_up else "",
            "key": tile["id"] == "ai" and here and set_up and placeholder_key,
            "on_own_phone": who.kind == "household" and set_up and bool(service.username),
            "words": w}


def chat_server(messages: dict, *, here: bool) -> str:
    """The chat server's address the apps ask for (`synapse` in the
    listing's `urls`), not the chat's web page: at localhost on this Mac
    (`here`), at the Mac's home-network address elsewhere.

        chat_server({"urls": {"synapse": "http://192.168.1.20:42031"}}, here=True)
        # "http://localhost:42031"
    """
    server = (messages.get("urls") or {}).get("synapse", "")
    return _local(server) if here else server


def sign_in_html(rows: list[tuple[str, str]], password: str | None, w: dict, *,
                 server: bool = False, guide: str = "") -> str:
    """The box "Sign in with": one row per (label, value) with Copy, then
    the password: masked with Show and Copy when it may be shown here,
    else where to find it. With `guide` (a link to the sign-in guide, on
    a device paired as the person) the password row is "Your sign-in"
    to the guide instead. The setup page's chat steps use it too, so a
    sign-in reads the same everywhere. Copy and Show are the page script's.
    `server` sets the first value large: the chat steps' server address,
    which a person types into an app.

        sign_in_html([("Server", "http://localhost:42031"), ("Username", "homer")],
                     "otter-maple-cloud-lamp", words("en"))
        # '<div class="setup-box" ...><dl class="setup-creds"><dt ...>Server</dt>...'
    """
    shown = "".join(f'<dt class="service-card-field">{_e(label)}</dt>'
                    f'<dd>{value_html(value, w, large=server and i == 0)}</dd>'
                    for i, (label, value) in enumerate(rows))
    if password is not None:
        last = f'<dt>{_e(w["password"])}</dt><dd>{secret_html(password, w)}</dd>'
    elif guide:
        last = (f'<dt>{_e(w["password"])}</dt><dd><a class="fs-link service-card-sign-in-link" '
                f'href="{_e(guide)}">{_e(w["your_sign_in"])} ›</a></dd>')
    else:
        last = (f'<dt>{_e(w["password"])}</dt><dd><span class="setup-note service-card-elsewhere">'
                f'{_e(w["elsewhere"])}</span></dd>')
    return (f'<div class="setup-box" data-copied="{_e(w["copied"])}" data-hide="{_e(w["hide"])}">'
            f'<h3 class="setup-subheading">{_e(w["sign_in_with"])}</h3><dl class="setup-creds">'
            f'{shown}{last}</dl></div>')


def value_html(value: str, w: dict, *, large: bool = False) -> str:
    """One value a person types into an app, with Copy beside it (hidden
    until a page script shows it). `large` sets it large, as the chat's
    server address is. The sign-in box and the sign-in guide
    (`sign_in_guide.py`) both use it, so a value reads the same everywhere.

        value_html("homer", words("en"))
        # '<code class="setup-value">homer</code><button class="setup-mini setup-copy" ...'
    """
    return (f'<code class="setup-value{" setup-server" if large else ""}">{_e(value)}</code>'
            + _copy(w))


def secret_html(password: str, w: dict) -> str:
    """The password, for a page on the Mac that runs famstack, or the
    person's own on a device paired as that person (`devices`): in the
    page as served, masked by the page script, with Show and Copy beside
    it. The sign-in box and the sign-in steps on the setup page
    (`sign_in_guide.steps_html`) both use it. A caller that may not show
    the password does not call it: it says where the password is instead.

        secret_html("otter-maple-cloud-lamp", words("en"))
        # '<code class="setup-value" data-secret>otter-maple-cloud-lamp</code><button class="setup-mini setup-show" ...'
    """
    return f'<code class="setup-value" data-secret>{_e(password)}</code>' + _copy(w, show=True)


def html(sc: dict) -> str:
    """One service card's markup, from `view`. Reads complete without
    JavaScript: Start falls back to the tile's command, "Show details" is
    a plain `<details>`.

        html(view(tile, messages, homer, language="en", who=MAC))
        # '<article class="service-card" data-service="messages" data-status="running" ...'
    """
    w, tile = sc["words"], sc["tile"]
    job = f' data-job="{_e(tile["job"])}"' if tile["job"] else ""
    head = (f'<header class="service-card-head"><h2 class="service-card-name">{_e(sc["name"])}</h2>'
            + (f'<span class="service-card-app">{_e(sc["app"])}</span>' if sc["app"] else "")
            + _status_html(sc) + "</header>")
    parts = [_p(sc["describe"], "service-card-text"), _act_html(sc), _sign_in_part(sc),
             _key_html(sc), _apps_html(sc), _guides_html(sc)]
    return (f'<article class="service-card" data-service="{_e(sc["id"])}" '
            f'data-id="{_e(sc["id"])}" data-status="{sc["status"] or tile["state"]}"{job} '
            f'data-show-details="{_e(w["show_details"])}">'
            + head + "".join(parts) + "</article>")


def status_html(status: str, word: str) -> str:
    """A status as a service card and a dashboard pill show it: its mark
    and its word, never colour alone; "" for no status.

        status_html("running", "Running")
        # '<span class="service-card-status" data-status="running"><span class="dot" ...'
    """
    if not status:
        return ""
    return (f'<span class="service-card-status" data-status="{status}">'
            f'<span class="dot" data-status="{status}" aria-hidden="true"></span>'
            f'{_e(word)}</span>')


# ── The parts of a service card ───────────────────────────────────────────

def _current_step(tile: dict) -> str:
    """The step a start is at: the one running, else the last one, else
    the tile's "Starting…"."""
    steps = tile["steps"]
    running = [s for s in steps if s.get("state") == "running"]
    return (running or steps or [{"text": tile["label"]}])[-1]["text"]


def _sign_in(service: Service, stacklet: dict, person: dict, *, here: bool,
             password: str | None, guide: str) -> dict:
    """The person's sign-in to one service: where, as whom, the password
    where it may be shown (None elsewhere), and the guide's link on the
    person's own paired device ("" elsewhere)."""
    address = (chat_server(stacklet, here=here) if service.address == "server"
               else stack_widget.open_link(stacklet, here=here))
    username = person.get("email", "") if service.username == "email" else person.get("id", "")
    return {"address_label": "server" if service.address == "server" else "address",
            "address": address, "username": username,
            "username_label": "email" if service.username == "email" else "username",
            "password": password, "guide": guide}


def _apps(service: Service, device: str, w: dict) -> list[tuple[str, str]]:
    return [(w["apps"][d].format(app=service.app), link) for d, link in service.apps if d == device]


def _status_html(sc: dict) -> str:
    return status_html(sc["status"], sc["status_word"])


def _act_html(sc: dict) -> str:
    """What the service card offers or follows by its state: Start, the
    live start, Open, or the tile's line and command."""
    tile, w = sc["tile"], sc["words"]
    if sc["status"] == "starting":
        return _live_html(sc["now"], sc["steps"], w)
    if sc["open"]:
        return (f'<div class="service-card-act"><a class="fs-button service-card-open" data-kind="go" '
                f'href="{_e(sc["open"])}" target="_blank" rel="noopener">{_e(sc["open_label"])}</a></div>')
    command = (f'<p class="tile-terminal"><span>{_e(tile["in_terminal"])}</span> '
               f'<code class="tile-command">{_e(tile["command"])}</code></p>'
               if sc["status"] != "running" else "")
    if tile["add"]:
        # Start needs the page's script; without it the command stands in.
        forms = " data-forms" if tile.get("forms") else ""
        return (f'<div class="service-card-act" data-start><button class="tile-add" type="button" '
                f'data-target="{_e(tile["id"])}"{forms}>{_e(tile["add"])}</button>{command}</div>')
    said = tile["label"] if tile["state"] in (stack_widget.ADD, stack_widget.FAILED) else ""
    return f'<div class="service-card-act">{_p(said, "service-card-said")}{command}</div>'


def _live_html(now: str, steps: list[dict], w: dict) -> str:
    """The current step with a spinner, and every step behind a closed
    "Show details". The page's script adds a step as it happens."""
    items = "".join(f'<li class="tile-step" data-status="{_e(s.get("state", ""))}"'
                    + (f' data-step="{_e(str(s["id"]))}"' if s.get("id") else "")
                    + f'>{_e(s["text"])}</li>' for s in steps)
    return (f'<div class="service-card-live"><p class="service-card-run" aria-live="polite">'
            f'<span class="service-card-spin" aria-hidden="true"></span>'
            f'<span class="service-card-now">{_e(now)}</span></p>'
            f'<details class="service-card-details"><summary>{_e(w["show_details"])}</summary>'
            f'<ol class="tile-steps">{items}</ol></details></div>')


def _sign_in_part(sc: dict) -> str:
    s, w = sc["sign_in"], sc["words"]
    if sc.get("on_own_phone"):
        # A shared device belongs to the household: it shows nobody's
        # username or password, only where each person finds their own.
        return f'<div class="service-card-part">{_p(w["on_own_phone"], "service-card-shared")}</div>'
    if not s:
        return ""
    rows = [(w[s["address_label"]], s["address"]), (w[s["username_label"]], s["username"])]
    return f'<div class="service-card-part">{sign_in_html(rows, s["password"], w, guide=s["guide"])}</div>'


def _key_html(sc: dict) -> str:
    """The AI's placeholder key and "Make a new key", which needs the
    page's script (`widgets/service-cards.js`): it posts the request and
    puts the answer, `new_key_done` with the key, in place of the line."""
    if not sc.get("key"):
        return ""
    w = sc["words"]
    return (f'<div class="service-card-part service-card-key" '
            f'data-done="{_e(w["new_key_done"])}" data-copy="{_e(w["copy"])}">'
            f'<p class="service-card-key-line">{_e(w["key_placeholder"])}</p>'
            f'<div class="service-card-act"><button class="fs-button service-card-new-key" '
            f'data-kind="soft" type="button">{_e(w["new_key"])}</button></div></div>')


def _apps_html(sc: dict) -> str:
    if not sc["apps"]:
        return ""
    links = "".join(f'<a class="fs-button" data-kind="soft" href="{_e(link)}"'
                    + ('' if link.startswith("/") else ' target="_blank" rel="noopener"')
                    + f'>{_e(label)}</a>' for label, link in sc["apps"])
    return (f'<div class="service-card-part"><h3 class="service-card-label">{_e(sc["words"]["app"])}</h3>'
            f'<div class="service-card-apps">{links}</div></div>')


def _guides_html(sc: dict) -> str:
    if not sc["guides"]:
        return ""
    return (f'<div class="service-card-part service-card-guides">'
            f'<h3 class="service-card-label">{_e(sc["words"]["guides"])}</h3>'
            f'<p class="service-card-later">{_e(sc["guides"])}</p></div>')


# ── Small helpers ─────────────────────────────────────────────────────────

def _copy(w: dict, *, show: bool = False) -> str:
    """Copy (and Show) beside a value, hidden until the page's script runs."""
    shown = (f'<button class="setup-mini setup-show" type="button" hidden>{_e(w["show"])}</button>'
             if show else "")
    return shown + f'<button class="setup-mini setup-copy" type="button" hidden>{_e(w["copy"])}</button>'


def _local(url: str) -> str:
    """A plain-HTTP address as this Mac reaches it: localhost, same port.
    An HTTPS address (domain mode) stays as it is: its certificate names it."""
    parts = urlsplit(url)
    if parts.scheme != "http" or not parts.port:
        return url
    return urlunsplit(parts._replace(netloc=f"localhost:{parts.port}"))


def _p(text: str, cls: str) -> str:
    return f'<p class="{cls}">{_e(text)}</p>' if text else ""


def _e(text: str) -> str:
    return escape(str(text), quote=True)
