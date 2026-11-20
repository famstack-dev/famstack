"""The browsers that wait to join, as an admin's device and the Mac show
them: the section "Devices waiting to join", and for each browser the
allow step and Dismiss.

    section(waiting_list(), family["people"], "en")   # the Mac's Devices page
    allow_step("K7MP-4XQD", family["people"], "en")    # one browser's choice, Allow, Cancel, Dismiss
    dismissed_line("Chrome on Android", "en")          # "Chrome on Android is dismissed."

Allowing takes two steps. A tap on a person (or Shared) only chooses:
the pill is highlighted, and an explainer says whom the browser will be
signed in as and which code it must show. The second click, Allow,
sends the request; Cancel goes back to the choice. A browser that joins
as Homer sees what Homer may see, so one tap on the wrong pill must not
let a stranger in, and the code in the explainer is the last check that
the browser in front of the admin is the one that asked.

    Allow as [Homer] [Marge] [Bart] [Lisa] [Shared]   [Dismiss]
                         │ tap: chooses, sends nothing
                         ▼
    This browser will be signed in as Bart and sees what Bart may see.
    Allow it only if this browser is in front of you and shows the code
    K7MP-4XQD.                                     [Allow] [Cancel]
                         │ Allow: POST /dashboard/api/devices/allow
                         ▼                 {"code": "K7MP4XQD", "as": "bart"}
    Bart's Chrome on a Mac is added.

Dismiss ends the request (`joining`'s decline, an admin's device or the
Mac only), and the waiting browser reads that nobody let it in.

The markup carries the explainer's words with the code filled in and
`{name}` left for the chosen person; the page script
(`dashboard/allow-step.js`, wired by `dashboard/devices.js`) fills it
and holds the choice. A person's own device that is not an admin's does
not use this module: it allows by its code for that person alone
(`pages.allow_page`). Pure: no request, no host API. The words that
other pages use too (Allow, Cancel, Shared, "Allow as", the device's
name) come from `pages.words`, so each label has one home.
"""

from __future__ import annotations

from html import escape

from . import names, pages

__all__ = ["WORDS", "words", "section", "allow_step", "dismissed_line"]

WORDS = {
    "en": {
        "heading": "Devices waiting to join",
        "row": "{device} · code {code} · since {time}",
        "explain_person": ("This browser will be signed in as {name} and sees what {name} may see. "
                           "Allow it only if this browser is in front of you and shows the code "
                           "{code}."),
        "explain_shared": ("This browser will be a shared device and sees what the family shares, "
                           "nobody's own things. Allow it only if this browser is in front of you "
                           "and shows the code {code}."),
        "dismiss": "Dismiss",
        "dismissed": "{device} is dismissed.",
    },
    "de": {
        "heading": "Geräte, die beitreten möchten",
        "row": "{device} · Code {code} · seit {time}",
        "explain_person": ("Dieser Browser wird als {name} angemeldet und sieht, was {name} sehen "
                           "darf. Erlaube ihn nur, wenn dieser Browser vor dir steht und den Code "
                           "{code} zeigt."),
        "explain_shared": ("Dieser Browser wird ein gemeinsames Gerät und sieht, was die Familie "
                           "teilt, nichts Eigenes von jemandem. Erlaube ihn nur, wenn dieser "
                           "Browser vor dir steht und den Code {code} zeigt."),
        "dismiss": "Ablehnen",
        "dismissed": "{device} ist abgelehnt.",
    },
}


def words(language: str) -> dict:
    """The labels of this module and the shared ones of `pages`, in
    `language` ("de", else English).

        words("de")["dismiss"]   # "Ablehnen"
        words("en")["allow"]     # "Allow", from pages.words
    """
    return {**pages.words(language), **WORDS["de" if language == "de" else "en"]}


def section(waiting: list[dict], people: list[dict], language: str) -> str:
    """The section "Devices waiting to join": its heading, and one row per
    browser in `waiting` (`code` as shown, "K7MP-4XQD", `device`, `since`)
    with the allow step for `people` (`id`, `name`). "" when no browser
    waits, so the page looks as it does without one.

        section([{"code": "K7MP-4XQD", "device": "Chrome on a Mac", "since": "21:14"}],
                [{"id": "bart", "name": "Bart"}], "en")
        # "... Devices waiting to join ... Chrome on a Mac · code K7MP-4XQD · since 21:14
        #  ... Allow as Bart Shared ... Dismiss ..."
        section([], people, "en")   # ""
    """
    if not waiting:
        return ""
    w = words(language)
    rows = "".join(_row(j, people, w, language) for j in waiting)
    return (f'<h2 class="setup-subheading devices-waiting-heading">{_e(w["heading"])}</h2>'
            f'<ul class="setup-people devices-waiting">{rows}</ul>')


def allow_step(code: str, people: list[dict], language: str) -> str:
    """One waiting browser's choice and its two steps: "Allow as" with a
    pill for each of `people` and Shared, Dismiss beside them, and the
    hidden step the script opens on a tap (the name field for Shared, the
    explainer, Allow, Cancel). `code` is as the browser shows it
    ("K7MP-4XQD"); the part's `data-join` holds it as the server takes
    it ("K7MP4XQD").

        allow_step("K7MP-4XQD", [{"id": "bart", "name": "Bart"}], "en")
        # '<div class="join-allow" data-join="K7MP4XQD"><nav ...>Allow as ...
        #  data-choose="bart" ... data-choose="shared" ... Dismiss</button></nav>
        #  <div class="allow-step" hidden data-explain="This browser will be signed in
        #  as {name} ... the code K7MP-4XQD." ...>'
    """
    w = words(language)
    pills = "".join(_pill(p["id"], p["name"]) for p in people) + _pill("shared", w["shared"])
    return (f'<div class="join-allow" data-join="{_e(code.replace("-", ""))}">'
            f'<nav class="fs-pills devices-as"><span>{_e(w["allow_as"])}</span>{pills}'
            f'<button class="setup-mini" type="button" data-decline>{_e(w["dismiss"])}</button>'
            f'</nav>{_step(code, w)}'
            f'<p class="setup-done" data-allowed hidden></p>'
            f'<p class="setup-waiting" data-refused hidden></p></div>')


def dismissed_line(device: str, language: str) -> str:
    """What the admin reads once a request is dismissed.

        dismissed_line("Chrome on Android", "en")   # "Chrome on Android is dismissed."
        dismissed_line("Chrome on a Mac", "de")     # "Chrome auf einem Mac ist abgelehnt."
    """
    return words(language)["dismissed"].format(device=names.said(device, language))


# ── Parts ────────────────────────────────────────────────────────────────

def _row(join: dict, people: list[dict], w: dict, language: str) -> str:
    """One browser that waits: its device, code and time, and its allow step."""
    line = w["row"].format(device=names.said(join["device"], language), code=join["code"],
                           time=join["since"])
    return (f'<li><span class="devices-wait">{_e(line)}</span>'
            f'{allow_step(join["code"], people, language)}</li>')


def _pill(choice: str, name: str) -> str:
    """A choice of "Allow as": a person's id or "shared". A tap only
    chooses; `aria-pressed` marks the chosen one."""
    return (f'<button class="fs-pill" type="button" data-choose="{_e(choice)}" '
            f'data-name="{_e(name)}" aria-pressed="false">'
            f'<span class="fs-pill-name">{_e(name)}</span></button>')


def _step(code: str, w: dict) -> str:
    """The second step, hidden until a choice: the explainers with the
    code filled in (`{name}` stays for the script), the name field for a
    shared device above the explainer, Allow and Cancel."""
    return (f'<div class="allow-step" hidden '
            f'data-explain="{_e(w["explain_person"].replace("{code}", code))}" '
            f'data-explain-shared="{_e(w["explain_shared"].format(code=code))}">'
            f'<label class="setup-text" data-shared-name hidden>{_e(w["name_device"])} '
            f'<input class="setup-value" name="name" placeholder="{_e(w["name_example"])}" '
            f'maxlength="60"></label>'
            f'<p class="setup-text" data-explainer></p>'
            f'<div class="setup-act">'
            f'<button class="fs-button" type="button" data-allow-step>{_e(w["allow"])}</button>'
            f'<button class="setup-mini" type="button" data-cancel>{_e(w["cancel"])}</button>'
            f'</div></div>')


def _e(text: str) -> str:
    return escape(text, quote=True)
