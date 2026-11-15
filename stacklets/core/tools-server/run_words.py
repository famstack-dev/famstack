"""run_words.py: a run's steps and its errors in the page's language.

    import run_words

    run_words.step({"text": "Downloading the language model",
                    "key": "ai.download.language_model"}, "de")
    # "Sprachmodell wird geladen"
    run_words.error({"error": "Not enough free disk space ...", "key": "ai.no_room",
                     "values": {"gb": 6.2}}, "de")
    # "Auf diesem Mac fehlt Platz. Mach 6,2 GB frei, dann „Erneut versuchen“."
    run_words.table("de")      # every step's words, for fs-stack.js

A run (`stack up`, the AI's preparation) reports its steps and its result
in English, for the terminal. A step or an error a screen can say carries
a key beside its text (`stack.said`). This module holds the words for
those keys, so a German page shows no English line from the command line.
A text without a key, or with a key this module does not know, is shown
as the run wrote it: the tile's own lead-in ("Einrichtung fehlgeschlagen:")
stays in the page's language.

The words live here, beside the tiles that show them (`stack_widget.py`,
`dashboard/widgets/fs-stack.js`), never in the framework: it only carries
the key.
"""

from __future__ import annotations

__all__ = ["error", "step", "table"]


STEPS = {
    "en": {
        "up.render": "Preparing the settings",
        "up.secrets": "Creating passwords",
        "up.configure": "Applying the answers",
        "up.install": "Installing",
        "up.env": "Saving the settings",
        "up.services": "Starting the services",
        "up.caddy": "Setting up the web addresses",
        "ai.install_omlx": "Installing oMLX",
        "ai.start_omlx": "Starting oMLX",
        "ai.start_omlx_settings": "Starting oMLX with its settings",
        "ai.download.language_model": "Downloading the language model",
        "ai.download.house_voice": "Downloading the voice",
        "ai.download.whisper_model": "Downloading the speech recognition (about 1.5 GB)",
        "ai.build_tools": "Installing build tools",
        "ai.whisper_source": "Downloading the speech recognition's source code",
        "ai.whisper_build": "Building the speech recognition (1 to 2 minutes)",
        "ai.question": "Asking the AI a short test question",
    },
    "de": {
        "up.render": "Einstellungen werden vorbereitet",
        "up.secrets": "Passwörter werden erzeugt",
        "up.configure": "Antworten werden übernommen",
        "up.install": "Wird installiert",
        "up.env": "Einstellungen werden gespeichert",
        "up.services": "Dienste werden gestartet",
        "up.caddy": "Webadressen werden eingerichtet",
        "ai.install_omlx": "oMLX wird installiert",
        "ai.start_omlx": "oMLX wird gestartet",
        "ai.start_omlx_settings": "oMLX startet mit seinen Einstellungen",
        "ai.download.language_model": "Sprachmodell wird geladen",
        "ai.download.house_voice": "Stimme wird geladen",
        "ai.download.whisper_model": "Spracherkennung wird geladen (etwa 1,5 GB)",
        "ai.build_tools": "Werkzeuge zum Bauen werden installiert",
        "ai.whisper_source": "Quellcode der Spracherkennung wird geladen",
        "ai.whisper_build": "Spracherkennung wird gebaut (1 bis 2 Minuten)",
        "ai.question": "Die KI bekommt eine kurze Testfrage",
    },
}

ERRORS = {
    "en": {
        "ai.no_room": "This Mac is short of space. Free up {gb} GB, then press Try again.",
    },
    "de": {
        "ai.no_room": "Auf diesem Mac fehlt Platz. Mach {gb} GB frei, dann „Erneut versuchen“.",
    },
}


def step(step: dict, language: str) -> str:
    """The line a page shows for one step of a run (a job's snapshot step,
    or a step event's data): the words for its `key`, else its `text`.

        step({"text": "Starting oMLX", "key": "ai.start_omlx"}, "de")   # "oMLX wird gestartet"
        step({"text": "Pulling images"}, "de")                          # "Pulling images"
    """
    return STEPS[_language(language)].get(step.get("key", "")) or step.get("text", "")


def error(outcome: dict, language: str) -> str:
    """Why a run failed, as a page says it: the words for the result's
    `key` with its `values` (a number in the page's own notation), else
    its `error` or `cancelled` text as the run wrote it.

        error({"error": "...", "key": "ai.no_room", "values": {"gb": 6.2}}, "de")
        # "Auf diesem Mac fehlt Platz. Mach 6,2 GB frei, dann „Erneut versuchen“."
        error({"error": "AI could not be prepared"}, "de")   # "AI could not be prepared"
    """
    lang = _language(language)
    said = ERRORS[lang].get(outcome.get("key", ""))
    if said is None:
        return outcome.get("error") or outcome.get("cancelled") or ""
    values = {name: _number(value, lang) for name, value in (outcome.get("values") or {}).items()}
    try:
        return said.format(**values)
    except (KeyError, IndexError):
        # A result without the values its words name reads as the run wrote it.
        return outcome.get("error") or ""


def table(language: str) -> dict:
    """Every step's words in `language`, by key: what fs-stack.js looks a
    step event's key up in (`data-step-words`)."""
    return dict(STEPS[_language(language)])


# ── Details ───────────────────────────────────────────────────────────────

def _language(language: str) -> str:
    return "de" if language == "de" else "en"


def _number(value, language: str) -> str:
    """A number with one decimal as the page writes it: 6.2, or 6,2 in German."""
    if not isinstance(value, (int, float)):
        return str(value)
    text = f"{value:.1f}"
    return text.replace(".", ",") if language == "de" else text
