"""stack ai connect <url> — choose the AI server the stack uses.

The stack talks to one OpenAI-compatible address, `[ai] openai_url`,
whoever serves it: the local engine `stack up ai` installs, another
machine on the network, or a hosted provider. This command points the
stack at one without installing anything here. It checks the server
answers, picks a model it actually has, and writes `[ai]` in stack.toml.
`local` switches back to the engine on this Mac.

Voice messages go to `[ai] whisper_url` when a speech server is set,
whatever the AI server offers. Without one they go to the AI server,
which can transcribe only if it lists a speech-to-text model: the
command looks for one and records it as `[ai] whisper_model`, and says
when there is none. `--whisper <url>` sets a speech server, `--whisper
ai` removes it.

Nothing restarts. The command names the running stacklets that read the
address, and `stack restart` with those ids applies it.

    stack ai connect 192.168.1.20:11434
    stack ai connect https://api.example.com --key sk-... --model gpt-4.1-mini
    stack ai connect 192.168.1.20:8000 --whisper 192.168.1.20:42062
    stack ai connect local
"""

HELP = "Choose the AI server: another machine, a hosted provider, or local"

import argparse
import sys
import tomllib
from pathlib import Path

# Any value works for a server without auth, and the OpenAI clients
# refuse an empty key. Same placeholder the local engine uses.
_NO_KEY = "local"

# The engine and speech server `stack up ai` installs on this Mac.
_LOCAL_URL = "http://localhost:42060/v1"
_LOCAL_WHISPER = "http://localhost:42062/v1"

# `--whisper ai`: no dedicated speech server, the AI server transcribes.
_VIA_AI = "ai"


def _parse(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="stack ai connect", description=HELP)
    p.add_argument("url", help="address of an OpenAI-compatible server, "
                               "or 'local' for the engine on this Mac")
    p.add_argument("--key", default="", help="API key, if the server needs one")
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
                "models": available}
    if current and (found := _matches(current, available)):
        return {"model": found}
    if len(available) == 1:
        return {"model": available[0]}
    return {"error": "The server lists several models. "
                     "Choose one with --model <id>.",
            "models": available}


def _speech_models(models: list[str]) -> list[str]:
    """Speech-to-text models in a server's list. A server that answers
    the transcription route without one loaded fails every voice message
    with "model not found", so the route alone proves nothing."""
    return [m for m in models if "whisper" in m.lower() or "transcribe" in m.lower()]


def _speech_server(opts, local: bool, current: str) -> str:
    """The dedicated speech server after this command, "" for none.

    An explicit `--whisper` decides. Otherwise `local` brings the local
    Whisper back with the local engine, and any other switch keeps the
    speech server the admin already chose: keeping voice on this Mac
    while text goes elsewhere is a reasonable choice to have made.
    """
    if opts.whisper == _VIA_AI:
        return ""
    if opts.whisper:
        from backend import normalize_url
        return normalize_url(opts.whisper)
    if local:
        return _LOCAL_WHISPER
    return current


# The template variables this command changes the value of.
_CHANGED_VARS = ("{ai_openai_", "{ai_whisper_", "{ai_default_model")


def _ai_consumers(repo_root: Path) -> set[str]:
    """Stacklets whose environment is rendered from a variable this
    command changes. They pick up the new value only when their env is
    rendered again."""
    ids = set()
    for manifest in repo_root.glob("stacklets/*/stacklet.toml"):
        env = tomllib.loads(manifest.read_text()).get("env", {}).get("defaults", {})
        if any(var in str(v) for v in env.values() for var in _CHANGED_VARS):
            ids.add(manifest.parent.name)
    return ids


def _running(ids: set[str]) -> list[str]:
    from stack.docker import project_states
    states = project_states()
    return sorted(i for i in ids if states.get(i) in ("running", "starting", "failing"))


def run(args, stacklet, config):
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from backend import normalize_url, _probe
    from stack.ai.probe import stays_home, transcribes

    opts = _parse(args)
    ai = config.get("stack", {}).get("ai", {})
    ai_installed = (Path(config["instance_dir"]) / ".stack" / "ai.setup-done").exists()

    local = opts.url == "local"
    if local and not ai_installed:
        return {"error": "The local AI engine is not installed. "
                         "Install it with './stack up ai'."}
    url = _LOCAL_URL if local else normalize_url(opts.url)
    key = _NO_KEY if local else opts.key

    probe = _probe(url, key)
    if probe.needs_auth:
        return {"error": f"The server at {url} wants an API key. "
                         "Pass it with --key <key>."}
    if not probe.reachable:
        if local:
            return {"error": f"The local AI engine is not answering at {url}. "
                             "Start it with './stack up ai'."}
        return {"error": f"Nothing answers at {url}. Check the address "
                         "and that the server is running."}

    speech = _speech_models(probe.models)
    chat = [m for m in probe.models if m not in speech]
    chosen = _choose_model(opts.model, ai.get("default", ""), chat)
    if "error" in chosen:
        return chosen

    whisper = _speech_server(opts, local, ai.get("whisper_url", ""))
    if not whisper and not speech and opts.whisper == _VIA_AI:
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
        if speech:
            notes.append(
                f"The AI server also lists {speech[0]}. "
                "'--whisper ai' sends voice messages there instead.")
    elif speech:
        current = ai.get("whisper_model", "")
        speech_model = current if current in speech else speech[0]
    else:
        warnings.append(
            f"The AI server at {url} lists no speech-to-text model, so "
            "voice messages will fail. Pass --whisper <url> for a "
            "speech server.")

    set_cfg = config["set_cfg"]
    set_cfg("ai", "provider", "managed" if local else "external")
    set_cfg("ai", "openai_url", url)
    set_cfg("ai", "openai_key", key or _NO_KEY)
    if chosen["model"]:
        set_cfg("ai", "default", chosen["model"])
    set_cfg("ai", "whisper_url", whisper)
    if opts.whisper_key or not whisper:
        set_cfg("ai", "whisper_key", opts.whisper_key)
    if speech_model:
        set_cfg("ai", "whisper_model", speech_model)

    voice_url = whisper or url
    leaving = sorted({u for u in (url, voice_url) if not stays_home(u)})
    if leaving:
        warnings.append(
            f"{' and '.join(leaving)} is outside your home network. Document "
            "text, notes, chat questions and voice messages are sent there "
            "and processed by whoever runs that server.")

    result = {
        "openai_url": url,
        "model": chosen["model"],
        "whisper_url": voice_url,
        "whisper_model": speech_model,
        "restart": _running(_ai_consumers(Path(config["repo_root"]))),
    }
    if warnings:
        result["warnings"] = warnings
    if notes:
        result["notes"] = notes

    if sys.stderr.isatty():
        _report(result, dedicated=bool(whisper))
    return result


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
