"""The AI step of the setup page: famstack's oMLX, or your own AI server.

    import ai_step

    sub = ai_step.sub_step(reads=False, answer=None, tile=ai_tile, provider="", choice="")
    sub                                              # "choose"
    ai_step.html(sub, plan, here=True, language="en", page="/setup?step=ai")

Step 4 of `/setup` (`setup_page.py`) sets up the AI by one of two routes:

    1. famstack installs oMLX      2. your own AI server
       (recommended)
    the command for the Mac's       an address and a key on the page;
    terminal, `./stack ai switch    Connect runs `stack ai switch
    managed`; the AI's tile         <address> --key -`, which checks the
    follows the run                 server, asks it one question and
                                    writes `[ai]`. Nothing on that server
                                    is changed, ever.

Route 1 needs the terminal: Homebrew and macOS ask for permissions there.
Route 2 says that famstack is tested and tuned with oMLX and recommends
it; once its server answered and it is an oMLX, the ready part shows the
settings recommended for this Mac.

`stack.toml` is the step's one truth. Every sub-step follows from `[ai]`,
the answer kept for it and the AI's run, read again on every load, so a
reload, a second browser or a change in the terminal shows the same one.
The page holds only the admin's choice of a route (`?ai=`), which shows a
command or a form and never claims a state:

    the AI's tile follows a run ──────────────────────▶ starting  (its tile)
    reads, answered ──────────────────────────────────▶ ready
    ?ai=other ────────────────────────────────────────▶ other     (route 2's form)
    ?ai=install ──────────────────────────────────────▶ install   (route 1's command)
    reads, not asked for this [ai] ───────────────────▶ not_asked (ask it; done all the same)
    reads, did not answer ────────────────────────────▶ no_answer (its error)
    [ai] names a server elsewhere that does not read ─▶ no_answer (nothing answers)
    [ai] names famstack's oMLX, the oMLX app runs ────▶ no_answer (quit the app)
    [ai] names famstack's oMLX, not running yet ──────▶ install   (route 1's command)
    [ai] names no server ─────────────────────────────▶ choose    (the two routes)

Once `[ai]` names a server the step never asks for a route again.

"Reads" is what the stack's state says (`stack list`: set up and online,
or a server elsewhere whose models answer); a server can list its models
and still fail every completion, as an oMLX built for a newer macOS does.
So "ready" waits for one real answer (`answer`): the AI's run (`stack up
ai`) ends by asking the AI one tiny question, `stack ai switch` asks a
server elsewhere, and both keep the answer in the instance for the
current address, model and key (`stacklets/ai/answer.py`). An AI not
asked for its current `[ai]` (an older install, a new key, a server an
older switch connected) counts as done, as before the question existed,
and the step offers to ask it now; one that did not answer shows what
kind of failure it is (the server cannot compute, a missing model, a
refused key, no answer in time) and the server's own error in one line,
with the ways on: check again, another server, and for a server elsewhere
famstack's oMLX instead. "Check again" starts the AI's run as its tile
does for famstack's oMLX, and asks a server elsewhere with `stack ai
check` through the dashboard.

`plan` is the AI stacklet's answer, `stack ai recommended show` through
the host API (`stacklets/ai/recommended.py`): `provider`, `memory_gb`,
`profile`, `speech`, the recommended `models`, and `other`, the server
elsewhere that `[ai]` names (`address`, `model`, `voice`, `home`), or None.

The page's script (`dashboard/ai-step.js`) posts route 2's form to
`/dashboard/api/ai/switch` and Check again to `/dashboard/api/ai/check`,
only from this Mac, as every change answers (`access.mac_only`); a server
that asks for a model gets a list to choose from.

Pure: no web framework, no I/O. The words are here, English and German.
"""

from __future__ import annotations

import json
from html import escape

import stack_widget

__all__ = ["SUB_STEPS", "CHOICES", "MANAGED_COMMAND", "SERVER_ADDRESS", "sub_step", "heading",
           "html", "words"]

# The reason of an answer that famstack's oMLX steps aside for the oMLX
# app (`stacklets/ai/omlx_service.py`, its guard): no question was asked.
OMLX_APP = "omlx_app"

SUB_STEPS = ("ready", "not_asked", "no_answer", "starting", "other", "install", "choose")
# What the admin chose on the page, in `?ai=`: famstack's oMLX, or their own server.
# The only thing the page holds; it never claims a state.
CHOICES = ("install", "other")
# Route 1 starts in the Mac's terminal, always with this command: it installs
# oMLX when missing, starts it, asks it one question and writes `[ai]`.
MANAGED_COMMAND = "./stack ai switch managed"
# Route 2's address field starts with the oMLX app's default address.
SERVER_ADDRESS = "http://localhost:8000/v1"
# Where the admin guide explains a speech server for an AI server elsewhere,
# and the settings famstack recommends for an oMLX it does not run.
ADMIN_GUIDE = "https://github.com/famstack-dev/famstack/blob/main/docs/admin-guide.md"
SPEECH_SERVER_DOCS = ADMIN_GUIDE + "#ai-on-another-machine-or-a-hosted-provider"
OMLX_SETTINGS_DOCS = ADMIN_GUIDE + "#which-settings-does-famstack-recommend-for-an-omlx"

WORDS = {
    "en": {
        "choose_heading": "How should your AI run?",
        "routes": {"managed": ("famstack installs oMLX",
                               "famstack sets up oMLX on this Mac and looks after its models. "
                               "It takes 20 to 30 minutes in the terminal.",
                               "Install oMLX"),
                   "server": ("Use your own AI server",
                              "Any server that speaks the OpenAI API, for example the oMLX app "
                              "or a server on another computer.",
                              "Enter a server")},
        "recommended": "Recommended",
        "server_instead": "Use your own AI server instead",
        "other_heading": "Use your own AI server", "address": "Address",
        "address_help": "For example http://localhost:8000/v1 or http://192.168.1.20:11434",
        "key": "API key", "key_help": "Leave it empty when the server needs none.",
        "model": "Model for reading and answering",
        "other_kept": "famstack keeps the key on this Mac and uses it only to talk to this server. "
                      "Nothing is downloaded or changed on the server.",
        "other_errors": {"key": "The server did not accept this key, or it needs one.",
                         "unreachable": "Nothing answers at this address. Check it, and that the "
                                        "server runs.",
                         "model": "The server has several models. Choose the one for reading "
                                  "and answering.",
                         "failed": "That did not work. Try again."},
        "omlx_note": "famstack is tested and tuned for local models with oMLX, and we recommend it.",
        "hints": {"intro": "For this Mac we recommend these settings in oMLX:",
                  "chat": "Reading and answering: {name}",
                  "listen": "Understanding voice messages: {name}",
                  "speak": "Speaking: {name}",
                  "ceiling": "Memory guard: custom, {gb} GB",
                  "prefill": "Chunked prefill: on",
                  "idle": "Unload the model after {min} minutes without a request",
                  "more": "More in the admin guide"},
        "other_model": "Reads and answers with {model} on {address}",
        "voice_needs": "Voice messages need a speech server:",
        "leaves_home": "This server is outside your home network. Your letters, notes, questions "
                       "and voice messages go there.",
        "connect": "Connect", "back": "Back",
        "elsewhere": "Enter the server on the Mac that runs famstack.",
        "ready_heading": "Your AI is ready",
        "not_asked_heading": "Your AI runs",
        "not_asked": "famstack has not asked your AI a question with its current setting yet. "
                     "Ask it now: famstack asks it one short question. The first answer can take "
                     "a minute while the model loads.",
        "ask": "Ask your AI now",
        "no_answer_heading": "Your AI does not answer",
        "no_answer": {"server": "Your AI lists its models, but it failed to answer a short "
                                "question. It said:",
                      "model": "Your AI does not have the model famstack asks for ({model}). "
                               "It said:",
                      "key": "Your AI refused famstack's API key. It said:",
                      "timeout": "Your AI did not answer a short question in time. A large "
                                 "model can take longer to load the first time: check again in "
                                 "a minute. It said:",
                      "unreachable": "Your AI did not answer a short question. It said:",
                      "down": "Nothing answers at your AI's address.",
                      "omlx_app": "The oMLX app runs on this Mac, so famstack's oMLX does not "
                                  "start beside it: two AI servers on one Mac's graphics chip "
                                  "can crash the Mac. Quit the oMLX app (its icon in the menu "
                                  "bar, then Quit), then check again."},
        "answer_again": "Check again", "other_again": "Enter another server",
        "install_instead": "Let famstack install oMLX instead",
        "can": {"reads": "Reads letters and answers questions",
                "hears": "Understands voice messages",
                "hears_off": "Voice messages: speech is off", "speaks": "Speaks"},
    },
    "de": {
        "choose_heading": "Wie soll eure KI laufen?",
        "routes": {"managed": ("famstack installiert oMLX",
                               "famstack richtet oMLX auf diesem Mac ein und kümmert sich um die "
                               "Modelle. Das dauert 20 bis 30 Minuten im Terminal.",
                               "oMLX installieren"),
                   "server": ("Eigenen KI-Server nutzen",
                              "Jeder Server, der die OpenAI-Schnittstelle spricht, zum Beispiel "
                              "die oMLX-App oder ein Server auf einem anderen Rechner.",
                              "Server eintragen")},
        "recommended": "Empfohlen",
        "server_instead": "Stattdessen eigenen KI-Server nutzen",
        "other_heading": "Eigenen KI-Server nutzen", "address": "Adresse",
        "address_help": "Zum Beispiel http://localhost:8000/v1 oder http://192.168.1.20:11434",
        "key": "Schlüssel", "key_help": "Leer lassen, wenn der Server keinen braucht.",
        "model": "Modell zum Lesen und Antworten",
        "other_kept": "famstack speichert den Schlüssel auf diesem Mac und nutzt ihn nur für "
                      "diesen Server. Auf dem Server wird nichts heruntergeladen oder geändert.",
        "other_errors": {"key": "Der Server hat diesen Schlüssel nicht angenommen oder verlangt "
                                "einen.",
                         "unreachable": "Unter dieser Adresse antwortet nichts. Prüfe die Adresse "
                                        "und ob der Server läuft.",
                         "model": "Der Server hat mehrere Modelle. Wähle das zum Lesen und "
                                  "Antworten.",
                         "failed": "Das hat nicht geklappt. Versuch es noch einmal."},
        "omlx_note": "famstack ist für lokale Modelle mit oMLX getestet und darauf abgestimmt, "
                     "und wir empfehlen es.",
        "hints": {"intro": "Für diesen Mac empfehlen wir diese Einstellungen in oMLX:",
                  "chat": "Lesen und Antworten: {name}",
                  "listen": "Sprachnachrichten verstehen: {name}",
                  "speak": "Sprechen: {name}",
                  "ceiling": "Memory Guard: Custom, {gb} GB",
                  "prefill": "Chunked Prefill: an",
                  "idle": "Modell nach {min} Minuten ohne Anfrage entladen",
                  "more": "Mehr dazu im Handbuch"},
        "other_model": "Liest und antwortet mit {model} auf {address}",
        "voice_needs": "Sprachnachrichten brauchen einen Sprach-Server:",
        "leaves_home": "Dieser Server steht außerhalb eures Heimnetzes. Eure Briefe, Notizen, "
                       "Fragen und Sprachnachrichten gehen dorthin.",
        "connect": "Verbinden", "back": "Zurück",
        "elsewhere": "Trag den Server an dem Mac ein, auf dem famstack läuft.",
        "ready_heading": "Eure KI ist bereit",
        "not_asked_heading": "Eure KI läuft",
        "not_asked": "famstack hat eurer KI mit dieser Einstellung noch keine Frage gestellt. "
                     "Frag sie jetzt: famstack stellt ihr eine kurze Frage. Die erste Antwort "
                     "kann eine Minute dauern, während das Modell lädt.",
        "ask": "Eure KI jetzt fragen",
        "no_answer_heading": "Eure KI antwortet nicht",
        "no_answer": {"server": "Eure KI nennt ihre Modelle, hat aber eine kurze Frage nicht "
                                "beantwortet. Die Meldung:",
                      "model": "Eure KI hat das Modell nicht, das famstack nutzt ({model}). "
                               "Die Meldung:",
                      "key": "Eure KI hat den Schlüssel von famstack abgelehnt. Die Meldung:",
                      "timeout": "Eure KI hat eine kurze Frage nicht rechtzeitig beantwortet. "
                                 "Ein großes Modell lädt beim ersten Mal länger: Prüf in einer "
                                 "Minute noch einmal. Die Meldung:",
                      "unreachable": "Eure KI hat eine kurze Frage nicht beantwortet. Die "
                                     "Meldung:",
                      "down": "Unter der Adresse eurer KI antwortet nichts.",
                      "omlx_app": "Auf diesem Mac läuft die oMLX-App, deshalb startet das oMLX "
                                  "von famstack nicht daneben: Zwei KI-Server auf dem Grafikchip "
                                  "eines Macs können den Mac abstürzen lassen. Beende die "
                                  "oMLX-App (ihr Symbol in der Menüleiste, dann Beenden) und "
                                  "prüf dann noch einmal."},
        "answer_again": "Erneut prüfen", "other_again": "Anderen Server eintragen",
        "install_instead": "Stattdessen famstack oMLX installieren lassen",
        "can": {"reads": "Liest Briefe und beantwortet Fragen",
                "hears": "Versteht Sprachnachrichten",
                "hears_off": "Sprachnachrichten: Die Sprachfunktionen sind aus", "speaks": "Spricht"},
    },
}


def words(language: str) -> dict:
    """The step's words in `language`: German for "de", English otherwise.

        words("de")["connect"]    # "Verbinden"
    """
    return WORDS["de" if language == "de" else "en"]


def sub_step(*, reads: bool, answer: dict | None, tile: dict | None, provider: str,
             choice: str) -> str:
    """Which part of step 4 the page shows, one of `SUB_STEPS`, from the
    state alone (the table in the module's header). `reads` is whether the
    stack's state says an AI reads, `answer` what the AI answered the
    stack's one short question for the current `[ai]` (`{"ok": True,
    ...}` or `{"ok": False, "reason": "server", "server_error": "..."}`,
    None while it was not asked; `{"ok": False, "reason": "omlx_app"}`
    while famstack's oMLX steps aside for the oMLX app), `provider` what
    `[ai] provider` says
    ("managed", "external", or "" before a route), `choice` is `?ai=`.

        sub_step(reads=False, answer=None, tile=None, provider="", choice="")  # "choose"
        sub_step(reads=False, answer=None, tile=None, provider="managed", choice="")
        # "install": the run has not finished yet
        sub_step(reads=True, answer={"ok": False, "server_error": "HTTP 500: ..."},
                 tile=None, provider="external", choice="")                    # "no_answer"
    """
    if reads and answer and answer["ok"]:
        return "ready"
    if tile and tile["state"] == stack_widget.ADDING:
        return "starting"
    if choice == "other":
        return "other"
    if choice == "install":
        return "install"
    if reads:
        return "no_answer" if answer else "not_asked"
    # `[ai]` names a server, so the route is taken and never asked again.
    if provider == "external":
        return "no_answer"
    if provider == "managed" and answer and answer.get("reason") == OMLX_APP:
        return "no_answer"
    if provider == "managed":
        return "install"
    return "choose"


def html(sub: str, plan: dict | None, *, here: bool, language: str, page: str,
         answer: dict | None = None, managed: bool = False) -> str:
    """The part of step 4 for `sub`, below the step's heading and text and
    above the AI's tile, which `setup_page.py` adds. `page` is the step's
    own address (`/setup?step=ai`), for the choices and Back. `answer` is
    what the AI answered, for "no_answer"; `managed` says the AI is the
    one the stack runs, so "Check again" starts its run, where a server
    elsewhere is asked again (`stack ai check`). `here` is the Mac's own
    address: only there are the form and Check again of a server.

        html("choose", {}, here=True, language="en", page="/setup?step=ai")
        # '<div class="ai-routes"><div class="setup-box ai-route" data-route="managed" …'
    """
    w = words(language)
    match sub:
        case "choose":
            return _choose(w, page)
        case "install":
            return _install(w, page)
        case "other":
            return _other(w, here, page)
        case "ready":
            return _ready(plan, w, omlx=bool((answer or {}).get("omlx")))
        case "not_asked":
            return _not_asked(w, page, here=here, managed=managed)
        case "no_answer":
            failed = answer if answer and not answer["ok"] else {"reason": "down"}
            return _no_answer(failed, w, page, here=here, managed=managed)
    return ""


def heading(sub: str, language: str) -> str:
    """The step's heading for `sub`, "" for the step's own ("Give famstack its AI").

        heading("choose", "de")    # "Wie soll eure KI laufen?"
    """
    w = words(language)
    return {"choose": w["choose_heading"], "other": w["other_heading"],
            "ready": w["ready_heading"], "not_asked": w["not_asked_heading"],
            "no_answer": w["no_answer_heading"]}.get(sub, "")


# ── Choose ───────────────────────────────────────────────────────────────

def _choose(w: dict, page: str) -> str:
    """The two routes, famstack's oMLX first and recommended. Each is a
    link that only opens its part of the step: nothing changes until the
    admin runs the command or connects a server."""
    boxes = []
    for route, choice in (("managed", "install"), ("server", "other")):
        title, text, button = w["routes"][route]
        mark = (f' data-recommended><p class="ai-route-mark">{_e(w["recommended"])}</p>'
                if route == "managed" else ">")
        kind = "go" if route == "managed" else "soft"
        boxes.append(f'<div class="setup-box ai-route" data-route="{route}"{mark}'
                     f'<h2 class="setup-subheading">{_e(title)}</h2>'
                     f'<p class="setup-note">{_e(text)}</p>'
                     f'<div class="setup-act"><a class="fs-button" data-kind="{kind}" '
                     f'href="{_e(_with(page, choice))}">{_e(button)}</a></div></div>')
    return '<div class="ai-routes">' + "".join(boxes) + "</div>"


# ── Route 1: famstack installs oMLX ──────────────────────────────────────

def _install(w: dict, page: str) -> str:
    """Below the terminal's box (`setup_page.py`, with `MANAGED_COMMAND`):
    route 2 as the way out, for a Mac where the command cannot run."""
    return (f'<p class="setup-note"><a class="fs-link ai-other-link" '
            f'href="{_e(_with(page, "other"))}">{_e(w["server_instead"])}</a></p>')


# ── Route 2: your own AI server ──────────────────────────────────────────

def _other(w: dict, here: bool, page: str) -> str:
    """The address (filled with `SERVER_ADDRESS`), the key, Connect and
    Back, and that famstack is tuned for oMLX. Only on this Mac: anywhere
    else the dashboard is plain HTTP on the home network, and a key typed
    there would cross the Wi-Fi in the clear (`access.mac_only`). The
    model's list stays hidden until the server names several; the script
    fills it. The errors' words go along in `data-errors`, by the `reason`
    the route answers."""
    note = f'<p class="setup-note ai-omlx-note">{_e(w["omlx_note"])}</p>'
    if not here:
        return note + f'<p class="setup-note ai-elsewhere">{_e(w["elsewhere"])}</p>'
    return (note + f'<form class="setup-box ai-other" data-done="{_e(page)}" '
            f'data-errors="{_e(json.dumps(w["other_errors"]))}">'
            f'<label class="fs-field"><span class="fs-field-label">{_e(w["address"])}</span>'
            f'<input class="fs-field-input" type="text" name="address" '
            f'value="{_e(SERVER_ADDRESS)}" autocomplete="off" spellcheck="false" required>'
            f'<span class="fs-field-help">{_e(w["address_help"])}</span></label>'
            f'<label class="fs-field"><span class="fs-field-label">{_e(w["key"])}</span>'
            f'<input class="fs-field-input" type="password" name="api_key" autocomplete="off" '
            f'spellcheck="false">'
            f'<span class="fs-field-help">{_e(w["key_help"])}</span></label>'
            # A plain wrapper carries `hidden`: `.fs-field` sets `display`,
            # which would show a hidden label anyway.
            f'<div class="ai-other-models" hidden><label class="fs-field">'
            f'<span class="fs-field-label">{_e(w["model"])}</span>'
            f'<select class="fs-field-input" name="model"></select></label></div>'
            f'<p class="ai-error" role="alert" hidden></p>'
            f'<p class="setup-note">{_e(w["other_kept"])}</p>'
            f'<div class="setup-act"><button class="fs-button" data-kind="go" type="submit">'
            f'{_e(w["connect"])}</button>'
            f'<a class="fs-button" data-kind="soft" href="{_e(page)}">{_e(w["back"])}</a>'
            f'</div></form>')


# ── Ready ────────────────────────────────────────────────────────────────

def _ready(plan: dict | None, w: dict, *, omlx: bool) -> str:
    """What the AI can do: reading always, hearing and speaking with speech.
    A server elsewhere (`plan["other"]`) has its own list (`_ready_other`),
    and, when it answered as an oMLX (`omlx`), the settings recommended
    for this Mac (`_hints`)."""
    if other := (plan or {}).get("other"):
        return _ready_other(other, w) + (_hints(plan or {}, w) if omlx else "")
    can = w["can"]
    speech = bool((plan or {}).get("speech", True))
    items = [("done", can["reads"])]
    items += [("done", can["hears"]), ("done", can["speaks"])] if speech else \
        [("off", can["hears_off"])]
    marks = {"done": "✓", "off": "○"}
    return ('<ul class="ai-can">' + "".join(
        f'<li data-status="{status}"><span aria-hidden="true">{marks[status]}</span> {_e(text)}</li>'
        for status, text in items) + "</ul>")


def _ready_other(other: dict, w: dict) -> str:
    """A server elsewhere: the model it reads with, then reading, and
    hearing when voice messages have somewhere to go; without, where the
    docs say how to name a speech server. Speaking is not listed: the
    house voice comes with the stack's own AI, which this route skips.
    Then that famstack is tuned for oMLX."""
    can = w["can"]
    line = w["other_model"].format(model=other["model"] or "?", address=other["address"])
    if other["voice"]:
        hears = f'<li data-status="done"><span aria-hidden="true">✓</span> {_e(can["hears"])}</li>'
    else:
        hears = (f'<li data-status="off"><span aria-hidden="true">○</span> {_e(w["voice_needs"])} '
                 f'<a class="fs-link" href="{_e(SPEECH_SERVER_DOCS)}" target="_blank" '
                 f'rel="noopener"><code>stack ai switch --whisper</code></a></li>')
    away = ("" if other.get("home", True) else
            f'<p class="setup-note ai-leaves-home" role="note">{_e(w["leaves_home"])}</p>')
    return (f'<p class="setup-text ai-other-model">{_e(line)}</p>{away}<ul class="ai-can">'
            f'<li data-status="done"><span aria-hidden="true">✓</span> {_e(can["reads"])}</li>'
            f'{hears}</ul><p class="setup-note ai-omlx-note">{_e(w["omlx_note"])}</p>')


def _hints(plan: dict, w: dict) -> str:
    """The settings recommended for this Mac, for an oMLX famstack does not
    run: the profile's chat model, the speech models with speech on, and
    oMLX's memory settings on a small Mac (`plan["memory"]`). Hints only:
    the admin sets them in oMLX, famstack changes nothing there.

        _hints({"speech": False, "models": [{"role": "chat", "name": "Qwen3.5 9B"}, ...],
                "memory": {"ceiling_gb": 9.0, "idle_minutes": 10}}, words("en"))
        # '<div class="setup-box ai-hints">…<li data-hint="chat">Reading and answering: Qwen3.5 9B</li>…'
    """
    h = w["hints"]
    speech = bool(plan.get("speech"))
    rows = [(m["role"], h[m["role"]].format(name=m["name"])) for m in plan.get("models", [])
            if m["role"] == "chat" or speech]
    if memory := plan.get("memory"):
        rows += [("ceiling", h["ceiling"].format(gb=f'{memory["ceiling_gb"]:g}')),
                 ("prefill", h["prefill"]),
                 ("idle", h["idle"].format(min=memory["idle_minutes"]))]
    items = "".join(f'<li data-hint="{kind}">{_e(text)}</li>' for kind, text in rows)
    return (f'<div class="setup-box ai-hints"><p class="setup-note">{_e(h["intro"])}</p>'
            f'<ul class="ai-hint-list">{items}</ul>'
            f'<p class="setup-note"><a class="fs-link" href="{_e(OMLX_SETTINGS_DOCS)}" '
            f'target="_blank" rel="noopener">{_e(h["more"])}</a></p></div>')


# ── Does the AI answer ───────────────────────────────────────────────────

def _not_asked(w: dict, page: str, *, here: bool, managed: bool) -> str:
    """The AI reads, and nobody asked it a question for its current
    `[ai]`: say so, and offer the question (`_ask`)."""
    return (f'<div class="setup-box ai-not-asked"><p class="setup-note">{_e(w["not_asked"])}</p>'
            f'{_ask(w, page, w["ask"], here=here, managed=managed, kind="soft")}</div>')


def _no_answer(answer: dict, w: dict, page: str, *, here: bool, managed: bool) -> str:
    """The AI did not answer: what kind of failure it is (the answer's
    `reason`, `stacklets/ai/answer.py`, or "down" when the stack's own
    check finds nothing at the address), the server's own error in one
    line, then the ways on: the question again (`_ask`), another server,
    and for a server elsewhere famstack's oMLX instead. Never a download:
    famstack changes nothing on a server it does not run."""
    reason = answer.get("reason", "")
    said = w["no_answer"].get(reason, w["no_answer"]["unreachable"])
    error = f'<p class="setup-note">{_e(said.format(model=answer.get("model") or "?"))}</p>'
    if answer.get("server_error"):
        error += f'<p class="ai-error"><code>{_e(answer["server_error"])}</code></p>'
    ways = [(w["other_again"], _with(page, "other"))]
    if not managed:
        ways.append((w["install_instead"], _with(page, "install")))
    links = "".join(f'<p class="setup-note"><a class="fs-link" href="{_e(href)}">{_e(label)}</a></p>'
                    for label, href in ways)
    return (f'<div class="setup-box ai-no-answer">{error}'
            f'{_ask(w, page, w["answer_again"], here=here, managed=managed)}{links}</div>')


def _ask(w: dict, page: str, label: str, *, here: bool, managed: bool,
         kind: str = "go") -> str:
    """The question again. The AI the stack runs: a button that starts its
    run as the AI's tile does (`FsStack`, `tile-add`), which ends by
    asking; the step's AI tile follows the run. A server elsewhere: a
    button the page's script turns into `stack ai check` through the
    dashboard (`ai-step.js`), only on this Mac. `kind` is the button's
    weight: "soft" beside the step's own way on."""
    if managed:
        return (f'<div class="setup-act"><button class="fs-button tile-add" type="button" '
                f'data-kind="{kind}" data-target="ai">{_e(label)}</button></div>')
    if not here:
        return ""
    return (f'<div class="setup-act"><button class="fs-button ai-check-again" type="button" '
            f'data-kind="{kind}" data-done="{_e(page)}">{_e(label)}</button></div>')


# ── Small helpers ────────────────────────────────────────────────────────

def _with(page: str, choice: str) -> str:
    return f"{page}&ai={choice}" if "?" in page else f"{page}?ai={choice}"


def _e(text: str) -> str:
    return escape(str(text), quote=True)
