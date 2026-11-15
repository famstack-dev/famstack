"""AI stacklet first-run configuration: the one question how the AI runs.

The route form has three answers, and every surface asks this form:

    How should famstack's AI run?
      1) famstack installs oMLX (recommended)   [ai] provider = "managed"
      2) Your own AI server                     the address and key, checked
      3) No AI for now                          [ai] provider = "none"

The installer asks it in the terminal before core starts, a first `stack
up ai` asks it, and the setup page answers it through the configure API
(`POST /dashboard/api/configure/ai`). Each answer writes `[ai]` in one
way: famstack's oMLX and your own AI server through `stack ai switch`
(`managed --no-start`, or the address), which the terminal and the page
run for a later change as well; no AI for now as `provider = "none"`.

All questions are setup forms (ADR-021). Without a terminal they are
answered with `--answer route=external --answer address=<url> --answer
api_key=<key>`. A server that lists several models sends back a form to
pick one (`NeedsAnswers` from `apply`), which the terminal asks and a
screen shows. A server that refuses the key or does not answer stores
nothing, and its error is raised as `stack ai switch` says it.

Once `[ai]` names a route it asks nothing: `stack ai switch` changes the
route, and `stack up ai` only installs and starts what the route runs.
No server on this Mac is probed, and nothing on a server famstack does
not run is changed. The words are German for a German stack, else English.
"""

import shutil

__all__ = ["questions", "apply"]

from stack.configure import NeedsAnswers, Refused
from stack.forms import Form
from stack.stack import AI_EXTERNAL, AI_MANAGED, AI_OFF, OWN_SERVER_ADDRESS

# An address `stack ai switch` takes: http(s)://, or a host name or address
# with an optional port and path. It starts with a letter, a digit or "["
# (IPv6), so it never reads as an option. `managed` is the switch's word
# for famstack's oMLX, never an address.
ADDRESS = r"^(?!managed$)(https?://)?[A-Za-z0-9\[]\S*$"
# `--key=-` makes the switch read the key from stdin, so "-" alone is no key.
KEY = r"^(?!-$)"

NO_HOMEBREW = (
    "Homebrew is required to install oMLX. Install it with "
    '/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)", '
    "then run './stack up ai' again.")
# The reason of that refusal, as a screen names it.
HOMEBREW = "homebrew"


def questions(ctx, answers):
    """The next open form of the AI's setup, or None.

    None once `[ai]` names a route, whatever the setup marker says: an ai
    stacklet set up before is asked as well while no route is named.
    Otherwise the route form, and for your own AI server its address and key. Reads,
    never writes, and asks no server.

        questions(ctx, {})                     # the route form, "ai.route"
        questions(ctx, {"route": "external"})  # the server form, "ai.server"
        questions(ctx, {"route": "none"})      # None: apply writes it
    """
    if ctx.cfg("provider", default=""):
        return None
    forms = _forms(_language(ctx))
    if form := forms["route"].pending(answers):
        return form
    return forms["server"].pending(answers) if answers["route"] == AI_EXTERNAL else None


def apply(ctx, answers):
    """Write `[ai]` for the route answered; raise when nothing was written.

    famstack's oMLX: refused without Homebrew (`Refused`, reason
    `homebrew`), and by `stack ai switch managed --no-start` while the
    oMLX app runs (reason `omlx_app`) or when famstack's oMLX settings file
    cannot be read; `on_prepare` then installs it. Your own AI server:
    checked, asked one question and written by `stack ai switch`; several
    models raise `NeedsAnswers` with the form to pick one, a refusal with
    a reason (`key`, `unreachable`) raises `Refused`. No AI for now:
    `provider = "none"`.

        apply(ctx, {"route": "none"})      # [ai] provider = "none"
        apply(ctx, {"route": "external", "address": "http://192.168.1.30:8000/v1",
                    "api_key": ""})        # NeedsAnswers("ai.model") when it lists two models
    """
    if answers["route"] == AI_OFF:
        ctx.cfgs({"provider": AI_OFF})
    elif answers["route"] == AI_MANAGED:
        _use_famstacks_omlx(ctx)
    else:
        _use_your_server(ctx, answers)


# ── The routes ───────────────────────────────────────────────────────────

def _use_famstacks_omlx(ctx) -> None:
    """Route 1: `stack ai switch managed --no-start`, which refuses while
    the oMLX app runs, after the check that Homebrew can install oMLX.
    Nothing is written when one fails, so the next start asks again."""
    if not shutil.which("brew"):
        raise Refused(NO_HOMEBREW, HOMEBREW)
    _switch(ctx, ["managed", "--no-start"])


def _use_your_server(ctx, answers: dict) -> None:
    """Route 2: `stack ai switch <address> --key=<key> [--model=<id>]`, in
    this process, so the key never shows on a command line. The key is one
    argument, `--key=<key>`: argparse then takes a key that starts with "-"
    as the value, never as an option."""
    args = [answers["address"], f"--key={answers.get('api_key', '')}"]
    if answers.get("model"):
        args.append(f"--model={answers['model']}")
    switched = _switched(ctx, args)
    if switched.get("reason") == "model" and switched.get("models"):
        question = WORDS[_language(ctx)]["model"]
        raise NeedsAnswers(_model_form(switched["models"], question), answers)
    _refuse(switched)


def _switch(ctx, args: list[str]) -> None:
    """`stack ai switch <args>` in this process; its error raised."""
    _refuse(_switched(ctx, args))


def _refuse(switched: dict) -> None:
    """Raise the switch's error: `Refused` when it names a reason a screen
    says in its own words, else as it is.

        _refuse({"error": "Nothing answers at ...", "reason": "unreachable"})  # Refused
        _refuse({"openai_url": "http://localhost:42060/v1"})                   # returns
    """
    if "error" not in switched:
        return
    if switched.get("reason"):
        raise Refused(switched["error"], switched["reason"])
    raise RuntimeError(switched["error"])


def _switched(ctx, args: list[str]) -> dict:
    """`stack ai switch <args>` in this process: its answer. Its argument
    parser exits on what it cannot parse; that is an error here, never the
    end of the install that asked."""
    try:
        return ctx.stack.run_cli_command("ai", "switch", args) or {}
    except SystemExit:
        return {"error": "stack ai switch did not take this address."}


# ── The forms ────────────────────────────────────────────────────────────

def _language(ctx) -> str:
    return "de" if ctx.stack.config.get("core", {}).get("language") == "de" else "en"


def _forms(language: str) -> dict[str, Form]:
    """The route form and the server form, in `language`."""
    w = WORDS[language]
    route = Form(
        id="ai.route", title=w["title"], text=w["text"],
        schema={"type": "object", "required": ["route"], "properties": {"route": {
            "type": "string", "title": w["question"], "default": AI_MANAGED,
            "oneOf": [{"const": AI_MANAGED, "title": w["managed"]},
                      {"const": AI_EXTERNAL, "title": w["external"]},
                      {"const": AI_OFF, "title": w["none"]}]}}})
    server = Form(
        id="ai.server", title=w["external"], text=w["server_text"],
        schema={"type": "object", "required": ["address"], "properties": {
            "address": {"type": "string", "title": w["address"], "default": OWN_SERVER_ADDRESS,
                        "minLength": 1, "pattern": ADDRESS, "x-pattern-error": w["no_address"]},
            "api_key": {"type": "string", "title": w["key"], "writeOnly": True,
                        "pattern": KEY, "x-pattern-error": w["dash_key"]}}})
    return {"route": route, "server": server}


def _model_form(models: list[str], question: str) -> Form:
    """The models a server lists, to pick one; Enter takes the first."""
    return Form(id="ai.model", title=question, schema={
        "type": "object", "required": ["model"], "properties": {"model": {
            "type": "string", "title": question, "default": models[0],
            "oneOf": [{"const": m, "title": m} for m in models]}}})


WORDS = {
    "en": {
        "title": "AI",
        "question": "How should famstack's AI run?",
        "text": ("famstack connects your services to a local AI. famstack can install oMLX on "
                 "this Mac and look after its models: the first start downloads them and takes "
                 "20 to 30 minutes, and nothing leaves your network. Your own AI server is any "
                 "server that speaks the OpenAI API, for example the oMLX app.\n\n"
                 "Without an AI, documents and photos work. Tags, the wiki and the diary come "
                 "with the AI."),
        "managed": "famstack installs oMLX (recommended)",
        "external": "Your own AI server (address and key)",
        "none": "No AI for now",
        "server_text": ("famstack checks that the server answers, picks its chat model and asks "
                        "it one short question. It changes nothing on the server."),
        "address": "Address of the AI server",
        "key": "Key (leave empty if the server needs none)",
        "no_address": "This is not the address of an AI server, for example "
                      "http://192.168.1.20:8000/v1.",
        "dash_key": "A key cannot be just \"-\". Type the key, or leave it empty.",
        "model": "The server lists several models. Which one should famstack use?",
    },
    "de": {
        "title": "KI",
        "question": "Wie soll famstacks KI laufen?",
        "text": ("famstack verbindet deine Dienste mit einer lokalen KI. Auf Wunsch installiert "
                 "famstack oMLX auf diesem Mac und kümmert sich um die Modelle. Der erste Start "
                 "lädt sie herunter und dauert 20 bis 30 Minuten. Nichts verlässt dabei dein "
                 "Netz. Als eigener KI-Server passt jeder Server, der die OpenAI-API spricht, "
                 "zum Beispiel die oMLX-App.\n\n"
                 "Dokumente und Fotos funktionieren auch ohne KI. Schlagwörter, Wiki und "
                 "Tagebuch kommen erst mit ihr."),
        "managed": "famstack installiert oMLX (empfohlen)",
        "external": "Eigener KI-Server (Adresse und Schlüssel)",
        "none": "Erst einmal ohne KI",
        "server_text": ("famstack prüft, ob der Server antwortet, wählt sein Chat-Modell und "
                        "stellt ihm eine kurze Frage. Auf dem Server ändert famstack nichts."),
        "address": "Adresse des KI-Servers",
        "key": "Schlüssel (leer lassen, wenn der Server keinen braucht)",
        "no_address": "Das ist keine Adresse eines KI-Servers. Ein Beispiel: "
                      "http://192.168.1.20:8000/v1",
        "dash_key": "Ein Schlüssel kann nicht nur \"-\" sein. Gib den Schlüssel ein oder lass "
                    "ihn leer.",
        "model": "Der Server bietet mehrere Modelle an. Welches soll famstack nutzen?",
    },
}
