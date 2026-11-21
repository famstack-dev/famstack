"""The profile page's words, in English and German.

    words("en")["devices_other"].format(name="Bart")   # "Bart's devices"

Labels that the device pages already say ("This device", "Remove",
"Not me", "Add a device") come from `devices.pages.WORDS`, and the word
for sister or brother from the family editor's (`family_view.WORDS`), so
one label reads the same on every page. This module holds the rest.
`{name}` is a first name, `{genitive}` its German genitive ("Barts").
The words of the dashboard's pill panel (`person_profile.panel`) are here
too, so the panel and the page say one profile the same way, and
`genitive`, `day_of_month` and `seen` say a name, a past day and when a
device was seen as both write it.
"""

from __future__ import annotations

from datetime import date

from devices import pages

__all__ = ["WORDS", "words", "genitive", "day_of_month", "seen"]

WORDS = {
    "en": {
        "title": "Profile",
        "admin": "Looks after famstack",
        "memory_own": "Your memory",
        "memory_other": "{name}'s memory",
        "settings": "Settings",
        "email": "Email",
        "password": "Your password",
        "password_line": "For the chat app and famstack's other apps. Only your own devices show it.",
        "show": "Show", "hide": "Hide", "copy": "Copy", "copied": "Copied",
        "settings_line": "The email and the word for sister or brother change on the Mac, under "
                         "Your family.",
        "devices_other": "{name}'s devices",
        "added": "added {day}",
        "seen": "seen {day}",
        "not_seen_since": "not seen since {day}",
        "seen_now": "seen now",
        "not_me_ask": ("This phone is not {name}'s? famstack forgets it, and it opens nothing of "
                       "famstack until it is added again."),
        "forget": "Forget this phone",
        "empty_own": "No device is added yet.",
        "empty_other": "{name} has no device yet.",
        "unread": "The devices could not be read just now. Reload the page in a moment.",
        "why_person": "{name}'s devices and password show on {name}'s own phone.",
        "why_shared": "Devices and passwords show on each person's own phone.",
        "this_mac": "On this Mac",
        "nobody_picked": "This Mac greets nobody by name.",
        "this_is_me": "This is me",
        "picked": "This Mac greets you as {name}. It is not a sign-in.",
        "passwords_where": "Passwords are on the setup page, under Your family.",
        "setup_page": "setup page",
        "nobody_heading": "Nobody here by that name",
        "nobody_line": "This address names nobody of the family.",
        "panel_why_person": "{name}'s devices show on {name}'s own phone.",
        "panel_why_shared": "Devices show on each person's own phone.",
        "panel_waiting_one": "1 device waits to join",
        "panel_waiting_many": "{count} devices wait to join",
        "panel_route": "Settings and devices",
        "panel_close": "Close",
    },
    "de": {
        "title": "Profil",
        "admin": "Kümmert sich um famstack",
        "memory_own": "Dein Gedächtnis",
        "memory_other": "{genitive} Gedächtnis",
        "settings": "Einstellungen",
        "email": "E-Mail",
        "password": "Dein Passwort",
        "password_line": ("Für die Chat-App und die anderen Apps von famstack. Nur deine eigenen "
                          "Geräte zeigen es."),
        "show": "Zeigen", "hide": "Verbergen", "copy": "Kopieren", "copied": "Kopiert",
        "settings_line": ("Die E-Mail und das Wort für Schwester oder Bruder änderst du am Mac, "
                          "unter Eure Familie."),
        "devices_other": "{genitive} Geräte",
        "added": "hinzugefügt {day}",
        "seen": "zuletzt gesehen {day}",
        "not_seen_since": "nicht mehr gesehen seit {day}",
        "seen_now": "jetzt gesehen",
        "not_me_ask": ("Dieses Handy gehört nicht {name}? famstack vergisst es, und es öffnet "
                       "nichts mehr von famstack, bis es wieder hinzugefügt wird."),
        "forget": "Dieses Handy vergessen",
        "empty_own": "Noch kein Gerät hinzugefügt.",
        "empty_other": "{name} hat noch kein Gerät.",
        "unread": "Die Geräte ließen sich gerade nicht lesen. Lade die Seite gleich noch einmal.",
        "why_person": "{genitive} Geräte und Passwort zeigt nur {genitive} eigenes Handy.",
        "why_shared": "Geräte und Passwörter zeigt nur das eigene Handy jeder Person.",
        "this_mac": "Auf diesem Mac",
        "nobody_picked": "Dieser Mac begrüßt niemanden mit Namen.",
        "this_is_me": "Das bin ich",
        "picked": "Dieser Mac begrüßt dich als {name}. Das ist keine Anmeldung.",
        "passwords_where": "Die Passwörter stehen auf der Einrichtungsseite, unter Eure Familie.",
        "setup_page": "Einrichtungsseite",
        "nobody_heading": "Hier heißt niemand so",
        "nobody_line": "Diese Adresse nennt niemanden aus der Familie.",
        "panel_why_person": "{genitive} Geräte zeigt nur {genitive} eigenes Handy.",
        "panel_why_shared": "Geräte zeigt nur das eigene Handy jeder Person.",
        "panel_waiting_one": "1 Gerät wartet auf Einlass",
        "panel_waiting_many": "{count} Geräte warten auf Einlass",
        "panel_route": "Einstellungen und Geräte",
        "panel_close": "Schließen",
    },
}


def words(language: str) -> dict:
    """The labels in `language`: German for "de", English for anything else."""
    return WORDS["de" if language == "de" else "en"]


def genitive(name: str) -> str:
    """A German name's genitive: "Homers", "Hans'".

        genitive("Homer")   # "Homers"
    """
    return name + ("'" if name[-1:].lower() in ("s", "ß", "x", "z") else "s")


def day_of_month(iso: str, language: str) -> str:
    """A past day as "not seen since" says it: "3 October", "3. Oktober";
    an unreadable day stays as it came.

        day_of_month("2026-10-03", "de")   # "3. Oktober"
    """
    try:
        said = date.fromisoformat(iso)
    except ValueError:
        return iso
    month = pages.words(language)["months"][said.month - 1]
    return f"{said.day}. {month}" if language == "de" else f"{said.day} {month}"


def seen(device: dict, *, this: str, today: date, language: str) -> str:
    """When `device` was last seen, as its row says it: "seen now" for the
    device that looks (`this`, its id), "not seen since 3 October" for a
    stale one (`device["stale"]`), else "seen yesterday", "seen 10 November".

        seen({"id": "d1", "seen": "2026-10-03", "stale": True}, this="", today=..., language="en")
        # "not seen since 3 October"
    """
    w = words(language)
    if device["id"] == this:
        return w["seen_now"]
    if device["stale"]:
        return w["not_seen_since"].format(day=day_of_month(device["seen"], language))
    return w["seen"].format(day=pages.day(device["seen"], language, today))
