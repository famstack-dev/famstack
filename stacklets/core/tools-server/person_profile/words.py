"""The profile page's words, in English and German.

    words("en")["devices_other"].format(name="Bart")   # "Bart's devices"

Labels that the device pages already say ("This device", "Remove",
"Not me", "Add a device") come from `devices.pages.WORDS`, and the word
for sister or brother from the family editor's (`family_view.WORDS`), so
one label reads the same on every page. This module holds the rest.
`{name}` is a first name, `{genitive}` its German genitive ("Barts").
"""

from __future__ import annotations

__all__ = ["WORDS", "words"]

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
    },
}


def words(language: str) -> dict:
    """The labels in `language`: German for "de", English for anything else."""
    return WORDS["de" if language == "de" else "en"]
