"""The sign-in guide: `/go/app/sign-in` explains, step by step, how the chat
app on this device signs in to the family's own chat server.

An app such as Element X signs in to matrix.org unless it is told
otherwise, and changing the server is the step early users got stuck on.
The phone page (`phone_page.py`, `/go/app`) offers the app and leads on to
this guide. The guide reads the device from the same User-Agent rule and
shows the steps of the app that device got:

    device          the app                 the steps
    an Android      Element X               install, open, server, warning,
    an iPhone, iPad Element X                 account, verify, notifications,
                                              threads
    anything else   Element for the         install, open, server, account,
                    computer                  verify

The step "server" holds the chat server's address on the home network,
the step "account" the admin's username, each with Copy. The password is
never on the page: the step says it is the one on the setup page on the
Mac. A phone's last step turns on threads, because famstack answers in
threads, and Element X has them in Labs.

    v = view(device="android", language="en", server="http://192.168.1.20:42031",
             username="homer")
    v["app"]                        # "element-x"
    [s["kind"] for s in v["steps"]]  # ["install", "open", "server", ..., "threads"]
    html(v, base="/dashboard")      # the whole page, as a str

Why it works this way:

- **Labels only where they were checked.** An app renames its buttons
  between versions. A step quotes the app's own label only where it was
  checked on the app (Element for the computer: "Sign in", "Edit";
  Element X: Settings › Labs › Threads, which the user guide documents).
  Elsewhere the step names the action, which survives a renamed button.
- **Complete without JavaScript.** Every value is in the page as served.
  The one script (`sign-in-guide.js`) only shows Copy. On the Mac's plain
  HTTP address a phone browser has no clipboard API, so Copy falls back to
  the browser's copy command (`copy-value.js`).
- **The same words as the rest.** The values are the sign-in box's values
  (`service_card.value_html`), the store buttons the phone page's.

**On the Mac, too.** Step 3 of the setup page ("Chat on your phone")
shows the same steps under its box "Sign in with" until the phone is
signed in (`setup_page.py`), so the admin reads them on the Mac's screen
while installing the app on the phone. There (`on_the_mac`) the install
step has no store button, because a store link opens on the phone, not on
the Mac, and the intro says the steps are for the phone. On the Mac that
runs famstack the setup page also passes the password, and the account
step shows it under the username, as the box does: masked by the page's
script, with Show and Copy (`service_card.secret_html`). The admin types
it into the phone, and reads it right where the step asks for it. Opened
from another device, the setup page passes none, and the step says where
the password is, as on the phone:

    v = view(device="iphone", language="en", server="http://192.168.1.20:42031",
             username="homer", on_the_mac=True, password="otter-maple-cloud-lamp")
    steps_html(v)                   # '<p class="setup-text">...<code class="setup-value" data-secret>otter-maple-cloud-lamp</code>...'

What it does not do: Element's QR sign-in, and other chat apps.

Where it sits: `server.py` serves it at `/<prefix>/app/sign-in`
(`phone_page.SIGN_IN`), on the public port like the phone page.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from html import escape
from string import Template

import phone_page
import service_card
from dashboard_page import PAGE_DIR

__all__ = ["view", "html", "steps_html", "words"]

# The app each device class gets, as the phone page offers it.
APPS = {"iphone": "element-x", "android": "element-x", "": "element"}

# Each step is (kind, text). A step that gives a value to enter names it in
# its kind: "server" and "account". "{app}" in a text is the store button.
WORDS = {
    "en": {
        "title": "Sign in to your family's chat",
        "intro": "Follow these steps on this device. They take about five minutes.",
        "intro_phone": "Follow these steps on your phone. They take about five minutes.",
        "copy": "Copy", "copied": "Copied",
        "password": "Your password: the one on the setup page on your Mac.",
        "password_changed": "Changed it in the app? Use yours.",
        "not_running": "The family chat is not started yet. Start it on the setup page on your Mac.",
        "element-x": [
            ("install", "Install Element X, if it is not on your phone yet."),
            ("open", "Open Element X and tap “Sign in manually”, not “Sign in with QR code”. You do "
                     "not need a new account."),
            ("server", "Change the server from matrix.org to your family's own. "
                       "Enter it in full, with http:// at the front:"),
            ("warning", "The app warns that the connection is not encrypted (no HTTPS). "
                        "On your home network that is fine: continue."),
            ("account", "Sign in with your username:"),
            ("wait", "The first sign-in can take a few minutes. Keep the app open until it is "
                     "done."),
            ("notifications", "Allow notifications, so new messages reach you."),
            ("threads", "Turn on threads: Settings › Labs › Threads, then close the app and "
                        "open it again. famstack answers in threads."),
        ],
        "element": [
            ("install", "Install Element for this computer, if it is not here yet."),
            ("open", "Open Element and click “Sign in”."),
            ("server", "Next to the server, click “Edit” and enter your family's server in "
                       "full, with http:// at the front:"),
            ("account", "Sign in with your username:"),
            ("verify", "If Element asks you to confirm it is you, confirm it with another "
                       "device, and accept the request in Element on your Mac."),
        ],
    },
    "de": {
        "title": "Melde dich in eurem Familienchat an",
        "intro": "Folge diesen Schritten auf diesem Gerät. Das dauert etwa fünf Minuten.",
        "intro_phone": "Folge diesen Schritten auf deinem Handy. Das dauert etwa fünf Minuten.",
        "copy": "Kopieren", "copied": "Kopiert",
        "password": "Dein Passwort steht auf der Einrichtungsseite an deinem Mac.",
        "password_changed": "In der App geändert? Dann gilt deins.",
        "not_running": "Der Familienchat ist noch nicht gestartet. Starte ihn auf der "
                       "Einrichtungsseite am Mac.",
        "element-x": [
            ("install", "Installiere Element X, falls es noch nicht auf deinem Handy ist."),
            ("open", "Öffne Element X und melde dich manuell an („Sign in manually“), nicht mit "
                     "QR-Code. Ein neues Konto brauchst du nicht."),
            ("server", "Ändere den Server von matrix.org auf euren eigenen. "
                       "Gib ihn vollständig ein, mit http:// am Anfang:"),
            ("warning", "Die App warnt, dass die Verbindung nicht verschlüsselt ist (kein HTTPS). In eurem Heimnetz "
                        "ist das in Ordnung. Mach einfach weiter."),
            ("account", "Melde dich mit deinem Benutzernamen an:"),
            ("wait", "Die erste Anmeldung kann ein paar Minuten dauern. Lass die App so lange "
                     "offen."),
            ("notifications", "Erlaube Benachrichtigungen, damit du neue Nachrichten mitbekommst."),
            ("threads", "Schalte Threads ein: Einstellungen › Labs › Threads. Schließe die App "
                        "danach und öffne sie neu. famstack antwortet in Threads."),
        ],
        "element": [
            ("install", "Installiere Element für diesen Computer, falls es hier noch fehlt."),
            ("open", "Öffne Element und klicke auf „Anmelden“."),
            ("server", "Klicke neben dem Server auf „Bearbeiten“ und gib euren Server "
                       "vollständig ein, mit http:// am Anfang:"),
            ("account", "Melde dich mit deinem Benutzernamen an:"),
            ("verify", "Wenn Element fragt, ob du es bist, bestätige es mit einem anderen Gerät "
                       "und nimm die Anfrage in Element an deinem Mac an."),
        ],
    },
}


def words(language: str) -> dict:
    """The guide's words in `language`: German for "de", English otherwise.

        words("de")["title"]   # "Melde dich in eurem Familienchat an"
    """
    return WORDS["de" if language == "de" else "en"]


def view(*, device: str, language: str, server: str, username: str,
         on_the_mac: bool = False, password: str | None = None) -> dict:
    """What the guide shows to `device` ("iphone", "android" or "", as
    `phone_page.device_of` names it).

    `server` is the chat server's address on the home network, "" while
    the chat does not run: the guide then has no steps and says so.
    `username` is the admin's. `on_the_mac` is the guide beside step 3's
    box "Sign in with": no store button, and the steps are for the phone.
    `password` is the password for the account step: the setup page on
    the Mac that runs famstack passes the admin's, and the guide's own
    page on the home address passes the person's own, on a device paired
    as that person only (`devices.Who.sees_password_of`).

    Returns `language`, `app` ("element-x" or "element"), `store` (label,
    link) for the install step or None, `intro` (the line above the
    steps), `password` (the password to show, or None), `password_where`
    (where the account step says the password is, without one), `steps`
    (each `kind`, `text`, and `value` or ""), and `words`.

        view(device="", language="en", server="http://192.168.1.20:42031",
             username="homer")["steps"][2]
        # {"kind": "server", "text": "Next to the server, click “Edit” ...",
        #  "value": "http://192.168.1.20:42031"}
    """
    w = words(language)
    app = APPS.get(device, "element")
    values = {"server": server, "account": username}
    steps = [{"kind": kind, "text": text, "value": values.get(kind, "")}
             for kind, text in w[app]] if server else []
    store = None if on_the_mac else phone_page.view(device=device, language=language, server="",
                                                    username="", browser="")["app"]
    return {"language": language, "app": app, "words": w, "steps": steps, "store": store,
            "intro": w["intro_phone" if on_the_mac else "intro"],
            "password": password, "password_where": w["password"]}


def html(v: dict, *, base: str) -> str:
    """The guide for view `v`: complete HTML, loading the dashboard's
    styles from `base` (`/dashboard`) and one script that shows Copy.

        html(view(...), base="/dashboard")   # '<!doctype html>...<ol class="sign-in-steps" ...'
    """
    w = v["words"]
    main = (f'<h1 class="setup-heading">{_e(w["title"])}</h1>'
            + (steps_html(v) if v["steps"] else f'<p class="setup-waiting">{_e(w["not_running"])}</p>'))
    script = f'<script type="module" src="{_e(base)}/static/sign-in-guide.js"></script>'
    return Template((PAGE_DIR / "phone.html").read_text()).substitute(
        lang=v["language"], base=base, title=_e(phone_page.words(v["language"])["title"]),
        main=main, script=script)


def steps_html(v: dict) -> str:
    """The guide's intro and numbered steps for view `v`, as markup to put
    in a page: the store button in "install" (when `v` has one), the value
    to enter in "server" and "account" with Copy hidden until the page's
    script shows it, and after the username the password (masked, with
    Show and Copy) when `v` has one, else where it is. The guide's page
    shows it; so does step 3 on the setup page.

        steps_html(view(device="android", ..., on_the_mac=True))
        # '<p class="setup-text">Follow these steps ...<ol class="sign-in-steps" data-app="element-x" ...'
    """
    w = v["words"]
    sc = {"copy": w["copy"]}
    box = service_card.words(v["language"])
    items = []
    for step in v["steps"]:
        body = f'<p class="sign-in-text">{_e(step["text"])}</p>'
        if step["kind"] == "install" and v["store"]:
            label, link = v["store"]
            body += (f'<a class="fs-button" data-kind="go" href="{_e(link)}" target="_blank" '
                     f'rel="noopener">{_e(label)}</a>')
        if step["value"]:
            body += (f'<p class="sign-in-value">'
                     f'{service_card.value_html(step["value"], sc, large=step["kind"] == "server")}</p>')
        if step["kind"] == "account":
            body += _password_html(v, box)
        items.append(f'<li class="sign-in-step" data-step="{step["kind"]}">{body}</li>')
    return (f'<p class="setup-text">{_e(v["intro"])}</p>'
            f'<ol class="sign-in-steps" data-app="{v["app"]}" data-copied="{_e(w["copied"])}" '
            f'data-hide="{_e(box["hide"])}">'
            + "".join(items) + "</ol>")


def _password_html(v: dict, box: dict) -> str:
    """The account step's password line: the password under the box's own
    label, masked with Show and Copy, when `v` has one, and under it the
    line that a password changed in the app counts there; else where the
    password is. `box` is the sign-in box's words, so the label and the
    buttons read as they do there.

    The shown password is the first one famstack stored. A person may
    have changed it in the app since, and famstack cannot know, so the
    page says which one counts instead of guessing."""
    if v["password"] is None:
        return f'<p class="sign-in-password setup-note">{_e(v["password_where"])}</p>'
    return (f'<p class="sign-in-value sign-in-password"><span class="sign-in-label">{_e(box["password"])}</span>'
            f'{service_card.secret_html(v["password"], box)}</p>'
            f'<p class="sign-in-password-changed setup-note">{_e(v["words"]["password_changed"])}</p>')


def _e(text: str) -> str:
    return escape(text, quote=True)
