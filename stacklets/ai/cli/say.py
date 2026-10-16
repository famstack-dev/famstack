"""stack ai say <text>: speak any text aloud on the Mac.

The text goes to the ai stacklet's speech server and plays on this Mac's
speakers. The voice is the default for `[ai] language`, as for
`stack ai hello`, unless `--voice` names one of the voices the speech
server loads.

    stack ai say "Dinner is ready"
    stack ai say "Dinner is ready" --voice nova
"""

HELP = "Speak a text aloud on this Mac"

import argparse
import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from speech import default_voice, known_voices, play, synthesize  # noqa: E402

USAGE = 'stack ai say "<text>" [--voice <name>]'


def _parse(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="stack ai say", description=HELP, usage=USAGE)
    p.add_argument("text", nargs="*", help="what to say; quotes are optional")
    p.add_argument("--voice", default="", help="one of the speech server's voices")
    return p.parse_args(args)


def run(args, stacklet, config):
    opts = _parse(args)
    text = " ".join(opts.text).strip()
    if not text:
        return {"error": f"Nothing to say. Usage: {USAGE}"}

    language = config.get("stack", {}).get("ai", {}).get("language", "en")
    voice = opts.voice or default_voice(language)
    voices = known_voices()
    if voice not in voices:
        return {"error": f"The speech server has no voice '{voice}'. "
                         f"Voices: {', '.join(voices)}"}

    print(f"\n  Speaking as {voice}...\n")
    try:
        audio = synthesize(text, voice)
    except urllib.error.HTTPError as e:
        return {"error": f"The speech server failed ({e.code}). "
                         "Check 'docker logs stack-ai-speech'."}
    except OSError:
        return {"error": "The speech server is not reachable. "
                         "Start the ai stacklet: stack up ai"}
    if not audio:
        return {"error": "The speech server returned no audio. "
                         "Check 'docker logs stack-ai-speech'."}

    play(audio)
    return {"ok": True, "voice": voice}
