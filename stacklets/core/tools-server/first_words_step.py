"""The AI's first words on the setup page: step 4f, below "Your AI is ready".

    import first_words_step

    first_words_step.shown(plan)                 # True: speech is on
    first_words_step.html("en")                  # '<section class="first-words" …>…'

Once the AI runs with speech on, the setup page lets it speak for the
first time, as the terminal does after the AI's first start (`stack ai
hello`, then `stack ai intro`). The admin hears it in the browser that
has the page open, on the Mac or a phone:

    "▶ Play"  ──▶ the welcome plays ──▶ "Did you hear it?"
                                          │            │
                         "Play again" ◀───┘            └──▶ "Yes"
                         (+ the volume hint, once)            │
                                                              ▼
                       the AI's introduction, shown as a quote while it plays

Every word is in famstack's own voice. The page's script
(`dashboard/first-words.js`) asks the tools server for each part, `POST
/dashboard/api/ai/first-words/welcome` and `/intro`; the tools server asks
the host API (`ai_welcome`, `ai_intro`), which runs `stack ai hello
--audio` and `stack ai intro --audio` on the Mac, and hands the page their
words and MP3. The browser's built-in speech synthesis is never used,
because it is not the voice the stack speaks with.

With speech off the AI has no voice, so the step is not shown and "Your AI
is ready" leads on by itself. Nothing here is stored: a reload shows
"▶ Play" again.

Pure: no web framework, no I/O. The words are here, English and German.
"""

from __future__ import annotations

import json
from html import escape

__all__ = ["PARTS", "shown", "html", "words"]

# What the page asks the tools server for, and the host API command each runs.
PARTS = {"welcome": "ai_welcome", "intro": "ai_intro"}

WORDS = {
    "en": {
        "heading": "Your Mac learned to speak",
        "text": "Want to hear it? Turn up your volume.",
        "play": "▶ Play", "heard": "Did you hear it?", "yes": "Yes", "again": "Play again",
        "hint": "Check that the volume is up and the speakers are on.",
        "silent": "The voice did not start. Your AI may still be loading it: play it again "
                  "in a minute.",
    },
    "de": {
        "heading": "Dieser Mac kann jetzt sprechen",
        "text": "Willst du es hören? Dreh die Lautstärke auf.",
        "play": "▶ Abspielen", "heard": "Hast du es gehört?", "yes": "Ja",
        "again": "Noch einmal abspielen",
        "hint": "Prüf, ob die Lautstärke aufgedreht ist und die Lautsprecher an sind.",
        "silent": "Die Stimme ist nicht gestartet. Vielleicht lädt eure KI sie noch: Spiel "
                  "sie in einer Minute noch einmal ab.",
    },
}


def words(language: str) -> dict:
    """The step's words in `language`: German for "de", English otherwise.

        words("de")["yes"]    # "Ja"
    """
    return WORDS["de" if language == "de" else "en"]


def shown(plan: dict | None) -> bool:
    """Whether the AI speaks, so the step is shown: `speech` of the AI's
    plan (`stack ai recommended show`). A plan the stack did not give
    counts as speech on, as "Your AI is ready" lists "Speaks" then. A
    server elsewhere (`other`) does not speak in the house voice, which
    comes with the stack's own AI, so it is never shown for one.

        shown({"speech": False})    # False
        shown(None)                 # True
        shown({"speech": True, "other": {"address": "http://192.168.1.30:11434"}})   # False
    """
    plan = plan or {}
    return not plan.get("other") and bool(plan.get("speech", True))


def html(language: str) -> str:
    """Step 4f in `language`: its heading, "▶ Play", and the hidden parts
    the script shows in turn (the question, the volume hint, a voice that
    did not start, the introduction's quote). The "Next" below it is the
    setup page's own.

        html("en")
        # '<section class="first-words" aria-labelledby="first-words-heading">…▶ Play…'
    """
    w = words(language)
    said = {"silent": w["silent"]}
    return (
        f'<section class="first-words" aria-labelledby="first-words-heading" '
        f'data-words="{_e(json.dumps(said))}">'
        f'<h2 class="first-words-heading" id="first-words-heading">{_e(w["heading"])}</h2>'
        f'<p class="setup-text">{_e(w["text"])}</p>'
        f'<div class="setup-act first-words-start">'
        f'{_button(w["play"], "play")}</div>'
        f'<p class="first-words-playing" hidden aria-hidden="true">'
        f'<i></i><i></i><i></i><i></i></p>'
        f'<div class="first-words-asked" hidden><p class="first-words-question">{_e(w["heard"])}</p>'
        f'<div class="setup-act">{_button(w["yes"], "yes")}'
        f'{_button(w["again"], "again", kind="soft")}</div>'
        f'<p class="setup-note first-words-hint" hidden>{_e(w["hint"])}</p></div>'
        f'<p class="setup-note first-words-silent" role="alert" hidden></p>'
        f'<blockquote class="first-words-intro" aria-live="polite" hidden></blockquote>'
        f'</section>')


# ── Small helpers ────────────────────────────────────────────────────────

def _button(label: str, act: str, *, kind: str = "") -> str:
    """A button the script answers by its `data-act`. The plain kind, as
    the mock has them: the step's primary button is the way on below it."""
    kinds = f' data-kind="{kind}"' if kind else ""
    return (f'<button class="fs-button"{kinds} data-act="{act}" type="button">'
            f'{_e(label)}</button>')


def _e(text: str) -> str:
    return escape(str(text), quote=True)
