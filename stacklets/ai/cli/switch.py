"""stack ai switch <target> — choose the AI server the stack uses.

The stack talks to one OpenAI-compatible address, `[ai] openai_url`,
whoever serves it: the engine the stack manages on this Mac, an AI app
on this Mac, another machine on the network, or a hosted provider.

`managed` is the engine the stack runs: it installs oMLX when it is
missing and starts it, through `stack up ai`, and gives up with `[ai]`
as it was when that fails. Any other target is an address the stack
does not manage: nothing is installed. Either way the command checks the
server answers, picks a model it actually has, and writes `[ai]` in
stack.toml.

Voice messages go to `[ai] whisper_url` when a speech server is set,
whatever the AI server offers. Without one they go to the AI server,
which can transcribe only if it lists a speech-to-text model: the
command looks for one and records it as `[ai] whisper_model`, and says
when there is none. `--whisper <url>` sets a speech server, `--whisper
ai` removes it.

Once `[ai]` is written for a server the stack does not manage, the
command asks it one tiny question and keeps the answer (`answer.py`), as
the AI's run does: a server that lists its models and fails every
completion is a warning with its error, and the setup page says the AI
does not answer. `managed` asks once, through the run it starts.

Nothing restarts. The command names the running stacklets that read the
address, and `stack restart` with those ids applies it.

`--key -` reads the key from stdin, as `--answers -` does: a command line
is visible to every process on the Mac. The setup page's "Use another AI
server" runs the command that way, through the host API. An error that
the page explains in its own words carries a `reason`: `key` (the server
wants a key, or refused the one given), `unreachable` (nothing answers)
or `model` (several models, or not the one asked for; `models` lists
them).

    stack ai switch managed
    stack ai switch localhost:8888
    stack ai switch 192.168.1.20:11434
    stack ai switch https://api.example.com --key sk-... --model gpt-4.1-mini
    echo sk-... | stack ai switch https://api.example.com --key -
    stack ai switch 192.168.1.20:8000 --whisper 192.168.1.20:42062
"""

HELP = "Choose the AI server: the engine the stack manages, or any other address"

import argparse
import sys
import tomllib
from pathlib import Path

from stack import runs
from stack.profile import speech_is_off

from .. import host_services
from ..local_mode import MANAGED_ENGINE, bring_up, managed_engine
from ..omlx_service import UnreadableSettings
# Any value works for a server without auth, and the OpenAI clients
# refuse an empty key.
from ..omlx import PLACEHOLDER_KEY as _NO_KEY

# `--key -`: the key comes on stdin, never on the command line.
_FROM_STDIN = "-"

# The target that names the engine the stack manages.
MANAGED = "managed"

# `--whisper ai`: no dedicated speech server, the AI server transcribes.
_VIA_AI = "ai"


def _parse(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="stack ai switch", description=HELP)
    p.add_argument("target", help="'managed' for the engine the stack runs on "
                                  "this Mac, or the address of an "
                                  "OpenAI-compatible server")
    p.add_argument("--key", default="", help="API key, if the server needs one; "
                                              "'-' reads it from stdin")
    p.add_argument("--model", default="", help="model id the stack uses by default")
    p.add_argument("--whisper", default="",
                   help="a dedicated speech-to-text server for voice messages, "
                        "or 'ai' to send them to the AI server")
    p.add_argument("--whisper-key", default="",
                   help="API key for the speech-to-text server, if it needs one")
    return p.parse_args(args)


def _matches(model: str, available: list[str]) -> str | None:
    """The listed id for a model, compared by short name either way.
    Servers list ids with or without the org prefix."""
    short = model.rsplit("/", 1)[-1]
    return next((m for m in available if short in m or m in short), None)


def _choose_model(asked: str, current: str, available: list[str]) -> dict:
    if not available:
        # Some providers do not list models. Nothing to check against.
        return {"model": asked or current}
    if asked:
        if found := _matches(asked, available):
            return {"model": found}
        return {"error": f"The server does not list {asked}.",
                "reason": "model", "models": available}
    if current and (found := _matches(current, available)):
        return {"model": found}
    if len(available) == 1:
        return {"model": available[0]}
    return {"error": "The server lists several models. Choose one with "
                     "--model <id>:\n" + "\n".join(f"    {m}" for m in available),
            "reason": "model", "models": available}


def _speech_models(models: list[str]) -> list[str]:
    """Speech-to-text models in a server's list. A server that answers
    the transcription route without one loaded fails every voice message
    with "model not found", so the route alone proves nothing."""
    return [m for m in models if "whisper" in m.lower() or "transcribe" in m.lower()]


def _speech_server(opts, managed: bool, current: str, *, speech: bool) -> str:
    """The dedicated speech server after this command, "" for none.

    An explicit `--whisper` decides. Otherwise `managed` brings the
    stack's Whisper back with its engine when `speech` is on, and none
    with speech off (`stack.profile.speech_is_off`), because its run
    installs none. Any other switch keeps the speech server the admin
    already chose: keeping voice on this Mac while text goes elsewhere is
    a reasonable choice to have made.
    """
    if opts.whisper == _VIA_AI:
        return ""
    if opts.whisper:
        from ..backend import normalize_url
        return normalize_url(opts.whisper)
    if managed:
        return MANAGED_ENGINE["whisper_url"] if speech else ""
    return current


# The template variables this command changes the value of.
_CHANGED_VARS = ("{ai_openai_", "{ai_whisper_", "{ai_default_model")


def ai_consumers(repo_root: Path) -> set[str]:
    """Stacklets whose environment is rendered from a variable this
    command changes. They pick up the new value only when their env is
    rendered again."""
    ids = set()
    for manifest in repo_root.glob("stacklets/*/stacklet.toml"):
        env = tomllib.loads(manifest.read_text()).get("env", {}).get("defaults", {})
        if any(var in str(v) for v in env.values() for var in _CHANGED_VARS):
            ids.add(manifest.parent.name)
    return ids


def running_stacklets() -> set[str]:
    from stack.docker import project_states
    return {i for i, state in project_states().items()
            if state in ("running", "starting", "failing")}


def _run_the_engine(config: dict, ai: dict, model: str) -> dict | None:
    """Hand `[ai]` to the engine the stack manages and bring it up.

    `stack up ai` installs whatever is missing (the stacklet, or oMLX on
    a Mac that ran only the voice services), downloads the default model
    and starts it, so a `--model` is written before it runs. When it
    fails, `[ai]` goes back to what it was, so the stack keeps a server
    that answers, and the LaunchAgents the run started are stopped and
    removed (`host_services`), so none runs for a server `[ai]` does not
    name, now or after the next login. A settings file of famstack's oMLX
    that cannot be read stops it before anything is written. Returns an
    error, or None when the engine is up.
    """
    set_cfg = config["set_cfg"]
    try:
        engine = managed_engine(config["data_dir"], ai.get("openai_key", ""))
    except UnreadableSettings as e:
        return {"error": str(e)}
    settings = {**engine, **({"default": model} if model else {})}
    before = {k: ai.get(k, "") for k in settings}
    agents_before = host_services.up(host_services.shell)
    for key, value in settings.items():
        set_cfg("ai", key, value)
    code = bring_up(config)
    if code == 0:
        return None
    for key, value in before.items():
        set_cfg("ai", key, value)
    # No agent stays for a server `[ai]` no longer names: what the failed
    # run started goes, what ran before (Whisper beside a server
    # elsewhere) stays.
    for label in host_services.up(host_services.shell) - agents_before:
        host_services.stop(host_services.shell, label)
    kept = before["openai_url"] or "no AI server"
    if code == runs.EXIT_ALREADY_RUNNING:
        # Refused, not failed: through the host API a run does not wait its
        # turn (`stack.runs`), and its refusal went to stderr only.
        holder = runs.running(Path(config["instance_dir"]))
        busy = f"another run goes on: {runs.describe(holder)}" if holder else "another run went on"
        return {"error": f"{busy}, so the stack still uses {kept}. Try again when it is done."}
    return {"error": f"Bringing up the ai stacklet failed, so the stack "
                     f"still uses {kept}. Its output above says why."}


def _written_ai(config: dict) -> dict:
    """`[ai]` as `stack.toml` holds it now, after the run wrote it."""
    toml = Path(config["instance_dir"]) / "stack.toml"
    return tomllib.loads(toml.read_text()).get("ai", {})


def run(args, stacklet, config):
    from ..answer import check, said
    from ..backend import normalize_url, _probe
    from stack.ai.probe import stays_home, transcribes

    opts = _parse(args)
    ai = config.get("stack", {}).get("ai", {})

    managed = opts.target == MANAGED
    if managed and (failed := _run_the_engine(config, ai, opts.model)):
        return failed
    if managed:
        # `[ai]` as the run left it: the run asked the engine with it and
        # kept that answer, which the setup page reads for this `[ai]`.
        # So the address, the key and the model stay as they are.
        ai = _written_ai(config)
    url = ai.get("openai_url", "") if managed else normalize_url(opts.target)
    key = ai.get("openai_key", "") if managed else _key(opts.key)
    speech_on = not speech_is_off({"ai": ai})

    probe = _probe(url, key)
    if probe.needs_auth:
        if key:
            return {"error": f"The server at {url} did not accept this API key.",
                    "reason": "key"}
        return {"error": f"The server at {url} wants an API key. "
                         "Pass it with --key <key>.", "reason": "key"}
    if not probe.reachable:
        if managed:
            return {"error": f"The engine the stack manages is not answering "
                             f"at {url}. './stack logs ai' may say why."}
        return {"error": f"Nothing answers at {url}. Check the address "
                         "and that the server is running.", "reason": "unreachable"}

    speech_models = _speech_models(probe.models)
    chat = [m for m in probe.models if m not in speech_models]
    chosen = ({"model": ai.get("default", "")} if managed
              else _choose_model(opts.model, ai.get("default", ""), chat))
    if "error" in chosen:
        return chosen

    whisper = _speech_server(opts, managed, ai.get("whisper_url", ""), speech=speech_on)
    if not whisper and not speech_models and opts.whisper == _VIA_AI:
        return {"error": f"The AI server at {url} lists no speech-to-text "
                         "model, so it cannot transcribe voice messages.",
                "models": probe.models}

    notes, warnings = [], []
    speech_model = ""
    if whisper:
        whisper_key = opts.whisper_key or ai.get("whisper_key") or _NO_KEY
        if not transcribes(whisper, whisper_key):
            warnings.append(
                f"The speech server at {whisper} does not answer the "
                "transcription call, so voice messages will fail.")
        if speech_models:
            notes.append(
                f"The AI server also lists {speech_models[0]}. "
                "'--whisper ai' sends voice messages there instead.")
    elif speech_models and speech_on:
        # With speech off no voice messages are transcribed, so a speech
        # model the server lists is neither set nor named.
        current = ai.get("whisper_model", "")
        speech_model = current if current in speech_models else speech_models[0]
    elif speech_on:
        # With speech off there are no voice messages to fail.
        warnings.append(
            f"The AI server at {url} lists no speech-to-text model, so "
            "voice messages will fail. Pass --whisper <url> for a "
            "speech server.")

    set_cfg = config["set_cfg"]
    set_cfg("ai", "provider", "managed" if managed else "external")
    set_cfg("ai", "openai_url", url)
    set_cfg("ai", "openai_key", key or _NO_KEY)
    if chosen["model"]:
        set_cfg("ai", "default", chosen["model"])
    set_cfg("ai", "whisper_url", whisper)
    if opts.whisper_key or not whisper:
        set_cfg("ai", "whisper_key", opts.whisper_key)
    if speech_model:
        set_cfg("ai", "whisper_model", speech_model)

    # The server listed its models; whether it answers is one question more,
    # asked with what was just written and kept for the setup page. The
    # engine the stack manages was asked by its run (`stack up ai`).
    asked = None if managed else check(
        config["instance_dir"], {"openai_url": url, "openai_key": key or _NO_KEY,
                                 "default": chosen["model"] or ai.get("default", "")})
    if asked and not asked["ok"]:
        warnings.append(said(asked))

    voice_url = whisper or url
    leaving = sorted({u for u in (url, voice_url) if not stays_home(u)})
    if leaving:
        warnings.append(
            f"{' and '.join(leaving)} is outside your home network. Document "
            "text, notes, chat questions and voice messages are sent there "
            "and processed by whoever runs that server.")

    running = running_stacklets()
    if not managed and "ai" in running and ai.get("provider") == "managed":
        notes.append("The engine the stack manages is still running and no "
                     "longer used. './stack down ai' stops it and frees its "
                     "memory.")

    result = {
        "openai_url": url,
        "model": chosen["model"],
        "whisper_url": voice_url,
        "whisper_model": speech_model,
        "restart": sorted(ai_consumers(Path(config["repo_root"])) & running),
    }
    if asked:
        result["answer"] = asked
    if warnings:
        result["warnings"] = warnings
    if notes:
        result["notes"] = notes

    # Also without a terminal: over ssh or from a script the admin would
    # otherwise see nothing. Under --json this goes to stderr.
    _report(result, dedicated=bool(whisper))
    return result


def _key(given: str) -> str:
    """The API key: `given`, or stdin's first line for `--key -`.

        _key("sk-123")    # "sk-123"
        _key("-")         # what the host API sent on stdin, "" for nothing
    """
    if given != _FROM_STDIN:
        return given
    return sys.stdin.readline().strip()


def _report(result: dict, *, dedicated: bool) -> None:
    from stack.prompt import nl, done, out, warn, dim, TEAL, RESET
    nl()
    done(f"AI server: {result['openai_url']}")
    done(f"Model: {result['model'] or '(unchanged)'}")
    if dedicated:
        out(f"   Voice messages: {result['whisper_url']} (speech server)")
    elif result["whisper_model"]:
        out(f"   Voice messages: the AI server, {result['whisper_model']}")
    for n in result.get("notes", []):
        dim(f"   {n}")
    for w in result.get("warnings", []):
        nl()
        warn(w)
    nl()
    if result["restart"]:
        out("Apply it to what is running:")
        out(f"  {TEAL}./stack restart {' '.join(result['restart'])}{RESET}")
    else:
        dim("Nothing that uses AI is running. It applies on the next 'stack up'.")
    nl()
