"""The phone page: `/go/app` offers the chat app that fits the device that opened it.

Step 3 of the setup page ("Chat on your phone") shows a QR code. The code
holds this page's address on the home network, never a store link: an App
Store link works only on an iPhone, and a phone that loads a page from
the Mac has shown that it reaches the Mac (`phone_reach.py`). The page
reads the device from its User-Agent and offers:

    device          the app                       the button
    an Android      Element X on Google Play      "Element X on Google Play"
    an iPhone, iPad Element X on the App Store    "Element X on the App Store"
    anything else   Element for the computer      "Get Element for Mac",
                                                  and the browser over HTTPS

Below it, the box "Sign in with": the chat server at the Mac's address
on the home network, and the username of whoever looks. The page opens
only for a paired device (`devices`). Right after the pairing code was
opened, the page starts with "This phone is now Homer's" and shows that
person's own password with Copy, once. On every later visit it shows the
username and "Your sign-in ›" to the sign-in guide, the one page on the
home address that carries a person's password, as a service card does.
A shared device sees no username and no password, and nobody sees
another person's.

    v = view(device="android", language="en", server="http://192.168.1.20:42031",
             username="homer", browser="")
    v["app"]        # ("Element X on Google Play", "https://play.google.com/...")
    html(v)         # the whole page, as a str

Why it works this way:

- **No JavaScript.** The page must work in any phone browser the camera
  hands it to, so it is plain HTML with the dashboard's styles. Copy
  buttons stay hidden without a script, so the values are plain text to
  select.
- **The browser only over HTTPS.** Element Web needs a secure context. On
  the Mac's home-network address in port mode (plain HTTP) it refuses to
  load, so a laptop is offered the browser only when the chat's web page
  is on HTTPS (domain mode).
- **An iPad in desktop mode is a Mac.** iPadOS names itself a Mac by
  default; without a script the page cannot tell, so such an iPad gets the
  computer's offer. An iPad that names itself gets the App Store.

Below the sign-in box, "Next: sign in step by step" leads to the guide
for the app (`sign_in_guide.py`, at `SIGN_IN`). Where the page sits: `server.py` serves it at `/<prefix>/app`
(`access.py` opens the prefix to the network); `setup_page.py` draws the
QR code that leads here.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

from html import escape
from string import Template

import service_card
from dashboard_page import PAGE_DIR

__all__ = ["LINK", "SIGN_IN", "view", "html", "device_of", "words"]

# The page's path under the link prefix: `/go/app`.
LINK = "app"
# The sign-in guide's path under the link prefix: `/go/app/sign-in`.
SIGN_IN = f"{LINK}/sign-in"
# Where the chat's apps are, as the admin guide recommends (§ Connect your
# phones). Links out; nothing is loaded from them.
STORES = {"iphone": "https://apps.apple.com/app/id1631335820",
          "android": "https://play.google.com/store/apps/details?id=io.element.android.x"}
COMPUTER_APP = "https://element.io/download"
PHONE_APP = "Element X"

WORDS = {
    "en": {
        "title": "Family chat",
        "phone_heading": "Get the family chat on your phone",
        "phone_text": "famstack's chat works with Element X, a free app for iPhone and Android.",
        "computer_heading": "Get the family chat on this computer",
        "computer_text": "famstack's chat works with Element, a free app for Mac, Windows and Linux.",
        "computer_get": "Get Element for Mac", "browser": "or open it in your browser",
        "not_running": "The family chat is not started yet. Whoever looks after the server starts it "
                       "on the Mac, with the command ./stack up messages in the terminal.",
        "guide": "Next: sign in step by step",
    },
    "de": {
        "title": "Familienchat",
        "phone_heading": "Hol dir den Familienchat aufs Handy",
        "phone_text": "Der Chat von famstack funktioniert mit Element X, einer kostenlosen App für "
                      "iPhone und Android.",
        "computer_heading": "Hol dir den Familienchat auf diesen Computer",
        "computer_text": "Für den Chat von famstack brauchst du Element, eine kostenlose App für Mac, Windows und "
                         "Linux.",
        "computer_get": "Element für Mac herunterladen", "browser": "oder im Browser öffnen",
        "not_running": "Der Familienchat ist noch nicht gestartet. Wer sich um den Server kümmert, "
                       "startet ihn am Mac, im Terminal mit dem Befehl ./stack up messages.",
        "guide": "Weiter zur Anleitung für die Anmeldung",
    },
}


def words(language: str) -> dict:
    """The page's words in `language`: German for "de", English otherwise.

        words("de")["phone_heading"]   # "Hol dir den Familienchat aufs Handy"
    """
    return WORDS["de" if language == "de" else "en"]


def device_of(user_agent: str) -> str:
    """The device class a User-Agent names: "iphone" for an iPhone or an
    iPad that names itself, "android", or "" for a computer.

        device_of("Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) ...")   # "iphone"
        device_of("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...")  # ""
    """
    if "ipad" in user_agent.lower():
        return "iphone"
    return service_card.device_of(user_agent)


def view(*, device: str, language: str, server: str, username: str, browser: str,
         password: str | None = None, paired: tuple[str, str] | None = None,
         guide: str = "") -> dict:
    """What the page shows to `device` ("iphone", "android" or "").

    `server` is the chat server's address on the home network ("" while
    the chat does not run: the page then says so in place of the sign-in),
    `username` the username of whoever looks (the paired device's person,
    the admin on the Mac's address, "" on a shared device), `browser` the
    chat's web page (offered only on a computer and only over HTTPS).
    `password` is that person's own password, passed only on the request
    that paired the device as that person. `paired` is the heading and
    the line right after the phone was paired ("This phone is now
    Homer's"), in place of the page's own. `guide` is the link of the
    sign-in guide, passed on a later visit of the person's own device:
    the box then says "Your sign-in ›" in place of the password.

    Returns `language`, `heading`, `text`, `app` (label, link),
    `browser` (the link or ""), `sign_in` (server, username) or None,
    `password`, `guide` and `words`.

        view(device="iphone", language="en", server="http://192.168.1.20:42031",
             username="homer", browser="")["app"]
        # ("Element X on the App Store", "https://apps.apple.com/app/id1631335820")
    """
    w = words(language)
    phone = device in STORES
    if phone:
        label = service_card.words(language)["apps"][device].format(app=PHONE_APP)
        app = (label, STORES[device])
    else:
        app = (w["computer_get"], COMPUTER_APP)
    where = "phone" if phone else "computer"
    heading, text = paired or (w[f"{where}_heading"], w[f"{where}_text"])
    return {"language": language, "heading": heading, "text": text,
            "app": app, "browser": browser if not phone and browser.startswith("https://") else "",
            "sign_in": (server, username) if server else None, "password": password,
            "guide": "" if password is not None else guide, "words": w}


def html(v: dict, *, base: str) -> str:
    """The page for view `v`: complete HTML without a script, loading the
    dashboard's styles from `base` (`/dashboard`). It holds a password
    only when `v` has one: the person's own, on their own paired device;
    its Copy then needs the guide's script.

        html(view(...), base="/dashboard")   # '<!doctype html>...<a class="fs-button" ...'
    """
    w = v["words"]
    label, link = v["app"]
    get = (f'<a class="fs-button" data-kind="go" href="{_e(link)}" target="_blank" '
           f'rel="noopener">{_e(label)}</a>')
    act = (f'<div class="setup-act">{get}'
           + (_link(w["browser"], v["browser"], cls="fs-link") if v["browser"] else "") + "</div>")
    main = (f'<h1 class="setup-heading">{_e(v["heading"])}</h1>'
            f'<p class="setup-text">{_e(v["text"])}</p>{act}{_sign_in(v)}{_guide(v)}')
    script = (f'<script type="module" src="{_e(base)}/static/sign-in-guide.js"></script>'
              if v["password"] else "")
    return Template((PAGE_DIR / "phone.html").read_text()).substitute(
        lang=v["language"], base=base, title=_e(w["title"]), main=main, script=script)


# ── Parts of the page ─────────────────────────────────────────────────────

def _sign_in(v: dict) -> str:
    """The box "Sign in with", the same box the setup page and the service
    cards show, with the password only when `v` has one; or the line that
    the chat is not started."""
    if not v["sign_in"]:
        return f'<p class="setup-waiting">{_e(v["words"]["not_running"])}</p>'
    server, username = v["sign_in"]
    sc = service_card.words(v["language"])
    rows = [(sc["server"], server)] + ([(sc["username"], username)] if username else [])
    # The password is passed only on the request that paired the device:
    # the person's first view of it, so the box says to write it down.
    return service_card.sign_in_html(rows, v["password"], sc, server=True, guide=v["guide"],
                                     first=v["password"] is not None)


def _guide(v: dict) -> str:
    """The way on to the sign-in guide, once the chat runs. Relative, so it
    stays under whatever link prefix served this page."""
    if not v["sign_in"]:
        return ""
    return (f'<div class="setup-act"><a class="fs-button" data-kind="go" '
            f'href="{_e(SIGN_IN)}">{_e(v["words"]["guide"])}</a></div>')


def _link(label: str, link: str, *, cls: str) -> str:
    return (f'<a class="{cls}" href="{_e(link)}" target="_blank" rel="noopener">'
            f'{_e(label)}</a>')


def _e(text: str) -> str:
    return escape(text, quote=True)
