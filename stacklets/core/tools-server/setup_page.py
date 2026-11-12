"""The setup page: `/setup` walks the admin through the install, one step at a time.

After the first run on `/dashboard` ("Welcome home", then "Start your
journey") the install goes on here, on a page of its own: the family's
dashboard is for every day, this page is for the admin who sets the stack
up. It has seven steps, in the order of the install:

    family     who is at home (done in the first run)
    chat       start the family chat: the chat's service card, Start from
               the page
    phone      the chat on the phone, the setup's one way into the chat, in
               two parts. A QR code opens
               famstack's phone page (`phone_page.py`, `/go/app`) on the
               phone, which offers the app for that phone; part 1, "Your
               phone reaches famstack", is done once it loaded there
               (`phone_reach.py`), and names the likely causes while it
               has not; when the Mac's firewall keeps phones out
               (`stack core firewall`), it says so at once, with the
               fix. Part 2 is the sign-in with the Mac's address on
               the home network, with the sign-in steps for Element X
               under it until the phone is signed in (`sign_in_guide.py`),
               to read on the Mac while installing on the phone.
               Done once the chat lists a sign-in of the admin's. A
               phone that cannot reach famstack has a fallback in the
               step: the chat in the browser or in Element on this Mac,
               with the sign-in on localhost; a sign-in there finishes
               the step too. The admin can always skip it. While part 2
               waits, the open page asks `SIGNED_IN` and goes on by itself
    ai         the AI: from the oMLX app, its key, the recommended models
               and their downloads (`ai_step.py`), or from the terminal;
               with speech, its first words (`first_words_step.py`)
    services   photos, documents and the family memory, a service card each
    try        the first letter: done once Documents has filed a document
               while an AI reads it (`_ai_reads`)
    ready      welcome home, what runs, what is not done yet

A rail shows every step as done, current or still to come, and links each
one, so a done step opens again and steps 3 to 6 open in any order. Below
the steps, once a service is set up, it links "Your stack" (`?step=stack`):
a service card for every set-up service (`service_card.py`), with its
state, its Open and how to sign in. The main column shows one step: the
one asked for (`?step=ai`), else the current one, which is the first step
not done. A skipped phone is not
done, but the current step moves past it. On a phone the rail is one line,
"Step 4 of 7: Your AI".

    # The chat, the AI and the admin's sign-in are done, Photos is not:
    v = view(listing, family, chat, history=history, language="en",
             base="/dashboard", now=now, step="ai", phone="signed", reached=True,
             phone_link="http://192.168.1.20:42000/go/app")
    v["current"]                  # "services"
    v["shown"]                    # "ai", asked for
    [s["status"] for s in v["steps"]][:3]   # ["done", "done", "done"]
    page(v, base="/dashboard")    # the whole page, as bytes

The steps run on the dashboard's machinery, unchanged: each tile is
`stack_widget.tile`, and each service card is built on one, so Start, the
setup forms and a run followed from the log (wherever it started) read the
same here as on `/dashboard`. A service card names the admin's own
accounts: the page is the admin's, and only on the Mac that runs famstack
does it show a password: the stored one, which the caller reads through
the host API (`passwords`) and passes as `passwords`. There the family
step shows each person's own, and the sign-in boxes the admin's. The AI's step shows its own parts
(`ai_step.py`) above the AI's tile, which follows its start. The page
loads the dashboard's files and events with absolute paths; its own script
(`dashboard/setup-page.js`) starts jobs and reads this page again when a
run ends. "Check again" and "Try again" stay on the page they are on.

What the admin said about the phone is kept in a cookie of this browser
(`PHONE_COOKIE`), because nothing on the server knows it. The likely
causes of a phone that does not reach the Mac are on the page from the
start, hidden; the page's script shows them after a while, because a
phone that is still being unlocked has not failed yet. A firewall that
keeps phones out is different: no phone will get through, so its warning
shows from the start. A firewall that cannot be read shows nothing. A step whose
data does not exist yet says what it waits for, never a button that
cannot work. One wording, in one place, English and German.

Pure: no web framework, no I/O.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from html import escape
from string import Template
from typing import TYPE_CHECKING

import ai_step
import first_words_step
import family_view
import qr_code
import service_card
import sign_in_guide
import stack_widget
from access import SETUP_PATH
from dashboard_page import PAGE_DIR

if TYPE_CHECKING:
    from devices import Who

__all__ = ["STEPS", "STACK", "PATH", "PHONE_COOKIE", "PHONE_ANSWERS", "view", "current_step",
           "asks_ai_app", "page", "words", "href", "MAC_SETUP", "only_on_the_mac", "SIGNED_IN", "FIRST_LETTER",
           "signed_in"]

# The page's address, outside the dashboard's prefix, where access.py serves it.
PATH = SETUP_PATH
# The setup on the Mac's address (`access.here`): core's compose file
# publishes it on 127.0.0.1:42002 only. The home address answers the setup
# and every change with the page that points there (`only_on_the_mac`).
MAC_SETUP = "http://127.0.0.1:42002" + SETUP_PATH
# What the open page asks while the phone step waits for the admin's sign-in. A
# sign-in in the chat app sends no event the page could follow, so the
# page's script asks this address every few seconds and reads the page
# again once it says yes (`setup-page.js`). It asks the chat only, not
# `stack list`, which would run every health check each time.
SIGNED_IN = SETUP_PATH + "/signed-in"
# The same for step 6, while it waits for the first document: a filing
# sends the page no event either (`first_letter_in`).
FIRST_LETTER = SETUP_PATH + "/first-letter"
STEPS = ("family", "chat", "phone", "ai", "services", "try", "ready")
# "Your stack": no step of the install, a view the rail links below them.
STACK = "stack"
# What the admin said about the phone, in a cookie of this browser.
PHONE_COOKIE = "fs-setup-phone"
PHONE_ANSWERS = ("signed", "skipped")
# What "photos and documents" starts, in this order; whatever they require
# and a page shows is offered beside them. Code, which the memory needs, is
# not: the memory's start sets it up (`dashboard = false`).
SERVICES = ("photos", "docs", "memory")
# The Element desktop app on a Mac, as the admin guide recommends
# (§ Connect your phones). Links out; nothing is loaded from it. The
# phone's apps are on the phone page (`phone_page.STORES`).
MAC_APP = "https://element.io/download"
# What a person is to the family, beside their name in the family step.
RELATIONS = {"en": {"parent": "parent", "child": "child", "grandparent": "grandparent",
                    "other": "family"},
             "de": {"parent": "Elternteil", "child": "Kind", "grandparent": "Großelternteil",
                    "other": "Familie"}}

WORDS = {
    "en": {
        "title": "Setup", "back": "‹ Dashboard",
        "rail": "Setup steps", "stack": "Your stack",
        "also": "Also available",
        "position": "Step {n} of {total}: {title}",
        "error": "The stack does not answer right now.", "retry": "Try again",
        "steps": {"family": ("Your family", "{names}"),
                  "chat": ("Family chat", "Your family's own chat, on this Mac"),
                  "phone": ("Chat on your phone", "Element X for iPhone or Android"),
                  "ai": ("Your AI", "Reads, listens and speaks"),
                  "services": ("Photos and documents", "Where everything you send ends up"),
                  "try": ("Try it", "Send your first letter"),
                  "ready": ("Ready", "")},
        "next": {"family": "Next: the family chat", "chat": "Next: chat on this Mac",
                 "phone": "Next: your AI",
                 "ai": "Next: photos and documents", "services": "Next: try it", "try": "Next"},
        "family_heading": "Your family",
        "add_a_phone": "Add a phone", "devices": "Devices",
        "password_changed": "Changed a password in an app? Then that one counts there.",
        "family_text": "{names} live here. {admin} looks after the server.",
        "chat_heading": "Start your family chat",
        "chat_text": "Your family talks here, and famstack reads along in the rooms you invite it "
                     "to. The chat server runs on this Mac.",
        "chat_start": "Start the chat", "chat_done": "Your family chat runs.",
        "chat_first": "Start the family chat first.",
        "mac_fallback": "Your phone can't reach famstack? Don't worry: you can use the chat here on "
                        "this Mac too, in your browser or in the Element app.",
        "mac_get": "Get Element for Mac", "mac_browser": "or open it in your browser",
        "copy": "Copy", "copied": "Copied",
        "phone_heading": "Get the family chat on your phone",
        "phone_text": "famstack's chat works with Element X, a free app for iPhone and Android.",
        "phone_qr": "Scan it with your phone's camera, not in the chat app.", "phone_get": "Get the app",
        "phone_reach": "Your phone reaches famstack",
        "phone_signed_in": "Signed in to the chat",
        "phone_slow": "Not reached yet. The likely causes:",
        "phone_causes": ("A guest Wi-Fi that keeps devices apart",
                         "A VPN on the phone",
                         "On an iPhone, the “Local Network” permission",
                         "The firewall of this Mac"),
        # What keeps phones out (`stack core firewall`), and the fix for it.
        "phone_firewall": {
            "block-all": "Your Mac's firewall keeps phones out. Fix: System Settings › Network › "
                         "Firewall › Options: turn off “Block all incoming connections”.",
            "orbstack": "Your Mac's firewall keeps phones out. Fix: System Settings › Network › "
                        "Firewall › Options: allow OrbStack."},
        "skip": "Skip for now",
        "phone_done": "{name} is in the family chat.",
        "ai_heading": "Give famstack its AI",
        "ai_text": "famstack's AI runs on your Mac. It reads your letters, understands voice "
                   "messages and answers questions. Nothing leaves your home.",
        "terminal_heading": "This one needs your Mac's terminal",
        "terminal_why": "macOS asks for permissions along the way.",
        "terminal_follows": "This page follows it once it starts.",
        "services_heading": "Start photos and documents", "start": "Start",
        "start_all": "Start all three",
        "services_lines": {"photos": "Every photo from every phone, backed up to this Mac.",
                           "docs": "Letters you photograph, read and filed.",
                           "memory": "The family wiki and diary, written from what you send."},
        "try_heading": "Send your first letter",
        "try_text": "Photograph a letter and send it to the family chat. famstack reads it, files "
                    "it and tells you in the chat.",
        "try_waiting": "Waiting for your first letter…",
        "try_no_docs": "Start Documents first: it files your letter.",
        "try_no_ai": "Your AI is not running yet. Send your letter when it is ready.",
        "try_done": "Filed: {title}", "try_done_untitled": "Your first letter is filed.",
        "ready_heading": "Welcome home, {called}.",
        "ready_text": "famstack is ready. Your dashboard shows what runs and what happened.",
        "ready_go": "Go to your dashboard", "what_runs": "What runs", "running": "Running",
        "not_done": "Not done yet", "set_it_up": "Set it up",
        "missing": {"phone": "The chat app is not on your phone yet"},
        "and": "and",
        "only_on_the_mac": {
            "title": "This happens on the Mac that runs famstack",
            "line": "Setting up, starting services and passwords are only on the Mac, so no "
                    "other device at home can change famstack.",
            "how": ("On the Mac: click famstack in the menu bar, then Setup…",
                    "Or open 127.0.0.1:42002 in the Mac's browser."),
            "back": "Back to the dashboard"},
    },
    "de": {
        "title": "Einrichtung", "back": "‹ Übersicht", "rail": "Schritte der Einrichtung",
        "stack": "Euer Stack", "also": "Weitere Dienste",
        "position": "Schritt {n} von {total}: {title}",
        "error": "famstack antwortet gerade nicht.", "retry": "Erneut versuchen",
        "steps": {"family": ("Eure Familie", "{names}"),
                  "chat": ("Familienchat", "Euer eigener Chat auf diesem Mac"),
                  "phone": ("Chat auf dem Handy", "Element X für iPhone oder Android"),
                  "ai": ("Eure KI", "Liest, hört zu und spricht"),
                  "services": ("Fotos und Dokumente", "Wo alles landet, was ihr schickt"),
                  "try": ("Ausprobieren", "Poste dein erstes Dokument"),
                  "ready": ("Fertig", "")},
        "next": {"family": "Weiter zum Familienchat", "chat": "Weiter zum Chat auf diesem Mac",
                 "phone": "Weiter zu eurer KI",
                 "ai": "Weiter zu Fotos und Dokumenten", "services": "Weiter zum Ausprobieren",
                 "try": "Weiter"},
        "family_heading": "Eure Familie",
        "add_a_phone": "Handy hinzufügen", "devices": "Geräte",
        "password_changed": "Ein Passwort in der App geändert? Dann gilt dort das neue.",
        "family_text": "{names} wohnen hier. {admin} kümmert sich um den Server.",
        "chat_heading": "Starte euren Familienchat",
        "chat_text": "Hier schreibt eure Familie miteinander, und famstack liest in den Räumen "
                     "mit, in die ihr es einladet. Der Chat-Server läuft auf diesem Mac.",
        "chat_start": "Chat starten", "chat_done": "Euer Familienchat läuft.",
        "chat_first": "Starte zuerst den Familienchat.",
        "mac_fallback": "Dein Handy erreicht famstack nicht? Keine Sorge: Du kannst den Chat auch "
                        "hier am Mac nutzen, im Browser oder in der Element-App.",
        "mac_get": "Element für Mac herunterladen", "mac_browser": "oder im Browser öffnen",
        "copy": "Kopieren", "copied": "Kopiert",
        "phone_heading": "Hol dir den Familienchat aufs Handy",
        "phone_text": "Der Chat von famstack funktioniert mit Element X, einer kostenlosen App für "
                      "iPhone und Android.",
        "phone_qr": "Scanne den Code mit der Kamera deines Handys, nicht in der Chat-App.", "phone_get": "App herunterladen",
        "phone_reach": "Dein Handy erreicht famstack",
        "phone_signed_in": "Im Chat angemeldet",
        "phone_slow": "Dein Handy erreicht famstack noch nicht. Häufige Gründe:",
        "phone_causes": ("Ein Gäste-WLAN, das Geräte voneinander trennt",
                         "Ein VPN auf dem Handy",
                         "Auf dem iPhone die Erlaubnis „Lokales Netzwerk“",
                         "Die Firewall dieses Macs"),
        "phone_firewall": {
            "block-all": "Die Firewall dieses Macs lässt keine Handys herein. Öffne "
                         "Systemeinstellungen › Netzwerk › Firewall › Optionen und schalte "
                         "„Alle eingehenden Verbindungen blockieren“ aus.",
            "orbstack": "Die Firewall dieses Macs lässt keine Handys herein. Öffne "
                        "Systemeinstellungen › Netzwerk › Firewall › Optionen und erlaube dort "
                        "OrbStack."},
        "skip": "Vorerst überspringen",
        "phone_done": "{name} ist im Familienchat.",
        "ai_heading": "Richte eure KI ein",
        "ai_text": "Die KI von famstack läuft auf diesem Mac. Sie liest eure Briefe, versteht "
                   "Sprachnachrichten und beantwortet Fragen. Nichts verlässt euer Zuhause.",
        "terminal_heading": "Dafür brauchst du das Terminal auf deinem Mac",
        "terminal_why": "macOS fragt dabei nach Berechtigungen.",
        "terminal_follows": "Sobald es losgeht, zeigt diese Seite den Fortschritt.",
        "services_heading": "Starte Fotos und Dokumente", "start": "Starten",
        "start_all": "Alle drei starten",
        "services_lines": {"photos": "Jedes Foto von jedem Handy, gesichert auf diesem Mac.",
                           "docs": "Fotografierte Briefe, gelesen und abgelegt.",
                           "memory": "Das Familien-Wiki und das Tagebuch, entstanden aus dem, "
                                     "was ihr schickt."},
        "try_heading": "Poste dein erstes Dokument",
        "try_text": "Fotografiere ein Dokument, etwa einen Brief, und schick es in den "
                    "Familienchat. famstack liest es, legt es ab und sagt dir im Chat Bescheid.",
        "try_waiting": "Warten auf dein erstes Dokument…",
        "try_no_docs": "Starte zuerst „Dokumente“. Dieser Dienst legt dein Dokument ab.",
        "try_no_ai": "Eure KI läuft noch nicht. Schick dein Dokument, sobald sie bereit ist.",
        "try_done": "Abgelegt: {title}", "try_done_untitled": "Dein erstes Dokument ist abgelegt.",
        "ready_heading": "Willkommen zu Hause, {called}.",
        "ready_text": "famstack ist bereit. In der Übersicht siehst du, was läuft und was passiert ist.",
        "ready_go": "Zur Übersicht", "what_runs": "Was läuft", "running": "Läuft",
        "not_done": "Noch nicht erledigt", "set_it_up": "Einrichten",
        "missing": {"phone": "Die Chat-App ist noch nicht auf deinem Handy"},
        "and": "und",
        "only_on_the_mac": {
            "title": "Das passiert auf dem Mac, auf dem famstack läuft",
            "line": "Einrichten, Dienste starten und Passwörter gibt es nur auf dem Mac. So kann "
                    "kein anderes Gerät bei euch zu Hause famstack verändern.",
            "how": ("Auf dem Mac: Klicke in der Menüleiste auf famstack und dann auf Setup…",
                    "Oder öffne 127.0.0.1:42002 im Browser des Macs."),
            "back": "Zurück zur Übersicht"},
    },
}


def words(language: str) -> dict:
    """The page's words in `language`: German for "de", English otherwise.

        words("de")["steps"]["ai"]    # ("Eure KI", "Liest, hört zu und spricht")
    """
    return WORDS["de" if language == "de" else "en"]


def href(step: str = "", *, phone: str = "") -> str:
    """The address of the page, at one step or with the phone's answer.

        href("ai")               # "/setup?step=ai"
        href(phone="skipped")    # "/setup?phone=skipped"
    """
    query = f"step={step}" if step else f"phone={phone}" if phone else ""
    return f"{PATH}?{query}" if query else PATH


# ── The view ──────────────────────────────────────────────────────────────

def view(listing: dict | None, family: dict | None, chat: dict | None, *, history: dict | None,
         language: str, base: str, now: datetime, who: Who, jobs: Sequence[dict] = (),
         step: str = "", phone: str = "", device: str = "", reached: bool = False,
         phone_link: str = "", firewall: str = "",
         ai: dict | None = None, ai_choice: str = "", placeholder_key: bool = False,
         pairing: str = "", passwords: dict[str, str] | None = None,
         ai_app: bool | None = None) -> dict:
    """What the page shows to `who` (`devices.Who`, the Mac on the
    setup's address). `listing`, `family`, `chat`, `history` and `jobs`
    are what the stack and the chat say (`stack list`, `stack family`,
    who is in the chat, the memory's history, the jobs);
    `step` the step asked for (or `STACK`, "Your stack"), `phone` the
    admin's answer about the phone ("signed", "skipped" or ""), `device`
    the device that looks (`service_card.device_of`), for the apps.
    `reached` says a device opened the phone page (`phone_reach.py`), and
    `phone_link` is that page's address on the home network, which step
    4's QR code holds ("" draws no code). `firewall` is what keeps phones
    out, as `stack core firewall` names it ("block-all", "orbstack",
    "signed"; "" for nothing or unknown). `ai` is the AI stacklet's
    `stack ai recommended show` (None when it was not asked or did not
    answer) and `ai_choice` the AI step's `?ai=` (`ai_step.CHOICES`).
    `ai_app` says whether the AI server `[ai]` names is the oMLX app on
    this Mac (`stack ai recommended app`), which reads only once the AI
    has started (`_ai_reads`); None when nobody knows (asked only when
    `asks_ai_app`).
    `placeholder_key` says the AI's key is still `local`, which the AI's
    service card names on this Mac (`service_card.view`). `pairing` is
    the phone step's QR code of a pairing code for the admin's own phone
    (`devices.pages.pairing_panel`), drawn in place of the plain one.
    `passwords` holds each person's stored password by id, which the page
    shows on the Mac's address (`who.here`) only. None
    for `listing` or `family` is the error state, whose "Try again" reloads
    the step asked for.

    Returns `state` ("content" or "error"), `steps` (each with `id`,
    `number`, `title`, `line`, `status`: done, current or todo), `current`,
    `shown` (the step in the main column), `position` (the phone's rail
    line), `stack` (the link to "Your stack", "" until a service is set
    up) and `body_html` (the shown step, rendered).

        view(listing_with_core_only, simpsons, None, history=None,
             language="en", base="/dashboard", now=now, who=MAC)["current"]   # "chat"
    """
    w = words(language)
    common = {"state": "error", "language": language, "title": w["title"], "back": w["back"],
              "rail_label": w["rail"], "error": w["error"], "retry": w["retry"],
              "retry_href": href(step if step in (*STEPS, STACK) else ""), "base": base}
    if listing is None or family is None:
        return {**common, "steps": [], "current": "", "shown": "", "position": "", "stack": "",
                "body_html": ""}
    facts = _facts(listing, family, chat, history, language, now, jobs, who, phone,
                   common["retry_href"], device, reached=reached, phone_link=phone_link,
                   firewall=firewall, ai=ai, ai_choice=ai_choice,
                   placeholder_key=placeholder_key, ai_app=ai_app)
    facts = replace(facts, pairing=pairing, passwords=dict(passwords or {}) if who.here else {})
    done = {sid: _done(sid, facts) for sid in STEPS}
    current = _current(done, facts.phone)
    stack = w["stack"] if _set_up_services(facts) else ""
    shown = step if step in STEPS or (step == STACK and stack) else current
    steps = [_rail_step(sid, i, done[sid], current, w, facts) for i, sid in enumerate(STEPS, 1)]
    if shown == STACK:
        position = stack
    else:
        at = steps[STEPS.index(shown)]
        position = w["position"].format(n=at["number"], total=len(STEPS), title=at["title"])
    return {**common, "state": "content", "steps": steps, "current": current, "shown": shown,
            "position": position, "stack": stack, "body_html": _BODIES[shown](facts, done)}


def current_step(listing: dict | None, family: dict | None, chat: dict | None, *,
                 history: dict | None, language: str, now: datetime, phone: str = "",
                 ai_app: bool | None = None) -> str:
    """The step the setup is at, as `view` finds it: the first step not
    done (a skipped phone counts as passed), "ready" once all are, "" while
    the stack or the family does not answer. The dashboard names it in its
    link "Continue setup" (`status_pills.py`). `ai_app` as for `view`.

        current_step(listing_with_chat_running, simpsons, nobody_signed_in,
                     history=None, language="en", now=now)              # "phone"
    """
    if listing is None or family is None:
        return ""
    facts = _facts(listing, family, chat, history, language, now, (), None, phone, "", "",
                   ai_app=ai_app)
    return _current({sid: _done(sid, facts) for sid in STEPS}, facts.phone)


# ── The page ──────────────────────────────────────────────────────────────

def page(v: dict, *, base: str) -> bytes:
    """The whole page for a view: the topbar, the rail and the shown step,
    in `dashboard/setup.html`, loading the dashboard's files from `base`.
    Reads complete without JavaScript: the rail's steps are links, Start is
    the tile's terminal command, and the phone's answers are links.

        page(view(...), base="/dashboard")[:15]    # b"<!doctype html>"
    """
    template = Template((PAGE_DIR / "setup.html").read_text(encoding="utf-8"))
    return template.substitute(
        lang=v["language"], base=base, title=_e(v["title"]), back=_e(v["back"]),
        rail=_rail_html(v), main=_main_html(v),
        page_data=family_view.page_data(v["language"], base)).encode()


def only_on_the_mac(language: str, *, base: str) -> bytes:
    """The page the home address answers the setup with, and a change a
    browser opens there: where the setup is, and the way back to the
    dashboard. In the setup page's frame, without its steps, so it reads
    complete without JavaScript. Pure: the 403 is the server's.

        only_on_the_mac("en", base="/dashboard")
        # b'<!doctype html>...<h1 class="only-on-the-mac-title">This happens on the Mac
        #   that runs famstack</h1>...<a ... href="/dashboard">Back to the dashboard</a>...'
    """
    w = words(language)
    o = w["only_on_the_mac"]
    how = "".join(f'<li class="only-on-the-mac-how">{_e(line)}</li>' for line in o["how"])
    main = (f'<section class="only-on-the-mac">'
            f'<h1 class="only-on-the-mac-title">{_e(o["title"])}</h1>'
            f'<p class="only-on-the-mac-line">{_e(o["line"])}</p>'
            f'<ul class="only-on-the-mac-ways">{how}</ul>'
            f'<p>{_button(o["back"], base)}</p></section>')
    template = Template((PAGE_DIR / "setup.html").read_text(encoding="utf-8"))
    return template.substitute(
        lang=language, base=base, title=_e(w["title"]), back=_e(w["back"]),
        rail="", main=main, page_data=family_view.page_data(language, base)).encode()


# ── What the steps read ───────────────────────────────────────────────────

@dataclass(frozen=True)
class _Facts:
    """What every step's rule and body reads, gathered once per request."""
    by_id: dict
    family: dict
    admin: dict | None
    chat: dict | None
    history: dict | None
    tiles: dict
    language: str
    w: dict
    # Who looks; None when only the steps' rules are read (`current_step`).
    who: Who | None
    phone: str
    page_href: str
    device: str
    reached: bool = False
    phone_link: str = ""
    firewall: str = ""
    ai: dict | None = None
    ai_choice: str = ""
    placeholder_key: bool = False
    ai_app: bool | None = None
    pairing: str = ""
    # Each person's stored password by id, on the Mac's address only.
    passwords: dict = field(default_factory=dict)

    @property
    def here(self) -> bool:
        """Whether the page is read on the Mac's address."""
        return self.who is not None and self.who.here

    def set_up(self, sid: str) -> bool:
        return bool(self.by_id.get(sid, {}).get("enabled"))

    def runs(self, sid: str) -> bool:
        """Set up here and online: online alone is what Docker says of a
        container by its name, which another instance's can share."""
        return self.set_up(sid) and bool(self.by_id.get(sid, {}).get("online"))


def _facts(listing: dict, family: dict, chat: dict | None, history: dict | None, language: str,
           now: datetime, jobs: Sequence[dict], who: Who | None, phone: str, page_href: str,
           device: str, *, reached: bool = False, phone_link: str = "",
           firewall: str = "", ai: dict | None = None,
           ai_choice: str = "", placeholder_key: bool = False,
           ai_app: bool | None = None) -> _Facts:
    stacklets = stack_widget.on_screens(listing.get("stacklets", []))
    shown_jobs = stack_widget.shown_jobs(jobs, now)
    here = who is not None and who.here
    tiles = {s["id"]: stack_widget.tile(s, stacklets, shown_jobs.get(s["id"]), language, here=here)
             for s in stacklets}
    admin = _admin_of(family)
    return _Facts({s["id"]: s for s in stacklets}, family, admin, chat, history, tiles, language,
                  words(language), who, phone if phone in PHONE_ANSWERS else "", page_href, device,
                  reached, phone_link, firewall, ai,
                  ai_choice if ai_choice in ai_step.CHOICES else "", placeholder_key, ai_app)


def asks_ai_app(listing: dict | None) -> bool:
    """Whether the page needs `ai_app` to know if the AI step is done:
    only while the AI is a server the stack does not manage and the ai
    stacklet is not set up. Once it is, the step is done either way, so
    the host is not asked on every page.

        asks_ai_app({"stacklets": [{"id": "ai", "remote": "http://localhost:42060/v1",
                                    "enabled": False}]})    # True
    """
    ai = next((s for s in (listing or {}).get("stacklets", []) if s.get("id") == "ai"), {})
    return _ai_app_matters(ai)


def _current(done: dict, phone: str) -> str:
    """The first step not done, past a skipped phone; "ready" after the last."""
    return next((sid for sid in STEPS[:-1]
                 if not done[sid] and not (sid == "phone" and phone == "skipped")), "ready")


def _done(sid: str, f: _Facts) -> bool:
    """Whether one step is done, by the stack's own state."""
    match sid:
        case "family":
            return bool(f.family.get("people"))
        case "chat":
            return f.runs("messages")
        case "phone":
            # Any sign-in of the admin's: with the phone app, or through the
            # step's fallback in the browser or the app on this Mac.
            return _signed_in(f) or f.phone == "signed"
        case "ai":
            return _ai_reads(f)
        case "services":
            return all(f.set_up(sid) for sid in SERVICES if sid in f.by_id)
        case "try":
            return (f.set_up("docs") and _ai_reads(f)
                    and _first_letter(f) is not None)
    return False


def _ai_reads(f: _Facts) -> bool:
    """Whether an AI reads what Documents files, as `stack list` reports the
    ai stacklet: set up and online here, or a server the stack uses and does
    not manage (`stack ai switch <url>`), which the listing gives as its
    `remote` address. Such a server reads as it is, on another machine or
    on this Mac (Ollama at localhost:11434, LM Studio at localhost:1234),
    except the oMLX app at whatever port (`f.ai_app`, which the host
    decides by what answers there): it reads once the stacklet is set up,
    because the stacklet's first start puts the models there. A server
    nobody can tell from the app (closed, or the host did not answer)
    does not read either.

        _ai_reads(facts)    # {"remote": "http://localhost:11434/v1"}, ai_app False: True
        _ai_reads(facts)    # {"remote": "http://localhost:42060/v1"}, ai_app True: False
        _ai_reads(facts)    # the same, ai_app None: False
    """
    ai = f.by_id.get("ai", {})
    if ai.get("enabled") and (ai.get("online") or ai.get("remote")):
        return True
    return _ai_app_matters(ai) and f.ai_app is False


def _ai_app_matters(ai: dict) -> bool:
    """Whether the AI step's state hangs on `ai_app`: the listing's ai row
    names a server the stack does not manage, and the stacklet is not set
    up."""
    return bool(ai.get("remote")) and not ai.get("enabled")


def _led(stacklets: list[dict], core: Sequence[str]) -> list[str]:
    """What a step offers: `core`, then whatever it requires, and whatever
    that requires, as `stack list` reports it; never the chat, which has a
    step of its own."""
    by_id = {s["id"]: s for s in stacklets}
    leads: list[str] = []
    todo = list(core)
    while todo:
        sid = todo.pop(0)
        if sid in leads or sid == "messages" or sid not in by_id:
            continue
        leads.append(sid)
        todo += by_id[sid].get("requires", [])
    return leads


def _signed_in(f: _Facts) -> bool:
    """Whether the admin's chat account has signed in on a device."""
    return bool((_admin_in_chat(f) or {}).get("last_seen"))


def signed_in(family: dict | None, chat: dict | None) -> bool:
    """Whether the family's admin has signed in to the chat, by what
    `stack family` and `stack messages users` answer; False while either
    does not. What `SIGNED_IN` answers, by the rule the phone step is done by.

        signed_in(simpsons, {"users": [{"id": "@homer:simpson", "last_seen": 1762700000000}]})
        # True
    """
    admin = _admin_of(family or {})
    if admin is None or not chat or "users" not in chat:
        return False
    return bool((_user_in(chat, admin["id"]) or {}).get("last_seen"))


def _admin_of(family: dict) -> dict | None:
    """The family's admin: the first person with the role, else the first person."""
    people = family.get("people", [])
    return next((p for p in people if p.get("role") == "admin"), people[0] if people else None)


def _user_in(chat: dict, person: str) -> dict | None:
    """One person's account as the chat lists it, by the person's id."""
    seen = {u["id"].split(":", 1)[0].lstrip("@"): u for u in chat.get("users", [])}
    return seen.get(person)


def _admin_in_chat(f: _Facts) -> dict | None:
    """The admin's account as the chat lists it, while the chat runs."""
    if not f.admin or not f.runs("messages") or f.chat is None:
        return None
    return _user_in(f.chat, f.admin["id"])


def _first_letter(f: _Facts) -> dict | None:
    """The newest document card the memory's history names, or None."""
    return _letter_in(f.history)


def first_letter_in(history: dict | None) -> bool:
    """Whether the memory's history names a document: what `FIRST_LETTER`
    answers, by the rule step 6 is done by (with Documents and the AI).

        first_letter_in({"changes": [{"cards": [{"type": "document"}]}]})   # True
        first_letter_in(None)                                               # False
    """
    return _letter_in(history) is not None


def _letter_in(history: dict | None) -> dict | None:
    """The newest document card `history` names, {} for a document without
    its card, or None."""
    for change in (history or {}).get("changes", []):
        for card in change.get("cards", []):
            if card.get("type") == "document":
                return card
        if "document" in (change.get("types") or {}):
            return {}
    return None


def _rail_step(sid: str, number: int, done: bool, current: str, w: dict, f: _Facts) -> dict:
    title, line = w["steps"][sid]
    names = _listed([_first(p["name"]) for p in f.family.get("people", [])], w["and"])
    return {"id": sid, "number": number, "title": title, "line": line.format(names=names),
            "status": "done" if done else "current" if sid == current else "todo"}


# ── The steps' bodies ─────────────────────────────────────────────────────

def _family_body(f: _Facts, done: dict) -> str:
    w = f.w
    names = _listed([_first(p["name"]) for p in f.family.get("people", [])], w["and"])
    admin = _admin_name(f)
    return (_heading(w["family_heading"])
            + _p(w["family_text"].format(names=names, admin=admin), "setup-text")
            + _family_list(f) + _next("family", w))


def _chat_body(f: _Facts, done: dict) -> str:
    """The chat's service card; its app is the next step."""
    w = f.w
    apps = [(f"{w['steps'][sid][0]} ›", href(sid)) for sid in ("phone",)]
    card = _service_cards(f, ["messages"], add=w["chat_start"], apps=apps)
    after = (_p(w["chat_done"], "setup-done") + _next("chat", w)) if done["chat"] else ""
    return _heading(w["chat_heading"]) + _p(w["chat_text"], "setup-text") + card + after


def _phone_body(f: _Facts, done: dict) -> str:
    """The QR code of the phone page, then the step's two parts: the phone
    reaches famstack, and the sign-in with the app. On a phone itself,
    which cannot scan its own screen, a button opens the page instead.
    "My phone is signed in" only while the chat cannot tell; "Skip for
    now" always, until the step is done."""
    w = f.w
    head = _heading(w["phone_heading"]) + _p(w["phone_text"], "setup-text")
    if not f.runs("messages"):
        return head + _p(w["chat_first"], "setup-waiting") + _back_to("chat", w)
    server = service_card.chat_server(f.by_id.get("messages", {}), here=False)
    box = _sign_in(f, server, password=f.here)
    if not done["phone"]:
        box += _phone_guide(f, server)
    if done["phone"]:
        after = _p(w["phone_done"].format(name=_admin_name(f)), "setup-done") + _next("phone", w)
    else:
        after = (_on_this_mac(f) + f'<div class="setup-act">'
                 f'<a class="fs-link setup-later" href="{href(phone="skipped")}">{_e(w["skip"])}</a></div>')
    parts = (f'<ol class="setup-parts">'
             f'{_part("reach", w["phone_reach"], f.reached, _firewall(f) + _causes(f))}'
             f'{_part("app", w["phone_signed_in"], done["phone"], box, watch=SIGNED_IN)}</ol>')
    return head + _to_the_phone_page(f) + parts + after


def _on_this_mac(f: _Facts) -> str:
    """The phone step's fallback, folded: the chat in Element for Mac or in
    the browser on this Mac, with the sign-in on localhost, which needs no
    home network. A sign-in there finishes the phone step too."""
    w = f.w
    messages = f.by_id.get("messages", {})
    browser = stack_widget.open_link(messages, here=f.here)
    act = (f'<div class="setup-act">{_button(w["mac_get"], MAC_APP, external=True)}'
           + (f'<a class="fs-link" href="{_e(browser)}" target="_blank" rel="noopener">'
              f'{_e(w["mac_browser"])}</a>' if browser else "") + "</div>")
    server = service_card.chat_server(messages, here=f.here)
    return (f'<details class="setup-fallback"><summary>{_e(w["mac_fallback"])}</summary>'
            f'{act}{_sign_in(f, server, password=f.here)}</details>')


def _phone_guide(f: _Facts, server: str) -> str:
    """The sign-in steps for Element X, to read on the Mac while the admin
    installs it on the phone: shown until the phone is signed in, also
    before the phone reached famstack and after a skip, because the admin
    who opens the step needs the steps beside the phone, and both of those
    answers are kept in this browser and can be out of date. The guide's own steps and words;
    on the Mac that runs famstack (`here`) the account step holds the
    password, as the box above does: the admin's stored one."""
    who = (f.admin or {}).get("id", "")
    guide = sign_in_guide.view(device="iphone", language=f.language, server=server,
                               username=who, on_the_mac=True,
                               password=f.passwords.get(who) if f.here else None)
    return f'<div class="setup-phone-guide">{sign_in_guide.steps_html(guide)}</div>'


def _to_the_phone_page(f: _Facts) -> str:
    """The QR code that holds the phone page's address, or on a phone a
    button to it; nothing while core has no link base. With a pairing
    code for the admin's phone (`pairing`), its QR code: the phone that
    opens it is the admin's, and still counts as the phone's reach."""
    if not f.phone_link:
        return ""
    w = f.w
    if f.pairing and not f.device:
        return f.pairing
    if f.device:
        return f'<div class="setup-act">{_button(w["phone_get"], f.phone_link)}</div>'
    code = qr_code.svg(f.phone_link, label=f"QR code: {f.phone_link}")
    return (f'<figure class="setup-qr">{code}'
            f'<figcaption>{_e(w["phone_qr"])}</figcaption></figure>')


def _part(name: str, title: str, done: bool, body: str, *, watch: str = "") -> str:
    """One part of a step: a head with ✓ when done, else a wait mark, and its
    body. `watch` is the address the open page asks while the part waits."""
    status = "done" if done else "waiting"
    mark = "✓" if done else "○"
    watched = f' data-watch="{_e(watch)}"' if watch and not done else ""
    return (f'<li class="setup-part" data-part="{name}" data-status="{status}"{watched}>'
            f'<p class="setup-part-head"><span class="setup-part-mark" aria-hidden="true">{mark}</span>'
            f'{_e(title)}</p>{body}</li>')


# What `stack core firewall` names, by the fix it needs: signed apps not
# allowed is fixed by allowing OrbStack, as a blocked OrbStack is.
_FIREWALL_FIX = {"block-all": "block-all", "orbstack": "orbstack", "signed": "orbstack"}


def _firewall(f: _Facts) -> str:
    """While no device reached the phone page and the Mac's firewall keeps
    phones out: a warning with the fix, shown at once; else nothing."""
    fix = _FIREWALL_FIX.get(f.firewall)
    if f.reached or fix is None:
        return ""
    return (f'<p class="setup-warning" role="note"><span aria-hidden="true">⚠ </span>'
            f'{_e(f.w["phone_firewall"][fix])}</p>')


def _causes(f: _Facts) -> str:
    """While no device reached the phone page: the likely causes, hidden
    until the page's script shows them (`setup-page.js`)."""
    if f.reached:
        return ""
    w = f.w
    causes = "".join(f'<li class="setup-cause">{_e(c)}</li>' for c in w["phone_causes"])
    return (f'<div class="setup-causes" hidden><p>{_e(w["phone_slow"])}</p>'
            f'<ul>{causes}</ul></div>')


def _ai_body(f: _Facts, done: dict) -> str:
    """The part of the AI step the state asks for (`ai_step.sub_step`),
    then, on the terminal's route and while the AI starts, the AI's tile,
    which follows the run; once the AI reads, what it can do, with speech
    its first words (`first_words_step.py`), and the way on.

    A ready AI shows no tile: on a stack that uses an AI server it does
    not manage, the AI stacklet is not set up, and its tile would offer
    `stack up ai` under "Your AI is ready"."""
    w = f.w
    tile = f.tiles.get("ai")
    sub = ai_step.sub_step(done=done["ai"], tile=tile, plan=f.ai, choice=f.ai_choice)
    own = ai_step.heading(sub, f.language)
    head = _heading(own) if own else _heading(w["ai_heading"]) + _p(w["ai_text"], "setup-text")
    part = ai_step.html(sub, f.ai, here=f.here, language=f.language, page=href("ai"))
    if sub == "ready":
        # With speech on, the AI speaks for the first time (4f) before the way on.
        first = first_words_step.html(f.language) if first_words_step.shown(f.ai) else ""
        return head + part + first + _next("ai", w)
    if sub in ("install", "starting"):
        return head + part + _terminal(tile, w) + _tiles([tile], f)
    if sub in ("models", "downloading"):
        # Continue, or the end of the downloads, starts the AI as a tile's
        # Start does (`ai-step.js`), and this tile follows the run. Until the
        # run starts it stays hidden, because its own act is the terminal's
        # route for a setup without the app; a failed run shows it.
        failed = bool(tile) and tile["state"] == stack_widget.FAILED
        return head + part + _tiles([tile], f, until_started=not failed)
    # The app's route: the tile's Start would ask the terminal's questions.
    return head + part


def _services_body(f: _Facts, done: dict) -> str:
    """A service card for each of photos, documents and the family memory,
    each with its sentence and its Start, and "Start all three"."""
    w = f.w
    offered = _led(list(f.by_id.values()), SERVICES)
    start_all = (f'<div class="setup-act"><button class="fs-button setup-start-all" type="button" '
                 f'data-kind="go" hidden>{_e(w["start_all"])}</button></div>'
                 if any(f.tiles.get(sid, {}).get("add") for sid in offered) else "")
    return (_heading(w["services_heading"]) + start_all
            + _service_cards(f, offered, add=w["start"], lines=w["services_lines"])
            + (_next("services", w) if done["services"] else ""))


def _stack_body(f: _Facts, done: dict) -> str:
    """"Your stack": a service card for every set-up service, then "Also
    available": what no open step offers, each with its line and Start, or
    its terminal command when its setup needs a person at the Mac."""
    w = f.w
    also = _also_available(f, done)
    lines = {sid: f.tiles[sid]["description"] for sid in also}
    offer = (f'<section class="setup-also"><h2 class="setup-subheading setup-also-heading">'
             f'{_e(w["also"])}</h2>{_service_cards(f, also, add=w["start"], lines=lines)}</section>'
             if also else "")
    return _heading(w["stack"]) + _service_cards(f, _set_up_services(f)) + offer


def _try_body(f: _Facts, done: dict) -> str:
    w = f.w
    head = _heading(w["try_heading"]) + _p(w["try_text"], "setup-text")
    if not f.set_up("docs"):
        return head + _p(w["try_no_docs"], "setup-note") + _back_to("services", w)
    if not _ai_reads(f):
        return head + _p(w["try_no_ai"], "setup-note") + _back_to("ai", w)
    letter = _first_letter(f)
    if letter is None:
        return head + (f'<p class="setup-waiting setup-live" data-watch="{_e(FIRST_LETTER)}">'
                       f'{_e(w["try_waiting"])}</p>')
    said = w["try_done"].format(title=letter["title"]) if letter.get("title") else w["try_done_untitled"]
    return head + _p(said, "setup-done") + _next("try", w)


def _ready_body(f: _Facts, done: dict) -> str:
    w = f.w
    called = family_view.called(f.family.get("name", ""), f.language)
    runs = [stack_widget.name_of(s, f.language) for s in f.by_id.values()
            if s["id"] != "core" and f.runs(s["id"])]
    running = "".join(f'<li><span class="dot" data-status="running" aria-hidden="true"></span>'
                      f'{_e(name)} <span class="setup-state">{_e(w["running"])}</span></li>'
                      for name in runs)
    missing = "".join(
        f'<li>{_e(w["missing"].get(sid) or w["steps"][sid][0])} · '
        f'<a class="fs-link" href="{href(sid)}">{_e(w["set_it_up"])}</a></li>'
        for sid in STEPS[:-1] if not done[sid])
    return (_heading(w["ready_heading"].format(called=called) if called else w["steps"]["ready"][0])
            + _p(w["ready_text"], "setup-text")
            + f'<div class="setup-act">{_button(w["ready_go"], "/dashboard")}</div>'
            + (f'<h2 class="setup-subheading">{_e(w["what_runs"])}</h2>'
               f'<ul class="setup-runs">{running}</ul>' if running else "")
            + (f'<h2 class="setup-subheading">{_e(w["not_done"])}</h2>'
               f'<ul class="setup-not-done">{missing}</ul>' if missing else ""))


_BODIES = {"family": _family_body, "chat": _chat_body, "phone": _phone_body,
           "ai": _ai_body, "services": _services_body, "try": _try_body, "ready": _ready_body,
           STACK: _stack_body}


# ── The parts of a step ───────────────────────────────────────────────────

def _heading(text: str) -> str:
    """The step's heading; the page's script moves the focus to it when a step opens."""
    return f'<h1 class="setup-heading" tabindex="-1">{_e(text)}</h1>'


def _next(sid: str, w: dict) -> str:
    """The primary button that leads on, in order, to the step after `sid`."""
    after = STEPS[STEPS.index(sid) + 1]
    return f'<div class="setup-act">{_button(w["next"][sid], href(after))}</div>'


def _back_to(sid: str, w: dict) -> str:
    """A link to the step this one waits for, by its rail title."""
    return (f'<p class="setup-note"><a class="fs-link" href="{href(sid)}">'
            f'{_e(w["steps"][sid][0])} ›</a></p>')


def _button(label: str, link: str, *, external: bool = False, kind: str = "go") -> str:
    out = ' target="_blank" rel="noopener"' if external else ""
    return f'<a class="fs-button" data-kind="{kind}" href="{_e(link)}"{out}>{_e(label)}</a>'


def _sign_in(f: _Facts, server: str, *, password: bool) -> str:
    """The box "Sign in with" of the chat's steps: the chat server's
    address, the admin's username and, where it may be shown, the admin's
    stored password, as a service card shows a sign-in."""
    w = service_card.words(f.language)
    who = f.admin["id"] if f.admin else ""
    return service_card.sign_in_html([(w["server"], server), (w["username"], who)],
                                     f.passwords.get(who) if password else None, w, server=True)


def _service_cards(f: _Facts, ids: list[str], *, add: str = "", lines: dict | None = None,
                   apps: list[tuple[str, str]] | None = None) -> str:
    """The service cards of `ids`, for the admin on this device. `add` is
    the step's own word for Start, `lines` a sentence per service, `apps`
    the chat's own links in place of a download."""
    cards = []
    for sid in ids:
        tile = f.tiles.get(sid)
        if not tile:
            continue
        tile = {**tile, "add": add if add and tile["add"] else tile["add"]}
        cards.append(service_card.html(service_card.view(
            tile, f.by_id[sid], f.admin, language=f.language, who=f.who, device=f.device,
            apps=apps, describe=(lines or {}).get(sid, ""),
            placeholder_key=f.placeholder_key,
            password=f.passwords.get((f.admin or {}).get("id", "")))))
    return f'<div class="service-cards">{"".join(cards)}</div>' if cards else ""


def _set_up_services(f: _Facts) -> list[str]:
    """The stacklets with a service card under "Your stack": set up here,
    shown on screens and not always on, the services first in their order,
    as the dashboard's pills list them. Core has none, and neither has
    Code, which the memory sets up (`dashboard = false`)."""
    order = list(service_card.SERVICES)
    set_up = [sid for sid, s in f.by_id.items() if f.set_up(sid) and not s.get("always_on")]
    return sorted(set_up, key=lambda sid: order.index(sid) if sid in order else len(order))


def _also_available(f: _Facts, done: dict) -> list[str]:
    """What "Your stack" offers under "Also available": every stacklet that
    can be added (`stack_widget.offered`, or one whose start is going on)
    and that no open step offers. The chat, the AI and the services have
    steps of their own while those are not done; Backup, the agent and the
    extensions have none. Once every step is done this is what the
    dashboard's "Set up N more" counts."""
    in_steps = {"messages"}
    if not done["ai"]:
        in_steps.add("ai")
    if not done["services"]:
        in_steps.update(_led(list(f.by_id.values()), SERVICES))
    return [sid for sid, s in f.by_id.items()
            if sid not in in_steps and not f.set_up(sid)
            and (stack_widget.offered(s) or f.tiles[sid]["state"] == stack_widget.ADDING)]


def _copy(w: dict) -> str:
    """Copy beside a value, hidden until the page's script runs."""
    return f'<button class="setup-mini setup-copy" type="button" hidden>{_e(w["copy"])}</button>'


def _terminal(tile: dict | None, w: dict) -> str:
    """The box for a step that needs the Mac's terminal: the command with
    Copy, while its tile is not running and not being started. The tile
    below it follows the run once it starts, wherever it started."""
    if not tile or tile["state"] in (stack_widget.RUNNING, stack_widget.ADDING) or tile["add"]:
        return ""
    return (f'<div class="setup-box setup-terminal" data-copied="{_e(w["copied"])}">'
            f'<h2 class="setup-subheading">{_e(w["terminal_heading"])}</h2>'
            f'<p class="setup-note">{_e(w["terminal_why"])}</p>'
            f'<p><code class="setup-value setup-terminal-command">{_e(tile["command"])}</code>'
            f'{_copy(w)}</p><p class="setup-note">{_e(w["terminal_follows"])}</p></div>')


def _tiles(tiles: list[dict | None], f: _Facts, *, until_started: bool = False) -> str:
    """The tiles, in a row. `until_started` hides them until a run of
    theirs starts, when the page's script shows them (`FsStack.started`)."""
    shown = [t for t in tiles if t]
    if not shown:
        return ""
    shared = {"retry_href": f.page_href, "check_again": stack_widget.words(f.language)["check_again"]}
    hidden = " data-until-started hidden" if until_started else ""
    return (f'<div class="tiles"{hidden}>' + "".join(stack_widget.tile_html(t, shared) for t in shown)
            + "</div>")


def _family_list(f: _Facts) -> str:
    """The family, each person with their password and "Add a phone" on
    the Mac's address (`devices.routes`), once under the list the line
    that a password changed in an app counts there (when the list shows
    a password), and the way to the Mac's list of devices."""
    rows = []
    for p in f.family.get("people", []):
        about = RELATIONS["de" if f.language == "de" else "en"].get(p.get("relation") or "", "")
        add = (f' <a class="fs-link" href="/setup/add-a-phone?person={_e(p["id"])}">'
               f'{_e(f.w["add_a_phone"])}</a>') if f.here else ""
        rows.append(f'<li><b>{_e(p["name"])}</b> <span>{_e(about)}</span>{add}'
                    f'{_person_password(f, p["id"])}</li>')
    # The passwords shown are the first ones famstack stored; a person may
    # have changed theirs in an app since, which famstack cannot know.
    changed = (f'<p class="setup-note">{_e(f.w["password_changed"])}</p>'
               if any(p["id"] in f.passwords for p in f.family.get("people", [])) else "")
    devices = (f'<div class="setup-act"><a class="fs-link" href="/setup/devices">'
               f'{_e(f.w["devices"])}</a></div>') if f.here else ""
    return f'<ul class="setup-people">{"".join(rows)}</ul>{changed}{devices}'


def _person_password(f: _Facts, person: str) -> str:
    """"Password: otter-maple-cloud-lamp" for one person of the family
    step, on its own line under the name, masked with Show and Copy as a
    service card shows it; "" where
    the page has no password for them (another device, none stored)."""
    if person not in f.passwords:
        return ""
    w = service_card.words(f.language)
    return (f'<div class="setup-person-password">{_e(w["password"])}: '
            f'{service_card.secret_html(f.passwords[person], w)}</div>')


# ── The rail and the frame ────────────────────────────────────────────────

# The tick, drawn as components/steps.js draws it.
_CHECK = ('<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
          'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
          '<path d="m5 12.5 4.5 4.5L19 7.5"/></svg>')


_CURRENT = ' aria-current="step"'


def _rail_html(v: dict) -> str:
    if v["state"] == "error":
        return ""
    items = "".join(
        f'<li class="setup-rail-step" data-step="{s["id"]}" data-status="{s["status"]}">'
        f'<a href="{href(s["id"])}"{_CURRENT if s["id"] == v["shown"] else ""}>'
        f'<span class="setup-rail-tick">{_CHECK if s["status"] == "done" else s["number"]}</span>'
        f'<span class="setup-rail-text"><b class="setup-rail-title">{_e(s["title"])}</b>'
        f'<span>{_e(s["line"])}</span></span></a></li>'
        for s in v["steps"])
    stack = (f'<p class="setup-rail-stack"><a href="{href(STACK)}"'
             f'{_CURRENT if v["shown"] == STACK else ""}>{_e(v["stack"])}</a></p>'
             if v["stack"] else "")
    return (f'<nav class="setup-rail" aria-label="{_e(v["rail_label"])}">'
            f'<p class="setup-position">{_e(v["position"])}</p>'
            f'<ol class="setup-rail-list">{items}</ol>{stack}</nav>')


def _main_html(v: dict) -> str:
    if v["state"] == "error":
        return (f'<section class="setup-error" role="alert"><p>{_e(v["error"])}</p>'
                f'<a class="fs-retry" href="{_e(v["retry_href"])}">{_e(v["retry"])}</a></section>')
    adding = stack_widget.words(v["language"])["adding"]
    forms = json.dumps(stack_widget.words(v["language"])["forms"])
    status = "" if v["shown"] == STACK else v["steps"][STEPS.index(v["shown"])]["status"]
    starting = service_card.words(v["language"])["status"]["starting"]
    return (f'<fs-setup-page data-base="{_e(v["base"])}" data-adding="{_e(adding)}" '
            f'data-starting="{_e(starting)}" data-forms-words="{_e(forms)}">'
            f'<section class="setup-step" data-step="{v["shown"]}" data-status="{status}">'
            f'{v["body_html"]}</section></fs-setup-page>')


# ── Small helpers ─────────────────────────────────────────────────────────

def _p(text: str, cls: str = "") -> str:
    klass = f' class="{cls}"' if cls else ""
    return f"<p{klass}>{_e(text)}</p>" if text else ""


def _admin_name(f: _Facts) -> str:
    """The first name of whoever looks after the server, "" without one."""
    return _first(f.admin["name"]) if f.admin else ""


def _first(name: str) -> str:
    return name.split()[0] if name.split() else name


def _listed(names: list[str], and_: str) -> str:
    if len(names) <= 1:
        return "".join(names)
    return f"{', '.join(names[:-1])} {and_} {names[-1]}"


def _e(text: str) -> str:
    return escape(str(text), quote=True)
