"""The AI step of the setup page: the AI from the oMLX app, or from the terminal.

    import ai_step

    sub = ai_step.sub_step(done=False, tile=ai_tile, plan=plan, choice="")
    sub                                              # "choose", "connect", "models", ...
    ai_step.html(sub, plan, tile_html, here=True, language="en")

Step 5 of `/setup` (`setup_page.py`) sets up the AI. The design's route is
the oMLX app: a Mac app that runs the AI server on this Mac. The step finds
it, takes its API key, shows what this Mac's memory profile recommends,
downloads that with a bar per model and starts the AI. The route of
earlier releases stays one link away: "Install oMLX for me instead" shows
`./stack up ai` for the Mac's terminal, and the AI's tile follows that run.

Every sub-step follows from the state, never from what the page did
before, so a reload, a second browser or a return after an hour shows the
same one:

    done ─────────────────────────────────────────────▶ ready     (5e)
    the AI's tile follows a run ──────────────────────▶ starting  (its tile)
    the AI stacklet did not answer, or ?ai=install ───▶ install   (the terminal)
    not connected, the app answers, ?ai=connect ──────▶ connect   (5b)
    not connected ────────────────────────────────────▶ choose    (5a)
    connected, a model downloading ───────────────────▶ downloading (5d)
    connected ────────────────────────────────────────▶ models    (5c)

`plan` is the AI stacklet's answer, `stack ai recommended show` through
the host API (`stacklets/ai/recommended.py`): `found` (the app's address),
`connected`, `memory_gb`, `profile`, `speech` and `models`, a row per
role (chat, listen, speak) with its `state` (on_mac, downloading, failed,
to_download), `size` and `done` in bytes.

The page's script (`dashboard/ai-step.js`) does the rest: Connect posts
the key to the setup forms' route (`/dashboard/api/configure/ai`), and only
from this Mac, as `setup_forms.py` keeps every secret; Download posts the
speech choice (`/dashboard/api/ai/download`); the bars ask
`/dashboard/api/ai` until nothing downloads, then the script starts
`stack up ai`, whose run the tile follows until 5e. A Mac that already
has every model gets "Continue" instead of "Download".

Pure: no web framework, no I/O. The words are here, English and German.
"""

from __future__ import annotations

import json
from html import escape

import stack_widget

__all__ = ["SUB_STEPS", "CHOICES", "OMLX_APP", "sub_step", "heading", "html", "words", "total",
           "minutes"]

SUB_STEPS = ("ready", "starting", "install", "connect", "choose", "downloading", "models")
# What the admin chose on the page, in `?ai=`: the app's key, or the terminal.
CHOICES = ("connect", "install")
# Where the oMLX app is downloaded: its releases, a signed and notarized
# disk image. A link out; nothing is loaded from there.
OMLX_APP = "https://github.com/jundot/omlx/releases/latest"
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
        "get": "Get the oMLX app",
        "get_text": "Download it, drag it to Applications and open it. Then come back here.",
        "get_app": "Download oMLX",
        "instead": "Install oMLX for me instead (20 to 30 minutes in the terminal)",
        "connect_heading": "Connect the oMLX app", "key": "API key",
        "key_help": "In the oMLX app: Settings, Security, API Key, Copy.",
        "key_kept": "famstack keeps the key on this Mac and uses it only to talk to the oMLX app.",
        "connect": "Connect", "elsewhere": "Enter the key on the Mac that runs famstack.",
        "download_elsewhere": "Download the models on the Mac that runs famstack.",
        "refused": "The oMLX app did not accept this key.",
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
        "can": {"reads": "Reads letters and answers questions",
                "hears": "Understands voice messages",
                "hears_off": "Voice messages: speech is off", "speaks": "Speaks"},
    },
    "de": {
        "found": "Die oMLX-App läuft auf diesem Mac", "use": "oMLX-App verwenden",
        "get": "Hol dir die oMLX-App",
        "get_text": "Lade sie herunter, zieh sie in den Ordner „Programme“ und öffne sie. "
                    "Komm dann hierher zurück.",
        "get_app": "oMLX herunterladen",
        "instead": "Stattdessen oMLX von famstack installieren lassen (20 bis 30 Minuten im Terminal)",
        "connect_heading": "Verbinde die oMLX-App", "key": "API-Schlüssel",
        "key_help": "Klick in der oMLX-App auf „Settings“, „Security“, „API Key“ und dann "
                    "auf „Copy“.",
        "key_kept": "famstack speichert den Schlüssel auf diesem Mac und nutzt ihn nur für die "
                    "oMLX-App.",
        "connect": "Verbinden",
        "elsewhere": "Gib den Schlüssel an dem Mac ein, auf dem famstack läuft.",
        "download_elsewhere": "Lade die Modelle an dem Mac herunter, auf dem famstack läuft.",
        "refused": "Die oMLX-App hat diesen Schlüssel nicht angenommen.",
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


def sub_step(*, done: bool, tile: dict | None, plan: dict | None, choice: str) -> str:
    """Which part of step 5 the page shows, one of `SUB_STEPS`, from the
    state alone (the table in the module's header). `choice` is `?ai=`.

        sub_step(done=False, tile=None, plan={"found": "", "connected": False},
                 choice="")                                        # "choose"
    """
    if done:
        return "ready"
    if tile and tile["state"] == stack_widget.ADDING:
        return "starting"
    if plan is None or choice == "install":
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


def html(sub: str, plan: dict | None, *, here: bool, language: str, page: str) -> str:
    """The part of step 5 for `sub`, below the step's heading and text and
    above the AI's tile, which `setup_page.py` adds. `page` is the step's
    own address (`/setup?step=ai`), for "Check again" and the choices.

        html("choose", {"found": "", "connected": False, "models": []},
             here=True, language="en", page="/setup?step=ai")
        # '<div class="setup-box ai-get">…Get the oMLX app…</div>…'
    """
    w = words(language)
    match sub:
        case "choose":
            return _choose(plan or {}, w, page, language)
        case "connect":
            return _connect(w, here, page)
        case "models":
            return _models(plan or {}, w, language, here)
        case "downloading":
            return _downloading(plan or {}, w, language)
        case "ready":
            return _ready(plan, w)
    return ""


def heading(sub: str, language: str) -> str:
    """The step's heading for `sub`, "" for the step's own ("Give famstack its AI").

        heading("models", "de")    # "Das bekommt eure KI"
    """
    w = words(language)
    return {"connect": w["connect_heading"], "models": w["models_heading"],
            "downloading": w["downloading_heading"], "ready": w["ready_heading"]}.get(sub, "")


# ── 5a: choose ───────────────────────────────────────────────────────────

def _choose(plan: dict, w: dict, page: str, language: str) -> str:
    instead = (f'<p class="setup-note"><a class="fs-link ai-instead" href="{_e(_with(page, "install"))}">'
               f'{_e(w["instead"])}</a></p>')
    if plan.get("found"):
        where = plan["found"].split("://", 1)[-1]
        return (f'<div class="setup-box ai-found"><p class="ai-found-line">'
                f'<span class="dot" data-status="running" aria-hidden="true"></span>'
                f'{_e(w["found"])} · <code>{_e(where)}</code></p>'
                f'<div class="setup-act">{_link(w["use"], _with(page, "connect"))}</div></div>'
                + instead)
    check = stack_widget.words(language)["check_again"]
    return (f'<div class="setup-box ai-get"><h2 class="setup-subheading">{_e(w["get"])}</h2>'
            f'<p class="setup-note">{_e(w["get_text"])}</p><div class="setup-act">'
            f'{_link(w["get_app"], OMLX_APP, external=True)}'
            f'{_link(check, page, kind="soft")}</div></div>' + instead)


# ── 5b: connect ──────────────────────────────────────────────────────────

def _connect(w: dict, here: bool, page: str) -> str:
    """The key's field, only on this Mac: anywhere else the dashboard is
    plain HTTP on the home network, and a key typed there would cross the
    Wi-Fi in the clear (`setup_forms.py`)."""
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


# ── 5c: what your AI gets ────────────────────────────────────────────────

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
             "continue": w["continue"], "rate": GB_PER_MINUTE, "decimal": _decimal(language)}
    left = total(plan, speech=speech)
    line = (w["total"].format(gb=_gb(left, language), min=minutes(left)) if left
            else w["nothing"])
    return (f'<p class="setup-text">{_e(w["recommended"].format(gb=plan.get("memory_gb", 0)))}</p>'
            f'<div class="ai-models" data-words="{_e(json.dumps(sizes))}">'
            f'<ul class="ai-rows">{rows}</ul>{switch}'
            f'<p class="ai-total">{_e(line)}</p>'
            f'<div class="setup-act"><button class="fs-button ai-download" data-kind="go" '
            f'type="button" hidden>{_e(w["download"] if left else w["continue"])}</button></div>'
            f'</div>')


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


# ── 5d: downloading ──────────────────────────────────────────────────────

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


# ── 5e: ready ────────────────────────────────────────────────────────────

def _ready(plan: dict | None, w: dict) -> str:
    """What the AI can do: reading always, hearing and speaking with speech."""
    can = w["can"]
    speech = bool((plan or {}).get("speech", True))
    items = [("done", can["reads"])]
    items += [("done", can["hears"]), ("done", can["speaks"])] if speech else \
        [("off", can["hears_off"])]
    marks = {"done": "✓", "off": "○"}
    return ('<ul class="ai-can">' + "".join(
        f'<li data-status="{status}"><span aria-hidden="true">{marks[status]}</span> {_e(text)}</li>'
        for status, text in items) + "</ul>")


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


def _e(text: str) -> str:
    return escape(str(text), quote=True)
