"""The profile page, drawn: one person's page as one viewer may see it.

    page(profile, language="en")        # the whole page, for GET /dashboard/person/<id>
    not_found("en")                     # "Nobody here by that name", for an unknown id
    me_on_the_mac("en")                 # /dashboard/me on the Mac: the script finds the pick

The page's parts, in this order, each one only when `profile.sees` says
so; the ids are the anchors a link opens the page at:

    header block   name, relation, "Looks after famstack", the memory link
    #waiting       "Devices waiting to join", yellow: Allow as <name> or Shared, Dismiss
    #settings      the email, #password (own device only), the word for sister or brother
    #devices       the devices, green: Remove, or "This device" and Not me
    #this-mac      "On this Mac": This is me / Not me (the Mac only)

A viewer who may not see the devices gets one line why in their place.
The page reads complete without a script, on the phone frame of the
device pages (`dashboard/phone.html`). The waiting browsers are the
section the Mac's Devices page shows (`devices.waiting.section`), with
its allow step: a tap on a name only chooses, Allow is the second click
(`dashboard/allow-step.js`, which `dashboard/devices.js` loads), Dismiss
ends the request. `dashboard/devices.js` also sends Remove and Not me;
`dashboard/profile.js` masks the password with Show, Hide and Copy, and
keeps the Mac's pick of who looks. Pure: no request, no host API.
"""

from __future__ import annotations

from datetime import date
from html import escape
from string import Template

import family_view
from access import DASHBOARD_PREFIX, SETUP_PATH
from devices import names, pages, waiting

from .profile import Profile
from .words import words

__all__ = ["page", "not_found", "me_on_the_mac"]


def page(profile: Profile, *, language: str) -> str:
    """The profile page of `profile`, in `language`.

        page(homers_profile_on_his_phone, language="en")
        # '... <h1 class="p-name">Homer</h1> ... <section class="sect tint-wait" id="waiting"> ...
        #  <section class="sect" id="settings"> ... <section class="sect tint-dev" id="devices"> ...'
    """
    shown, w = profile.sees, words(language)
    parts = [_header(profile, language)]
    if shown.waiting and profile.waiting:
        parts.append(_waiting(profile, language))
    if shown.settings:
        parts.append(_settings(profile, language))
    if shown.devices:
        parts.append(_devices(profile, language))
    else:
        parts.append(_p(_why(profile, language), "note p-why"))
    if shown.this_mac:
        parts.append(_this_mac(profile, language))
    main = (_back(language) + f'<article class="profile" data-person="{_e(profile.id)}">'
            + "".join(parts) + "</article>" + pages.ask_dialog(language))
    return _frame(language, w["title"], main)


def not_found(language: str) -> str:
    """The page for an id that names nobody of the family.

        not_found("en")   # '... <h1 class="p-name">Nobody here by that name</h1> ...'
    """
    w = words(language)
    main = (_back(language) + f'<h1 class="p-name">{_e(w["nobody_heading"])}</h1>'
            + _p(w["nobody_line"], "quiet"))
    return _frame(language, w["nobody_heading"], main)


def me_on_the_mac(language: str) -> str:
    """`/dashboard/me` on the Mac's address. The person picked lives in
    this browser (`components/family.js`, "This is me"), not on the
    server, so the page's script opens that person's profile, or the
    dashboard with nobody picked; without a script it is a link back.

        me_on_the_mac("en")   # '... data-me ... Back to the dashboard ...'
    """
    w = words(language)
    return _frame(language, w["title"], f'<div data-me>{_back(language)}</div>')


# ── The header block ─────────────────────────────────────────────────────

def _header(profile: Profile, language: str) -> str:
    """Avatar, name, relation, the admin badge and the memory link."""
    w, person = words(language), profile.person
    first = family_view.first_name(person.get("name", ""))
    memory = (w["memory_own"] if profile.sees.own
              else w["memory_other"].format(name=first, genitive=_genitive(first)))
    relation = profile.relation[:1].upper() + profile.relation[1:]
    badge = f'<span class="badge">{_e(w["admin"])}</span>' if person.get("role") == "admin" else ""
    return (f'<header class="p-head"><span class="fs-avatar" aria-hidden="true">'
            f'{_e(first[:1].upper())}</span><div><h1 class="p-name">{_e(person.get("name", ""))}</h1>'
            f'<p class="p-meta"><span class="p-relation">{_e(relation)}</span>{badge}'
            f'<a class="p-link" href="/memory/person/{_e(profile.id)}">{_e(memory)}</a></p>'
            f'</div></header>')


def _why(profile: Profile, language: str) -> str:
    """The line that stands where the devices would be."""
    w = words(language)
    if profile.sees.why == "person":
        first = family_view.first_name(profile.person.get("name", ""))
        return w["why_person"].format(name=first, genitive=_genitive(first))
    return w["why_shared"]


# ── Devices waiting to join ──────────────────────────────────────────────

def _waiting(profile: Profile, language: str) -> str:
    """The browsers that wait, each to allow as this profile's person or
    as a shared device, as the Mac's Devices page draws them."""
    person = [{"id": profile.id, "name": family_view.first_name(profile.person.get("name", ""))}]
    return (f'<section class="sect tint-wait" id="waiting">'
            f'{waiting.section(profile.waiting, person, language)}</section>')


# ── Settings ─────────────────────────────────────────────────────────────

def _settings(profile: Profile, language: str) -> str:
    """The email, the password on one's own device, the word for sister
    or brother, read-only, and where they change."""
    w, person = words(language), profile.person
    editor = family_view.WORDS[family_view.language(language)]["editor"]
    word = dict(editor["word_choices"]).get(person.get("words") or "", "")
    password = (f'<li id="password" class="p-password"><div class="what"><span>{_e(w["password"])}'
                f'</span><code class="pw" data-secret>{_e(profile.password)}</code>'
                f'<span>{_e(w["password_line"])}</span></div><div class="acts">'
                f'<button class="setup-mini" type="button" data-show hidden data-hide="'
                f'{_e(w["hide"])}">{_e(w["show"])}</button><button class="setup-mini" type="button" '
                f'data-copy hidden data-copied="{_e(w["copied"])}">{_e(w["copy"])}</button></div></li>'
                if profile.password else "")
    return (f'<section class="sect" id="settings"><h2>{_e(w["settings"])}</h2><ul class="rows">'
            + _fact(w["email"], person.get("email", "")) + password
            + _fact(editor["words"], word) + "</ul>" + _p(w["settings_line"], "quiet")
            + "</section>")


def _fact(label: str, value: str) -> str:
    return f'<li><dl><dt>{_e(label)}</dt><dd>{_e(value)}</dd></dl></li>'


# ── Devices ──────────────────────────────────────────────────────────────

def _devices(profile: Profile, language: str) -> str:
    """The person's devices, or the line that they have none, and the way
    to add one: "Add a device" on one's own, "Add Bart's phone" for an
    admin's device and the Mac."""
    w, d = words(language), pages.words(language)
    first = family_view.first_name(profile.person.get("name", ""))
    own = profile.sees.own
    heading = d["your_devices"] if own else w["devices_other"].format(name=first,
                                                                      genitive=_genitive(first))
    rows = "".join(_device_row(device, profile, language) for device in profile.devices)
    listed = (f'<ul class="rows">{rows}</ul>' if rows
              else _p(w["empty_own"] if own else w["empty_other"].format(name=first), "quiet"))
    return (f'<section class="sect tint-dev" id="devices"><h2>{_e(heading)}</h2>{listed}'
            f'<div class="setup-act">{_add(profile, language)}</div></section>')


def _device_row(device: dict, profile: Profile, language: str) -> str:
    """One device: its name, when it was added and seen (in red past 30
    days), and Remove; the device that looks has "This device" and Not me
    in its place, which asks first and then forgets it as Remove does."""
    w, d = words(language), pages.words(language)
    this = device["id"] == profile.this
    name = names.said(device["name"], language)
    first = family_view.first_name(profile.person.get("name", ""))
    added = w["added"].format(day=pages.day(device["paired"], language, profile.today))
    if this:
        seen = _span(w["seen_now"], "")
    elif device["stale"]:
        seen = _span(w["not_seen_since"].format(day=_date(device["seen"], language)), "stale")
    else:
        seen = _span(w["seen"].format(day=pages.day(device["seen"], language, profile.today)), "")
    if this:
        ask = w["not_me_ask"].format(name=first)
        act = (f'<button class="setup-mini" type="button" data-remove="{_e(device["id"])}" data-this '
               f'data-ask="{_e(ask)}" data-yes="{_e(w["forget"])}">{_e(d["not_me"])}</button>')
    else:
        ask = d["remove_ask"].format(name=first, genitive=_genitive(first), device=name)
        act = (f'<button class="setup-mini" type="button" data-remove="{_e(device["id"])}" '
               f'data-ask="{_e(ask)}">{_e(d["remove"])}</button>')
    tag = f'<span class="tag">{_e(d["this_device"])}</span>' if this else ""
    return (f'<li data-device="{_e(device["id"])}"{" data-this" if this else ""}>'
            f'<div class="what"><b>{_e(name)}{tag}</b>{_span(added, "")} · {seen}</div>'
            f'<div class="acts">{act}</div></li>')


def _add(profile: Profile, language: str) -> str:
    """The link that adds a device for this profile's person."""
    d = pages.words(language)
    if profile.sees.own:
        return f'<a class="fs-button" href="{DASHBOARD_PREFIX}/devices/add">{_e(d["add_device_heading"])}</a>'
    first = family_view.first_name(profile.person.get("name", ""))
    where = ("/setup/add-a-phone" if profile.sees.this_mac
             else f"{DASHBOARD_PREFIX}/devices/add-a-phone")
    label = d["add_heading"].format(name=first, genitive=_genitive(first))
    return f'<a class="fs-button" href="{where}?person={_e(profile.id)}">{_e(label)}</a>'


def _date(iso: str, language: str) -> str:
    """A past day as the stale line says it: "3 October", "3. Oktober"."""
    try:
        said = date.fromisoformat(iso)
    except ValueError:
        return iso
    month = pages.words(language)["months"][said.month - 1]
    return f"{said.day}. {month}" if language == "de" else f"{said.day} {month}"


# ── On this Mac ──────────────────────────────────────────────────────────

def _this_mac(profile: Profile, language: str) -> str:
    """Who this Mac's browser greets: the person picked here, kept in the
    browser (`profile.js` shows the picked line when it is this person)."""
    w, d = words(language), pages.words(language)
    first = family_view.first_name(profile.person.get("name", ""))
    where = _e(w["passwords_where"]).replace(
        _e(w["setup_page"]), f'<a class="p-link" href="{SETUP_PATH}?step=family">{_e(w["setup_page"])}</a>')
    return (f'<section class="sect" id="this-mac" data-pick="{_e(profile.id)}"><h2>{_e(w["this_mac"])}</h2>'
            f'<ul class="rows"><li><div class="what"><span data-when="nobody">{_e(w["nobody_picked"])}'
            f'</span><span data-when="picked" hidden>{_e(w["picked"].format(name=first))}</span></div>'
            f'<div class="acts"><button class="setup-mini" type="button" data-pick-me>'
            f'{_e(w["this_is_me"])}</button><button class="setup-mini" type="button" data-not-picked '
            f'hidden>{_e(d["not_me"])}</button></div></li></ul><p class="quiet">{where}</p></section>')


# ── Parts ────────────────────────────────────────────────────────────────

def _back(language: str) -> str:
    return (f'<a class="back" href="{DASHBOARD_PREFIX}">'
            f'{_e(pages.words(language)["dashboard"])}</a>')


def _frame(language: str, title: str, main: str) -> str:
    """`main` on the phone frame of the device pages, with the profile's
    styles and the two scripts that act on it."""
    base = DASHBOARD_PREFIX
    styles = "".join(f'<link rel="stylesheet" href="{base}/static/{name}">'
                     for name in ("widgets/fs-widget.css", "components/pills.css", "profile.css"))
    scripts = "".join(f'<script type="module" src="{base}/static/{name}"></script>'
                      for name in ("devices.js", "profile.js"))
    return Template(pages.FRAME.read_text(encoding="utf-8")).substitute(
        lang="de" if language == "de" else "en", base=base, title=_e(title), main=main,
        script=styles + scripts)


def _genitive(name: str) -> str:
    """A German name's genitive: "Homers", "Hans'"."""
    return name + ("'" if name[-1:].lower() in ("s", "ß", "x", "z") else "s")


def _span(text: str, cls: str) -> str:
    return f'<span class="{cls}">{_e(text)}</span>' if cls else f"<span>{_e(text)}</span>"


def _p(text: str, cls: str) -> str:
    return f'<p class="{cls}">{_e(text)}</p>'


def _e(text: str | None) -> str:
    return escape(text or "", quote=True)
