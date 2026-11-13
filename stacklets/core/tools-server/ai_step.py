"""The AI step of the setup page: the AI from the terminal, or a server elsewhere.

    import ai_step

    sub = ai_step.sub_step(reads=False, answer=None, tile=ai_tile, plan=plan, choice="")
    sub                                              # "choose", "install", "other", ...
    ai_step.html(sub, plan, here=True, language="en", page="/setup?step=ai")

Step 4 of `/setup` (`setup_page.py`) sets up the AI. "Install oMLX for me"
shows the command for the Mac's terminal, and the AI's tile follows that
run. "Use another AI server" is for a family that already runs one (the
oMLX app, another Mac, Ollama, LM Studio, a hosted service): an address
and a key, and Connect does what `stack ai switch <address> --key <key>`
does. Nothing is downloaded or changed on that server.

Every sub-step follows from the state, never from what the page did
before, so a reload, a second browser or a return after an hour shows the
same one:

    the AI's tile follows a run ──────────────────────▶ starting  (its tile)
    reads, answered ──────────────────────────────────▶ ready
    reads, not asked for this [ai] ───────────────────▶ not_asked (ask it; done all the same)
    reads, did not answer ────────────────────────────▶ no_answer (its error)
    ?ai=other ────────────────────────────────────────▶ other     (another server)
    the AI stacklet did not answer, or ?ai=install ───▶ install   (the terminal)
    otherwise ────────────────────────────────────────▶ choose

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
refused key, no answer in time) and the server's own error in one line.
"Check again" starts the AI's run as its tile does, when the ai stacklet
is set up; a server elsewhere is asked again through "Use another AI
server", which runs `stack ai switch`.

`plan` is the AI stacklet's answer, `stack ai recommended show` through
the host API (`stacklets/ai/recommended.py`): `memory_gb`, `profile`,
`speech`, the recommended `models`, and `other`, the server elsewhere
that `[ai]` names (`address`, `model`, `voice`, `home`), or None.

The page's script (`dashboard/ai-step.js`) does the rest: another
server's Connect posts the address and the key to
`/dashboard/api/ai/switch`, only from this Mac, as every change answers
(`access.mac_only`), which runs `stack ai switch` through the host API;
a server that asks for a model gets a list to choose from.

Once a server elsewhere reads, "ready" says which model it reads with,
whether voice messages have a speech server, and when the server is
outside the home network that what the stack sends goes there.

Pure: no web framework, no I/O. The words are here, English and German.
"""

from __future__ import annotations

import json
from html import escape

import stack_widget

__all__ = ["SUB_STEPS", "CHOICES", "sub_step", "heading", "html", "words"]

SUB_STEPS = ("ready", "not_asked", "no_answer", "starting", "other", "install", "choose")
# What the admin chose on the page, in `?ai=`: the terminal, or a server elsewhere.
CHOICES = ("install", "other")
# Where the admin guide explains a speech server for an AI server elsewhere.
SPEECH_SERVER_DOCS = ("https://github.com/famstack-dev/famstack/blob/main/docs/admin-guide.md"
                      "#ai-on-another-machine-or-a-hosted-provider")
WORDS = {
    "en": {
        "instead": "Install oMLX for me instead (20 to 30 minutes in the terminal)",
        "other": "Use another AI server (another Mac, Ollama, LM Studio, a hosted service)",
        "other_heading": "Connect another AI server", "address": "Address",
        "address_help": "For example 192.168.1.20:11434 or https://api.example.com",
        "key_optional": "API key (optional)", "key_optional_help": "Only if the server asks for one.",
        "model": "Model for reading and answering",
        "other_kept": "famstack keeps the key on this Mac and uses it only to talk to this server. "
                      "Nothing is downloaded or changed on the server.",
        "other_errors": {"key": "The server did not accept this key, or it needs one.",
                         "unreachable": "Nothing answers at this address. Check it, and that the "
                                        "server runs.",
                         "model": "The server has several models. Choose the one for reading "
                                  "and answering.",
                         "failed": "That did not work. Try again."},
        "other_model": "Reads and answers with {model} on {address}",
        "voice_needs": "Voice messages need a speech server:",
        "leaves_home": "This server is outside your home network. Your letters, notes, questions "
                       "and voice messages go there.",
        "connect": "Connect", "elsewhere": "Enter the key on the Mac that runs famstack.",
        "ready_heading": "Your AI is ready",
        "not_asked_heading": "Your AI runs",
        "not_asked": {"start": "famstack has not asked your AI a question with its current "
                               "setting yet. Ask it now: its start asks one short question. The "
                               "first answer can take a minute while the model loads.",
                      "connect": "famstack has not asked your AI a question with its current "
                                 "setting yet. Connect it again with “Use another AI server”: "
                                 "the connection asks one short question."},
        "ask": "Ask your AI now",
        "no_answer_heading": "Your AI does not answer",
        "no_answer": {"server": "Your AI lists its models, but it failed to answer a short "
                                "question. It said:",
                      "model": "Your AI does not have the model famstack asks for ({model}). "
                               "Choose another model with “Use another AI server”, or download "
                               "it there. It said:",
                      "key": "Your AI refused famstack's API key. Enter the key it uses with "
                             "“Use another AI server”. It said:",
                      "timeout": "Your AI did not answer a short question in time. A large "
                                 "model can take longer to load the first time: check again in "
                                 "a minute. It said:",
                      "unreachable": "Your AI did not answer a short question. It said:"},
        "answer_again": "Check again",
        "can": {"reads": "Reads letters and answers questions",
                "hears": "Understands voice messages",
                "hears_off": "Voice messages: speech is off", "speaks": "Speaks"},
    },
    "de": {
        "instead": "Stattdessen oMLX von famstack installieren lassen (20 bis 30 Minuten im Terminal)",
        "other": "Einen anderen KI-Server verwenden (ein anderer Mac, Ollama, LM Studio, ein "
                 "Online-Dienst)",
        "other_heading": "Verbinde einen anderen KI-Server", "address": "Adresse",
        "address_help": "Zum Beispiel 192.168.1.20:11434 oder https://api.example.com",
        "key_optional": "API-Schlüssel (optional)",
        "key_optional_help": "Nur wenn der Server einen verlangt.",
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
        "other_model": "Liest und antwortet mit {model} auf {address}",
        "voice_needs": "Sprachnachrichten brauchen einen Sprach-Server:",
        "leaves_home": "Dieser Server steht außerhalb eures Heimnetzes. Eure Briefe, Notizen, "
                       "Fragen und Sprachnachrichten gehen dorthin.",
        "connect": "Verbinden",
        "elsewhere": "Gib den Schlüssel an dem Mac ein, auf dem famstack läuft.",
        "ready_heading": "Eure KI ist bereit",
        "not_asked_heading": "Eure KI läuft",
        "not_asked": {"start": "famstack hat eurer KI mit dieser Einstellung noch keine Frage "
                               "gestellt. Frag sie jetzt: Beim Start stellt famstack ihr eine "
                               "kurze Frage. Die erste Antwort kann eine Minute dauern, während "
                               "das Modell lädt.",
                      "connect": "famstack hat eurer KI mit dieser Einstellung noch keine Frage "
                                 "gestellt. Verbinde sie noch einmal mit „Einen anderen "
                                 "KI-Server verwenden“: Beim Verbinden stellt famstack ihr eine "
                                 "kurze Frage."},
        "ask": "Eure KI jetzt fragen",
        "no_answer_heading": "Eure KI antwortet nicht",
        "no_answer": {"server": "Eure KI nennt ihre Modelle, hat aber eine kurze Frage nicht "
                                "beantwortet. Die Meldung:",
                      "model": "Eure KI hat das Modell nicht, das famstack nutzt ({model}). "
                               "Wähl mit „Einen anderen KI-Server verwenden“ ein anderes "
                               "Modell, oder lade es dort herunter. Die Meldung:",
                      "key": "Eure KI hat den API-Schlüssel von famstack abgelehnt. Trag mit "
                             "„Einen anderen KI-Server verwenden“ den Schlüssel ein, den sie "
                             "nutzt. Die Meldung:",
                      "timeout": "Eure KI hat eine kurze Frage nicht rechtzeitig beantwortet. "
                                 "Ein großes Modell lädt beim ersten Mal länger: Prüf in einer "
                                 "Minute noch einmal. Die Meldung:",
                      "unreachable": "Eure KI hat eine kurze Frage nicht beantwortet. Die "
                                     "Meldung:"},
        "answer_again": "Noch einmal prüfen",
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


def sub_step(*, reads: bool, answer: dict | None, tile: dict | None, plan: dict | None,
             choice: str) -> str:
    """Which part of step 4 the page shows, one of `SUB_STEPS`, from the
    state alone (the table in the module's header). `reads` is whether the
    stack's state says an AI reads, `answer` what the AI answered the
    stack's one short question for the current `[ai]` (`{"ok": True,
    ...}` or `{"ok": False, "reason": "server", "server_error": "..."}`,
    None while it was not asked), `choice` is `?ai=`.

        sub_step(reads=False, answer=None, tile=None, plan={}, choice="")  # "choose"
        sub_step(reads=True, answer=None, tile=None, plan=None, choice="")
        # "not_asked"
        sub_step(reads=True, answer={"ok": False, "server_error": "HTTP 500: ..."},
                 tile=None, plan=None, choice="")                    # "no_answer"
    """
    if reads and answer and answer["ok"]:
        return "ready"
    if tile and tile["state"] == stack_widget.ADDING:
        return "starting"
    if reads and choice not in CHOICES:
        return "no_answer" if answer else "not_asked"
    if choice == "other":
        return "other"
    if plan is None or choice == "install":
        return "install"
    return "choose"


def html(sub: str, plan: dict | None, *, here: bool, language: str, page: str,
         answer: dict | None = None, set_up: bool = False) -> str:
    """The part of step 4 for `sub`, below the step's heading and text and
    above the AI's tile, which `setup_page.py` adds. `page` is the step's
    own address (`/setup?step=ai`), for "Check again" and the choices.
    `answer` is what the AI answered, for "no_answer"; `set_up` says the
    ai stacklet is set up, so "Check again" starts its run.

        html("choose", {}, here=True, language="en", page="/setup?step=ai")
        # '<p class="setup-note"><a class="fs-link ai-instead" …>Install oMLX for me …'
    """
    w = words(language)
    match sub:
        case "choose":
            return _choose(w, page)
        case "other":
            return _other(w, here, page)
        case "ready":
            return _ready(plan, w)
        case "not_asked":
            return _not_asked(w, page, set_up=set_up)
        case "no_answer":
            return _no_answer(answer or {}, w, page, set_up=set_up)
    return ""


def heading(sub: str, language: str) -> str:
    """The step's heading for `sub`, "" for the step's own ("Give famstack its AI").

        heading("ready", "de")    # "Eure KI ist bereit"
    """
    w = words(language)
    return {"other": w["other_heading"], "ready": w["ready_heading"],
            "not_asked": w["not_asked_heading"],
            "no_answer": w["no_answer_heading"]}.get(sub, "")


# ── Choose ───────────────────────────────────────────────────────────────

def _choose(w: dict, page: str) -> str:
    """The two routes: the terminal's install, or another AI server."""
    return (f'<p class="setup-note"><a class="fs-link ai-instead" href="{_e(_with(page, "install"))}">'
            f'{_e(w["instead"])}</a></p>'
            f'<p class="setup-note"><a class="fs-link ai-other-link" '
            f'href="{_e(_with(page, "other"))}">{_e(w["other"])}</a></p>')


# ── Another AI server ────────────────────────────────────────────────────

def _other(w: dict, here: bool, page: str) -> str:
    """The address, the key and Connect, only on this Mac: anywhere else
    the dashboard is plain HTTP on the home network, and a key typed there
    would cross the Wi-Fi in the clear (`access.mac_only`). The model's
    list stays hidden until the server names several; the script fills
    it. The errors' words go along in `data-errors`, by the `reason` the
    route answers."""
    if not here:
        return f'<p class="setup-note ai-elsewhere">{_e(w["elsewhere"])}</p>'
    return (f'<form class="setup-box ai-other" data-done="{_e(page)}" '
            f'data-errors="{_e(json.dumps(w["other_errors"]))}">'
            f'<label class="fs-field"><span class="fs-field-label">{_e(w["address"])}</span>'
            f'<input class="fs-field-input" type="text" name="address" value="" '
            f'autocomplete="off" spellcheck="false" required>'
            f'<span class="fs-field-help">{_e(w["address_help"])}</span></label>'
            f'<label class="fs-field"><span class="fs-field-label">{_e(w["key_optional"])}</span>'
            f'<input class="fs-field-input" type="password" name="api_key" autocomplete="off" '
            f'spellcheck="false">'
            f'<span class="fs-field-help">{_e(w["key_optional_help"])}</span></label>'
            # A plain wrapper carries `hidden`: `.fs-field` sets `display`,
            # which would show a hidden label anyway.
            f'<div class="ai-other-models" hidden><label class="fs-field">'
            f'<span class="fs-field-label">{_e(w["model"])}</span>'
            f'<select class="fs-field-input" name="model"></select></label></div>'
            f'<p class="ai-error" role="alert" hidden></p>'
            f'<p class="setup-note">{_e(w["other_kept"])}</p>'
            f'<div class="setup-act"><button class="fs-button" data-kind="go" type="submit">'
            f'{_e(w["connect"])}</button></div></form>')


# ── Ready ────────────────────────────────────────────────────────────────

def _ready(plan: dict | None, w: dict) -> str:
    """What the AI can do: reading always, hearing and speaking with speech.
    A server elsewhere (`plan["other"]`) has its own list (`_ready_other`)."""
    if other := (plan or {}).get("other"):
        return _ready_other(other, w)
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
    house voice comes with the stack's own AI, which this route skips."""
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
            f'{hears}</ul>')


# ── Does the AI answer ───────────────────────────────────────────────────

def _not_asked(w: dict, page: str, *, set_up: bool) -> str:
    """The AI reads, and nobody asked it a question for its current
    `[ai]`: say so, and offer the question (`_ask_again`): its start, or
    for a server elsewhere without one, connecting it again."""
    said = w["not_asked"]["start" if set_up else "connect"]
    return (f'<div class="setup-box ai-not-asked"><p class="setup-note">{_e(said)}</p>'
            f'{_ask_again(w, page, w["ask"], set_up=set_up, kind="soft")}</div>')


def _no_answer(answer: dict, w: dict, page: str, *, set_up: bool) -> str:
    """The AI did not answer: what kind of failure it is (the answer's
    `reason`, `stacklets/ai/answer.py`), the server's own error in one
    line, then the question again (`_ask_again`). A missing model, a
    refused key and no answer in time say what they are."""
    reason = answer.get("reason", "")
    said = w["no_answer"].get(reason, w["no_answer"]["unreachable"])
    error = (f'<p class="setup-note">{_e(said.format(model=answer.get("model") or "?"))}</p>'
             f'<p class="ai-error"><code>{_e(answer.get("server_error", ""))}</code></p>')
    return (f'<div class="setup-box ai-no-answer">{error}'
            f'{_ask_again(w, page, w["answer_again"], set_up=set_up)}</div>')


def _ask_again(w: dict, page: str, label: str, *, set_up: bool, kind: str = "go") -> str:
    """The question again, and another AI server. With the ai stacklet set
    up, a button that starts its run as the AI's tile does (`FsStack`,
    `tile-add`), which ends by asking; the step's AI tile follows the
    run. A server elsewhere without the stacklet is asked by "Use another
    AI server", which runs `stack ai switch` and asks. `kind` is the
    button's weight: "soft" beside the step's own way on."""
    other = (f'<p class="setup-note"><a class="fs-link ai-other-link" '
             f'href="{_e(_with(page, "other"))}">{_e(w["other"])}</a></p>')
    if not set_up:
        return other
    return (f'<div class="setup-act"><button class="fs-button tile-add" type="button" '
            f'data-kind="{kind}" data-target="ai">{_e(label)}</button></div>' + other)


# ── Small helpers ────────────────────────────────────────────────────────

def _with(page: str, choice: str) -> str:
    return f"{page}&ai={choice}" if "?" in page else f"{page}?ai={choice}"


def _e(text: str) -> str:
    return escape(str(text), quote=True)
