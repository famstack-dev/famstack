"""The AI step of the setup page: the AI from the oMLX app, the terminal, or a server elsewhere.

    import ai_step

    sub = ai_step.sub_step(reads=False, answer=None, tile=ai_tile, plan=plan, choice="")
    sub                                              # "choose", "connect", "models", ...
    ai_step.html(sub, plan, here=True, language="en", page="/setup?step=ai")

Step 4 of `/setup` (`setup_page.py`) sets up the AI. The design's route is
the oMLX app: a Mac app that runs the AI server on this Mac. The step finds
it, takes its API key, shows what this Mac's memory profile recommends,
downloads that with a bar per model and starts the AI. The route of
earlier releases stays one link away: "Install oMLX for me instead" shows
`./stack up ai` for the Mac's terminal, and the AI's tile follows that run.
A second link, "Use another AI server", is for a family that already runs
one (another Mac, Ollama, LM Studio, a hosted service): an address and a
key, and Connect does what `stack ai switch <address> --key <key>` does.
Nothing is downloaded or changed on that server.

Every sub-step follows from the state, never from what the page did
before, so a reload, a second browser or a return after an hour shows the
same one:

    reads, answered once ─────────────────────────────▶ ready     (4e)
    reads, not asked yet, or ?ai=answer ──────────────▶ answering ("checks")
    reads, did not answer ────────────────────────────▶ no_answer (its error)
    set up, its health check fails, not asked yet ────▶ answering ("checks")
    the AI's tile follows a run ──────────────────────▶ starting  (its tile)
    ?ai=other ────────────────────────────────────────▶ other     (another server)
    the AI stacklet did not answer, or ?ai=install ───▶ install   (the terminal)
    not connected, ?ai=check ─────────────────────────▶ check     ("I installed oMLX")
    nothing answers, oMLX from Homebrew ──────────────▶ install   (the terminal)
    not connected, the app answers, ?ai=connect ──────▶ connect   (4b)
    not connected ────────────────────────────────────▶ choose    (4a)
    connected, a model downloading ───────────────────▶ downloading (4d)
    connected ────────────────────────────────────────▶ models    (4c)

"Reads" is what the stack's state says (`stack list`: set up and online,
or a server elsewhere whose models answer); a server can list its models
and still fail every completion, as an oMLX built for a newer macOS does.
So "ready" waits for one real answer (`answer`, what `stack ai check`
said, which the setup page keeps per AI server). Until it is in, the step
says that it checks, and the open page watches the address the caller
passes (`watch`), which runs the check and answers once it has a result;
the page then reads itself again. An AI that did not answer shows the
server's own error in one line, with what kind of failure it is (the
server cannot compute, a missing model, a refused key); the oMLX
download for this Mac's macOS only when the server cannot compute and
oMLX is on this Mac; another AI server, and "Check again" (`?ai=answer`).
A check that ran out of time is no answer: the step goes on checking,
because a large model may still be loading.

"I installed oMLX" first tries to connect the app at its default address
without a key, as Connect of 4b does. When that is not enough the page
opens `?ai=check`, which says what the host found and what to do next:

    the app answers and wants a key ───▶ the address and key fields, and
                                         where the app shows its key
    the app answers without a key ─────▶ the same fields, to connect it
    oMLX installed, nothing answers ───▶ "open the app", the same fields
                                         filled with the default address
    no oMLX on this Mac ───────────────▶ "found no oMLX", the download

The app answers at its default address, or at the address of the stack's
own engine, where the settings file it shares with that engine may put
it; the check fills in the address it answers at.

The fields are the form of "Use another AI server", so Connect checks
the address and the key as `stack ai switch` does and stores nothing
when the server refuses them. The app may listen on another port than
its default: it reads its port from the settings file the Homebrew oMLX
shares, so a Mac where the stack once ran that oMLX has the app on 42060.

`plan` is the AI stacklet's answer, `stack ai recommended show` through
the host API (`stacklets/ai/recommended.py`): `found` (the app's address),
`connected`, `homebrew` (nothing answers and oMLX is installed with
Homebrew, which `./stack up ai` sets up), `installed` (oMLX is on this
Mac: the app in Applications or the formula), `check` (the address the
check fills in, whether the app `answers` there, whether it `needs_key`),
`memory_gb`, `profile`, `speech` and `models`, a row per
role (chat, listen, speak) with its `state` (on_mac, downloading, failed,
to_download), `size` and `done` in bytes, `app`, where "Download oMLX"
points (`stacklets/ai/omlx_app.py`), and `other`, the server
elsewhere that `[ai]` names (`address`, `model`, `voice`, `home`), or None.

The page's script (`dashboard/ai-step.js`) does the rest: Connect posts
the key to the setup forms' route (`/dashboard/api/configure/ai`), and only
from this Mac, as every change answers (`access.mac_only`); Download posts the
speech choice (`/dashboard/api/ai/download`); the bars ask
`/dashboard/api/ai` until nothing downloads, then the script starts
`stack up ai`, whose run the tile follows until 4e. A Mac that already
has every model gets "Continue" instead of "Download". Another server's
Connect posts the address and the key to `/dashboard/api/ai/switch`, again
only from this Mac, which runs `stack ai switch` through the host API; a
server that asks for a model gets a list to choose from.

Once a server elsewhere reads, 4e says which model it reads with, whether
voice messages have a speech server, and when the server is outside the
home network that what the stack sends goes there.

Pure: no web framework, no I/O. The words are here, English and German.
"""

from __future__ import annotations

import json
from html import escape

import stack_widget

__all__ = ["SUB_STEPS", "CHOICES", "sub_step", "heading", "html", "words", "total", "minutes"]

SUB_STEPS = ("ready", "answering", "no_answer", "starting", "other", "install", "connect", "check",
             "choose", "downloading", "models")
# What the admin chose on the page, in `?ai=`: the app's key, the terminal,
# a server elsewhere, the app's check, or asking the AI again.
CHOICES = ("connect", "install", "other", "check", "answer")
# Where the admin guide explains a speech server for an AI server elsewhere.
SPEECH_SERVER_DOCS = ("https://github.com/famstack-dev/famstack/blob/main/docs/admin-guide.md"
                      "#ai-on-another-machine-or-a-hosted-provider")
# How fast a download goes, for "about N minutes": the copy deck's own
# rate, 7.7 GB in 13 minutes. A guess, so it is said as "about".
GB_PER_MINUTE = 0.59
# A gigabyte as oMLX counts it ("5.8 GB" for 6,227,702,579 bytes), so the
# page says the size the oMLX app says.
GB = 1024 ** 3
# The roles that come with speech, as `recommended.py` names them.
SPEECH_ROLES = ("listen", "speak")
SMALL = "small"

WORDS = {
    "en": {
        "found": "The oMLX app runs on this Mac", "use": "Use the oMLX app",
        "homebrew": "oMLX is installed on this Mac with Homebrew",
        "get": "Get the oMLX app",
        "get_text": "Download it, drag it to Applications and open it. Then come back here.",
        "get_app": "Download oMLX", "installed": "I installed oMLX",
        "opens_first": "oMLX is installed on this Mac, but it does not answer yet. Open the oMLX "
                       "app. When macOS asks whether to open it, confirm. Then press “I installed "
                       "oMLX” again.",
        "get_macos": "oMLX {version} comes for macOS 15 and for macOS 26 and 27. Choose the "
                     "disk image for your Mac on the release page.",
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
        "connect_heading": "Connect the oMLX app", "key": "API key",
        "key_help": "In the oMLX app: Settings, Security, API Key, Copy.",
        "checked": {"key": "oMLX answers at {address} and wants an API key. It is in the oMLX "
                           "app: Settings, Security, API Key.",
                    "answers": "oMLX answers at {address}. Press Connect.",
                    "silent": "oMLX is installed, but it does not answer at {address}. Open the "
                              "oMLX app, and confirm when macOS asks whether to open it. If it "
                              "uses another address or an API key, enter them here.",
                    "none": "famstack found no oMLX on this Mac."},
        "key_kept": "famstack keeps the key on this Mac and uses it only to talk to the oMLX app.",
        "connect": "Connect", "elsewhere": "Enter the key on the Mac that runs famstack.",
        "download_elsewhere": "Download the models on the Mac that runs famstack.",
        "refused": "The oMLX app did not accept this key.",
        "start_failed": "That did not work: {error}",
        "models_heading": "What your AI gets",
        "recommended": "Recommended for this Mac ({gb} GB):",
        "roles": {"chat": "Reading and answering", "listen": "Understanding voice messages",
                  "speak": "Speaking"},
        "speech": "Speech",
        "speech_small": "Off on 16 GB Macs: speech keeps 1.6 GB busy. You can turn it on later.",
        "on_mac": "Already on this Mac", "failed": "The download failed",
        "total": "{gb} GB to download, about {min} minutes",
        "nothing": "Everything is already on this Mac",
        "download": "Download", "continue": "Continue",
        "downloading_heading": "Downloading your AI",
        "bar": "{name} · {got} of {of} GB", "left": " · about {min} minutes left",
        "leave": "You can leave this page. The download goes on, and this step shows where it "
                 "is when you come back.",
        "go_on": "Continue while it downloads",
        "ready_heading": "Your AI is ready",
        "answering_heading": "famstack checks your AI",
        "answering": "famstack asks your AI one short question. The first answer can take a "
                     "minute while the model loads.",
        "no_answer_heading": "Your AI does not answer",
        "no_answer": {"server": "It lists its models, but it failed to answer a short question. "
                                "It said:",
                      "model": "It does not have the model famstack asks for ({model}). Choose "
                               "another model with “Use another AI server”, or download it "
                               "there. It said:",
                      "key": "It refused famstack's API key. Enter the key it uses with “Use "
                             "another AI server”. It said:",
                      "unreachable": "It did not answer a short question. It said:"},
        "no_answer_omlx": "An oMLX app built for another macOS fails like this. Download the "
                          "app for this Mac, replace the one in Applications and open it. Then "
                          "check again.",
        "answer_again": "Check again",
        "can": {"reads": "Reads letters and answers questions",
                "hears": "Understands voice messages",
                "hears_off": "Voice messages: speech is off", "speaks": "Speaks"},
    },
    "de": {
        "found": "Die oMLX-App läuft auf diesem Mac", "use": "oMLX-App verwenden",
        "homebrew": "oMLX ist auf diesem Mac mit Homebrew installiert",
        "get": "Hol dir die oMLX-App",
        "get_text": "Lade sie herunter, zieh sie in den Ordner „Programme“ und öffne sie. "
                    "Komm dann hierher zurück.",
        "get_app": "oMLX herunterladen", "installed": "Ich habe oMLX installiert",
        "opens_first": "oMLX ist auf diesem Mac installiert, antwortet aber noch nicht. Öffne die "
                       "oMLX-App. Wenn macOS fragt, ob du sie öffnen willst, bestätige das. Klick "
                       "dann noch einmal auf „Ich habe oMLX installiert“.",
        "get_macos": "oMLX {version} gibt es für macOS 15 und für macOS 26 und 27. Wähl auf der "
                     "Release-Seite das Disk-Image für deinen Mac.",
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
        "connect_heading": "Verbinde die oMLX-App", "key": "API-Schlüssel",
        "key_help": "Klick in der oMLX-App auf „Settings“, „Security“, „API Key“ und dann "
                    "auf „Copy“.",
        "checked": {"key": "oMLX antwortet unter {address} und verlangt einen API-Schlüssel. "
                           "Du findest ihn in der oMLX-App unter „Settings“, „Security“, "
                           "„API Key“.",
                    "answers": "oMLX antwortet unter {address}. Klick auf „Verbinden“.",
                    "silent": "oMLX ist installiert, antwortet aber nicht unter {address}. "
                              "Öffne die oMLX-App und bestätige die Frage von macOS, ob du sie "
                              "öffnen willst. Nutzt sie eine andere Adresse oder einen "
                              "API-Schlüssel, trag das hier ein.",
                    "none": "famstack hat auf diesem Mac kein oMLX gefunden."},
        "key_kept": "famstack speichert den Schlüssel auf diesem Mac und nutzt ihn nur für die "
                    "oMLX-App.",
        "connect": "Verbinden",
        "elsewhere": "Gib den Schlüssel an dem Mac ein, auf dem famstack läuft.",
        "download_elsewhere": "Lade die Modelle an dem Mac herunter, auf dem famstack läuft.",
        "refused": "Die oMLX-App hat diesen Schlüssel nicht angenommen.",
        "start_failed": "Das hat nicht geklappt: {error}",
        "models_heading": "Das bekommt eure KI",
        "recommended": "Empfohlen für diesen Mac ({gb} GB):",
        "roles": {"chat": "Lesen und Antworten", "listen": "Sprachnachrichten verstehen",
                  "speak": "Sprechen"},
        "speech": "Sprachfunktionen",
        "speech_small": "Auf Macs mit 16 GB aus: Die Sprachfunktionen belegen dauerhaft 1,6 GB. Du kannst sie später "
                        "einschalten.",
        "on_mac": "Schon auf diesem Mac", "failed": "Der Download ist fehlgeschlagen",
        "total": "{gb} GB zum Herunterladen, etwa {min} Minuten",
        "nothing": "Alles ist schon auf diesem Mac",
        "download": "Herunterladen", "continue": "Weiter",
        "downloading_heading": "Eure KI wird heruntergeladen",
        "bar": "{name} · {got} von {of} GB", "left": " · noch etwa {min} Minuten",
        "leave": "Du kannst diese Seite verlassen. Der Download läuft weiter, und dieser Schritt "
                 "zeigt dir den Stand, wenn du zurückkommst.",
        "go_on": "Weiter zu Fotos und Dokumenten",
        "ready_heading": "Eure KI ist bereit",
        "answering_heading": "famstack prüft gerade eure KI",
        "answering": "famstack stellt eurer KI eine kurze Frage. Die erste Antwort kann eine "
                     "Minute dauern, während das Modell lädt.",
        "no_answer_heading": "Eure KI antwortet nicht",
        "no_answer": {"server": "Sie nennt ihre Modelle, hat aber eine kurze Frage nicht "
                                "beantwortet. Die Meldung:",
                      "model": "Sie hat das Modell nicht, das famstack nutzt ({model}). Wähl "
                               "mit „Einen anderen KI-Server verwenden“ ein anderes Modell, "
                               "oder lade es dort herunter. Die Meldung:",
                      "key": "Sie hat den API-Schlüssel von famstack abgelehnt. Trag mit "
                             "„Einen anderen KI-Server verwenden“ den Schlüssel ein, den sie "
                             "nutzt. Die Meldung:",
                      "unreachable": "Sie hat eine kurze Frage nicht beantwortet. Die Meldung:"},
        "no_answer_omlx": "So scheitert eine oMLX-App, die für eine andere macOS-Version gebaut "
                          "ist. Lade die App für diesen Mac herunter, ersetz die im Ordner "
                          "„Programme“ und öffne sie. Prüf dann noch einmal.",
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
             choice: str, waits: bool = False) -> str:
    """Which part of step 4 the page shows, one of `SUB_STEPS`, from the
    state alone (the table in the module's header). `reads` is whether the
    stack's state says an AI reads, `answer` what `stack ai check` said of
    it (`{"ok": True}` or `{"ok": False, "server_error": "..."}`, None
    while nobody asked), `choice` is `?ai=`. `waits` says the AI is set
    up and the stack's health check of its server fails, as right after
    its run while the server starts: then the step asks the AI itself
    before it offers anything else.

        sub_step(reads=False, answer=None, tile=None,
                 plan={"found": "", "connected": False}, choice="")  # "choose"
        sub_step(reads=True, answer=None, tile=None, plan=None, choice="")
        # "answering"
        sub_step(reads=True, answer={"ok": False, "server_error": "HTTP 500: ..."},
                 tile=None, plan=None, choice="")                    # "no_answer"
    """
    if reads and choice not in ("other", "install"):
        if answer is None or choice == "answer":
            return "answering"
        return "ready" if answer["ok"] else "no_answer"
    if reads and answer and answer["ok"]:
        return "ready"
    if waits and choice not in ("other", "install") and (answer is None or choice == "answer"):
        # Its answer, good or bad, comes with the state it came in; the page
        # read after it goes on from there (`ai_answer.py`).
        return "answering"
    if tile and tile["state"] == stack_widget.ADDING:
        return "starting"
    if choice == "other":
        return "other"
    if plan is None or choice == "install":
        return "install"
    if not plan["connected"] and choice == "check":
        # "I installed oMLX" was pressed: what it checked comes before
        # the Homebrew route.
        return "check"
    if not plan["connected"] and not plan["found"] and plan.get("homebrew") and choice != "connect":
        # `./stack up ai` sets up the oMLX already there; no download needed.
        return "install"
    if not plan["connected"]:
        return "connect" if choice == "connect" and plan["found"] else "choose"
    if any(m["state"] == "downloading" for m in plan["models"]):
        return "downloading"
    return "models"


def total(plan: dict, *, speech: bool) -> int:
    """The bytes still to download for the choice: the rows not on the
    Mac, without the speech rows when `speech` is off.

        total({"models": [{"role": "chat", "state": "to_download", "size": 5_800_000_000},
                          {"role": "listen", "state": "to_download", "size": 1_600_000_000}]},
              speech=False)                                        # 5800000000
    """
    return sum(m["size"] for m in plan["models"]
               if m["state"] != "on_mac" and (speech or m["role"] not in SPEECH_ROLES))


def minutes(size: int) -> int:
    """About how many minutes `size` bytes take, at least one.

        minutes(8_267_812_454)    # 13: 7.7 GB
    """
    return max(1, round(size / GB / GB_PER_MINUTE))


def html(sub: str, plan: dict | None, *, here: bool, language: str, page: str,
         answer: dict | None = None, watch: str = "") -> str:
    """The part of step 4 for `sub`, below the step's heading and text and
    above the AI's tile, which `setup_page.py` adds. `page` is the step's
    own address (`/setup?step=ai`), for "Check again" and the choices.
    `answer` is what the AI's check said, for "no_answer"; `watch` the
    address the open page asks while "answering" waits for it.

        html("choose", {"found": "", "connected": False, "models": []},
             here=True, language="en", page="/setup?step=ai")
        # '<div class="setup-box ai-get">…Get the oMLX app…</div>…'
    """
    w = words(language)
    match sub:
        case "choose":
            return _choose(plan or {}, w, page)
        case "install":
            return _homebrew(w) if plan and plan.get("homebrew") else ""
        case "connect":
            return _connect(w, here, page)
        case "check":
            return _check(plan or {}, w, here, page)
        case "other":
            return _other(w, here, page)
        case "models":
            return _models(plan or {}, w, language, here)
        case "downloading":
            return _downloading(plan or {}, w, language)
        case "ready":
            return _ready(plan, w)
        case "answering":
            return _answering(w, watch)
        case "no_answer":
            return _no_answer(plan or {}, answer or {}, w, page)
    return ""


def heading(sub: str, language: str) -> str:
    """The step's heading for `sub`, "" for the step's own ("Give famstack its AI").

        heading("models", "de")    # "Das bekommt eure KI"
    """
    w = words(language)
    return {"connect": w["connect_heading"], "other": w["other_heading"], "models": w["models_heading"],
            "downloading": w["downloading_heading"], "ready": w["ready_heading"],
            "answering": w["answering_heading"],
            "no_answer": w["no_answer_heading"]}.get(sub, "")


# ── 4a: choose ───────────────────────────────────────────────────────────

def _choose(plan: dict, w: dict, page: str) -> str:
    """4a: the oMLX app found here, the app installed but silent (open it,
    then check again), or where to get it. "Download oMLX"
    downloads the disk image of the release the stack is tested with,
    for this Mac's macOS, as the host's plan names it (`plan["app"]`); a
    link out, nothing is loaded from there. Without a disk image for this
    macOS it opens the release's page, and the step says which macOS
    oMLX needs."""
    instead = (f'<p class="setup-note"><a class="fs-link ai-instead" href="{_e(_with(page, "install"))}">'
               f'{_e(w["instead"])}</a></p>'
               f'<p class="setup-note"><a class="fs-link ai-other-link" '
               f'href="{_e(_with(page, "other"))}">{_e(w["other"])}</a></p>')
    if plan.get("found"):
        where = plan["found"].split("://", 1)[-1]
        return (f'<div class="setup-box ai-found"><p class="ai-found-line">'
                f'<span class="dot" data-status="running" aria-hidden="true"></span>'
                f'{_e(w["found"])} · <code>{_e(where)}</code></p>'
                f'<div class="setup-act">{_link(w["use"], _with(page, "connect"))}</div></div>'
                + instead)
    if plan.get("installed"):
        # Installed and silent, as right after a first install: the app
        # starts its server only once macOS's first-open dialog is confirmed.
        return (f'<div class="setup-box ai-installed"><p class="setup-note">{_e(w["opens_first"])}</p>'
                f'<div class="setup-act">{_installed(w, page)}</div></div>' + instead)
    app = plan["app"]
    macos = ("" if app["disk_image"] else
             f'<p class="setup-note ai-get-macos">{_e(w["get_macos"].format(version=app["version"]))}</p>')
    return (f'<div class="setup-box ai-get"><h2 class="setup-subheading">{_e(w["get"])}</h2>'
            f'<p class="setup-note">{_e(w["get_text"])}</p>{macos}<div class="setup-act">'
            f'{_link(w["get_app"], app["url"], external=True)}'
            f'{_installed(w, page)}</div></div>' + instead)


def _installed(w: dict, page: str) -> str:
    """"I installed oMLX": a link to the check, which the page's script
    first turns into a Connect without a key (`data-done` is where a
    connected app goes on)."""
    return (f'<a class="fs-button ai-check" data-kind="soft" data-done="{_e(page)}" '
            f'href="{_e(_with(page, "check"))}">{_e(w["installed"])}</a>')


def _homebrew(w: dict) -> str:
    """The line above the terminal's route when oMLX came from Homebrew."""
    return (f'<div class="setup-box ai-found ai-homebrew"><p class="ai-found-line">'
            f'{_e(w["homebrew"])}</p></div>')


# ── 4b: connect ──────────────────────────────────────────────────────────

def _connect(w: dict, here: bool, page: str) -> str:
    """The key's field, only on this Mac: anywhere else the dashboard is
    plain HTTP on the home network, and a key typed there would cross the
    Wi-Fi in the clear (`access.mac_only`)."""
    if not here:
        return f'<p class="setup-note ai-elsewhere">{_e(w["elsewhere"])}</p>'
    return (f'<form class="setup-box ai-connect" data-done="{_e(page)}">'
            f'<label class="fs-field"><span class="fs-field-label">{_e(w["key"])}</span>'
            f'<input class="fs-field-input" type="password" name="api_key" autocomplete="off" '
            f'spellcheck="false" required>'
            f'<span class="fs-field-help">{_e(w["key_help"])}</span></label>'
            f'<p class="ai-error" role="alert" hidden>{_e(w["refused"])}</p>'
            f'<p class="setup-note">{_e(w["key_kept"])}</p>'
            f'<div class="setup-act"><button class="fs-button" data-kind="go" type="submit">'
            f'{_e(w["connect"])}</button></div></form>')


# ── Another AI server ────────────────────────────────────────────────────

def _other(w: dict, here: bool, page: str) -> str:
    """The address, the key and Connect, only on this Mac, for the same
    reason as the app's key (`_connect`). The model's list stays hidden
    until the server names several; the script fills it. The errors'
    words go along in `data-errors`, by the `reason` the route answers."""
    return _server_form(w, here, page, address="",
                        key=(w["key_optional"], w["key_optional_help"]), kept=w["other_kept"])


def _server_form(w: dict, here: bool, page: str, *, address: str, key: tuple[str, str],
                 kept: str) -> str:
    """The form of "Use another AI server", with the address filled in
    and the key's label and help (`key`) and the line on how the key is
    kept (`kept`) of the server it is for. Only on this Mac."""
    if not here:
        return f'<p class="setup-note ai-elsewhere">{_e(w["elsewhere"])}</p>'
    return (f'<form class="setup-box ai-other" data-done="{_e(page)}" '
            f'data-errors="{_e(json.dumps(w["other_errors"]))}">'
            f'<label class="fs-field"><span class="fs-field-label">{_e(w["address"])}</span>'
            f'<input class="fs-field-input" type="text" name="address" value="{_e(address)}" '
            f'autocomplete="off" spellcheck="false" required>'
            f'<span class="fs-field-help">{_e(w["address_help"])}</span></label>'
            f'<label class="fs-field"><span class="fs-field-label">{_e(key[0])}</span>'
            f'<input class="fs-field-input" type="password" name="api_key" autocomplete="off" '
            f'spellcheck="false">'
            f'<span class="fs-field-help">{_e(key[1])}</span></label>'
            # A plain wrapper carries `hidden`: `.fs-field` sets `display`,
            # which would show a hidden label anyway.
            f'<div class="ai-other-models" hidden><label class="fs-field">'
            f'<span class="fs-field-label">{_e(w["model"])}</span>'
            f'<select class="fs-field-input" name="model"></select></label></div>'
            f'<p class="ai-error" role="alert" hidden></p>'
            f'<p class="setup-note">{_e(kept)}</p>'
            f'<div class="setup-act"><button class="fs-button" data-kind="go" type="submit">'
            f'{_e(w["connect"])}</button></div></form>')


# ── "I installed oMLX": what the step checked ───────────────────────────

def _check(plan: dict, w: dict, here: bool, page: str) -> str:
    """What the host found, in one line, and what to do next: the
    address and key fields while oMLX is on this Mac, else the download.
    The fields are filled with the address the app answered at, else
    with its default address (`plan["check"]`)."""
    case = _checked(plan)
    address = plan.get("check", {}).get("address", "")
    line = (f'<p class="setup-text ai-checked">'
            f'{_e(w["checked"][case]).replace("{address}", _code(address))}</p>')
    if case == "none":
        return line + _choose(plan, w, page)
    return line + _server_form(w, here, page, address=address,
                               key=(w["key"], w["key_help"]), kept=w["key_kept"])


def _checked(plan: dict) -> str:
    """Which case of the check the plan is: "key", "answers", "silent"
    (installed, nothing answers) or "none" (no oMLX on this Mac)."""
    check = plan.get("check", {})
    if check.get("answers"):
        return "key" if check.get("needs_key") else "answers"
    return "silent" if plan.get("installed") else "none"


# ── 4c: what your AI gets ────────────────────────────────────────────────

def _models(plan: dict, w: dict, language: str, here: bool) -> str:
    """The rows, the speech switch and the total. The script recomputes
    the total when the switch moves, from each row's `data-size`. Only on
    this Mac: anywhere else the rows and a note, because the download
    writes `[ai]` into `stack.toml` and only this Mac changes the server
    (the route refuses any other device)."""
    speech = bool(plan.get("speech"))
    rows = "".join(_model_row(m, w, language) for m in plan["models"])
    if not here:
        return (f'<p class="setup-text">{_e(w["recommended"].format(gb=plan.get("memory_gb", 0)))}</p>'
                f'<div class="ai-models"><ul class="ai-rows">{rows}</ul>'
                f'<p class="setup-note ai-download-elsewhere">{_e(w["download_elsewhere"])}</p></div>')
    note = (f'<p class="setup-note ai-speech-note">{_e(w["speech_small"])}</p>'
            if plan.get("profile") == SMALL else "")
    switch = (f'<label class="ai-speech"><input type="checkbox" role="switch" name="speech"'
              f'{" checked" if speech else ""}> {_e(w["speech"])}</label>{note}')
    sizes = {"total": w["total"], "nothing": w["nothing"], "download": w["download"],
             "continue": w["continue"], "failed": w["start_failed"], "rate": GB_PER_MINUTE,
             "decimal": _decimal(language)}
    left = total(plan, speech=speech)
    line = (w["total"].format(gb=_gb(left, language), min=minutes(left)) if left
            else w["nothing"])
    return (f'<p class="setup-text">{_e(w["recommended"].format(gb=plan.get("memory_gb", 0)))}</p>'
            f'<div class="ai-models" data-words="{_e(json.dumps(sizes))}">'
            f'<ul class="ai-rows">{rows}</ul>{switch}'
            f'<p class="ai-total">{_e(line)}</p>'
            f'<div class="setup-act"><button class="fs-button ai-download" data-kind="go" '
            f'type="button" hidden>{_e(w["download"] if left else w["continue"])}</button></div>'
            f'<p class="ai-error" role="alert" hidden></p></div>')


def _model_row(m: dict, w: dict, language: str) -> str:
    on_mac = m["state"] == "on_mac"
    what = (w["on_mac"] if on_mac else
            f'{w["failed"]} · {_gb(m["size"], language)} GB' if m["state"] == "failed" else
            f'{_gb(m["size"], language)} GB')
    return (f'<li class="ai-row" data-role="{_e(m["role"])}" data-state="{_e(m["state"])}" '
            f'data-size="{0 if on_mac else int(m["size"])}">'
            f'<b>{_e(w["roles"].get(m["role"], m["role"]))}</b> '
            f'<span class="ai-model">{_e(m["name"])}</span> '
            f'<span class="ai-size">{_e(what)}</span></li>')


# ── 4d: downloading ──────────────────────────────────────────────────────

def _downloading(plan: dict, w: dict, language: str) -> str:
    """A bar per model oMLX downloads. The script asks for the plan again
    and moves them, with the same words (`data-words`)."""
    texts = {"bar": w["bar"], "left": w["left"], "rate": GB_PER_MINUTE,
             "decimal": _decimal(language)}
    bars = "".join(_bar(m, w, language) for m in plan["models"] if m["state"] == "downloading")
    return (f'<div class="ai-bars" aria-live="polite" data-words="{_e(json.dumps(texts))}">'
            f'{bars}</div><p class="setup-note">{_e(w["leave"])}</p>'
            f'<div class="setup-act">{_link(w["go_on"], "/setup?step=services")}</div>')


def _bar(m: dict, w: dict, language: str) -> str:
    left = max(0, m["size"] - m["done"])
    text = w["bar"].format(name=m["name"], got=_gb(m["done"], language),
                           of=_gb(m["size"], language))
    if left:
        text += w["left"].format(min=minutes(left))
    return (f'<div class="ai-bar" data-role="{_e(m["role"])}"><p class="ai-bar-text">{_e(text)}</p>'
            f'<progress max="{max(1, int(m["size"]))}" value="{int(m["done"])}"></progress></div>')


# ── 4e: ready ────────────────────────────────────────────────────────────

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

def _answering(w: dict, watch: str) -> str:
    """While the check runs: one waiting line that names the address the
    open page asks (`setup-page.js`), which goes on by itself."""
    return (f'<div class="setup-box ai-answering"><p class="setup-waiting setup-live" '
            f'data-watch="{_e(watch)}">{_e(w["answering"])}</p></div>')


def _no_answer(plan: dict, answer: dict, w: dict, page: str) -> str:
    """The AI did not answer: what kind of failure it is (`stack ai
    check`'s `reason`), the server's own error in one line, then what
    fits. Only a server that cannot compute (HTTP 5xx) gets, with oMLX on
    this Mac, its download for this Mac's macOS (`plan["app"]`, as 4a
    offers it), because a build for another macOS lists its models and
    fails every answer so; a missing model and a refused key say what
    they are. Always "Check again" and another AI server."""
    reason = answer.get("reason", "")
    said = w["no_answer"].get(reason, w["no_answer"]["unreachable"])
    error = (f'<p class="setup-note">{_e(said.format(model=answer.get("model") or "?"))}</p>'
             f'<p class="ai-error"><code>{_e(answer.get("server_error", ""))}</code></p>')
    omlx = ""
    if reason == "server" and plan.get("installed") and plan.get("app"):
        app = plan["app"]
        macos = ("" if app["disk_image"] else
                 f'<p class="setup-note">{_e(w["get_macos"].format(version=app["version"]))}</p>')
        omlx = (f'<p class="setup-note">{_e(w["no_answer_omlx"])}</p>{macos}'
                f'<div class="setup-act">{_link(w["get_app"], app["url"], external=True, kind="soft")}'
                f'</div>')
    return (f'<div class="setup-box ai-no-answer">{error}{omlx}'
            f'<div class="setup-act">{_link(w["answer_again"], _with(page, "answer"))}</div>'
            f'<p class="setup-note"><a class="fs-link ai-other-link" '
            f'href="{_e(_with(page, "other"))}">{_e(w["other"])}</a></p></div>')


# ── Small helpers ────────────────────────────────────────────────────────

def _with(page: str, choice: str) -> str:
    return f"{page}&ai={choice}" if "?" in page else f"{page}?ai={choice}"


def _link(label: str, href: str, *, external: bool = False, kind: str = "go") -> str:
    out = ' target="_blank" rel="noopener"' if external else ""
    return f'<a class="fs-button" data-kind="{kind}" href="{_e(href)}"{out}>{_e(label)}</a>'


def _decimal(language: str) -> str:
    return "," if language == "de" else "."


def _gb(size: int, language: str) -> str:
    """Bytes as GB (`GB`) with one decimal, in the language's notation: "5.8", "5,8"."""
    return f"{size / GB:.1f}".replace(".", _decimal(language))


def _code(address: str) -> str:
    """An address as the page shows it, without its scheme: `localhost:8000`."""
    return f'<code>{_e(address.split("://", 1)[-1])}</code>'


def _e(text: str) -> str:
    return escape(str(text), quote=True)
