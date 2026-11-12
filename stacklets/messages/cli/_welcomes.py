"""The stacker bot's first messages in the chat: the Server room's welcome and the Memories room's.

    from ._welcomes import memories, server_room
    plain, html = server_room("de", family="Simpson")
    plain[:38]                  # "Der Server der Familie Simpson läuft."
    plain, html = memories("en")

`stack messages setup` posts both once, when it makes the rooms
(`setup.py`, `_post_welcome_messages`). Each comes as plain text and as
HTML, because Matrix shows the HTML and keeps the plain text for clients
that cannot. The texts are in the stack's language (`[core] language`):
German for "de", English for any other language.

Pure: no Matrix, no I/O.
"""

from __future__ import annotations

__all__ = ["server_room", "memories"]


def server_room(language: str, *, family: str = "") -> tuple[str, str]:
    """The Server room's welcome, as (plain, html). `family` is the
    family's name (`[core] stack_owner`), for the headline; "" for none.

        server_room("en", family="Simpson")[0].splitlines()[0]
        # "The Simpsons' server is live."
    """
    w = _SERVER_ROOM["de" if language == "de" else "en"]
    headline = w["headline"](family) if family else w["no_family"]
    commands = "".join(f"  {command:<21}{line}\n" for command, line in w["commands"])
    rows = "".join(f"<tr><td><code>{command}</code></td><td>{line}</td></tr>"
                   for command, line in w["commands"])
    plain = (f"{headline}\n\n{w['yours']}\n\n{w['room']}\n\n{w['next']}:\n\n"
             f"  {NEXT_STEP}\n\n{w['next_why']}\n\n{w['available']}:\n\n{commands}")
    html = (f"<h3>{headline}</h3><p>{w['yours']}</p><p>{w['room_html']}</p>"
            f"<h4>{w['next']}</h4><pre><code>{NEXT_STEP}</code></pre><p>{w['next_why']}</p>"
            f"<h4>{w['available']}</h4><table>{rows}</table>")
    return plain, html


def memories(language: str) -> tuple[str, str]:
    """The Memories room's welcome, as (plain, html).

        memories("de")[0].splitlines()[0]   # "Das ist die Erinnerungssammlung eurer Familie."
    """
    w = _MEMORIES["de" if language == "de" else "en"]
    return w["plain"], w["html"]


# ── The words ─────────────────────────────────────────────────────────────

# The first command the welcome recommends, as typed in the famstack folder.
NEXT_STEP = "./stack up docs"


def _possessive(family: str) -> str:
    return f"{family}'" if family.endswith("s") else f"{family}s'"


_SERVER_ROOM = {
    "en": {
        "headline": lambda family: f"The {_possessive(family)} server is live.",
        "no_family": "Your family server is live.",
        "yours": "This is your own famstack. Everything here is yours. Nothing leaves your network.",
        "room": "This room is your server's control center. Status updates and alerts will appear here.",
        "room_html": ("This room is your server's <b>control center</b>. "
                      "Status updates and alerts will appear here."),
        "next": "Recommended next step",
        "next_why": ("Sets up your document archive. "
                     "Scan a receipt or letter and it's filed and searchable forever."),
        "available": "Available commands",
        "commands": [("./stack status", "See what's running"),
                     ("./stack up photos", "Private photo library"),
                     ("./stack up docs", "Document archive with OCR"),
                     ("./stack up ai", "Local AI engine"),
                     ("./stack up code", "Private git server"),
                     ("./stack up memory", "Family wiki and curated knowledge")],
    },
    "de": {
        "headline": lambda family: f"Der Server der Familie {family} läuft.",
        "no_family": "Euer Familienserver läuft.",
        "yours": "Das ist euer eigenes famstack. Alles hier gehört euch. Nichts verlässt euer Netzwerk.",
        "room": "Dieser Raum ist die Zentrale eures Servers. Hier erscheinen Statusmeldungen und Warnungen.",
        "room_html": ("Dieser Raum ist die <b>Zentrale</b> eures Servers. "
                      "Hier erscheinen Statusmeldungen und Warnungen."),
        "next": "Als Nächstes empfohlen",
        "next_why": ("Das richtet euer Dokumentenarchiv ein. Scanne einen Kassenzettel oder einen Brief, "
                     "dann ist er abgelegt und lässt sich für immer durchsuchen."),
        "available": "Diese Befehle gibt es",
        "commands": [("./stack status", "Zeigt, was läuft"),
                     ("./stack up photos", "Eure private Fotobibliothek"),
                     ("./stack up docs", "Dokumentenarchiv mit Texterkennung"),
                     ("./stack up ai", "Lokale KI"),
                     ("./stack up code", "Euer privater Git-Server"),
                     ("./stack up memory", "Familien-Wiki und gesammeltes Wissen")],
    },
}

_MEMORIES = {
    "en": {
        "plain": (
            "This is your family's memory collection.\n\n"
            "Record voice messages with your kids, capture what made today special, "
            "snap a photo at the zoo, keep a holiday diary. "
            "Let the kids talk to their future selves.\n\n"
            "Voice messages are transcribed, and your memories become a family "
            "diary you can read back. Nothing leaves your home.\n\n"
            "We made it a habit to press record once or twice a week at the dinner table "
            "and capture a couple of moments. What was funny, what was special, "
            "what the kids want to tell their future selves.\n\n"
            "There's no wrong way to use this. Just start recording."
        ),
        "html": (
            "<p>This is your family's <b>memory collection</b>.</p>"
            "<p>Record voice messages with your kids, capture what made today special, "
            "snap a photo at the zoo, keep a holiday diary. "
            "Let the kids talk to their future selves.</p>"
            "<p>Voice messages are transcribed, and your memories become a "
            "<b>family diary</b> you can read back. Nothing leaves your home.</p>"
            "<p>We made it a habit to press record once or twice a week at the dinner table "
            "and capture a couple of moments. What was funny, what was special, "
            "what the kids want to tell their future selves.</p>"
            "<p><em>There's no wrong way to use this. Just start recording.</em></p>"
        ),
    },
    "de": {
        "plain": (
            "Das ist die Erinnerungssammlung eurer Familie.\n\n"
            "Nimm mit euren Kindern Sprachnachrichten auf, halte fest, was den Tag besonders "
            "gemacht hat, mach ein Foto im Zoo, führ ein Urlaubstagebuch. "
            "Lass die Kinder mit ihrem späteren Ich sprechen.\n\n"
            "Sprachnachrichten werden abgetippt, und aus euren Erinnerungen wird ein "
            "Familientagebuch, in dem ihr später nachlesen könnt. Nichts verlässt euer Zuhause.\n\n"
            "Wir haben es uns angewöhnt, ein- oder zweimal pro Woche beim Abendessen auf "
            "Aufnahme zu drücken und ein paar Momente festzuhalten. Was lustig war, was "
            "besonders war, was die Kinder ihrem späteren Ich sagen wollen.\n\n"
            "Hier gibt es kein Richtig oder Falsch. Fang einfach an aufzunehmen."
        ),
        "html": (
            "<p>Das ist die <b>Erinnerungssammlung</b> eurer Familie.</p>"
            "<p>Nimm mit euren Kindern Sprachnachrichten auf, halte fest, was den Tag besonders "
            "gemacht hat, mach ein Foto im Zoo, führ ein Urlaubstagebuch. "
            "Lass die Kinder mit ihrem späteren Ich sprechen.</p>"
            "<p>Sprachnachrichten werden abgetippt, und aus euren Erinnerungen wird ein "
            "<b>Familientagebuch</b>, in dem ihr später nachlesen könnt. "
            "Nichts verlässt euer Zuhause.</p>"
            "<p>Wir haben es uns angewöhnt, ein- oder zweimal pro Woche beim Abendessen auf "
            "Aufnahme zu drücken und ein paar Momente festzuhalten. Was lustig war, was "
            "besonders war, was die Kinder ihrem späteren Ich sagen wollen.</p>"
            "<p><em>Hier gibt es kein Richtig oder Falsch. Fang einfach an aufzunehmen.</em></p>"
        ),
    },
}
